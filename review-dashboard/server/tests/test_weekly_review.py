"""Controlled calendar and FIFO tests for scheduled weekly review."""
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import re
from threading import Event, Thread
import time
from unittest.mock import Mock, patch

from support import Fixture
import test_weekly_review_batch as batch_tests
import audit
from adapters.base import AdapterError, LookupTools, Result
from drafts import Drafts
from ledger_lock import ledger_lock
from settings import APPLICATION
from store import dump, read_json
from worker import Worker


class WeeklyReviewTests(Fixture):
    add = batch_tests.WeeklyReviewBatchTests.add
    clock = batch_tests.WeeklyReviewBatchTests.clock

    def worker(self, *, today='2026-11-23'):
        worker = Worker(self.settings, Drafts(self.ledger))
        worker.weekly.today = lambda: today
        worker.weekly.gate = Mock()
        worker.daily_refresh.tick = Mock()
        return worker

    def wait_for(self, condition):
        deadline = time.monotonic() + 25
        while not condition():
            self.assertLess(time.monotonic(), deadline, 'Weekly review did not reach the expected state')
            time.sleep(.02)

    def review(self, task):
        if 'fine' in task.schema['properties']:
            notes = json.loads(task.lookups.call('review_run_facts', {})['data'])
            checked = [row['line_id'] for row in notes['verdicts'] if row.get('verdict') == 'ok']
            return Result(dict(fine=[dict(lines=checked, reason='Checked fictional records')] if checked else [],
                               decisions=[row['id'] for row in notes['decisions']], patterns=[]))
        lines = []
        for row in task.lookups.call('review_pending', {})['items']:
            task.lookups.call('review_audit_line', {'line_id': row['id']})
            lines.append(dict(line=row['id'], verdict='ok', note='Checked fictional record'))
        return Result(dict(lines=lines, rules=[]))

    def start_review(self, weekly):
        return weekly.action('start', 'a' * 32, None)['run_id']

    def state(self, weekly):
        return weekly.state()['active']

    def test_existing_weekly_review_true_enables_monday_without_migration(self):
        manifest = self.ledger / 'ledger.json'
        dump(manifest, {**read_json(manifest), 'modules': {'weeklyReview': True}})
        reviewed, flagged, pending = [self.add() for _ in range(3)]
        for line, verdict in ((reviewed, 'confirm'), (flagged, 'propose removal')):
            row = audit.event('review', line_id=line, verdict=verdict, note='Checked fictional record', reviewer='Offline')
            with audit.audit_path(self.ledger, row).open('a') as stream:
                stream.write(json.dumps(row) + '\n')
        self.checkpoint('Legacy fictional settings and verdicts')
        before = {path: path.read_bytes() for path in [manifest, *self.ledger.glob('data/additions/*.jsonl')]}

        weekly = self.worker(today='2026-11-24').weekly
        status = weekly.status(0, 1)
        self.assertEqual((status['enabled'], status['day'], status['enabled_on']), (True, 'Monday', None))
        self.assertTrue(weekly.tick())
        run = self.state(weekly)
        self.assertEqual((run['trigger'], run['slot']), ('scheduled', '2026-11-23'))
        self.assertTrue(weekly.tick())
        batches = read_json(weekly.plan_path(run))['batches']
        self.assertEqual([line for batch in batches for line in batch['lines']], [pending])
        self.assertEqual([item['line'] for batch in batches for item in batch.get('carry', [])], [flagged])
        self.assertEqual({row['line_id']: row['verdict'] for row in audit.records(self.ledger) if row['kind'] == 'review'},
                         {reviewed: 'ok', flagged: 'issue'})
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_review_policy_names_only_review_tools(self):
        old = self.ledger / 'fictional-old.ledger'
        old.touch()
        reviewer = replace(self.settings, old_ledger=old).for_role('review')
        inventory = {tool['name'] for tool in LookupTools.for_settings(reviewer, probe=True).definitions}
        policy = APPLICATION / 'policy/WEEKLY-REVIEW-AGENT.md'
        self.assertEqual(set(re.findall(r'`([a-z_]+)`', policy.read_text())), inventory)

        retired = ('make weekly-review', 'weekly_review.py', 'scheduling/', 'run-weekly-review.sh',
                   'weekly-review.cron', 'weekly-review.service', 'weekly-review.timer', 'org.example.expense-weekly.plist')
        docs = [APPLICATION / name for name in ('AGENTS.md', 'README.md', 'SETUP.md')]
        docs.extend((APPLICATION / 'review-dashboard/server').glob('*.md'))
        for root in ('docs', 'policy'):
            docs.extend((APPLICATION / root).rglob('*.md'))
        for path in docs:
            with self.subTest(path=path.relative_to(APPLICATION)):
                content = path.read_text()
                self.assertFalse([name for name in retired if name in content])

    def test_weekly_slot_clock_handles_missed_weeks(self):
        from weekly_review_service import due_slot
        modules = dict(weeklyReview=True, weeklyReviewDay='Wednesday', weeklyReviewEnabledOn='2026-11-12')
        self.assertIsNone(due_slot(modules, '2026-11-17', None))
        self.assertEqual(due_slot(modules, '2026-11-18', None), '2026-11-16')
        self.assertEqual(due_slot(modules, '2026-12-01', None), '2026-11-23')
        self.assertIsNone(due_slot({**modules, 'weeklyReviewDay': 'Monday'}, '2026-11-23', '2026-11-23'))
        self.assertEqual(due_slot(dict(weeklyReview=True), '2026-11-23', None), '2026-11-23')
        self.assertIsNone(due_slot(dict(weeklyReview=False), '2026-11-23', None))

        with self.client(worker=False) as client:
            weekly = client.app.state.worker.weekly
            weekly.today = lambda: '2026-11-24'
            configure = '/api/settings/weekly-review'
            config = dict(enabled=True, day='Wednesday', request_id='1' * 32)
            first = client.post(configure, json=config)
            self.assertEqual(first.status_code, 200, first.text)
            weekly.today = lambda: '2026-11-25'
            self.assertEqual(client.post(configure, json=config).json()['commit_id'], first.json()['commit_id'])
            self.assertEqual(client.post(configure, json={**config, 'day': 'Friday'}).status_code, 409)
            self.assertEqual(client.post(configure, json={**config, 'day': 'Funday'}).status_code, 422)
            url = '/api/review/weekly'
            status = client.get(url).json()
            self.assertEqual((status['enabled'], status['day'], status['enabled_on']), (True, 'Wednesday', '2026-11-24'))
            first = client.post(url, json=dict(action='start', request_id='2' * 32))
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(client.post(url, json=dict(action='start', request_id='3' * 32)).json(), first.json())
            while weekly.tick():
                # Explicitly partial close before attempting reviewed work.
                if self.state(weekly) and self.state(weekly)['slot']:
                    break
            run = self.state(weekly)
            self.assertEqual(run['slot'], '2026-11-23')
            body = dict(action='close-partial', request_id='4' * 32, run_id=run['run_id'])
            self.assertEqual(client.post(url, json=body).status_code, 200)
            weekly.tick()
            history = client.get(url + '?limit=1').json()['history']
            self.assertEqual((history[0]['status'], history[0]['checked']), ('partial', 0))
            self.assertEqual(client.post(url, json=body).json(), first.json())
            self.assertFalse(weekly.tick())
            disabled = client.post(configure, json=dict(enabled=False, day='Wednesday', request_id='5' * 32))
            self.assertEqual(disabled.status_code, 200, disabled.text)
            status = client.get(url).json()
            self.assertEqual((status['enabled'], status['day'], status['enabled_on']), (False, 'Wednesday', None))

    def test_review_call_yields_after_deadline(self):
        lines = [self.add() for _ in range(2)]
        self.checkpoint('Two fictional audit lines')
        worker = self.worker()
        first, release, foreground, prepared, publish = (Event() for _ in range(5))
        calls = []

        def invoke(task):
            calls.append(task)
            if len(calls) == 1:
                first.set()
                if release.wait(20):
                    replace(task, deadline=0).remaining()
                raise AssertionError('Timed call was never released')
            self.assertTrue(foreground.is_set())
            if len(calls) == 2:
                self.assertEqual(self.state(worker.weekly)['batches'], [dict(lines=[line], rules={}) for line in lines])
                self.assertEqual(worker.weekly.status(0, 20)['active']['checked'], 0)
                prepared.set()
                self.assertTrue(publish.wait(20))
            return self.review(task)

        observed = []
        def enqueue(signal):
            def work():
                observed.append(len([row for row in audit.records(self.ledger) if row['kind'] == 'review']))
                signal.set()
            job = Thread(target=worker.call, args=(work,), daemon=True)
            job.start()
            self.wait_for(lambda: bool(worker.calls))
            return job

        with patch('adapters.get_adapter', return_value=Mock(run=invoke)):
            run_id = self.start_review(worker.weekly)
            worker.start()
            try:
                self.assertTrue(first.wait(20))
                self.assertEqual(worker.weekly.status(0, 20)['active']['checked'], 0)
                job = enqueue(foreground)
                release.set()
                self.assertTrue(prepared.wait(20))
                saved = enqueue(Event())
                publish.set()
                self.wait_for(lambda: self.state(worker.weekly) is None)
                job.join(2)
                saved.join(2)
            finally:
                release.set()
                publish.set()
                worker.close()
        self.assertEqual(observed, [0, 0])
        self.assertEqual(len(calls), 4)
        self.assertEqual([row['line_id'] for row in audit.records(self.ledger) if row['kind'] == 'review'], lines)
        finish = worker.weekly.status(0, 20)['history'][0]
        self.assertEqual((finish['run_id'], finish['status'], finish['checked']), (run_id, 'partial', 2))
        self.assertTrue(finish['history_errors'])

    def test_review_restarts_and_role_pauses_are_independent(self):
        self.add()
        path = self.ledger / 'ledger.json'
        dump(path, {**read_json(path), 'modules': {'weeklyReview': True}})
        self.checkpoint('Fictional pending review')
        worker = self.worker()
        tick = 100
        current = self.clock
        worker.clock = lambda: tick
        worker.review_pause.clock = worker.pause.clock = lambda: current
        worker.pause.fail('usage_limit')
        run_id = self.start_review(worker.weekly)
        worker.weekly.gate.require_ready.side_effect = ValueError('Reviewer needs to sign in')
        adapter = Mock(run=Mock(side_effect=[AdapterError('Offline fake usage limit', kind='usage_limit'),
                                             *[AdapterError('Fake timeout', kind='timeout') for _ in range(3)],
                                             self.review]))
        with patch('adapters.get_adapter', return_value=adapter):
            while worker.weekly.tick():
                pass
            self.assertEqual(worker.weekly.status(0, 20)['active']['reason'], 'Reviewer needs to sign in')
            adapter.run.assert_not_called()
            tick += 30
            worker.weekly.gate.status.return_value = {'state': 'paused', 'reason': 'Retry readiness'}
            worker.weekly.gate.require_ready.side_effect = None
            worker.weekly.tick()
            worker.weekly.gate.require_ready.assert_called_with(force=True, cancel=worker.stop_event)
            worker.weekly.tick()
            self.assertEqual(worker.review_pause.status()['kind'], 'usage_limit')
            self.assertEqual(worker.pause.status()['attempt'], 1)

            worker = self.worker()
            worker.clock = lambda: tick
            worker.review_pause.clock = worker.pause.clock = lambda: current
            self.assertEqual(self.state(worker.weekly)['run_id'], run_id)
            self.assertEqual(self.state(worker.weekly)['slot'], '2026-11-23')
            self.assertFalse(worker.weekly.tick())
            current += timedelta(minutes=16)
            tick += 30
            worker.weekly.tick()
            for _ in range(3):
                worker.weekly.tick()
            self.assertIn('deadline', self.state(worker.weekly)['error'])
            worker.weekly.action('retry', 'b' * 32, run_id)
            adapter.run.side_effect = self.review
            worker.weekly.tick()
            with ledger_lock(self.ledger):
                self.assertFalse(worker.weekly.tick())
                self.assertEqual(worker.weekly.status(0, 20)['active']['checked'], 0)
            calls = adapter.run.call_count
            dirty = self.ledger / 'uncommitted-fixture'
            dirty.write_text('Fictional pending edit')
            tick += 30
            self.assertFalse(worker.weekly.tick())
            self.assertEqual(worker.weekly.status(0, 20)['active']['checked'], 0)
            dirty.unlink()
            tick += 30
            while worker.weekly.tick():
                pass
            self.assertEqual(adapter.run.call_count, calls + 1)
            self.assertEqual(worker.weekly.status(0, 20)['history'][0]['checked'], 1)
            self.assertIsNone(worker.review_pause.status())
            self.assertEqual(worker.pause.status()['kind'], 'usage_limit')

    def test_empty_finish_and_history_diagnostics(self):
        path = self.ledger / 'ledger.json'
        manifest = read_json(path)
        dump(path, {**manifest, 'auditStart': {'after': None}, 'modules': {'weeklyReview': True}})
        self.checkpoint('Fictional schedule')
        worker = self.worker()
        self.assertTrue(worker.weekly.tick())  # Reserve the slot.
        self.assertTrue(worker.weekly.tick())  # Freeze.
        self.assertTrue(worker.weekly.tick())  # Read the empty plan.
        dirty = self.ledger / 'uncommitted-fixture'
        dirty.write_text('Fictional pending edit')
        self.assertFalse(worker.weekly.tick())
        self.assertIsNone(self.state(worker.weekly).get('error'))
        dirty.unlink()
        worker.weekly.next_check = 0
        while worker.weekly.tick():
            pass
        status = worker.weekly.status(0, 20)
        self.assertIsNone(status['active'])
        self.assertEqual((status['history'][0]['status'], status['history'][0]['slot']), ('empty', '2026-11-23'))
        worker.weekly.gate.require_ready.assert_not_called()

    def test_new_reviewer_retries_only_outstanding_frozen_work(self):
        lines = [self.add() for _ in range(4)]
        self.checkpoint('Fictional backlog')
        worker = self.worker()
        with patch('adapters.get_adapter', return_value=Mock(run=self.review)):
            old_id = self.start_review(worker.weekly)
            while worker.weekly.tick():
                pass
            self.assertTrue(worker.weekly.tick())  # Prepare the first batch.
            self.assertTrue(worker.weekly.tick())  # Commit it.
            later = self.add()
            self.checkpoint('A later fictional arrival')
            self.settings = replace(self.settings, review_profile=(('model', 'offline-reviewer'),))
            worker = self.worker()
            while worker.weekly.tick():
                pass
            old = worker.weekly.status(0, 20)['history'][0]
            self.assertEqual((old['run_id'], old['status'], old['checked']), (old_id, 'partial', 3))
            new_id = worker.weekly.action('retry', 'c' * 32, old_id)['run_id']
            while worker.weekly.tick():
                pass
            while worker.weekly.tick():
                pass
        history = worker.weekly.status(0, 20)['history']
        self.assertEqual((history[0]['run_id'], history[0]['model'], history[0]['checked']),
                         (new_id, 'offline-reviewer', 1))
        records = audit.records(self.ledger)
        self.assertEqual([row['line_id'] for row in records if row['kind'] == 'review'], lines)
        self.assertEqual([row['id'] for row in audit.unreviewed(records)], [later])
