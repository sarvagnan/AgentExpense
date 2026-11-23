"""One weekly review per ledger, with FIFO turns between calls and commits."""
from collections import Counter
from datetime import date, datetime, timedelta
import logging
import os
from pathlib import Path
import secrets
import shutil
from tempfile import TemporaryDirectory

import agent_timeouts
import audit
from adapters.base import AdapterError
from audit_changes import identity
from daily_refresh import local_today
from ledger_lock import ledger_lock
from ledger_manifest import read_manifest
from review_lookup import committed_snapshot
from store import read_json
from transactions import Transaction, clean
from weekly_review_batch import freeze_run, period, run_batch, save


DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
WAITING = 'Waiting for a clean ledger'


def due_slot(modules, today, satisfied):
    if not modules['weeklyReview']:
        return None
    current = date.fromisoformat(today)
    weekday = DAYS.index(modules.get('weeklyReviewDay', 'Monday'))
    due = current - timedelta(days=(current.weekday() - weekday) % 7)
    slot = (due - timedelta(days=weekday)).isoformat()
    if due.isoformat() >= modules.get('weeklyReviewEnabledOn', '') and slot > (satisfied or ''):
        return slot


def split(batch):
    """Keep each shared rule with the last of its source lines."""
    return [dict(lines=[line], rules={rule: sources for rule, sources in batch['rules'].items()
                                     if line == next(s for s in reversed(batch['lines']) if s in sources)})
            for line in batch['lines']]


class WeeklyReview:
    def __init__(self, settings, pause, stop, *, clock, today=local_today):
        from agent_gate import get_gate
        self.settings, self.pause, self.stop = settings, pause, stop
        self.root = settings.ledger
        self.folder = self.root / 'drafts/weekly-review'
        self.path = self.folder / 'state.json'
        self.clock, self.today = clock, today
        self.next_check = 0
        self.ready = False
        self.waiting = None
        self.cached = None
        reviewer = settings.for_role('review')
        self.gate = get_gate(reviewer)
        self.profile = reviewer.pause_key()
        self.reviewer = dict(adapter=reviewer.adapter, model=reviewer.model, effort=reviewer.effort)

    def state(self):
        return read_json(self.path) if self.path.exists() else {'requests': {}, 'active': None}

    def write(self, state):
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        save(self.path, state)

    def published(self):
        revision = identity(self.root, 'main')['commit']
        cached = self.cached
        if cached and cached[0] == revision:
            return cached
        with TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp')) as folder:
            snapshot = committed_snapshot(self.root, Path(folder), revision, audit_only=True)
            modules, rows = read_manifest(snapshot)['modules'], audit.records(snapshot)
        published = revision, modules, [r for r in rows if r['kind'] == 'weekly-review-run' or
                                        r['kind'] == 'rule-review' and r.get('run_id') and r['rule_verdict'] == 'propose-removal']
        self.cached = published
        return published

    def plan_path(self, run):
        return self.folder / run['run_id'] / 'plan.json'

    def diagnostics(self, run):
        path = self.plan_path(run)
        plan = read_json(path) if path.exists() else {}
        return {key: plan.get(key, []) for key in ('missing_actors', 'history_errors')}

    @staticmethod
    def progress(run, rows):
        markers = [row for row in rows if row.get('run_id') == run['run_id'] and row.get('status') == 'batch']
        return dict(checked=sum(row['checked'] for row in markers), issues=sum(row['issues'] for row in markers),
                    period=period([t for row in markers for t in row['period'] if t is not None]))

    def status(self, offset, limit):
        run = self.state()['active']
        _, modules, rows = self.published()
        history = sorted((row for row in rows if row.get('status') in ('completed', 'empty', 'partial')),
                         key=lambda row: (datetime.fromisoformat(row['ended']), row['id']), reverse=True)
        proposals = Counter(row['run_id'] for row in rows if row['kind'] == 'rule-review')
        if run and not any(row['run_id'] == run['run_id'] for row in history):
            active = {key: run[key] for key in ('run_id', 'trigger', 'slot', 'started', *self.reviewer)}
            active.update(self.progress(run, rows), **self.diagnostics(run), batch_count=len(run.get('batches', [])),
                          committed_batches=sum(row['run_id'] == run['run_id'] and row.get('status') == 'batch' for row in rows),
                          rule_proposals=proposals[run['run_id']],
                          state='failed' if run.get('error') else 'closing' if run.get('closing') else
                          'waiting' if self.waiting or self.pause.blocked() else 'running',
                          reason=run.get('error') or self.waiting)
        else:
            active = None
        return dict(enabled=modules['weeklyReview'], day=modules.get('weeklyReviewDay', 'Monday'),
                    enabled_on=modules.get('weeklyReviewEnabledOn'), active=active,
                    agent_pause=self.pause.status(), gate=self.gate.status(),
                    history=[{**row, **self.diagnostics(row), 'rule_proposals': proposals[row['run_id']]}
                             for row in history[offset:offset + limit]],
                    next_offset=offset + limit if offset + limit < len(history) else None)

    def new_run(self, trigger, slot):
        self.ready = False
        return dict(run_id=secrets.token_hex(16), trigger=trigger, slot=slot, started=audit.stamp(),
                    **self.reviewer, profile=self.profile, index=0)

    def slot(self, modules, rows):
        satisfied = max((row['slot'] for row in rows if row.get('status') in ('completed', 'empty', 'partial')
                         and row['slot']), default=None)
        return due_slot(modules, self.today(), satisfied)

    def transaction(self, run, *, finish=False):
        folder = self.plan_path(run).parent / ('finish' if finish else str(run['index'] + 1))
        return Transaction(self.settings, None, {'id': run['run_id']}, journal=folder / 'transaction.json')

    def action(self, action, request_id, run_id):
        state = self.state()
        request = [action, run_id]
        if request_id in state['requests']:
            previous, result = state['requests'][request_id]
            if request != previous:
                raise ValueError('This weekly review request changed')
            return result
        run = state['active']
        if action == 'start':
            if not run:
                _, modules, rows = self.published()
                run = self.new_run('manual', self.slot(modules, rows))
        elif action == 'retry' and not run and (old := state.get('replaced')) and old['run_id'] == run_id:
            run = self.new_run('manual', old['slot'])
            if self.plan_path(old).exists():
                plan = read_json(self.plan_path(old))
                plan.update(run_id=run['run_id'], trigger=run['trigger'], started=run['started'],
                            batches=old.get('batches', plan['batches'])[old['index']:],
                            previous_runs=[*plan.get('previous_runs', []), old['run_id']])
                path = self.plan_path(run)
                path.parent.mkdir(mode=0o700, parents=True)
                save(path, plan)
            state.pop('replaced')
        elif not run or run_id != run['run_id']:
            raise ValueError('This weekly review run has changed')
        elif action == 'retry':
            if run.get('closing') and not run.get('error'):
                raise ValueError('This weekly review is closing')
            attempt = self.transaction(run).journal.with_name('attempt.json')
            if run.get('error') and attempt.exists():
                value = read_json(attempt)
                save(attempt, {**value, 'error': '', 'failures': 0})
            run.pop('error', None)
            agent_timeouts.reset(run)
        elif action == 'close-partial':
            if self.transaction(run).journal.exists():
                raise ValueError('Recover the interrupted weekly review batch first')
            run.update(closing=True, error=None)
        self.next_check, self.waiting = 0, None
        result = {'run_id': run['run_id']}
        state['requests'][request_id] = [request, result]
        self.write({**state, 'active': run})
        return result

    def recover(self):
        """Clear an owned interrupted append before foreground writers inspect the ledger."""
        state = self.state()
        if not (run := state['active']):
            return True
        for finish in (False, True):
            tx = self.transaction(run, finish=finish)
            if not tx.journal.exists():
                continue
            try:
                with ledger_lock(self.root, blocking=False):
                    revision, _, rows = self.published()
                    if any(row['run_id'] == run['run_id'] and row.get('batch') ==
                           (None if finish else run['index'] + 1) for row in rows if row['kind'] == 'weekly-review-run'):
                        tx.finish_commit()
                    else:
                        tx.recover_audit(revision)
            except BlockingIOError:
                return False
            except Exception:
                reason = 'Weekly review recovery could not finish. Preserve transaction.json and check the ledger.'
                if run.get('error') != reason:
                    logging.exception('Weekly review recovery failed')
                    run['error'] = reason
                    self.write(state)
        return True

    def sweep_workspaces(self):
        for pattern in ('work-*', '*/work-*', '*/*/work-*'):
            for folder in self.folder.glob(pattern):
                if folder.is_dir() and not folder.is_symlink():
                    shutil.rmtree(folder)

    def finish(self, run, rows, revision):
        summary = self.progress(run, rows)
        diagnostics = self.diagnostics(run)
        reviewed = any(row['run_id'] == run['run_id'] and row.get('status') == 'batch' for row in rows)
        status = 'partial' if run.get('closing') or any(diagnostics.values()) else 'completed' if reviewed else 'empty'
        with ledger_lock(self.root, blocking=False) as fd:
            if not clean(self.root) or identity(self.root, '@-')['commit'] != revision:
                self.waiting = WAITING
                return False
            tx = self.transaction(run, finish=True)
            tx.journal.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fields = {key: run[key] for key in ('run_id', 'trigger', 'slot', 'started', *self.reviewer)}
            marker = audit.event('weekly-review-run', **fields, **summary, ended=audit.stamp(), status=status)
            tx.publish_audit([marker], 'Finish weekly review', fd)
        return True

    def delay(self):
        return max(0, self.next_check - self.clock())

    def tick(self):
        if self.delay():
            return False
        self.next_check = self.clock() + 30
        try:
            again = self.advance(self.state())
        except BlockingIOError:
            self.waiting = WAITING
            return False
        except Exception:
            logging.exception('Weekly review failed')
            state = self.state()
            if run := state['active']:
                run['error'] = 'Weekly review could not continue. Retry after checking the ledger.'
                self.write(state)
            return False
        if again:
            self.next_check = 0
        return again

    def advance(self, state):
        from review_followup import capped, job_for, pending, turn
        from review_decisions import published

        run = state['active']
        if (self.root / '.jj').is_dir() and (self.root / 'data/additions').is_dir():
            pinned = identity(self.root, 'main')['commit']
            audit_rows = published(self.root, pinned)
            ready = [row for issue, row in pending(audit_rows).items()
                     if not job_for(state, row).get('error') and not capped(audit_rows, issue, self.today())]
            owner = min(ready, key=lambda row: datetime.fromisoformat(row['time']), default=None)
            if owner and (not run or run.get('error') or datetime.fromisoformat(owner['time']) <=
                          datetime.fromisoformat(run.get('queued_at', run['started']))):
                return turn(self, state, pinned, audit_rows, owner)
        if not run and (not (self.root / 'ledger.json').is_file() or
                        not read_manifest(self.root)['modules']['weeklyReview']):
            return False
        revision, modules, rows = self.published()
        finished = [row for row in rows if row.get('status') in ('completed', 'empty', 'partial')]
        if not run:
            if not (slot := self.slot(modules, finished)):
                return False
            state['active'] = self.new_run('scheduled', slot)
            self.write(state)
            return True
        if run['profile'] != self.profile and not run.get('closing') and not any(
                self.transaction(run, finish=finish).journal.exists() for finish in (False, True)):
            run.update(closing=True, error=None)
            state['replaced'] = run
            self.write(state)
            return True
        recorded = any(row['run_id'] == run['run_id'] for row in finished)
        exhausted = 'batches' in run and run['index'] == len(run['batches'])
        if recorded or run.get('closing') or exhausted:
            if not recorded and (run.get('error') or not self.finish(run, rows, revision)):
                return False
            state['active'] = None
            self.waiting = None
            self.write(state)
            return True
        if run.get('error'):
            return False
        path = self.plan_path(run)
        if not path.exists():
            with ledger_lock(self.root, blocking=False):
                if not clean(self.root) or identity(self.root, '@-')['commit'] != revision:
                    self.waiting = WAITING
                    return False
            freeze_run(self.settings, run=run['run_id'], trigger=run['trigger'], slot=run['slot'], started=run['started'])
            return True
        if 'batches' not in run:
            run['batches'] = read_json(path)['batches']
            self.write(state)
            return True

        batch = run['batches'][run['index']]
        attempt = self.transaction(run).journal.with_name('attempt.json')
        if not (attempt.exists() and read_json(attempt).get('answer')) and not self.can_call():
            return False
        self.waiting = None
        try:
            status = run_batch(self.settings, path, run['index'] + 1, batch=batch, one_turn=True, cancel=self.stop)
        except AdapterError as exc:
            if self.stop.is_set():
                return False
            if exc.kind != 'timeout':
                agent_timeouts.reset(run)
                self.pause.fail(exc.kind, exc.reset_at)
                self.ready = False
            elif len(batch['lines']) > 1:
                run['batches'][run['index']:run['index']+1] = split(batch)
                agent_timeouts.reset(run)
            elif agent_timeouts.failed(run, exc.kind):
                run['error'] = 'The reviewer reached its deadline three times. Retry this batch.'
        else:
            if status['status'] in ('busy', 'dirty'):
                self.waiting = WAITING
                self.next_check = self.clock() + 30
                return False
            if status['status'] in ('prepared', 'retry', 'failed'):
                self.pause.success()
            agent_timeouts.reset(run)
            if status['status'] in ('committed', 'empty'):
                run['index'] += 1
                run['queued_at'] = audit.stamp()
            if status['status'] == 'failed':
                run['error'] = status['error']
        self.write(state)
        return not run.get('error')

    def can_call(self):
        pause = self.pause.status()
        if pause:
            remaining = (datetime.fromisoformat(pause['resumes_at']) - self.pause.clock()).total_seconds()
            if remaining > 0:
                self.waiting = pause['reason']
                self.next_check = self.clock() + min(30, remaining)
                return False
        if self.ready:
            return True
        try:
            force = bool(pause and pause['kind'] == 'auth') or self.gate.status().get('state') == 'paused'
            self.gate.require_ready(force=force, cancel=self.stop)
        except ValueError as exc:
            self.waiting = str(exc)
        else:
            if pause and pause['kind'] == 'auth':
                self.pause.success()
            self.ready, self.next_check, self.waiting = True, 0, None
        return False
