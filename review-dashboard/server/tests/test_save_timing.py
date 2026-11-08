"""One structured timing line per save, in the server log only; disposable ledgers."""
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from support import Fixture, APPLICATION
from ledger_lock import ledger_lock
import save_timing

# hledger is the background second check: a save never waits for it (ledger_check.py).
VERIFY_STEPS = {'commit', 'verify', 'journal', 'store_check', 'schema', 'strict_check', 'commit_jj', 'jj'}


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append((record.getMessage(), record.save_timing))


@contextmanager
def captured():
    handler = Capture()
    save_timing.LOG.addHandler(handler)
    previous = save_timing.LOG.level
    save_timing.LOG.setLevel(logging.INFO)
    try:
        yield handler.lines
    finally:
        save_timing.LOG.removeHandler(handler)
        save_timing.LOG.setLevel(previous)


class SaveTimingTests(Fixture):
    def ledger_bytes(self):
        return {path: data for path, data in self.bytes().items() if path != 'expense_ledger.ledger'}

    def test_settings_save_logs_every_step_once_and_writes_nothing_into_the_ledger(self):
        with captured() as lines, self.client(worker=False) as client:
            response = client.post('/api/settings/name', json={'name': 'Timing Ledger'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(client.get('/api/health').status_code, 200)
        self.assertEqual(len(lines), 1, lines)
        message, line = lines[0]
        self.assertTrue(message.startswith('save-timing {'))
        self.assertEqual(json.loads(message.split(' ', 1)[1]), line)
        self.assertEqual((line['operation'], line['outcome']), ('settings', 'ok'))
        self.assertLessEqual(VERIFY_STEPS | {'lock_wait'}, set(line['steps']))
        steps = line['steps']
        # Nested steps fit inside their parents; the total covers the commit and the rebuild.
        self.assertLessEqual(steps['verify'], steps['commit'])
        self.assertNotIn('hledger', steps, 'Saves no longer wait for hledger')
        self.assertLessEqual(steps['journal'] + steps['store_check'], steps['verify'] + .0002)  # four-decimal rounding
        self.assertLessEqual(steps['schema'] + steps['strict_check'], steps['store_check'] + .0002)
        self.assertLessEqual(steps['commit'], line['total'])
        self.assertNotIn('data', steps, 'Background builds do not delay the save')
        self.assertEqual(list(steps), [s for s in save_timing.STEPS if s in steps])
        text = json.dumps(self.ledger_bytes(), default=lambda b: b.decode('utf-8', 'replace'))
        self.assertNotIn('save-timing', text)
        self.assertNotIn('strict_check', text)
        self.assertNotIn('save-timing', self.jj('log', '--no-graph', '-T', 'description'))

    def test_agent_add_logs_pickup_agent_and_prepare(self):
        with captured() as lines, self.client() as client:
            identity = self.create(client)
            self.save(client, identity)
            self.wait(client, identity, 'added')
        self.assertEqual([l['operation'] for _, l in lines], ['add'])
        line = lines[0][1]
        self.assertEqual(line['outcome'], 'ok')
        self.assertLessEqual(VERIFY_STEPS | {'pickup', 'agent', 'prepare', 'lock_wait'}, set(line['steps']))
        self.assertLess(line['steps']['pickup'], 60)
        self.assertLessEqual(line['steps']['agent'] + line['steps']['commit'], line['total'])

    def test_failed_commit_logs_error_outcome_and_reads_log_nothing(self):
        from currency_settings import update_name
        with captured() as lines, patch('commit.verify', side_effect=ValueError('Injected verification failure')):
            with self.assertRaisesRegex(RuntimeError, 'failed verification'):
                update_name(self.settings, 'Refused Ledger')
        self.assertEqual(len(lines), 1, lines)
        line = lines[0][1]
        self.assertEqual((line['operation'], line['outcome']), ('settings', 'error'))
        self.assertNotIn('data', line['steps'])
        with captured() as lines, self.client(worker=False) as client:
            self.assertEqual(client.get('/api/rules').status_code, 200)
            self.assertEqual(client.get('/api/drafts').status_code, 200)
        self.assertEqual(lines, [])

    def test_lock_wait_counts_time_spent_behind_another_writer(self):
        from currency_settings import update_name
        errors = []
        def save():
            try: update_name(self.settings, 'Waiting Ledger')
            except Exception as exc: errors.append(exc)
        with captured() as lines:
            with ledger_lock(self.ledger):
                thread = threading.Thread(target=save)
                thread.start()
                time.sleep(.4)
            thread.join(timeout=180)
        self.assertEqual(errors, [])
        self.assertEqual(len(lines), 1, lines)
        self.assertGreaterEqual(lines[0][1]['steps']['lock_wait'], .35)

    def test_hledger_is_timed_only_when_a_save_asks(self):
        env = {k: v for k, v in os.environ.items() if k != 'EXPENSES_STEP_TIMINGS'}
        env['LEDGER_DIR'] = str(self.ledger)
        plain = subprocess.run(['make', '-n', 'check'], cwd=self.application, env=env, capture_output=True, text=True)
        timed = subprocess.run(['make', '-n', 'check'], cwd=self.application, env={**env, 'EXPENSES_STEP_TIMINGS': str(self.base / 'steps.jsonl')}, capture_output=True, text=True)
        self.assertEqual((plain.returncode, timed.returncode), (0, 0), plain.stderr + timed.stderr)
        self.assertNotIn('step_timings.py', plain.stdout)
        self.assertIn('scripts/ledger/step_timings.py hledger -- ', timed.stdout)
        self.assertEqual(plain.stdout.splitlines()[-1], timed.stdout.splitlines()[-1].split(' -- ', 1)[1])
        self.assertIn('scripts/ledger/second_check.py verify', plain.stdout)
        # make commit (so every save) leaves hledger to the background check.
        background = subprocess.run(['make', '-n', 'check'], cwd=self.application, env={**env, 'EXPENSES_HLEDGER_CHECK': 'background'}, capture_output=True, text=True)
        self.assertEqual(background.returncode, 0, background.stderr)
        self.assertNotIn('second_check.py', background.stdout)
        self.assertIn('hledger second check: in the background', background.stdout)


class StepTimingsTests(Fixture):
    def test_wrapper_records_step_and_keeps_exit_status(self):
        sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
        import step_timings
        target = self.base / 'steps.jsonl'
        script = str(APPLICATION / 'scripts/ledger/step_timings.py')
        env = {**os.environ, step_timings.ENV: str(target)}
        self.assertEqual(subprocess.run([sys.executable, script, 'hledger', '--', sys.executable, '-c', 'raise SystemExit(3)'], env=env).returncode, 3)
        self.assertEqual(subprocess.run([sys.executable, script, 'hledger', '--', sys.executable, '-c', 'pass'], env=env).returncode, 0)
        self.assertEqual(list(step_timings.read(target)), ['hledger'])
        self.assertEqual(len(target.read_text().splitlines()), 2)
        quiet = {k: v for k, v in os.environ.items() if k != step_timings.ENV}
        self.assertEqual(subprocess.run([sys.executable, script, 'hledger', '--', sys.executable, '-c', 'pass'], env=quiet).returncode, 0)
        self.assertEqual(len(target.read_text().splitlines()), 2)
        self.assertEqual(step_timings.read(self.base / 'missing.jsonl'), {})
        self.assertNotEqual(subprocess.run([sys.executable, script, 'hledger'], env=env, capture_output=True).returncode, 0)
