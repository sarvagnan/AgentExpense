"""Force second-check completion at the boundaries where people can act concurrently."""
from copy import deepcopy
from dataclasses import replace
import threading
from types import SimpleNamespace
from unittest.mock import patch

from support import Fixture
import agent
import ledger_check
from ledger_check import LedgerCheck
from store import dump
from test_ledger_check import lunch, fix_answer


class LedgerCheckRaces(Fixture):
    def checker(self):
        self.settings = replace(self.settings, ledger_check=True)
        check = LedgerCheck(self.settings, hledger='fictional-hledger')
        check.drafts = self.drafts
        self.addCleanup(check.close)
        self.failure = dict(commit='b' * 40, checked_at='2026-11-01T00:00:00+00:00', outcome='fail',
                            failure=dict(finding=dict(kind='account', title='An account isn’t declared', summary='Fictional finding',
                                                      entry_id='1' * 16, accounts=['Assets:Cash']), changed=[], app_problem=False))
        check.write(self.failure)
        return check

    def proposal(self):
        from drafts import Drafts
        self.drafts = Drafts(self.ledger)
        dump(self.ledger / 'data/expenses/2026.json', [lunch('1' * 16)])
        check = self.checker()
        worker = SimpleNamespace(settings=self.settings, drafts=self.drafts, wake=threading.Event(), stop_event=threading.Event(),
                                 pause=SimpleNamespace(success=lambda: None, fail=lambda *args: None))
        return check, worker

    def test_apply_cannot_miss_a_failure_between_blocked_and_pending(self):
        from test_ledger_check import LedgerCheckTests
        from drafts import Drafts
        self.drafts = Drafts(self.ledger)
        check = self.checker()
        check.write(dict(outcome='pass', good=dict(commit='a' * 40)))
        check.running, check.checking_commit = True, 'b' * 40
        finishing, finished = threading.Event(), threading.Event()
        threads, acquired = [], []
        blocked = check.blocked
        def complete():
            immediate = check.condition.acquire(blocking=False)
            acquired.append(immediate)
            finishing.set()
            if not immediate: check.condition.acquire()
            try:
                check.write(self.failure); check.running = False
            finally: check.condition.release()
            finished.set()
        def read(account):
            reason = blocked(account)
            thread = threading.Thread(target=complete, daemon=True); threads.append(thread); thread.start()
            self.assertTrue(finishing.wait(5))
            if acquired[0]: self.assertTrue(finished.wait(5))
            return reason
        with self.client(worker=False, ledger_check=False) as client:
            statements = client.app.state.statements; statements.ledger_check = check
            identity = LedgerCheckTests.session(self, client, 'Assets:Cash')
            session = statements.get(identity); session.update(state='applying', queued_at='2026-11-01T00:00:00+00:00'); statements.put(session)
            with patch.object(check, 'blocked', read), patch('statements.StatementTransaction.apply_statement', side_effect=AssertionError('Apply bypassed the check')) as apply:
                statements.work(threading.Event())
            for thread in threads: thread.join(5)
            self.assertTrue(finished.is_set())
            self.assertEqual(apply.call_count, 0, 'Apply bypassed a failure that completed between its two guards')

    def test_a_ready_answer_cannot_overwrite_resolution_between_its_read_and_write(self):
        check, worker = self.proposal()
        draft = check.fix(self.drafts, worker)
        get = self.drafts.get
        threads, triggered, answered = [], [False], [False]
        def read(identity, **kwargs):
            saved = get(identity, **kwargs)
            if kwargs.get('include_discarded') and answered[0] and not triggered[0]:
                triggered[0] = True
                done, attempted, acquired = threading.Event(), threading.Event(), []
                def resolve():
                    immediate = self.drafts.mutex.acquire(blocking=False)
                    acquired.append(immediate); attempted.set()
                    if not immediate: self.drafts.mutex.acquire()
                    try: check.resolve_fixes()
                    finally: self.drafts.mutex.release()
                    done.set()
                thread = threading.Thread(target=resolve, daemon=True); threads.append(thread); thread.start()
                self.assertTrue(attempted.wait(5))
                if acquired[0]: self.assertTrue(done.wait(5))
            return saved
        def answer(*args, **kwargs):
            answered[0] = True
            return fix_answer(lunch('1' * 16)), {}
        with patch('agent.run', answer), patch.object(self.drafts, 'get', read):
            ledger_check.propose(worker, draft)
        for thread in threads: thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(get(draft['id'])['ledger_fix']['state'], 'resolved')

    def test_question_failure_and_retry_preserve_a_pass_while_the_agent_works(self):
        for outcome in ('question', 'transient', 'bad_answer'):
            with self.subTest(outcome=outcome):
                check, worker = self.proposal()
                draft = check.fix(self.drafts, worker)
                def run(*args, **kwargs):
                    check.resolve_fixes()
                    if outcome == 'question': return dict(status='question', question='Which entry?', explanation=None), {}
                    raise agent.AgentFailure('Fictional failure', dict(failure_kind=outcome))
                with patch('agent.run', run): ledger_check.propose(worker, draft)
                self.assertEqual(self.drafts.get(draft['id'])['ledger_fix']['state'], 'resolved')
                self.assertEqual(self.drafts.get(draft['id'])['state'], 'question')

    def test_a_pass_before_queueing_never_starts_the_agent(self):
        check, worker = self.proposal()
        condition = check.condition
        class FinishAfterLink:
            def __enter__(self): return condition.__enter__()
            def __exit__(self, *args):
                result = condition.__exit__(*args)
                if check.read().get('fix'):
                    check.write(dict(outcome='pass', failure=None, good=dict(commit='b' * 40)))
                    check.resolve_fixes()
                return result
        with patch.object(check, 'condition', FinishAfterLink()): draft = check.fix(self.drafts, worker)
        self.assertEqual(self.drafts.get(draft['id'])['state'], 'question')
        with patch('agent.run') as run: ledger_check.propose(worker, self.drafts.get(draft['id']))
        self.assertEqual(run.call_count, 0)

    def test_check_again_remembers_a_missing_last_good_commit_until_a_pass(self):
        check, _ = self.proposal()
        check.write(dict(outcome='pass', good=dict(commit='a' * 40)))
        bases = []
        def runner(root, commit, base):
            bases.append(base)
            return dict(ok=False, finding=self.failure['failure']['finding'], changed=None, base_missing=bool(base))
        check.runner = runner
        with patch('second_check.committed', return_value='b' * 40):
            check.check_once(); self.assertEqual(check.item()['changes'], 'missing')
            check.check_once(); self.assertEqual(check.item()['changes'], 'missing')
            check.runner = lambda *args: dict(ok=True)
            check.check_once(); self.assertIsNone(check.item())
            check.runner = runner
            check.check_once(); self.assertEqual(bases[-1], 'b' * 40)
