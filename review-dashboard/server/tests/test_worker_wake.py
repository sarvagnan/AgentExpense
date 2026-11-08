"""Worker deadlines and check completion use controlled clocks/events, not sleeps."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import secrets
import threading
from unittest.mock import patch

from fastapi.testclient import TestClient
from support import Fixture
from drafts import Drafts
from worker import Worker
from statements import Statements
from app import create_app


class WorkerWakeTests(Fixture):
    def test_pause_and_claim_expiry_resume_queued_work_when_the_deadline_fires(self):
        from support import ready
        for kind, expected in [('pause',5),('claim',3),('claim-at-inspection',0)]:
            with self.subTest(kind=kind), self.client(worker=False) as client:
                answer = ready(new=False);answer['attachment-names'] = []
                self.set_answers(answer)
                worker, drafts = client.app.state.worker, client.app.state.drafts
                wall, monotonic = [datetime.now(timezone.utc)], [100]
                worker.pause.clock = drafts.clock = lambda:wall[0]
                clock_reads = []
                def clock():
                    clock_reads.append(1)
                    if kind == 'claim-at-inspection' and len(clock_reads) == 3:
                        wall[0] += timedelta(seconds=3);monotonic[0] += 3
                    return monotonic[0]
                worker.clock = clock
                worker.daily_refresh.next_check = float('inf')
                identity = self.create(client,upload=False);self.save(client,identity)
                if kind == 'pause':worker.pause.fail('transient')
                else:
                    draft = drafts.get(identity);drafts.claim(draft)
                    draft['claim']['expires_at'] = (wall[0]+timedelta(seconds=3)).isoformat();drafts.put(draft)
                fired = []
                case = self
                class Event:
                    def set(self):pass
                    def clear(self):pass
                    def wait(self,delay):
                        if drafts.get(identity)['state']=='added':worker.stop_event.set();return
                        case.assertEqual(delay,expected)
                        fired.append(delay);wall[0] += timedelta(seconds=delay);monotonic[0] += delay
                worker.wake = Event()
                worker.loop()
                self.assertEqual(fired,[expected])
                self.assertEqual(drafts.get(identity)['state'],'added')

    def test_finished_pause_recheck_wakes_the_worker(self):
        wall = [datetime.now(timezone.utc)]
        worker = Worker(self.settings, Drafts(self.ledger))
        worker.pause.clock = lambda:wall[0]
        worker.pause.fail('auth');wall[0] += timedelta(seconds=30)
        entered, release = threading.Event(), threading.Event()
        class Gate:
            def require_ready(self, **kwargs):
                entered.set()
                if not release.wait(10): raise RuntimeError('pause check was not released')
        worker.pause.tick(Gate())
        self.assertTrue(entered.wait(10));release.set()
        worker.pause.check_thread.join(10)
        self.assertFalse(worker.pause.check_thread.is_alive())
        self.assertTrue(worker.wake.is_set(), 'The completed pause check left work asleep')

    def test_idle_wait_uses_every_unsignalled_deadline(self):
        wall = datetime.now(timezone.utc)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return wall.astimezone(tz) if tz else wall.replace(tzinfo=None)
        for name, pause, claims, waiting, expected in [
            ('maintenance', False, [], None, 30),
            ('pause', True, [], None, 5),
            ('claim', False, [9, 3], None, 3),
            ('check-wait', False, [], 'Waiting for the ledger check of the latest save.', .5),
            ('writer-wait', False, [], 'Waiting for other ledger changes to finish.', .5),
            ('agent-statement', True, [], 'agent', 5),
            ('minimum', True, [2], 'Waiting for other ledger changes to finish.', .5),
        ]:
            with self.subTest(deadline=name):
                root = self.base/name;root.mkdir()
                settings = replace(self.settings, ledger=root)
                drafts = Drafts(root, clock=lambda:wall)
                worker = Worker(settings, drafts)
                worker.clock = lambda:100
                worker.pause.clock = lambda:wall
                worker.daily_refresh.next_check = float('inf')
                if pause: worker.pause.fail('transient')
                for seconds in claims:
                    draft = drafts.create('Claimed fictional words')
                    draft.update(state='queued', queued_at=wall.isoformat(), claim={
                        'token':'fixture', 'expires_at':(wall+timedelta(seconds=seconds)).isoformat()})
                    drafts.put(draft)
                if waiting:
                    worker.statements = Statements(settings)
                    worker.statements.agent_pause = worker.pause
                    worker.statements.put(dict(id=secrets.token_hex(16),
                        state='agent_waiting' if waiting == 'agent' else 'applying',
                        waiting=None if waiting == 'agent' else waiting,
                        queued_at=wall.isoformat(), created_at=wall.isoformat(), last_check=wall.timestamp()))
                delays = []
                class Event:
                    def set(self): pass
                    def clear(self): pass
                    def wait(self, delay):
                        delays.append(delay);worker.stop_event.set()
                worker.wake = Event()
                with patch('statements.datetime', Clock): worker.loop()
                self.assertEqual(delays, [expected])

    def test_check_completion_wakes_the_worker_on_success_and_failure(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                app = create_app(replace(self.settings, ledger_check=True), start_worker=False)
                check = app.state.ledger_check
                check.hledger = '/bin/false'
                entered, release = threading.Event(), threading.Event()
                def held(root, commit, base):
                    entered.set()
                    if not release.wait(20): raise RuntimeError('check was not released')
                    if failure: raise RuntimeError('injected check failure')
                    return dict(ok=True, seconds=0)
                check.runner = held
                with TestClient(app):
                    try:
                        self.assertTrue(entered.wait(10))
                        app.state.worker.wake.clear()
                    finally:
                        release.set()
                    self.assertTrue(check.wait_idle(10))
                    self.assertTrue(app.state.worker.wake.is_set(), 'An ended check did not notify the worker')
