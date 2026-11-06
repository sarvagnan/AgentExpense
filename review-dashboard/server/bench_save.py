"""Time representative saves on a generated large fictional ledger (`make bench-save`).

Every operation goes through the real server (in-process, over its HTTP API), its
FIFO worker, ledger lock, verification, commit and data rebuild, against a ledger
from `scripts/fixtures/generate_large.py` in a disposable /tmp folder. Add uses the
offline fake agent; no model ever runs and nothing touches the network.

Environment:
- BENCH_ENTRIES: generated entries (default: the generator's default, about five
  years). Any other value is a stress run, reported without targets;
- BENCH_REPS: measured repetitions of each operation (default 5; 2 from 50,000 entries);
- BENCH_SEED: generator seed;
- BENCH_REPORT / BENCH_JSON: also write the Markdown table / raw samples to these paths;
- BENCH_KEEP=1: keep the generated ledger and server state after the run;
- BENCH_BILL=1: include the bill in runs above 20,000 entries, where it is skipped
  because saving a bill reads the whole audit log once per entry.

Each operation reports `response`, the time the person waits for the saved result
(an HTTP response, or the queued item reaching its result); `committed`, the server's
time to the commit (its operation total without the agent's reading and the data
rebuild); and the server's per-step timings (`save_timing.py`). Targets, at the
default size only: committed median ≤ 1 s and 95th percentile ≤ 2 s for every
operation, data rebuild median ≤ 2 s, and Add pickup median ≤ 0.2 s. The run exits
with status 1 when a target is missed by more than 50%, as a regression guard.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import json
import logging
import math
import os
from pathlib import Path
import secrets
import shutil
import statistics
import sys
import tempfile
import time

SERVER = Path(__file__).resolve().parent
APPLICATION = SERVER.parents[1]
sys.path[:0] = [str(SERVER), str(SERVER / 'tests'), str(APPLICATION / 'scripts/fixtures')]

TARGETS = dict(committed_median=1.0, committed_p95=2.0, data_median=2.0, pickup_median=0.2)
SLACK = 1.5  # a regression is a target missed by more than 50%
OPERATIONS = ('add', 'hand-add', 'edit', 'delete', 'restore', 'bill', 'statement-apply', 'review', 'settings')
STEP_COLUMNS = ('prepare', 'lock_wait', 'journal', 'store_check', 'schema', 'forecast_hledger', 'hledger', 'verify', 'commit_jj', 'commit', 'jj', 'other', 'data')
AGENT_DATE = '2026-10-30'


@dataclass
class Sample:
    operation: str
    response: float
    line: dict
    pickup: float | None = None

    @property
    def committed(self):
        steps = self.line['steps']
        return self.line['total'] - steps.get('data', 0.0) - steps.get('agent', 0.0)

    def step(self, name):
        steps = self.line['steps']
        if name == 'other':  # committed time outside every named top-level step
            return self.committed - sum(steps.get(s, 0.0) for s in ('prepare', 'lock_wait', 'commit', 'jj'))
        return steps.get(name)


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(math.ceil(fraction * len(ordered)) - 1, 0)] if ordered else None


def answer(description, account, amount, payer='Assets:Cash', day=AGENT_DATE):
    return dict(status='ready', question=None, explanation=None, attachment_names=[], doubts=[], new_accounts=[],
                entry=dict(date=day, status='confirmed', description=description, order_id=None, reference=None,
                           classification=None, original_amount=None, fuel_filled_litres=None, trip_km=None, notes=[],
                           postings=[dict(account=account, amount=amount, commodity='INR', cost=None),
                                     dict(account=payer, amount='-' + amount, commodity='INR', cost=None)]))


class Bench:
    def __init__(self, root: Path, entries: int | None, reps: int, seed: int | None):
        self.root, self.entries, self.reps, self.seed = root, entries, reps, seed
        self.samples: list[Sample] = []
        self.lines: list[dict] = []
        self.notes: list[str] = []
        self.epoch = time.time()
        # A deadlock guard only; a stress-size save takes minutes.
        self.timeout = 600 * max(1, (entries or 0) // 5000)
        # Saving a bill reads and validates the whole audit log once per entry
        # (rules_service.queue_bill), which takes hours at stress sizes.
        self.skip_bill = bool(entries and entries > 20000) and os.environ.get('BENCH_BILL') != '1'
        if self.skip_bill:
            self.notes.append('Bill skipped: saving a bill reads the whole audit log once per entry (BENCH_BILL=1 includes it)')

    # --- setup -----------------------------------------------------------
    def build(self):
        import generate_large
        args = ['--dir', str(self.root / 'ledger')]
        if self.entries:
            args += ['--entries', str(self.entries)]
        if self.seed is not None:
            args += ['--seed', str(self.seed)]
        started = time.perf_counter()
        self.ledger = generate_large.main(args)
        self.notes.append(f'Generated in {time.perf_counter() - started:.1f} s')
        journal = self.ledger / 'expense_ledger.ledger'
        self.config = self.root / 'fake.json'
        self.config.write_text(json.dumps({'steps': [{'answer': answer('Birchwood Grocer top-up', 'Expenses:Groceries', '412.50')}]}))
        fake = self.root / 'fake-codex'
        fake.write_text(f'#!{sys.executable}\n' + (SERVER / 'tests/fake_agent.py').read_text().split('\n', 1)[1])
        fake.chmod(0o755)
        os.environ.update(EXPENSES_FAKE_CONFIG=str(self.config), npm_config_cache=str(self.root / '.npm-cache'),
                          npm_config_update_notifier='false', DASHBOARD_DATA_DIR=str(self.root / 'dashboard'),
                          PYTHONDONTWRITEBYTECODE='1', EXPENSES_TIMING_LOG='0')
        from settings import Settings
        self.settings = Settings(application=APPLICATION, ledger=self.ledger, codex=str(fake), dashboard_data=self.root / 'dashboard',
                                 state_dir=self.root / 'state', dev=True, timeout=self.timeout, old_ledger=None)
        return journal

    def start(self, stack: ExitStack):
        import save_timing
        from app import create_app
        from fastapi.testclient import TestClient
        bench = self

        class Capture(logging.Handler):
            def emit(self, record):
                bench.lines.append(dict(record.save_timing, at=round(record.created - bench.epoch, 3)))
        handler = Capture(logging.INFO)
        save_timing.LOG.addHandler(handler)
        stack.callback(save_timing.LOG.removeHandler, handler)
        app = create_app(self.settings, start_worker=True)
        self.client = stack.enter_context(TestClient(app))
        self.drafts = app.state.drafts
        self.sessions = app.state.statements
        from transactions import refresh
        result = refresh(self.settings, {})
        if result.get('data_stale'):
            raise RuntimeError('Initial data build failed: ' + result.get('refresh_error', ''))

    # --- helpers ---------------------------------------------------------
    def documents(self):
        from store import load
        return load(self.ledger)

    def actual(self):
        from store import entries
        return entries(self.documents())

    def take_line(self, kinds, since):
        for index in range(since, len(self.lines)):
            if self.lines[index]['operation'] in kinds:
                return self.lines[index]
        raise RuntimeError(f'No timing line for {kinds}: {self.lines[since:]}')

    def ok(self, response, status=200):
        if response.status_code != status:
            raise RuntimeError(f'{response.request.method} {response.request.url.path}: {response.status_code} {response.text[:2000]}')
        return response.json() if response.content else None

    def wait_draft(self, identity, predicate):
        with self.drafts.changed:
            if not self.drafts.changed.wait_for(lambda: predicate(self.drafts.get(identity)), timeout=self.timeout):
                raise RuntimeError(f'Draft {identity} never reached the expected state: {self.drafts.get(identity)}')
            return self.drafts.get(identity)

    def wait_session(self, identity, states):
        with self.sessions.changed:
            if not self.sessions.changed.wait_for(lambda: self.sessions.get(identity, include_discarded=True).get('state') in states, timeout=self.timeout):
                raise RuntimeError(f'Statement {identity} never reached {states}: {self.sessions.get(identity, include_discarded=True)}')
            return self.sessions.get(identity, include_discarded=True)

    def record(self, operation, response, kinds, since, pickup=None):
        sample = Sample(operation, response, self.take_line(kinds, since), pickup)
        sample.line = dict(sample.line, done=round(time.time() - self.epoch, 3))
        if sample.line['outcome'] != 'ok':
            raise RuntimeError(f'{operation} failed: {sample.line}')
        self.samples.append(sample)
        return sample

    # --- operations ------------------------------------------------------
    def add(self, measured=True):
        since = len(self.lines)
        identity = self.ok(self.client.post('/api/drafts', json={'text': 'Birchwood Grocer top-up 412.50 cash'}), 201)['id']
        started = time.perf_counter()
        self.ok(self.client.post(f'/api/drafts/{identity}/save'))
        self.wait_draft(identity, lambda d: d['state'] != 'queued')
        pickup = time.perf_counter() - started
        draft = self.wait_draft(identity, lambda d: d['state'] in ('added', 'failed', 'question'))
        elapsed = time.perf_counter() - started
        if draft['state'] != 'added':
            raise RuntimeError(f'Add ended {draft["state"]}: {draft.get("error")}')
        if measured:
            self.record('add', elapsed, {'add'}, since, pickup)
        return draft['result']['entry_id']

    def hand_add(self):
        since = len(self.lines)
        # Finishing by hand takes a queued item the agent has not started: pause
        # the agent (as during a usage limit) while this item is queued and claimed.
        pause = self.client.app.state.worker.pause
        pause.fail('usage_limit')
        try:
            identity = self.ok(self.client.post('/api/drafts', json={'text': 'Pebble Bakery bread 186 cash'}), 201)['id']
            url = f'/api/drafts/{identity}'
            self.ok(self.client.post(url + '/save'))
            token = self.ok(self.client.post(url + '/claim', json={}))['claim_token']
        finally:
            pause.success()
        form = answer('Pebble Bakery', 'Expenses:Food:Dining & Delivery', '186.00')
        checked = self.ok(self.client.post(url + '/hand-form', json={'claim_token': token, 'proposal': form}))
        if not checked.get('ok'):
            raise RuntimeError(f'Hand form refused: {checked}')
        started = time.perf_counter()
        self.ok(self.client.post(url + '/hand-add', json=dict(claim_token=token, proposal_hash=checked['proposal_hash'], approved_accounts=[])))
        draft = self.wait_draft(identity, lambda d: d['state'] in ('added', 'failed'))
        if draft['state'] != 'added':
            raise RuntimeError(f'Hand form ended {draft["state"]}: {draft.get("error")}')
        self.record('hand-add', time.perf_counter() - started, {'hand-add'}, since)

    def edit(self, entry):
        since = len(self.lines)
        opened = self.ok(self.client.post(f'/api/entries/{entry["id"]}/correction-form'))
        form = deepcopy(opened['hand_form'])
        form['entry']['description'] = entry['description'] + ' (checked)'
        body = dict(version=opened['correction']['version'], proposal=form, sources=entry.get('sources', []), approved_accounts=[])
        started = time.perf_counter()
        self.ok(self.client.post(f'/api/entries/{entry["id"]}/corrections/{secrets.token_hex(16)}', json=body))
        self.record('edit', time.perf_counter() - started, {'edit'}, since)

    def delete(self, entry_id):
        since = len(self.lines)
        status = self.ok(self.client.get(f'/api/entries/{entry_id}/retention'))
        if not status.get('offered'):
            raise RuntimeError(f'Delete is not offered: {status}')
        started = time.perf_counter()
        self.ok(self.client.post(f'/api/retention/operations/{secrets.token_hex(16)}', json=dict(action='delete', entry_id=entry_id, version=status['version'])))
        self.record('delete', time.perf_counter() - started, {'retention-delete'}, since)

    def restore(self, entry_id):
        since = len(self.lines)
        items = self.ok(self.client.get('/api/trash'))['items']
        item = next(i for i in items if i.get('entry', {}).get('id') == entry_id or i.get('entry_id') == entry_id)
        started = time.perf_counter()
        self.ok(self.client.post(f'/api/retention/operations/{secrets.token_hex(16)}', json=dict(action='restore', item_id=item['id'])))
        self.record('restore', time.perf_counter() - started, {'retention-restore'}, since)

    def bill(self, rule):
        since = len(self.lines)
        identity = self.ok(self.client.post('/api/drafts', json={'text': rule['description'] + ' bill'}), 201)['id']
        amount = rule['postings'][0]['amount']
        started = time.perf_counter()
        self.ok(self.client.post(f'/api/drafts/{identity}/bill', json=dict(rule_id=rule['id'], amount=amount, date='2026-11-01', amount_anyway=True)))
        draft = self.wait_draft(identity, lambda d: d['state'] in ('added', 'failed'))
        if draft['state'] != 'added':
            raise RuntimeError(f'Bill ended {draft["state"]}: {draft.get("error")}')
        self.record('bill', time.perf_counter() - started, {'bill'}, since)

    def statement(self):
        """Apply the next unfiled card statement period, built from the ledger's own rows."""
        from generate_large import CARD, CARD_IDENTIFIER, add_months
        from statement_fixtures import statement_pdf
        documents = self.documents()
        filed = [s for s in documents[self.ledger / 'data/card-statements.json'] if s['account'] == CARD]
        prior = max(filed, key=lambda s: s['end'])
        start = date.fromisoformat(prior['end']) + timedelta(days=1)
        end = add_months(date.fromisoformat(prior['end']), 1, 28)
        rows, purchases, credits = [], Decimal(0), Decimal(0)
        for entry in self.actual():
            if not start.isoformat() <= entry['date'] <= end.isoformat() or entry.get('statement-end'):
                continue
            card = [p for p in entry['postings'] if p['account'] == CARD]
            if len(card) != 1 or any(p['account'].startswith('Equity:') for p in entry['postings']):
                continue
            value = Decimal(card[0]['amount'])
            purchases += -value if value < 0 else 0
            credits += value if value > 0 else 0
            rows.append((entry, f'QZ-{end:%Y%m}-{len(rows) + 1:03d}', value))
        due = Decimal(prior['total-due']) + purchases - credits
        lines = ['Example Card Statement', f'Statement period: {start} to {end}', f'Card last four digits: {CARD_IDENTIFIER}',
                 f'Previous balance: {prior["total-due"]}', f'Purchases: {purchases:.2f}', 'Cash advances: 0.00',
                 f'Credits: {credits:.2f}', f'Total due: {due:.2f}', f'Minimum due: {due / 10:.2f}',
                 f'Due date: {add_months(end, 1, 15)}', 'Date | Serial | Description | Amount | Direction | Foreign amount | Currency']
        pages, page = [], lines
        for index, (entry, serial, value) in enumerate(rows):
            if index and index % 40 == 0:
                pages.append(page)
                page = []
            page.append(f'{entry["date"]} | {serial} | {entry["description"].replace("|", " ")} | {abs(value):.2f} | {"Debit" if value < 0 else "Credit"} | |')
        pages.append(page)
        pdf = statement_pdf('\f'.join('\n'.join(p) for p in pages))
        since = len(self.lines)
        upload = self.ok(self.client.post('/api/statements', files={'file': (f'quartz-{end}.pdf', pdf, 'application/pdf')},
                                          data={'client_time': '2026-11-07T10:00:00+05:30', 'client_zone': 'Asia/Kolkata'}), 201)
        session = self.wait_session(upload['id'], ('found', 'review', 'couldnt_read', 'failed', 'account_needed', 'already_filed', 'currency_mismatch'))
        if session['state'] not in ('found', 'review'):
            raise RuntimeError(f'Statement reading ended {session["state"]}: {session.get("error")}')
        expected = {serial: entry['id'] for entry, serial, _ in rows}
        decided = 0
        for item in session['plan']['items']:
            if item['needs_user'] and not item['decision']:
                target = expected[item['row']['serial']]
                action = dict(action='match', entry_id=target) if target in [c['entryId'] for c in item.get('candidates', [])] else dict(action='pending')
                self.ok(self.client.patch(f'/api/statements/{session["id"]}/rows/{item["row"]["serial"]}', json=action))
                decided += 1
        started = time.perf_counter()
        self.ok(self.client.post(f'/api/statements/{session["id"]}/apply'), 202)
        session = self.wait_session(session['id'], ('applied', 'failed'))
        if session['state'] != 'applied':
            raise RuntimeError(f'Statement Apply ended {session["state"]}: {session.get("error")}')
        sample = self.record('statement-apply', time.perf_counter() - started, {'statement-apply'}, since)
        sample.line = dict(sample.line, rows=len(rows), decided=decided)

    def review(self, entry_id):
        since = len(self.lines)
        state = self.ok(self.client.get('/api/review'))
        version = state['entries'][entry_id]['version']
        body = dict(action='resolve', request_id=secrets.token_hex(16), entry_id=entry_id, versions={entry_id: version},
                    decision=dict(status='resolved', account='Expenses:Household', label='', notes='Checked in the timing run', updatedAt='2026-11-07T10:00:00+05:30'))
        started = time.perf_counter()
        self.ok(self.client.post('/api/review/actions', json=body))
        self.record('review', time.perf_counter() - started, {'review-resolve'}, since)

    def rename(self, index):
        since = len(self.lines)
        started = time.perf_counter()
        self.ok(self.client.post('/api/settings/name', json={'name': f'Ira Castellan timing {index}'}))
        self.record('settings', time.perf_counter() - started, {'settings'}, since)

    # --- run ---------------------------------------------------------------
    def run(self):
        journal = self.build()
        with ExitStack() as stack:
            self.start(stack)
            documents = self.documents()
            actual = self.actual()
            cutoff = (date.fromisoformat(AGENT_DATE) - timedelta(days=60)).isoformat()
            editable = [e for e in actual if e['date'] >= cutoff and not e.get('rule') and not e.get('statement-end') and e['status'] == 'confirmed'
                        and len(e['postings']) == 2 and any(p['account'] == 'Assets:Cash' for p in e['postings'])]
            pending = [e['id'] for e in actual if e['status'] == 'pending']
            varying = [r for r in documents[self.ledger / 'data/forecast.json'] if r.get('amount') == 'varies' and not r.get('bill')]
            from generate_large import UNFILED_STATEMENTS
            # Warm up once (agent gate, caches); these saves are not counted.
            self.add(measured=False)
            self.rename(0)
            self.samples.clear()
            self.notes.append(f'{len(actual)} entries, journal {journal.stat().st_size / 1e6:.2f} MB, '
                              f'{len(documents[self.ledger / "data/forecast.json"])} recurring rules')
            for rep in range(self.reps):
                print(f'Repetition {rep + 1} of {self.reps}', flush=True)
                added = self.add()
                self.hand_add()
                if rep < len(editable):
                    self.edit(editable[-1 - rep])
                self.delete(added)
                self.restore(added)
                if rep < len(varying) and not self.skip_bill:
                    self.bill(varying[rep])
                if rep < UNFILED_STATEMENTS - 1:
                    self.statement()
                if rep < len(pending):
                    self.review(pending[rep])
                self.rename(rep + 1)

    # --- report --------------------------------------------------------------
    def table(self, targets: bool):
        def cell(values):
            values = [v for v in values if v is not None]
            if not values:
                return '–'
            return f'{statistics.median(values):.2f} / {percentile(values, .95):.2f}'
        out = ['| Operation | n | Response | Committed | Pickup | ' + ' | '.join(STEP_COLUMNS) + ' |',
               '|---|---|---|---|---|' + '---|' * len(STEP_COLUMNS)]
        for operation in OPERATIONS:
            rows = [s for s in self.samples if s.operation == operation]
            if not rows:
                continue
            steps = [cell([s.step(step) for s in rows]) for step in STEP_COLUMNS]
            out.append(f'| {operation} | {len(rows)} | {cell([s.response for s in rows])} | {cell([s.committed for s in rows])} | '
                       f'{cell([s.pickup for s in rows])} | ' + ' | '.join(steps) + ' |')
        out.append('')
        out.append('Seconds, median / 95th percentile (nearest rank). Committed: the server\'s operation time up to the commit '
                   '(total without the agent and data steps). Response: what the person waits for today. '
                   'commit contains verify (journal, store_check with schema and forecast_hledger, hledger) and commit_jj; '
                   'jj is the server\'s own jj calls; other is committed time outside prepare, lock_wait, commit and jj.')
        failures = []
        if targets:
            for operation in OPERATIONS:
                rows = [s for s in self.samples if s.operation == operation]
                if not rows:
                    continue
                committed = [s.committed for s in rows]
                data = [s.line['steps']['data'] for s in rows if 'data' in s.line['steps']]
                checks = [('committed median', statistics.median(committed), TARGETS['committed_median']),
                          ('committed p95', percentile(committed, .95), TARGETS['committed_p95'])]
                if data:
                    checks.append(('data median', statistics.median(data), TARGETS['data_median']))
                pickups = [s.pickup for s in rows if s.pickup is not None]
                if pickups:
                    checks.append(('pickup median', statistics.median(pickups), TARGETS['pickup_median']))
                for label, value, target in checks:
                    if value > target * SLACK:
                        failures.append(f'{operation}: {label} {value:.2f} s misses the {target:.2f} s target by more than 50%')
        return '\n'.join(out), failures


def main():
    entries_env = os.environ.get('BENCH_ENTRIES', '').strip()
    entries = int(entries_env) if entries_env else None
    import generate_large
    stress = entries is not None and entries != generate_large.DEFAULT_ENTRIES
    reps = int(os.environ.get('BENCH_REPS') or (2 if entries and entries >= 50000 else 5))
    if reps < 1:
        raise SystemExit('BENCH_REPS must be positive')
    seed = int(os.environ['BENCH_SEED']) if os.environ.get('BENCH_SEED') else None
    base = Path(os.environ.get('AGENTEXPENSE_TEST_ROOT') or '/tmp')
    root = Path(tempfile.mkdtemp(prefix='agentexpense-bench-', dir=base))
    if not str(root.resolve()).startswith(('/tmp/', '/private/tmp/')):
        raise SystemExit('The timing run must stay under /tmp')
    bench = Bench(root, entries, reps, seed)
    status = 0
    try:
        started = time.perf_counter()
        bench.run()
        table, failures = bench.table(targets=not stress)
        header = [f'# Save timings ({"stress run, no targets" if stress else "targets checked"})', '',
                  *(f'- {note}' for note in bench.notes), f'- {reps} measured repetitions; run took {time.perf_counter() - started:.0f} s', '']
        report = '\n'.join([*header, table, ''])
        if failures:
            report += '\nMissed by more than 50%:\n' + '\n'.join(f'- {f}' for f in failures) + '\n'
            status = 1
        elif not stress:
            report += '\nEvery target is met or missed by less than 50%.\n'
        print(report)
        if os.environ.get('BENCH_REPORT'):
            Path(os.environ['BENCH_REPORT']).write_text(report)
        if os.environ.get('BENCH_JSON'):
            Path(os.environ['BENCH_JSON']).write_text(json.dumps(dict(
                samples=[dict(operation=s.operation, response=s.response, committed=s.committed, pickup=s.pickup, line=s.line) for s in bench.samples],
                lines=bench.lines, notes=bench.notes), indent=1))
    finally:
        if os.environ.get('BENCH_KEEP') == '1':
            print(f'Kept {root}')
        else:
            shutil.rmtree(root, ignore_errors=True)
    return status


if __name__ == '__main__':
    sys.exit(main())
