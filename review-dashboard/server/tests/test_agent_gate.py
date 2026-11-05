"""Gate transitions, caching, startup concurrency and maintainer commands, offline."""
from contextlib import redirect_stdout
from dataclasses import replace
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent import AgentFailure, run
from agent_check import NESTED_TOOLS, OfflineFailure
from agent_gate import AgentGate, POLICY_HASH, SIGNED_OUT, fingerprint
from agent_tools import login, upgrade
from settings import APPLICATION, Settings


# Gate fingerprints read the selected manifest through Settings.
PERSONA_SENSITIVE = True


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp'), prefix='expense-gate-test-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name).resolve()
        self.config = self.work / 'fake.json'
        self.config.write_text('{"steps": []}')
        fake = self.work / 'fake-codex'
        fake.write_text(f'#!{sys.executable}\n' + Path(__file__).with_name('fake_agent.py').read_text().split('\n', 1)[1])
        fake.chmod(0o755)
        self.settings = Settings(codex=str(fake), state_dir=self.work / 'state')
        environment = patch.dict(os.environ, {'EXPENSES_FAKE_CONFIG': str(self.config)})
        environment.start()
        self.addCleanup(environment.stop)
        self.gate = AgentGate(self.settings)
        self.addCleanup(self.gate.close)

    def configure(self, **values):
        data = json.loads(self.config.read_text())
        data.update(values)
        self.config.write_text(json.dumps(data))

    def checks(self, command):
        path = self.config.with_suffix('.checks')
        calls = list(map(json.loads, path.read_text().splitlines())) if path.exists() else []
        return [c for c in calls if c['argv'][:len(command)] == command]

    def test_pass_persists_and_skips_both_checks_including_after_restart(self):
        self.gate.require_ready()
        self.assertEqual(self.gate.status(), {'state': 'ready', 'reason': None})
        saved = json.loads((self.settings.state_dir / 'agent-check.json').read_text())
        self.assertEqual(saved['state'], 'ready')
        self.gate.require_ready()
        AgentGate(self.settings).require_ready()
        # A new interpreter must derive the same policy key from loaded code.
        code = ('from pathlib import Path; from settings import Settings; from agent_gate import AgentGate; '
                f'AgentGate(Settings(codex={self.settings.codex!r}, state_dir=Path({str(self.settings.state_dir)!r}))).require_ready()')
        subprocess.run([sys.executable, '-c', code], cwd=APPLICATION / 'review-dashboard/server', check=True, capture_output=True)
        self.assertEqual(len(self.checks(['--version'])), 4)
        self.assertEqual(len(self.checks(['debug'])), 1)
        probe = self.checks(['exec'])
        self.assertEqual(len(probe), 1)
        self.assertIn('model_reasoning_effort="low"', probe[0]['argv'])
        self.assertTrue(all(c['home'] == str(self.settings.codex_home) for c in self.checks([])))
        self.assertFalse(Path(probe[0]['cwd']).exists())

    def test_each_key_input_invalidates_a_pass(self):
        self.gate.require_ready()
        self.configure(version='0.166.1')
        self.gate.require_ready()
        AgentGate(replace(self.settings, model='fixture-model')).require_ready()
        import agent
        with patch.object(agent, 'ISOLATION_FLAGS', [*agent.ISOLATION_FLAGS, '-c', 'fixture=true']):
            self.gate.require_ready()
        with patch('agent_check.NESTED_TOOLS', NESTED_TOOLS | {'mcp__ledger__future_tool'}):
            self.configure(extra_tool='mcp__ledger__future_tool')
            self.gate.require_ready()
        self.assertEqual(len(self.checks(['debug'])), 5)
        self.assertEqual(len(self.checks(['exec'])), 5)

    def test_policy_key_uses_imported_source_snapshot_and_constants(self):
        import agent_check
        import hashlib
        self.assertEqual(POLICY_HASH, hashlib.sha256(Path(agent_check.__file__).read_bytes()).hexdigest())
        before = fingerprint(self.settings, 'fixture-version')
        with patch('agent_gate.Path.read_bytes', side_effect=AssertionError('source reread')), \
                patch('agent_check.inspect_prompt', side_effect=AssertionError('bytecode inspected')):
            self.assertEqual(fingerprint(self.settings, 'fixture-version'), before)
        with patch('agent_gate.POLICY_HASH', 'changed-source'):
            self.assertNotEqual(fingerprint(self.settings, 'fixture-version'), before)

    def test_sign_in_recovers_next_draft_without_restart_or_cached_failure(self):
        self.configure(signed_out=True)
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'make agent-login'):
                self.gate.require_ready()
            self.assertEqual(self.gate.status(), {'state': 'signed out', 'reason': SIGNED_OUT})
            self.assertFalse((self.settings.state_dir / 'agent-check.json').exists())
        self.assertFalse(self.checks(['exec']))
        self.configure(signed_out=False)
        self.gate.require_ready()
        self.assertEqual(self.gate.status()['state'], 'ready')

    def test_background_notices_login_without_requiring_a_disabled_submission(self):
        self.configure(signed_out=True)
        self.gate.login_poll_seconds = .05
        self.gate.start()
        end = time.monotonic() + 5
        while self.gate.status()['state'] != 'signed out' and time.monotonic() < end:
            time.sleep(.01)
        self.assertEqual(self.gate.status()['state'], 'signed out')
        self.configure(signed_out=False)
        end = time.monotonic() + 5
        while self.gate.status()['state'] != 'ready' and time.monotonic() < end:
            time.sleep(.01)
        self.assertEqual(self.gate.status()['state'], 'ready')

    def test_background_recovers_login_lost_after_startup_finished(self):
        self.gate.login_poll_seconds = .05
        self.gate.start()
        self.gate.thread.join(5)
        self.assertEqual(self.gate.status()['state'], 'ready')
        self.configure(signed_out=True)
        with self.assertRaisesRegex(ValueError, 'make agent-login'):
            self.gate.require_ready()
        self.configure(signed_out=False)
        end = time.monotonic() + 5
        while self.gate.status()['state'] != 'ready' and time.monotonic() < end:
            time.sleep(.01)
        self.assertEqual(self.gate.status()['state'], 'ready')

    def test_sign_out_after_a_cached_pass_blocks_execution_and_recovers(self):
        self.gate.require_ready()
        before = (self.settings.state_dir / 'agent-check.json').read_bytes()
        self.configure(signed_out=True)
        with self.assertRaisesRegex(ValueError, 'make agent-login'):
            self.gate.require_ready()
        self.assertEqual(self.gate.status()['state'], 'signed out')
        self.assertEqual((self.settings.state_dir / 'agent-check.json').read_bytes(), before)
        self.configure(signed_out=False)
        self.gate.require_ready()
        self.assertEqual(self.gate.status()['state'], 'ready')
        self.assertEqual(len(self.checks(['debug'])), 1)
        self.assertEqual(len(self.checks(['exec'])), 1)

    def test_allowlist_failure_names_only_is_persistent_and_never_probes(self):
        self.configure(extra_tool='exec_command', extra_section=True)
        for gate in (self.gate, self.gate, AgentGate(self.settings)):
            with self.assertRaisesRegex(ValueError, 'exec_command'):
                gate.require_ready()
            self.assertEqual(gate.status()['state'], 'paused')
            self.assertIn('future_section', gate.status()['reason'])
            self.assertNotIn('PRIVATE CONTENT', gate.status()['reason'])
        self.assertEqual(len(self.checks(['debug'])), 1)
        self.assertFalse(self.checks(['exec']))
        self.configure(version='0.166.1', extra_tool=None, extra_section=False)
        self.gate.require_ready()
        self.assertEqual(self.gate.status()['state'], 'ready')

    def test_transient_probe_failure_is_retried_on_next_draft(self):
        self.configure(probe_fail=True)
        for count in (1, 2):
            with self.assertRaisesRegex(ValueError, 'Agent check unavailable'):
                self.gate.require_ready()
            self.assertEqual(self.gate.status()['state'], 'unavailable')
            self.assertFalse((self.settings.state_dir / 'agent-check.json').exists())
            self.assertEqual(len(self.checks(['exec'])), count)
        self.configure(probe_fail=False)
        self.gate.require_ready()
        self.assertEqual(self.gate.status()['state'], 'ready')

    def test_home_and_feature_policy_failures_are_stored(self):
        self.settings.codex_home.mkdir(parents=True)
        forbidden = self.settings.codex_home / 'AGENTS.md'
        forbidden.write_text('PRIVATE CONTENT')
        for gate in (self.gate, AgentGate(self.settings)):
            with self.assertRaisesRegex(ValueError, 'Agent paused.*Home names: AGENTS.md'):
                gate.require_ready()
        self.assertFalse(self.checks(['debug']))
        forbidden.unlink()
        self.configure(version='missing-feature', missing_feature='shell_tool')
        for gate in (self.gate, AgentGate(self.settings)):
            with self.assertRaisesRegex(ValueError, 'Agent paused.*Missing feature names: shell_tool'):
                gate.require_ready()
        self.assertEqual(len(self.checks(['debug'])), 1)
        self.assertFalse(self.checks(['exec']))
        self.assertEqual(json.loads((self.settings.state_dir / 'agent-check.json').read_text())['state'], 'paused')

    def test_policy_probe_failure_cached_but_login_loss_not_cached(self):
        self.configure(probe_mode='shell')
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'Agent paused'):
                self.gate.require_ready()
        self.assertEqual(len(self.checks(['exec'])), 1)
        self.configure(version='0.166.1')
        # Model a login expiring between the offline check and the live probe.
        import agent_check
        original = agent_check.check_live
        def lose_login(*args, **kwargs):
            self.configure(signed_out=True)
            return original(*args, **kwargs)
        previous = (self.settings.state_dir / 'agent-check.json').read_bytes()
        with patch('agent_check.check_live', side_effect=lose_login):
            with self.assertRaisesRegex(ValueError, 'make agent-login'):
                self.gate.require_ready()
        self.assertEqual((self.settings.state_dir / 'agent-check.json').read_bytes(), previous)
        self.configure(signed_out=False, probe_mode=None)
        self.gate.require_ready()
        self.assertEqual(self.gate.status()['state'], 'ready')

    def test_verified_probe_policy_failures_are_persistent(self):
        for mode in ('nonledger', 'shell'):
            with self.subTest(mode=mode):
                self.configure(version=mode, probe_mode=mode)
                before = len(self.checks(['exec']))
                for gate in (self.gate, AgentGate(self.settings)):
                    with self.assertRaisesRegex(ValueError, 'Agent paused'):
                        gate.require_ready()
                    self.assertEqual(gate.status()['state'], 'paused')
                self.assertEqual(len(self.checks(['exec'])), before + 1)
                self.assertEqual(json.loads((self.settings.state_dir / 'agent-check.json').read_text())['state'], 'paused')

    def test_inconclusive_probe_is_uncached_and_retried_on_next_draft(self):
        for mode, reason in (
                ('lookup_failed', 'Probe ledger lookup failed'),
                ('missing_lookup', 'Probe did not complete one turn with an account lookup'),
                ('wrong_account', 'Probe account or shell-unavailable assertion failed'),
                ('shell_available', 'Probe account or shell-unavailable assertion failed'),
                ('missing_evidence', 'Probe event log did not evidence the fixture account')):
            with self.subTest(mode=mode):
                settings = replace(self.settings, state_dir=self.work / mode)
                gate = AgentGate(settings)
                self.addCleanup(gate.close)
                self.configure(probe_mode=mode)
                before = len(self.checks(['exec']))
                for count, attempt in enumerate((gate, gate, AgentGate(settings)), 1):
                    with self.assertRaisesRegex(ValueError, 'Agent check unavailable: ' + reason):
                        attempt.require_ready()
                    self.assertEqual(attempt.status(), {'state': 'unavailable', 'reason': 'Agent check unavailable: ' + reason})
                    self.assertFalse((settings.state_dir / 'agent-check.json').exists())
                    self.assertEqual(len(self.checks(['exec'])), before + count)
                self.configure(probe_mode=None)
                gate.require_ready()
                self.assertEqual(gate.status()['state'], 'ready')
                self.assertEqual(len(self.checks(['exec'])), before + 4)
                self.assertEqual(json.loads((settings.state_dir / 'agent-check.json').read_text())['state'], 'ready')

    def test_invalid_or_incomplete_probe_is_uncached_and_recovers(self):
        self.gate.require_ready()
        cache = self.settings.state_dir / 'agent-check.json'
        previous = cache.read_bytes()
        for mode in ('missing_answer', 'invalid_json', 'invalid_schema', 'network_error',
                     'rate_limit', 'failed_turn', 'malformed_log', 'unknown_item',
                     'lookup_failed', 'missing_lookup', 'wrong_account', 'shell_available', 'missing_evidence'):
            with self.subTest(mode=mode):
                self.configure(version=mode, probe_mode=mode)
                before = len(self.checks(['exec']))
                with self.assertRaisesRegex(ValueError, 'Agent check unavailable'):
                    self.gate.require_ready()
                self.assertEqual(self.gate.status()['state'], 'unavailable')
                self.assertNotIn('PRIVATE', self.gate.status()['reason'])
                self.assertEqual(cache.read_bytes(), previous)
                self.configure(probe_mode=None)
                AgentGate(self.settings).require_ready()
                self.assertEqual(len(self.checks(['exec'])), before + 2)
                previous = cache.read_bytes()

    def test_unexpected_probe_exceptions_are_safe_and_retried(self):
        for error in (ValueError('PRIVATE CONTENT'), RuntimeError('PRIVATE CONTENT')):
            with self.subTest(error=type(error).__name__), patch('agent_check.check_live', side_effect=error) as probe:
                for _ in range(2):
                    with self.assertRaisesRegex(ValueError, 'Agent check unavailable'):
                        self.gate.require_ready()
                    self.assertEqual(self.gate.status()['state'], 'unavailable')
                    self.assertNotIn('PRIVATE', self.gate.status()['reason'])
                    self.assertFalse((self.settings.state_dir / 'agent-check.json').exists())
                self.assertEqual(probe.call_count, 2)

    def test_transient_offline_diagnostic_does_not_pause(self):
        import agent_check
        original = agent_check.invoke
        def unavailable(command, *args, **kwargs):
            if command[1:3] == ['debug', 'prompt-input']:
                raise agent_check.DiagnosticUnavailable('Codex diagnostic timed out')
            return original(command, *args, **kwargs)
        with patch('agent_check.invoke', side_effect=unavailable):
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, 'Agent check unavailable'):
                    self.gate.require_ready()
                self.assertFalse((self.settings.state_dir / 'agent-check.json').exists())
        self.gate.require_ready()
        self.assertEqual(self.gate.status()['state'], 'ready')

    def wait_for_probe(self):
        path = self.config.with_suffix('.probe')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                return json.loads(path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                threading.Event().wait(.01)
        self.fail('Slow fake probe did not start')

    def assert_processes_gone(self, pids):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            remaining = []
            for pid in pids:
                try:
                    os.kill(pid, 0)
                    remaining.append(pid)
                except ProcessLookupError:
                    pass
            if not remaining:
                return
            threading.Event().wait(.01)
        self.fail(f'Probe processes still running: {remaining}')

    def cleanup_probe(self, pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def test_shutdown_kills_slow_probe_group_without_changing_cache(self):
        self.gate.require_ready()
        cache = self.settings.state_dir / 'agent-check.json'
        previous = cache.read_bytes()
        self.configure(version='slow-version', probe_mode='slow')
        self.gate.start()
        pids = self.wait_for_probe()
        self.addCleanup(self.cleanup_probe, pids[0])
        started = time.monotonic()
        self.gate.close()
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(self.gate.thread.is_alive())
        self.assert_processes_gone(pids)
        self.assertEqual(cache.read_bytes(), previous)
        self.assertEqual(self.gate.status()['state'], 'unavailable')

    def test_app_shutdown_cancels_probe_before_joining_waiting_worker(self):
        from app import create_app
        from fastapi.testclient import TestClient
        from agent_check import fixture
        ledger, old, _ = fixture(self.work)
        app = create_app(replace(self.settings, ledger=ledger, old_ledger=old))
        gate, worker = app.state.agent_gate, app.state.worker
        failures = []
        def wait_for_gate():
            try:
                gate.require_ready()
            except ValueError as exc:
                failures.append(str(exc))
        waiting = threading.Thread(target=wait_for_gate, daemon=True)
        def close_worker():
            waiting.join(3)
            self.assertFalse(waiting.is_alive(), 'Worker joined before probe was cancelled')
        self.configure(probe_mode='slow')
        with patch.object(worker, 'start', side_effect=waiting.start), \
                patch.object(worker, 'close', side_effect=close_worker):
            with TestClient(app):
                pids = self.wait_for_probe()
                self.addCleanup(self.cleanup_probe, pids[0])
                started = time.monotonic()
        self.assertLess(time.monotonic() - started, 3)
        self.assert_processes_gone(pids)
        self.assertFalse(gate.thread.is_alive())
        self.assertTrue(failures)
        self.assertFalse((self.settings.state_dir / 'agent-check.json').exists())

    def test_shutdown_also_waits_for_foreground_probe_cancellation(self):
        self.gate.start()
        self.gate.thread.join(5)
        self.assertEqual(self.gate.status()['state'], 'ready')
        cache = self.settings.state_dir / 'agent-check.json'
        previous = cache.read_bytes()
        self.configure(version='foreground-recheck', probe_mode='slow')
        errors = []
        def recheck():
            try:
                self.gate.require_ready()
            except ValueError as exc:
                errors.append(str(exc))
        worker = threading.Thread(target=recheck, daemon=True)
        worker.start()
        pids = self.wait_for_probe()
        self.addCleanup(self.cleanup_probe, pids[0])
        started = time.monotonic()
        self.gate.close()
        self.assertLess(time.monotonic() - started, 3)
        # The directly owned child has been reaped before close returns.
        with self.assertRaises(ProcessLookupError):
            os.kill(pids[0], 0)
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        self.assert_processes_gone(pids)
        self.assertEqual(cache.read_bytes(), previous)

    def test_probe_timeout_kills_group_and_next_draft_retries(self):
        self.gate = AgentGate(replace(self.settings, timeout=1))
        self.addCleanup(self.gate.close)
        self.configure(probe_mode='slow')
        with self.assertRaisesRegex(ValueError, 'Agent check unavailable: Codex diagnostic timed out'):
            self.gate.require_ready()
        pids = self.wait_for_probe()
        self.addCleanup(self.cleanup_probe, pids[0])
        self.assert_processes_gone(pids)
        self.assertFalse((self.settings.state_dir / 'agent-check.json').exists())
        self.configure(probe_mode=None)
        self.gate.require_ready()
        self.assertEqual(self.gate.status()['state'], 'ready')

    def test_worker_waits_for_background_check_once(self):
        entered, release = threading.Event(), threading.Event()
        import agent_check
        original = agent_check.check_offline
        def blocked(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(*args, **kwargs)
        failures = []
        def wait():
            try:
                self.gate.require_ready()
            except Exception as exc:
                failures.append(exc)
        with patch('agent_check.check_offline', side_effect=blocked):
            self.gate.start()
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.gate.status()['state'], 'checking')
            worker = threading.Thread(target=wait)
            worker.start()
            release.set()
            worker.join(10)
            self.gate.close()
        self.assertFalse(worker.is_alive())
        self.assertFalse(failures)
        self.assertEqual(len(self.checks(['exec'])), 1)

    def test_signed_out_background_result_is_shared_with_waiting_worker(self):
        entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
        self.configure(signed_out=True)
        import agent_check
        original = agent_check.check_offline
        def blocked(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(*args, **kwargs)
        errors = []
        def worker():
            waiting.set()
            try:
                self.gate.require_ready()
            except ValueError as exc:
                errors.append(str(exc))
        with patch('agent_check.check_offline', side_effect=blocked):
            self.gate.start()
            self.assertTrue(entered.wait(5))
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(waiting.wait(5))
            # Wait until the worker has entered require_ready and is blocked on
            # the gate lock, without relying on scheduling sleeps.
            import time
            deadline = time.monotonic() + 5
            while thread.ident and time.monotonic() < deadline:
                frame = sys._current_frames().get(thread.ident)
                if frame and frame.f_code.co_name == 'require_ready':
                    break
                threading.Event().wait(.001)
            else:
                self.fail('Worker did not wait for the startup gate')
            release.set()
            thread.join(10)
            self.gate.close()
        self.assertEqual(errors, [SIGNED_OUT])
        self.assertEqual(len(self.checks(['debug'])), 1)

    def test_health_exposes_background_state_and_reason(self):
        from app import create_app
        from fastapi.testclient import TestClient
        from agent_check import fixture
        ledger, old, _ = fixture(self.work)
        settings = replace(self.settings, ledger=ledger, old_ledger=old)
        self.configure(signed_out=True)
        app = create_app(settings, start_worker=False)
        with TestClient(app) as client:
            app.state.agent_gate.thread.join(5)
            self.assertFalse(app.state.agent_gate.thread.is_alive())
            self.assertEqual(client.get('/api/health').json()['agent'],
                             {'state': 'signed out', 'reason': SIGNED_OUT})
            self.configure(signed_out=False)
            app.state.agent_gate.require_ready()
            self.assertEqual(client.get('/api/health').json()['agent'], {'state': 'ready', 'reason': None})
            self.configure(version='temporarily-unavailable', probe_fail=True)
            with self.assertRaises(ValueError) as error:
                app.state.agent_gate.require_ready()
            self.assertEqual(client.get('/api/health').json()['agent'],
                             {'state': 'unavailable', 'reason': str(error.exception)})

    def test_make_installs_only_when_packages_differ_from_the_lockfile(self):
        work = self.work / 'make-fixture'
        dashboard = work / 'review-dashboard'
        dashboard.mkdir(parents=True)
        (work / 'scripts').mkdir()
        shutil.copy(APPLICATION / 'scripts/dependencies.py', work / 'scripts')
        lock = dashboard / 'package-lock.json'
        def locked(own, dependency):
            lock.write_text(json.dumps({'version': own, 'packages': {'': {'version': own}, 'node_modules/a': {'version': dependency}}}))
        locked('0.1.0', '1.0.0')
        binary = work / 'bin'
        binary.mkdir()
        npm = binary / 'npm'
        # A fake npm ci installs exactly the locked packages.
        npm.write_text(f'#!{sys.executable}\nimport json\nfrom pathlib import Path\n'
                       'p=Path("ci-calls"); p.write_text(p.read_text()+"ci\\n" if p.exists() else "ci\\n")\n'
                       'packages={k:v for k,v in json.loads(Path("review-dashboard/package-lock.json").read_text())["packages"].items() if k}\n'
                       'p=Path("review-dashboard/node_modules/.package-lock.json"); p.parent.mkdir(exist_ok=True); p.write_text(json.dumps({"packages":packages}))\n')
        npm.chmod(0o755)
        env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'])
        command = ['make', '-f', str(APPLICATION / 'Makefile'), 'NODE_BIN=' + str(binary), 'node-modules']
        calls = lambda: (work / 'ci-calls').read_text().count('ci\n')
        for _ in range(2):
            subprocess.run(command, cwd=work, env=env, check=True, capture_output=True)
        self.assertEqual(calls(), 1)
        # Timestamps and the application's own version don't matter; package versions do.
        os.utime(dashboard / 'node_modules/.package-lock.json', (1, 1))
        locked('0.2.0', '1.0.0')
        subprocess.run(command, cwd=work, env=env, check=True, capture_output=True)
        self.assertEqual(calls(), 1)
        locked('0.2.0', '1.1.0')
        subprocess.run(command, cwd=work, env=env, check=True, capture_output=True)
        self.assertEqual(calls(), 2)

    def test_runner_gates_both_add_and_statements_before_prompt_or_exec(self):
        self.configure(signed_out=True)
        with patch('agent.prompt_for') as prompt, patch('agent.subprocess.Popen') as popen, \
                patch('agent_gate.get_gate', return_value=self.gate):
            # Let the gate use its own fake subprocess outside the runner patch.
            with patch.object(self.gate, 'require_ready', side_effect=ValueError(SIGNED_OUT)):
                for draft in ({}, {'statement_task': True}):
                    with self.assertRaisesRegex(AgentFailure, 'make agent-login') as raised:
                        run(self.settings, draft, self.work)
                    self.assertFalse(raised.exception.retryable)
            prompt.assert_not_called()
            popen.assert_not_called()

    def test_runner_records_safe_unavailable_reason_for_add_and_statements(self):
        self.configure(probe_fail=True)
        with patch('agent.prompt_for') as prompt, patch('agent_gate.get_gate', return_value=self.gate):
            for draft in ({}, {'statement_task': True}):
                with self.assertRaises(AgentFailure) as error:
                    run(self.settings, draft, self.work)
                self.assertEqual(str(error.exception), self.gate.status()['reason'])
                self.assertIn('Agent check unavailable', str(error.exception))
                self.assertNotIn('PRIVATE', str(error.exception))
                self.assertFalse(error.exception.retryable)
                self.assertEqual(error.exception.record['error'], str(error.exception))
            prompt.assert_not_called()
        self.assertEqual(len(self.checks(['exec'])), 2)

    def test_missing_binary_guidance(self):
        gate = AgentGate(replace(self.settings, codex=str(self.work / 'missing')))
        with self.assertRaisesRegex(ValueError, 'make dashboard'):
            gate.require_ready()
        self.assertEqual(gate.status()['state'], 'unavailable')
        self.assertFalse((self.settings.state_dir / 'agent-check.json').exists())

    def test_login_uses_pinned_binary_and_dedicated_home(self):
        with patch('agent_tools.subprocess.call', return_value=0) as call:
            self.assertEqual(login(self.settings), 0)
        self.assertTrue(self.settings.codex_home.is_dir())
        self.assertEqual(call.call_args.args[0], [self.settings.codex, 'login', '--device-auth'])
        self.assertEqual(call.call_args.kwargs['env']['CODEX_HOME'], str(self.settings.codex_home))

    def test_upgrade_expected_login_and_rejection_leave_manifests_untouched(self):
        application = self.work / 'application'
        dashboard = application / 'review-dashboard'
        dashboard.mkdir(parents=True)
        for name in ('package.json', 'package-lock.json'):
            (dashboard / name).write_text('original ' + name)
        settings = replace(self.settings, application=application)
        self.enterContext(patch('agent_tools.node_environment', return_value={**os.environ,'npm_config_update_notifier':'false'}))
        for error, succeeds in [(OfflineFailure([], signed_out=True), True),
                                (OfflineFailure(['tools: future_tool'], signed_out=True), False)]:
            with patch('agent_tools.subprocess.run') as npm, patch('agent_tools.check_offline', side_effect=error), \
                    redirect_stdout(io.StringIO()):
                if succeeds:
                    self.assertEqual(upgrade(settings, '0.166.0', dry_run=True), 0)
                else:
                    with self.assertRaisesRegex(OfflineFailure, 'future_tool'):
                        upgrade(settings, '0.166.0')
            self.assertEqual(npm.call_count, 1)
            self.assertEqual(npm.call_args.kwargs['env']['npm_config_update_notifier'],'false')
            self.assertTrue(npm.call_args.args[0][3].startswith(str(Path(os.environ['EXPENSES_TEST_TMP'])/'expense-agent-upgrade-')))
            self.assertEqual((dashboard / 'package.json').read_text(), 'original package.json')
            self.assertEqual((dashboard / 'package-lock.json').read_text(), 'original package-lock.json')
        with patch('agent_tools.subprocess.run') as npm, patch('agent_tools.check_offline', side_effect=OfflineFailure([], signed_out=True)), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(upgrade(settings, '0.166.0'), 0)
        self.assertEqual(npm.call_count, 2)
        self.assertEqual(npm.call_args.args[0][-1], '@openai/codex@0.166.0')

    def test_service_checks_host_before_install_and_only_copies_changed_unit(self):
        spec = importlib.util.spec_from_file_location('install_service', APPLICATION / 'scripts/install_expense_service.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = self.work / 'config'
        ledger = self.work / 'service-ledger'
        tools = {'systemctl': '/fake/systemctl', 'uv': '/fake/uv'}
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config), 'LEDGER_DIR': str(ledger)}), patch.object(module.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'systemctl --user is unavailable'):
                module.install()
        self.assertFalse(config.exists())
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config), 'LEDGER_DIR': str(ledger)}), patch.object(module.shutil, 'which', side_effect=tools.get), \
                patch.object(module.subprocess, 'run') as systemctl, patch.object(module, 'node_environment', return_value={'PATH':'/fixture/node/bin:/fixture/tools'}):
            systemctl.return_value.returncode = 0
            # No ledger selected: nothing is installed.
            with self.assertRaisesRegex(ValueError, 'set LEDGER_DIR to your ledger'):
                module.install()
            self.assertFalse(config.exists())
            ledger.mkdir()
            (ledger / 'ledger.json').write_text('{}')
            systemctl.reset_mock()
            module.install()
            unit = config / 'systemd/user/expense-dashboard.service'
            text = unit.read_text()
            self.assertIn('Environment="PATH=/fixture/node/bin:/fixture/tools"', text)
            self.assertIn('Environment=npm_config_update_notifier=false', text)
            self.assertIn(f'Environment=LEDGER_DIR={ledger.resolve()}', text)
            self.assertIn(f'WorkingDirectory={APPLICATION}/review-dashboard/server', text)
            self.assertIn(f'ExecStart={Path("/fake/uv").resolve()} run --locked --offline app.py', text)
            for placeholder in ('@APPLICATION@', '@LEDGER_DIR@', '@UV@'):
                self.assertNotIn(placeholder, text)
            for machine in ('EXPENSES_ALLOWED_HOSTS', 'EXPENSES_ALLOWED_LOGINS', 'EXPENSES_AGENT_MODEL', 'EXPENSES_DEV'):
                self.assertNotIn(machine, text)
            modified = unit.stat().st_mtime_ns
            module.install()
            self.assertEqual(unit.stat().st_mtime_ns, modified)
            self.assertEqual([c.args[0][2] for c in systemctl.call_args_list],
                             ['show-environment', 'daemon-reload', 'restart'] * 2)
