"""hledger as the background second check of every commit.

The JSON strict check is the gate every save waits for (`make commit` sets
EXPENSES_HLEDGER_CHECK=background). hledger, when installed, checks each commit
afterwards, here:

- **When:** after every commit through `make commit` (`save_timing.make_commit` calls
  `committed`), at start-up, on "Check again", and when the worker's tick sees that the
  main bookmark moved (a commit made outside the server, such as `make commit` in a
  terminal). Without hledger nothing runs, nothing is recorded and no banner shows.
- **Coalesced:** at most one check runs and at most one waits. A waiting check reads the
  newest commit when it starts, so the last check always covers the latest commit.
- **Snapshot, no lock:** `second_check.py snapshot` reads the store at the commit with
  read-only jj commands, renders the journal in a temporary folder and runs hledger
  there. It never takes the ledger lock and never reads the working copy, so a save in
  progress is never seen half-written.
- **Record:** `drafts/ledger-check.json` (ignored, temporary): the commit checked, the
  time, pass or fail, hledger's finding and the entries changed since the last good check.
- **When it fails:** the banner and the "Ledger check" item in Queue › Needs you; statement
  Apply (which also confirms a balance) is refused for the accounts involved; "Ask the
  agent to fix it" opens Edit entry with the agent's proposal, which the person saves.
  The item closes when a check passes.
"""
from __future__ import annotations

from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from agent import now
from settings import APPLICATION

sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
import second_check  # noqa: E402

LOG = logging.getLogger(__name__)
SCRIPT = APPLICATION / 'scripts/ledger/second_check.py'
# How many changed entries the item lists; the accounts involved still count all of them.
LISTED = 50
_active: dict[Path, 'LedgerCheck'] = {}
_active_mutex = threading.Lock()


def committed(root) -> None:
    """A commit through `make commit` finished: check it in the background."""
    with _active_mutex:
        check = _active.get(Path(root).resolve())
    if check:
        check.request('commit')


class LedgerCheck:
    # The worker's tick looks for commits made outside the server at most this often.
    tick_interval = 30.0

    def __init__(self, settings, *, runner=None, hledger: str | None | bool = True, clock=time.monotonic):
        self.settings, self.root = settings, settings.ledger
        self.path = self.root / 'drafts/ledger-check.json'
        self.hledger = second_check.find_hledger() if hledger is True else hledger or None
        self.runner = runner or self.run_snapshot
        self.clock = clock
        self.condition = threading.Condition(threading.RLock())
        self.running = self.queued = False
        self.closed = True
        self.thread = None
        self.runs = 0
        self.last_tick = None
        self.drafts = None
        self.child = None  # the running snapshot process, killed on close
        self.checking_commit = None
        # One "Ask the agent" at a time, and one Check again waiting: more answer with the status.
        self.fix_mutex, self.again_mutex = threading.Lock(), threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.hledger) and getattr(self.settings, 'ledger_check', True)

    # Lifecycle and scheduling.

    def start(self):
        if not self.available:
            return
        with self.condition:
            self.closed = False
        with _active_mutex:
            _active[Path(self.root).resolve()] = self
        self.request('start-up')

    def close(self):
        with _active_mutex:
            if _active.get(Path(self.root).resolve()) is self:
                del _active[Path(self.root).resolve()]
        with self.condition:
            self.closed = True
            self.queued = False
            thread, child = self.thread, self.child
        if child and child.poll() is None:
            child.kill()  # a hung jj or hledger must not hold up the service's stop
        if thread:
            thread.join(timeout=10)

    def request(self, reason='') -> bool:
        """Check the latest commit soon. Returns False when no check can run."""
        with self.condition:
            if self.closed or not self.available:
                return False
            if self.running:
                self.queued = True
                return True
            self.running = True
            self.thread = threading.Thread(target=self._loop, name='ledger-check', daemon=True)
            self.thread.start()
            return True

    def wait_idle(self, timeout=None) -> bool:
        with self.condition:
            return self.condition.wait_for(lambda: not self.running, timeout=timeout)

    def tick(self):
        """From the worker loop: notice a commit made outside the server, at most every 30 s."""
        if self.closed or not self.available:
            return
        at = self.clock()
        if self.last_tick is not None and at - self.last_tick < self.tick_interval:
            return
        self.last_tick = at
        with self.condition:
            if self.running:
                return
        commit = second_check.committed(self.root)
        if commit and commit != self.read().get('commit'):
            self.request('new commit')

    def _loop(self):
        while True:
            try:
                self.check_once()
            except Exception as exc:
                LOG.exception('The ledger check could not run')
                self.record_error(exc)
            with self.condition:
                if self.queued and not self.closed:
                    self.queued = False
                    continue
                self.running = False
                self.condition.notify_all()
                return

    # One check.

    def run_snapshot(self, root, commit, base):
        """`second_check.py snapshot` in its own process, so hledger and the export never
        hold the server's interpreter; the result is its JSON."""
        scratch = os.environ.get('EXPENSES_TEST_TMP') or None
        with tempfile.TemporaryDirectory(prefix='expense-ledger-check-', dir=scratch) as work:
            command = [sys.executable, str(SCRIPT), 'snapshot', '--root', str(root), '--commit', commit,
                       '--hledger', self.hledger, '--work', work, *(['--base', base] if base else [])]
            with self.condition:
                if self.closed:
                    raise RuntimeError('The server is stopping')
                self.child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                              env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
            try:
                stdout, stderr = self.child.communicate(timeout=600)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.communicate()
                raise RuntimeError('The snapshot and hledger did not finish within 10 minutes') from None
            finally:
                returncode = self.child.returncode
                self.child = None
        if returncode:
            raise RuntimeError('AgentExpense couldn’t export the ledger for hledger: '
                               + ((stderr or stdout).strip().splitlines() or ['no output'])[-1][:500])
        return json.loads(stdout)

    def record_error(self, exc):
        """A check that couldn't run is an app problem: the item explains it, and the tick
        doesn't retry the same commit every 30 seconds (Check again does)."""
        commit = self.checking_commit
        self.checking_commit = None
        if not commit:
            return
        found = dict(second_check.finding(''), title='The second check couldn’t run',
                     summary='AgentExpense couldn’t run hledger’s check of the latest save.')
        with self.condition:
            record = self.read()
            record.update(commit=commit, checked_at=now(), outcome='fail', seconds=None,
                          failure=dict(finding=found, output=f'{type(exc).__name__}: {exc}'[-3000:], app_problem=True,
                                       json_check=None, changed=None, base_missing=bool(record.get('missing_good'))))
            self.write(record)

    def check_once(self):
        commit = second_check.committed(self.root)
        if not commit:
            return
        with self.condition:
            record = self.read()
        good = record.get('good') if isinstance(record.get('good'), dict) else None
        self.checking_commit = commit
        result = self.runner(self.root, commit, good['commit'] if good else None)
        self.checking_commit = None
        self.runs += 1
        with self.condition:
            record = self.read()
            record.update(commit=commit, checked_at=now(), outcome='pass' if result['ok'] else 'fail',
                          seconds=result.get('seconds'))
            if result['ok']:
                record.update(good=dict(commit=commit, checked_at=record['checked_at']), failure=None, fix=None)
            else:
                record['failure'] = {key: result.get(key) for key in ('finding', 'output', 'app_problem', 'json_check', 'changed', 'base_missing')}
                if not good and (record.get('missing_good') or result.get('base_missing')):
                    record['failure']['base_missing'] = True
                if result.get('base_missing'):
                    # The last good commit left the history (a rollback, an abandoned change).
                    record['good'] = None
                    record['missing_good'] = True
            if result['ok']:
                record.pop('missing_good', None)
            self.write(record)
        if result['ok']:
            self.resolve_fixes()
        LOG.info('ledger-check %s', json.dumps(dict(commit=commit[:12], outcome=record['outcome'], seconds=result.get('seconds'))))

    def read(self) -> dict:
        """The record, or {} when it is missing or not the shape this version writes."""
        try:
            value = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(value, dict):
            return {}
        failure = value.get('failure')
        if failure is not None and not (isinstance(failure, dict) and isinstance(failure.get('finding') or {}, dict)
                                        and isinstance(failure.get('changed') if failure.get('changed') is not None else [], list)):
            value['failure'] = None
        for key in ('good', 'fix'):
            if value.get(key) is not None and not isinstance(value[key], dict):
                value[key] = None
        return value

    def resolve_fixes(self):
        """A pass leaves no problem to fix: open fix proposals say so, and stay for the person to discard."""
        if not self.drafts:
            return
        with self.drafts.mutex:
            for draft in self.drafts.all():
                task = draft.get('ledger_fix')
                if task and draft['state'] != 'added' and task.get('state') != 'resolved':
                    task['state'] = 'resolved'
                    if draft['state'] in ('queued', 'working'):
                        draft['state'] = 'question'
                    self.drafts.put(draft)

    def write(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
        temporary.replace(self.path)

    # What the page shows.

    def status(self) -> dict:
        if not self.available:
            return dict(available=False, checking=False, item=None)
        with self.condition:
            checking = self.running
            record = self.read()
        try:
            item = self.item(record)
        except Exception:
            # The poll that carries this status also carries the Queue: never let it fail.
            LOG.exception('The ledger check record could not be read')
            item = None
        return dict(available=True, checking=checking, commit=record.get('commit'), checked_at=record.get('checked_at'),
                    outcome=record.get('outcome'), item=item)

    def item(self, record=None, *, with_fix=True) -> dict | None:
        """The open Ledger check item, or None. `with_fix=False` never reads drafts, so callers
        holding the statements mutex (Apply) keep the drafts → statements lock order.
        A record this version can't read gives no item: the poll and Apply never fail on it."""
        record = self.read() if record is None else record
        try:
            return self._item(record, with_fix)
        except (KeyError, TypeError, ValueError, AttributeError):
            LOG.exception('The ledger check record could not be read')
            return None

    def _item(self, record, with_fix):
        failure = record.get('failure') if record.get('outcome') == 'fail' else None
        if not failure or not self.available:
            return None
        found = failure.get('finding') or second_check.finding('')
        changed = failure.get('changed')
        target = self.target(found, changed or [])
        accounts = set(found.get('accounts') or [])
        for entry in changed or []:
            accounts.update(entry.get('accounts') or [])
        involved = sorted(accounts) or None
        reason = ('The second ledger check found a problem with this account. Fix it from Ledger check in Queue first.' if involved else
                  'The second ledger check found a problem it can’t tie to one account, so every statement waits. Fix it from Ledger check in Queue first.')
        changes = 'missing' if failure.get('base_missing') else 'known' if changed is not None else 'first'
        return dict(id='ledger-check', commit=record.get('commit'), checked_at=record.get('checked_at'),
                    kind=found.get('kind'), title=found.get('title'), summary=found.get('summary'),
                    details=failure.get('output') or '', app_problem=bool(failure.get('app_problem')),
                    explanation=second_check.APP_PROBLEM if failure.get('app_problem') else None,
                    entry=target, entries=[{k: e[k] for k in ('id', 'date', 'description', 'added')} for e in (changed or [])[:LISTED]],
                    more=max(len(changed or []) - LISTED, 0), since=(record.get('good') or {}).get('checked_at') if changes == 'known' else None,
                    changes=changes, changes_known=changed is not None, accounts=involved, block=dict(accounts=involved, reason=reason),
                    fix=self.fix_state(record) if with_fix else None)

    def target(self, found, changed):
        """The entry Edit entry and the agent work on, with `guess` when hledger didn't name it.

        - An assertion: the newest changed entry that posts to the asserted account (the
          confirmed balance itself is usually locked), else the confirmed balance's entry.
        - Any other finding in an entry: that entry.
        - A finding in a forecast rule, or on a line that isn't an entry (a price line, the
          declarations): none; the item lists the changed entries instead.
        - No location at all: the newest changed entry, as a guess."""
        def pick(entry, guess):
            return dict(id=entry['id'], date=entry.get('date'), description=entry.get('description'), guess=guess)
        named = found.get('entry_id')
        if found.get('kind') == 'assertion' and found.get('account'):
            posting = next((e for e in changed if e['id'] != named and found['account'] in (e.get('accounts') or [])), None)
            if posting:
                return pick(posting, True)
        if named:
            listed = next((e for e in changed if e['id'] == named), None)
            return dict(id=named, date=found.get('date'), description=(listed or {}).get('description') or found.get('description'), guess=False)
        if found.get('rule_id') or found.get('line') or found.get('file'):
            return None
        return pick(changed[0], True) if changed else None

    def fix_state(self, record):
        fix = record.get('fix')
        if not fix or not self.drafts:
            return None
        try:
            draft = self.drafts.get(fix['draft_id'])
        except (FileNotFoundError, ValueError):
            return None
        if draft['state'] == 'added':
            return None
        task = draft.get('ledger_fix') or {}
        return dict(draft_id=draft['id'], state=task.get('state'), question=task.get('question'), message=task.get('message'))

    def blocked(self, account) -> str | None:
        """Why statement Apply (and so a confirmed balance) waits for this account, or None.
        It reads the record only (never drafts): Apply calls it holding the statements mutex."""
        item = self.item(with_fix=False)
        if not item:
            return None
        accounts = item['block']['accounts']
        return item['block']['reason'] if accounts is None or account in accounts else None

    def pending(self) -> bool:
        """A check of a commit newer than the last good one is running: its result may yet
        block an Apply, so a queued Apply waits for it (`Statements.work`)."""
        with self.condition:
            if not self.running or not self.available:
                return False
            good = self.read().get('good') or {}
            return self.checking_commit is None or self.checking_commit != good.get('commit')

    def apply_guard(self, account) -> tuple[str | None, bool]:
        """Read the refusal and pending state together; completion cannot pass between
        them. Called under the ledger lock, and never reads drafts or statements."""
        with self.condition:
            return self.blocked(account), self.pending()

    # Ask the agent to fix it: a correction draft the worker hands to the agent.

    def fix(self, drafts, worker):
        """Ask the agent to fix it. One request at a time: a second (a double click, another
        tab) returns the same draft. The draft is linked in the record before it is queued,
        so a pass that lands meanwhile resolves it instead of leaving it unlinked."""
        from corrections import open_form
        with self.fix_mutex:
            # Not under the condition: item() reads drafts, and a commit made under drafts.mutex
            # (Continue with a new account) calls request(), which takes the condition.
            item = self.item(self.read())
            if not item:
                raise ValueError('The ledger check has no open problem.')
            if item['app_problem']:
                raise ValueError('This is a problem in AgentExpense, not in an entry. Copy the details instead.')
            if not item['entry']:
                raise ValueError('hledger’s finding doesn’t name an entry. Edit the entry you changed, then check again.')
            if item['fix']:
                return drafts.get(item['fix']['draft_id'])
            draft = open_form(self.settings, drafts, item['entry']['id'])
            with drafts.mutex:
                draft = drafts.get(draft['id'])
                draft['correction']['reason'] = 'Ledger check: ' + item['title']
                draft.update(text='Fix the ledger check: ' + item['title'],
                             ledger_fix=dict(state='queued', commit=item['commit'], kind=item['kind'], title=item['title'],
                                             summary=item['summary'], details=item['details'], entry_id=item['entry']['id'],
                                             guess=bool(item['entry'].get('guess'))))
                drafts.put(draft)
            with self.condition:
                record = self.read()
                linked = record.get('outcome') == 'fail' and bool(record.get('failure'))
                if linked:
                    record['fix'] = dict(draft_id=draft['id'], commit=item['commit'])
                    self.write(record)
            with drafts.mutex:
                draft = drafts.get(draft['id'])
                if linked and draft['ledger_fix'].get('state') != 'resolved':
                    draft.update(state='queued', queued_at=now())
                else:
                    draft['ledger_fix']['state'] = 'resolved'
                drafts.put(draft)
        worker.wake.set()
        return draft


def propose(worker, draft):
    """The worker's turn for a fix draft: the agent proposes the corrected entry.

    Nothing is saved here. The answer becomes the Edit entry form; the person checks it
    and saves it through the usual correction (one verified commit)."""
    import agent
    from answers import AnswerError, check as check_answer
    from corrections import wire_entry
    settings, drafts = worker.settings, worker.drafts
    def save(draft_state, *, form=None, **updates):
        # A pass and a worker answer are concurrent writers. Merge into the
        # latest draft under one lock; resolution is terminal for this proposal.
        with drafts.mutex:
            latest = drafts.get(draft['id'], include_discarded=True)
            latest.update(runs=draft['runs'], check_errors=draft['check_errors'], waiting=None)
            resolved = latest['ledger_fix'].get('state') == 'resolved'
            if not resolved:
                latest['ledger_fix'].update(updates)
                latest['state'] = draft_state
                if form is not None: latest['hand_form'] = form
            drafts.put(latest)
            return resolved
    while not worker.stop_event.is_set():
        with drafts.mutex:
            draft = drafts.get(draft['id'], include_discarded=True)
            if draft['ledger_fix'].get('state') == 'resolved' or draft.get('trashed_at') or draft.get('discard_requested'):
                return
            task = draft['ledger_fix']
            task.update(state='working', form=wire_entry(settings, draft['correction']['before']))
            draft['runs'].append(dict(start=now(), end=None, model=settings.model, effort=settings.effort, outcome=None, question=None,
                                      doubts=[], error=None, duration=None, usage={}, lookups=[]))
            drafts.put(draft)
        try:
            answer, run = agent.run(settings, {**draft, 'ledger_fix_task': task}, drafts.folder(draft['id']), cancel=worker.stop_event)
        except agent.AgentFailure as exc:
            draft['runs'][-1] = exc.record
            if exc.kind != 'bad_answer' or worker.stop_event.is_set():
                if not worker.stop_event.is_set():
                    worker.pause.fail(exc.kind, exc.reset_at)
                save('queued', state='queued')
                return
            answer = None
            error = str(exc)
        else:
            worker.pause.success()
            draft['runs'][-1] = run
            error = None
            if answer['status'] == 'ready':
                try:
                    check_answer(answer, settings.ledger, form=False)
                except AnswerError as failure:
                    error = str(failure)
        if error is None and answer['status'] == 'ready':
            proposal = deepcopy(answer)
            proposal['attachment-names'] = []
            save('question', form=proposal, state='ready', doubts=answer.get('doubts') or [], message=answer.get('explanation'))
            return
        if error is None:
            # A question or "not an expense": the person edits the entry themselves.
            save('question', state='question', question=answer.get('question') or answer.get('explanation'))
            return
        draft['check_errors'].append(error)
        if len(draft['check_errors']) > 1:
            save('question', state='failed', message='The agent couldn’t propose a fix. Edit the entry yourself.')
            return
        if save('working', state='working'):
            return
