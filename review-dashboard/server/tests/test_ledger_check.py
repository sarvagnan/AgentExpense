"""hledger as the background second check: coalescing, snapshots, records and the Ledger check item."""
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import threading
import unittest
from unittest.mock import patch

from support import Fixture, entry as store_entry, posting
from store import dump, load, entries
from ledger_lock import ledger_lock
from settings import APPLICATION
import ledger_check
from ledger_check import LedgerCheck
import second_check

HLEDGER = second_check.find_hledger()
needs_hledger = unittest.skipUnless(HLEDGER, 'hledger is not installed')


def lunch(identity, day='2026-10-29', account='Expenses:Food', amount='100.00'):
    return store_entry(identity, day, 'Cafe — lunch — cash', [posting(account, amount), posting('Assets:Cash', '-' + amount)])


def fix_answer(entry, account='Expenses:Food'):
    """The fake agent's proposal: the same entry with a declared expense account."""
    return {'status': 'ready', 'question': None, 'doubts': ['Expenses:Drinks is not declared; Expenses:Food is'],
            'new-accounts': [], 'attachment-names': [], 'explanation': 'Use the declared food account.',
            'entry': {'date': entry['date'], 'status': entry['status'], 'description': entry['description'],
                      'references': None, 'classification': None, 'original-amount': None, 'extensions': {'fuel': None},
                      'notes': [], 'postings': [dict(account=account, amount=entry['postings'][0]['amount'], commodity='INR', cost=None),
                                               dict(account='Assets:Cash', amount=entry['postings'][1]['amount'], commodity='INR', cost=None)]}}


class LedgerCheckTests(Fixture):
    def setUp(self):
        super().setUp()
        self.settings = self.settings.__class__(**{**self.settings.__dict__, 'ledger_check': True})

    # Helpers: commits made by hand (as a sync or a terminal would), and the check itself.

    def commit(self, rows, message='Fictional change', year='2026'):
        dump(self.ledger / f'data/expenses/{year}.json', rows)
        self.jj('commit', '-m', message)
        self.jj('bookmark', 'set', 'main', '-r', '@-')
        return self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')

    def checker(self, **kwargs):
        check = LedgerCheck(self.settings, **{'hledger': HLEDGER or 'hledger', **kwargs})
        self.addCleanup(check.close)
        return check

    def run_now(self, check):
        check.closed = False
        self.assertTrue(check.request('test'))
        self.assertTrue(check.wait_idle(timeout=120))
        return check.read()

    def fake_hledger(self, body):
        """An executable standing in for hledger: `body` is Python run with the journal path in `journal`."""
        path = self.base / ('fake-hledger-' + secrets.token_hex(4))
        path.write_text(f'#!{sys.executable}\nimport sys, os, json, fcntl\njournal = sys.argv[sys.argv.index("-f") + 1]\n' + body)
        path.chmod(0o755)
        return str(path)

    # Scheduling.

    def test_many_commits_coalesce_into_at_most_two_runs_and_the_last_checks_the_latest(self):
        started, release, checked = threading.Event(), threading.Event(), []

        def runner(root, commit, base):
            checked.append(commit)
            started.set()
            self.assertTrue(release.wait(60))
            return dict(commit=commit, ok=True, output='', seconds=0)

        check = self.checker(runner=runner)
        check.start()
        self.assertTrue(started.wait(60))
        rows = []
        for n in range(6):
            rows.append(lunch(f'{n + 1:016x}', day=f'2026-10-{n + 10:02d}'))
            latest = self.commit(rows, f'Fictional change {n}')
            ledger_check.committed(self.ledger)  # what save_timing.make_commit calls after each commit
        self.assertTrue(check.running and check.queued)
        release.set()
        self.assertTrue(check.wait_idle(timeout=60))
        self.assertEqual(check.runs, 2)
        self.assertEqual(len(checked), 2)
        self.assertEqual(checked[-1], latest)
        record = check.read()
        self.assertEqual((record['commit'], record['outcome'], record['good']['commit']), (latest, 'pass', latest))

    def test_the_tick_notices_outside_commits_at_most_every_30_seconds(self):
        now, calls = [1000.0], []
        check = self.checker(runner=lambda root, commit, base: calls.append(commit) or dict(commit=commit, ok=True, output=''),
                             clock=lambda: now[0])
        check.closed = False
        check.tick()
        check.wait_idle(60)
        self.assertEqual(len(calls), 1, 'The first tick checks the main bookmark it has never checked')
        first = self.commit([lunch('1' * 16)])
        now[0] += 29.9
        check.tick()
        check.wait_idle(60)
        self.assertEqual(len(calls), 1, 'Less than 30 seconds later the tick does nothing')
        now[0] += 0.1
        check.tick()
        check.wait_idle(60)
        self.assertEqual(calls[-1], first)
        now[0] += 30
        check.tick()
        check.wait_idle(60)
        self.assertEqual(len(calls), 2, 'An unchanged main bookmark is not checked again')

    def test_start_up_check_and_commits_through_the_server(self):
        with patch.object(LedgerCheck, 'run_snapshot', lambda self, root, commit, base: dict(commit=commit, ok=True, output='', seconds=0)):
            with self.client(worker=False, ledger_check=True) as client:
                check = client.app.state.ledger_check
                self.assertTrue(check.wait_idle(60))
                self.assertEqual(check.runs, 1, 'One check at start-up')
                baseline = check.read()['commit']
                self.assertEqual(baseline, self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'))
                response = client.post('/api/settings/name', json={'name': 'Renamed fixture'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(check.wait_idle(60))
                self.assertEqual(check.runs, 2, 'The save’s commit was checked in the background')
                self.assertNotEqual(check.read()['commit'], baseline)
                status = client.get('/api/ledger-check').json()
                self.assertEqual((status['available'], status['outcome'], status['item']), (True, 'pass', None))

    # Isolation: never the lock, never the working copy.

    @needs_hledger
    def test_no_lock_is_held_while_hledger_runs_and_a_held_lock_does_not_stop_the_check(self):
        probe = self.base / 'lock-probe.json'
        fake = self.fake_hledger(
            f'lock = open({str(self.ledger / ".lock")!r}, "a+b")\n'
            'try:\n    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB); free = True; fcntl.flock(lock.fileno(), fcntl.LOCK_UN)\n'
            'except BlockingIOError:\n    free = False\n'
            f'open({str(probe)!r}, "w").write(json.dumps(free))\n'
            f'os.execv({HLEDGER!r}, [{HLEDGER!r}, *sys.argv[1:]])\n')
        self.commit([lunch('1' * 16)])
        check = self.checker(hledger=fake)
        self.assertEqual(self.run_now(check)['outcome'], 'pass')
        self.assertTrue(json.loads(probe.read_text()), 'The ledger lock was free while hledger ran')
        with ledger_lock(self.ledger):
            # A save holding the lock: the check neither waits for it nor needs it.
            self.assertEqual(self.run_now(check)['outcome'], 'pass')
            self.assertFalse(json.loads(probe.read_text()), 'This time the test held the lock, and the check still ran')

    @needs_hledger
    def test_the_snapshot_is_the_committed_store_never_a_save_in_progress(self):
        seen = self.base / 'seen.ledger'
        fake = self.fake_hledger(f'import shutil\nshutil.copyfile(journal, {str(seen)!r})\nos.execv({HLEDGER!r}, [{HLEDGER!r}, *sys.argv[1:]])\n')
        committed = self.commit([lunch('1' * 16)])
        check = self.checker(hledger=fake)
        with ledger_lock(self.ledger):
            # Half-written by a save in progress: an undeclared account and a broken year file.
            dump(self.ledger / 'data/expenses/2026.json', [lunch('1' * 16), lunch('2' * 16, account='Expenses:In Progress')])
            (self.ledger / 'data/expenses/2027.json').write_text('[{"id": "3333')
            record = self.run_now(check)
        self.assertEqual((record['commit'], record['outcome']), (committed, 'pass'))
        journal = seen.read_text()
        self.assertIn('; id: 1111111111111111', journal)
        self.assertNotIn('In Progress', journal)
        self.assertNotIn('2222222222222222', journal)

    # Results and the item.

    @needs_hledger
    def test_a_failure_is_recorded_with_the_finding_and_the_changes_since_the_last_good_check(self):
        good = self.commit([lunch('1' * 16)])
        check = self.checker()
        self.assertEqual(self.run_now(check)['good']['commit'], good)
        bad = self.commit([lunch('1' * 16), lunch('2' * 16, day='2026-10-30', account='Expenses:Drinks')], 'Fictional hand edit')
        record = self.run_now(check)
        self.assertEqual((record['commit'], record['outcome'], record['good']['commit']), (bad, 'fail', good))
        self.assertTrue(record['checked_at'])
        failure = record['failure']
        self.assertEqual(failure['finding']['kind'], 'account')
        self.assertEqual(failure['finding']['entry_id'], '2' * 16)
        self.assertIn('Expenses:Drinks', failure['output'])
        self.assertFalse(failure['app_problem'], 'The JSON strict check refuses it too: the data has the problem')
        self.assertEqual([e['id'] for e in failure['changed']], ['2' * 16])
        item = check.item()
        self.assertEqual((item['title'], item['entry']['id'], item['entry']['description']), ('An account isn’t declared', '2' * 16, 'Cafe — lunch — cash'))
        self.assertIn('Expenses:Drinks, which isn’t in the ledger’s list of accounts', item['summary'])
        self.assertEqual(item['accounts'], ['Assets:Cash', 'Expenses:Drinks'])
        self.assertEqual(item['since'], record['good']['checked_at'])
        self.assertTrue((self.ledger / 'drafts/ledger-check.json').is_file())
        self.assertNotIn('drafts/ledger-check.json', self.jj('file', 'list'))

    @needs_hledger
    def test_the_common_failure_kinds_are_translated(self):
        head = 'commodity INR\ncommodity USD\naccount Assets:Cash\naccount Expenses:Food\n\n'
        tx = '{date} * {what}\n    ; id: {id}\n    {account}    {amount}\n    Assets:Cash    {payment}\n\n'
        cases = {
            'unbalanced': (tx.format(date='2026-01-02', what='Lunch', id='a' * 16, account='Expenses:Food', amount='100.00 INR', payment='-99.00 INR'),
                           'An entry doesn’t balance', 'off by 1.00 INR', ['Expenses:Food', 'Assets:Cash']),
            'conversion': (tx.format(date='2026-01-02', what='Lunch', id='a' * 16, account='Expenses:Food', amount='100.00 USD', payment='-100.00 INR'),
                           'An entry mixes currencies without a conversion', 'no cost that converts', ['Expenses:Food', 'Assets:Cash']),
            'assertion': (tx.format(date='2026-01-02', what='Lunch', id='a' * 16, account='Expenses:Food', amount='100.00 INR', payment='-100.00 INR = 50.00 INR'),
                          'A confirmed balance doesn’t hold', 'confirmed 50.00 INR, calculated -100.00 INR', ['Assets:Cash', 'Expenses:Food']),
            'account': (tx.format(date='2026-01-02', what='Lunch', id='a' * 16, account='Expenses:Drinks', amount='100.00 INR', payment='-100.00 INR'),
                        'An account isn’t declared', 'uses Expenses:Drinks', ['Expenses:Drinks', 'Assets:Cash']),
            'commodity': (tx.format(date='2026-01-02', what='Lunch', id='a' * 16, account='Expenses:Food', amount='100.00 EUR', payment='-100.00 EUR'),
                          'A currency isn’t declared', 'EUR is used by an entry', ['Expenses:Food', 'Assets:Cash']),
            'dates': (tx.format(date='2026-01-05', what='Lunch', id='b' * 16, account='Expenses:Food', amount='1.00 INR', payment='-1.00 INR')
                      + tx.format(date='2026-01-02', what='Dinner', id='a' * 16, account='Expenses:Food', amount='1.00 INR', payment='-1.00 INR'),
                      'Entries are out of date order', 'comes after a later-dated entry', ['Expenses:Food', 'Assets:Cash']),
            'parse': (tx.format(date='2026-01-02', what='Lunch', id='a' * 16, account='Expenses:Food', amount='100.00 INR @@', payment='-100.00 INR'),
                      'hledger couldn’t read the ledger', 'couldn’t read line', ['Expenses:Food', 'Assets:Cash']),
        }
        for kind, (body, title, words, accounts) in cases.items():
            with self.subTest(kind):
                journal = self.base / f'{kind}.journal'
                journal.write_text(head + body)
                ok, output = second_check.run_hledger(HLEDGER, journal)
                self.assertFalse(ok)
                found = second_check.finding(output, journal.read_text(), str(journal))
                self.assertEqual((found['kind'], found['title'], found['entry_id']), (kind, title, 'a' * 16))
                self.assertIn(words, found['summary'])
                self.assertEqual(found['accounts'], accounts)
        unknown = second_check.finding('hledger: something new went wrong')
        self.assertEqual((unknown['kind'], unknown['entry_id'], unknown['accounts']), ('other', None, []))

    def fed(self, output_for, *, json_fails=False):
        """A runner that feeds hledger's result (the browser harness does the same), on the real snapshot."""
        def runner(root, commit, base):
            work = self.base / ('work-' + secrets.token_hex(4))
            try:
                with patch.object(second_check, 'json_check', (lambda target: 'refused by the JSON check') if json_fails else second_check.json_check):
                    return second_check.snapshot(root, commit, base, 'hledger', work, hledger_result=lambda path, text: output_for(path, text, commit))
            finally:
                shutil.rmtree(work, ignore_errors=True)
        return runner

    @staticmethod
    def unbalanced(path, text, entry_id='1' * 16):
        line = next(b for b in second_check.blocks(text) if b['id'] == entry_id)
        return False, f'hledger: Error: {path}:{line["start"]}-{line["end"]}:\n\nThis transaction is unbalanced.\nThe real postings\' sum should be 0 but is: 1.00 INR'

    def test_hledger_refusing_what_the_json_check_passes_is_an_app_problem(self):
        self.commit([lunch('1' * 16)])
        check = self.checker(runner=self.fed(lambda path, text, commit: self.unbalanced(path, text)))
        self.assertEqual(self.run_now(check)['outcome'], 'fail')
        item = check.item()
        self.assertTrue(item['app_problem'])
        self.assertEqual(item['explanation'], second_check.APP_PROBLEM)
        self.assertIn('This transaction is unbalanced', item['details'])
        with self.assertRaisesRegex(ValueError, 'problem in AgentExpense'):
            check.fix(None, None)

    def test_the_item_closes_when_a_check_passes(self):
        self.commit([lunch('1' * 16)])
        failing = [True]
        check = self.checker(runner=self.fed(lambda path, text, commit: self.unbalanced(path, text) if failing[0] else (True, '')))
        self.run_now(check)
        self.assertIsNotNone(check.item())
        record = check.read()
        record['fix'] = dict(draft_id='0' * 32, commit=record['commit'])
        check.write(record)
        failing[0] = False
        record = self.run_now(check)
        self.assertEqual((record['outcome'], record['failure'], record['fix']), ('pass', None, None))
        self.assertIsNone(check.item())
        self.assertEqual(check.status()['item'], None)

    def test_hledger_missing_means_no_check_no_record_no_item_and_saves_still_work(self):
        with patch.object(second_check, 'find_hledger', lambda environ=None: None):
            with self.client(worker=False, ledger_check=True) as client:
                check = client.app.state.ledger_check
                self.assertFalse(check.available)
                self.assertEqual(client.get('/api/ledger-check').json(), dict(available=False, checking=False, item=None))
                again = client.post('/api/ledger-check/run')
                self.assertEqual(again.status_code, 409)
                self.assertIn('hledger isn’t installed', again.text)
                response = client.post('/api/settings/name', json={'name': 'Renamed without hledger'})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(check.runs, 0)
                self.assertIsNone(check.blocked('Assets:Cash'))
        self.assertFalse((self.ledger / 'drafts/ledger-check.json').exists())

    # Statement Apply (and so a confirmed balance) waits for the accounts involved.

    def session(self, client, account):
        session = dict(id=secrets.token_hex(16), state='review', original_name='Fictional statement.pdf', created_at='2026-11-01T10:00:00+00:00',
                       error=None, waiting=None, statement=dict(account=account, periodEnd='2026-10-31', kind='bank'),
                       plan=dict(items=[dict(needs_user=False, decision=None, rule='match', row=dict(serial='1'))]), decisions=[])
        client.app.state.statements.put(session)
        return session['id']

    def test_apply_is_refused_for_the_accounts_involved_while_the_item_is_open_and_allowed_after(self):
        self.commit([lunch('1' * 16)])
        failing = [True]
        accounts = load(self.ledger)[self.ledger / 'data/accounts.json']
        accounts['accounts']['Assets:Bank'] = __import__('support').metadata('Assets:Bank', kind='bank', currency='INR')
        dump(self.ledger / 'data/accounts.json', accounts)
        self.commit([lunch('1' * 16)], 'Declare a fictional bank')
        check = self.checker(runner=self.fed(lambda path, text, commit: self.unbalanced(path, text) if failing[0] else (True, '')))
        with self.client(worker=False, ledger_check=False) as client:
            client.app.state.statements.ledger_check = check
            self.run_now(check)
            item = check.item()
            self.assertEqual(item['accounts'], ['Assets:Cash', 'Expenses:Food'])
            involved, other = self.session(client, 'Assets:Cash'), self.session(client, 'Assets:Bank')
            refused = client.post(f'/api/statements/{involved}/apply')
            self.assertEqual(refused.status_code, 409)
            self.assertEqual(refused.json()['detail'], item['block']['reason'])
            with self.assertRaisesRegex(ValueError, 'second ledger check'):
                client.app.state.statements.first_run_import([involved])
            self.assertEqual(client.post(f'/api/statements/{other}/apply').status_code, 202, 'Another account is not involved')
            # A finding that names no account and no known changes: every account waits.
            record = check.read()
            record['failure'].update(finding=second_check.finding('hledger: something new went wrong'), changed=None)
            check.write(record)
            self.assertIsNone(check.item()['accounts'])
            again = self.session(client, 'Assets:Bank')
            self.assertEqual(client.post(f'/api/statements/{again}/apply').status_code, 409)
            self.assertIn('every statement waits', check.blocked('Assets:Bank'))
            failing[0] = False
            self.run_now(check)
            self.assertEqual(client.post(f'/api/statements/{involved}/apply').status_code, 202, 'Allowed again once a check passes')

    # Review round: lock order, Apply when it runs, and the minor items.

    def failing(self, *, entry='1' * 16, json_fails=True, rows=None):
        """A committed ledger whose check fails on one entry (fed hledger result)."""
        self.commit(rows or [lunch('1' * 16)])
        check = self.checker(runner=self.fed(lambda path, text, commit: self.unbalanced(path, text, entry), json_fails=json_fails))
        self.run_now(check)
        self.assertIsNotNone(check.item())
        return check

    def test_apply_never_waits_for_drafts_while_holding_the_statements_mutex(self):
        """Item 1: with a fix recorded, Apply's check reads the record only, so a request holding
        drafts → statements can't deadlock with Apply holding statements → drafts."""
        check = self.failing()
        record = check.read();record['fix'] = dict(draft_id='a' * 32, commit=record['commit']);check.write(record)
        with self.client(worker=False, ledger_check=False) as client:
            statements, drafts = client.app.state.statements, client.app.state.drafts
            statements.ledger_check = check;check.drafts = drafts
            identity = self.session(client, 'Assets:Other')
            entered, results = threading.Event(), []
            real = check.blocked
            check.blocked = lambda account: (entered.set(), real(account))[1]

            def other_request():  # Keep, handover, retention: drafts.mutex, then statements.mutex
                with drafts.mutex:
                    entered.wait(10)
                    with statements.mutex:
                        results.append('other')

            def apply():
                try:
                    statements.queue_apply(identity)
                    results.append('apply')
                except Exception as exc:
                    results.append(exc)
            first = threading.Thread(target=other_request, daemon=True);first.start()
            second = threading.Thread(target=apply, daemon=True);second.start()
            second.join(15);first.join(15)
            self.assertFalse(second.is_alive() or first.is_alive(), 'Apply and the other request deadlocked')
            self.assertEqual(sorted(map(str, results)), ['apply', 'other'])

    def test_a_check_failing_after_apply_was_queued_stops_it_when_it_runs(self):
        """Item 2: Statements.work asks again under the ledger lock; a refused session goes back to review
        and nothing is applied. While a check of a newer commit runs, Apply waits for it."""
        check = self.failing()
        with self.client(worker=False, ledger_check=False) as client:
            statements = client.app.state.statements
            statements.ledger_check = check
            identity = self.session(client, 'Assets:Cash')
            session = statements.get(identity);session.update(state='applying', queued_at='2026-11-01T10:00:00+00:00')
            statements.put(session)
            with patch('statements.StatementTransaction.apply_statement', return_value=dict(outcome='ready')) as applied, \
                    patch('statements.refresh', side_effect=lambda settings, result: result):
                statements.work(threading.Event())
                refused = statements.get(identity)
                self.assertEqual((refused['state'], refused['error'], applied.call_count), ('review', check.blocked('Assets:Cash'), 0))
                # Fixed and checked again, but a check of a newer commit is still running: Apply waits.
                record = check.read();record.update(outcome='pass', failure=None);check.write(record)
                session = statements.get(identity);session.update(state='applying', error=None);statements.put(session)
                with check.condition:
                    check.running, check.checking_commit = True, '0' * 40
                statements.work(threading.Event())
                self.assertEqual((statements.get(identity)['waiting'], applied.call_count), ('Waiting for the ledger check of the latest save.', 0))
                with check.condition:
                    check.running, check.checking_commit = False, None
                session = statements.get(identity);session['last_check'] = 0;statements.put(session)  # a waiting session rejoins after 0.5 s
                statements.work(threading.Event())
                self.assertEqual(applied.call_count, 1, 'Applied once the check has nothing against it')

    def test_a_pass_resolves_open_fix_proposals(self):
        """Item 5: the fix draft stays for the person, marked resolved."""
        check = self.failing()
        from drafts import Drafts
        drafts = Drafts(self.ledger);check.drafts = drafts
        draft = drafts.create('Fix the ledger check: An entry doesn’t balance')
        draft.update(state='question', ledger_fix=dict(state='ready', title='An entry doesn’t balance'));drafts.put(draft)
        check.runner = lambda root, commit, base: dict(commit=commit, ok=True, output='')
        self.run_now(check)
        self.assertEqual(drafts.get(draft['id'])['ledger_fix']['state'], 'resolved')

    def test_two_quick_fix_requests_make_one_draft(self):
        """Item 6: fix() is serialised; the second request returns the first draft."""
        check = self.failing()
        from drafts import Drafts
        from types import SimpleNamespace
        drafts = Drafts(self.ledger);check.drafts = drafts
        worker = SimpleNamespace(wake=threading.Event())
        barrier, results = threading.Barrier(2), []
        def ask():
            barrier.wait(5);results.append(check.fix(drafts, worker)['id'])
        threads = [threading.Thread(target=ask) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(30)
        self.assertEqual(len(set(results)), 1, results)
        self.assertEqual(len([d for d in drafts.all() if d.get('ledger_fix')]), 1)
        self.assertEqual(drafts.get(results[0])['state'], 'queued')

    def test_asking_for_a_fix_never_holds_the_check_while_reading_drafts(self):
        """A commit under drafts.mutex (Continue with a new account) calls request(), which takes the
        condition; fix() reads drafts, so it must not hold the condition while it does."""
        check = self.failing()
        from drafts import Drafts
        from types import SimpleNamespace
        drafts = Drafts(self.ledger);check.drafts = drafts
        record = check.read();record['fix'] = dict(draft_id='a' * 32, commit=record['commit']);check.write(record)
        entered, results = threading.Event(), []
        real = check.item
        check.item = lambda *args, **kwargs: (entered.set(), real(*args, **kwargs))[1]

        def commit_under_drafts_mutex():
            # What request() does first, with a timeout: a regression fails here and leaves no thread stuck.
            with drafts.mutex:
                entered.wait(10)
                got = check.condition.acquire(timeout=5)
                if got:
                    check.condition.release()
                results.append(got)

        def ask():
            results.append(check.fix(drafts, SimpleNamespace(wake=threading.Event()))['id'])
        first = threading.Thread(target=commit_under_drafts_mutex, daemon=True);first.start()
        second = threading.Thread(target=ask, daemon=True);second.start()
        first.join(30);second.join(30)
        self.assertFalse(first.is_alive() or second.is_alive(), 'Still running')
        self.assertIn(True, results, 'fix() held the condition while it waited for drafts.mutex')
        self.assertEqual(len(results), 2)

    def test_the_target_is_never_an_unrelated_entry(self):
        """Item 7: a rule or a non-entry line names no target; an assertion prefers a changed entry
        posting to its account; with no location the newest change is marked as a guess."""
        check = self.checker()
        changed = [dict(id='2' * 16, date='2026-10-30', description='Newest', accounts=['Assets:Cash', 'Expenses:Food']),
                   dict(id='3' * 16, date='2026-10-29', description='Older', accounts=['Expenses:Food'])]
        rule = dict(second_check.finding(''), kind='unbalanced', rule_id='f' * 16, line=40)
        self.assertIsNone(check.target(rule, changed))
        self.assertIsNone(check.target(dict(second_check.finding(''), kind='parse', line=3), changed))
        self.assertIsNone(check.target(dict(second_check.finding(''), file='mutual-funds.prices'), changed))
        assertion = dict(second_check.finding(''), kind='assertion', account='Assets:Cash', entry_id='9' * 16, line=50)
        self.assertEqual((check.target(assertion, changed)['id'], check.target(assertion, changed)['guess']), ('2' * 16, True))
        self.assertEqual((check.target(assertion, [])['id'], check.target(assertion, [])['guess']), ('9' * 16, False))
        named = dict(second_check.finding(''), kind='account', entry_id='3' * 16, line=9)
        self.assertEqual((check.target(named, changed)['id'], check.target(named, changed)['guess']), ('3' * 16, False))
        other = second_check.finding('hledger: something new went wrong')
        self.assertEqual((check.target(other, changed)['id'], check.target(other, changed)['guess']), ('2' * 16, True))

    def test_a_last_good_commit_that_left_the_history_is_said_plainly(self):
        """Item 8: the changes can't be listed because the good commit is gone, not because none passed."""
        self.commit([lunch('1' * 16)])
        check = self.checker(runner=self.fed(lambda path, text, commit: self.unbalanced(path, text), json_fails=True))
        gone = 'ab' * 20  # not jj's root commit (all zeros), and in no history
        check.write(dict(commit=gone, outcome='pass', good=dict(commit=gone, checked_at='2026-11-01T10:00:00+00:00')))
        record = self.run_now(check)
        self.assertIsNone(record['good'])
        item = check.item()
        self.assertEqual((item['changes'], item['since'], item['changes_known']), ('missing', None, False))

    def test_a_record_of_another_shape_never_breaks_the_status(self):
        """Item 9: the status carries no item rather than an error."""
        check = self.checker()
        check.path.parent.mkdir(parents=True, exist_ok=True)
        for broken in ({'outcome': 'fail', 'failure': 'not a dict'}, {'outcome': 'fail', 'failure': {'finding': [], 'changed': 3}},
                       {'outcome': 'fail', 'failure': {'finding': {'accounts': 5}, 'changed': [{'no': 'id'}]}}, [1, 2]):
            check.path.write_text(json.dumps(broken))
            status = check.status()
            self.assertIsNone(status['item'])
            self.assertIsNone(check.blocked('Assets:Cash'))

    def test_only_one_check_again_waits(self):
        """Item 10: while one Check again waits for the result, others answer at once."""
        release = threading.Event()
        def runner(root, commit, base):
            release.wait(30)
            return dict(commit=commit, ok=True, output='')
        with patch.object(LedgerCheck, 'run_snapshot', lambda self, root, commit, base: runner(root, commit, base)):
            with self.client(worker=False, ledger_check=True) as client:
                check = client.app.state.ledger_check
                self.assertTrue(check.again_mutex.acquire(blocking=False))  # a first request is waiting
                try:
                    import time
                    started = time.monotonic()
                    status = client.post('/api/ledger-check/run').json()
                    self.assertLess(time.monotonic() - started, 5)
                    self.assertTrue(status['checking'])
                finally:
                    check.again_mutex.release();release.set()

    @needs_hledger
    def test_stopping_the_server_kills_a_hung_check(self):
        """Item 11: close() kills the snapshot's process and doesn't wait for its 10-minute limit."""
        self.commit([lunch('1' * 16)])
        started = self.base / 'hung-started'
        fake = self.fake_hledger(f'open({str(started)!r}, "w").write("yes")\nimport time\ntime.sleep(120)\n')
        check = self.checker(hledger=fake)
        check.closed = False
        check.request('test')
        import time
        deadline = time.monotonic() + 60
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(started.exists())
        began = time.monotonic()
        check.close()
        self.assertLess(time.monotonic() - began, 12)

    def test_a_check_that_cannot_run_is_recorded_and_not_retried_by_the_tick(self):
        """Item 12: an error becomes an app-problem failure for that commit; the tick leaves it."""
        commit = self.commit([lunch('1' * 16)])
        calls = []
        def runner(root, c, base):
            calls.append(c)
            raise RuntimeError('the snapshot printed something else')
        check = self.checker(runner=runner, clock=lambda: 1000.0)
        record = self.run_now(check)
        self.assertEqual((record['commit'], record['outcome'], record['failure']['app_problem']), (commit, 'fail', True))
        item = check.item()
        self.assertEqual(item['title'], 'The second check couldn’t run')
        self.assertIn('the snapshot printed something else', item['details'])
        check.tick();check.wait_idle(30)
        self.assertEqual(len(calls), 1, 'The tick does not re-run the same commit')

    # Ask the agent to fix it, offline: the fake agent proposes, the person approves.

    @needs_hledger
    def test_the_fake_agent_proposes_the_fix_the_person_saves_it_and_the_item_closes(self):
        self.commit([lunch('1' * 16)])
        broken = lunch('2' * 16, day='2026-10-30', account='Expenses:Drinks', amount='40.00')
        self.commit([lunch('1' * 16), broken], 'Fictional hand edit with an undeclared account')
        self.set_answers(fix_answer(broken))
        with self.client(ledger_check=True) as client:
            check = client.app.state.ledger_check
            self.assertTrue(check.wait_idle(120))
            status = client.get('/api/ledger-check').json()
            self.assertEqual((status['outcome'], status['item']['kind'], status['item']['entry']['id']), ('fail', 'account', '2' * 16))
            self.assertFalse(status['item']['app_problem'])
            before = self.bytes()
            created = client.post('/api/ledger-check/fix')
            self.assertEqual(created.status_code, 201, created.text)
            identity = created.json()['id']
            draft = self.wait_condition(client, identity, lambda d: d['state'] == 'question')
            self.assertEqual(draft['ledger_fix']['state'], 'ready', draft)
            self.assertEqual(self.bytes(), before, 'The proposal changed nothing in the ledger')
            self.assertEqual(client.post('/api/ledger-check/fix').json()['id'], identity, 'Asking again opens the same proposal')
            self.assertEqual(client.get('/api/ledger-check').json()['item']['fix'], dict(draft_id=identity, state='ready', question=None, message='Use the declared food account.'))
            prompt = self.calls()[-1]['argv'][-1]
            self.assertIn('An account isn’t declared', prompt)
            self.assertIn('Expenses:Drinks', prompt)
            # The person opens Edit entry with the proposal, checks it and saves it.
            claimed = client.post(f'/api/drafts/{identity}/claim', json={}).json()
            form, token = claimed['form'], claimed['claim_token']
            self.assertEqual(form['entry']['postings'][0]['account'], 'Expenses:Food')
            checked = client.post(f'/api/drafts/{identity}/hand-form', json=dict(claim_token=token, proposal=form))
            self.assertTrue(checked.json()['ok'], checked.text)
            body = dict(version=draft['correction']['version'], proposal=form, sources=[], approved_accounts=[], draft_id=identity,
                        reason=draft['correction']['reason'])
            saved = client.post(f'/api/entries/{"2" * 16}/corrections/{secrets.token_hex(16)}', json=body)
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertTrue(check.wait_idle(120))
            status = client.get('/api/ledger-check').json()
            self.assertEqual((status['outcome'], status['item']), ('pass', None))
            fixed = next(e for e in entries(load(self.ledger)) if e['id'] == '2' * 16)
            self.assertEqual(fixed['postings'][0]['account'], 'Expenses:Food')
            self.assertIn('Reason: "Ledger check: An account isn’t declared"', self.jj('log', '-r', 'main', '--no-graph', '-T', 'description'))
            self.assertEqual(client.get(f'/api/drafts/{identity}').json()['state'], 'added')


if __name__ == '__main__':
    unittest.main()
