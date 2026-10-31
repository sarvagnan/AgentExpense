"""One background isolation check shared by every use of the agent runner."""
from dataclasses import replace
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time

import agent_check as check
from agent import command_for, codex_environment

SIGNED_OUT = "The agent isn't signed in. Run: make agent-login"
# Snapshot policy source once; Python upgrades must not change the key.
POLICY_HASH = hashlib.sha256(Path(check.__file__).read_bytes()).hexdigest()


def fingerprint(settings, version):
    command = command_for(settings, check.OFFLINE_PROMPT, [])
    return {
        'Codex version': version,
        'isolation flags': command[2:command.index('-m')],
        'model': settings.model,
        'allow-lists': {
            'sections': sorted(check.DEVELOPER_SECTIONS),
            'nested': sorted(check.NESTED_TOOLS), 'lookups': sorted(check.LOOKUPS),
            'policy': POLICY_HASH,
        },
    }


class AgentGate:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.status_lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.generation = 0
        self.state = {'state': 'checking', 'reason': None}

    def status(self):
        with self.status_lock:
            return dict(self.state)

    def set_state(self, state, reason=None):
        with self.status_lock:
            self.state = {'state': state, 'reason': reason}

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.stop.clear()
            self.thread = threading.Thread(target=self._background, name='expense-agent-check', daemon=True)
            self.thread.start()

    def close(self):
        deadline = time.monotonic() + 5
        with self.status_lock:
            self.stop.set()
        # A later draft can own the check after the startup thread has exited.
        if self.lock.acquire(timeout=max(0, deadline - time.monotonic())):
            self.lock.release()
        if self.thread:
            self.thread.join(timeout=max(0, deadline - time.monotonic()))

    def _background(self):
        try:
            self.require_ready()
        except ValueError:
            pass  # Health and draft errors expose the same safe reason.

    def read_cache(self):
        try:
            value = json.loads((self.settings.state_dir / 'agent-check.json').read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_cache(self, value):
        with self.status_lock:
            self.check_stopped()
            self._save_cache(value)

    def check_stopped(self):
        if self.stop.is_set():
            raise check.CheckCancelled()

    def _save_cache(self, value):
        self.settings.state_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=self.settings.state_dir, delete=False) as handle:
            path = Path(handle.name)
            try:
                json.dump(value, handle, sort_keys=True)
                handle.flush()
                path.replace(self.settings.state_dir / 'agent-check.json')
            finally:
                path.unlink(missing_ok=True)

    def require_ready(self):
        # The FIFO worker joins a startup check here. Version is read before each
        # run, even after a cached pass, so replacing the binary needs no restart.
        with self.status_lock:
            generation = self.generation
        with self.lock:
            if self.stop.is_set():
                reason = str(check.CheckCancelled())
                self.set_state('unavailable', reason)
                raise ValueError(reason)
            # Reuse the result we were waiting for, even if it was signed out.
            # Only a subsequent draft retries an uncached authentication failure.
            if generation != self.generation:
                status = self.status()
                if status['state'] == 'ready':
                    return
                raise ValueError(status['reason'])
            self.set_state('checking')
            try:
                self._check()
            except check.OfflineFailure as exc:
                if exc.signed_out:
                    self.set_state('signed out', SIGNED_OUT)
                    raise ValueError(SIGNED_OUT) from None
                self.set_state('paused', str(exc))
                raise ValueError(str(exc)) from None
            except check.PolicyFailure as exc:
                self.set_state('paused', str(exc))
                raise ValueError(str(exc)) from None
            except Exception as exc:
                # No process stderr, prompt contents, paths or schema values escape.
                detail = str(exc) if isinstance(exc, check.DiagnosticUnavailable) else type(exc).__name__
                reason = 'Agent check unavailable: ' + detail
                self.set_state('unavailable', reason)
                raise ValueError(reason) from None
            finally:
                with self.status_lock:
                    self.generation += 1

    def _check(self):
        settings = self.settings
        check.require_codex(settings)
        settings.codex_home.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='expense-agent-gate-') as folder:
            work = Path(folder)
            version = check.invoke([settings.codex, '--version'], work, settings.timeout,
                                   environment=codex_environment(settings), stop=self.stop).strip()
            values = fingerprint(settings, version)
            key = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
            cached = self.read_cache()
            if cached.get('key') == key and cached.get('state') in ('ready', 'paused'):
                if cached['state'] == 'ready' and not check.signed_in(settings, work, stop=self.stop):
                    raise check.OfflineFailure([], signed_out=True)
                self.set_state(cached['state'], cached.get('reason'))
                if cached['state'] == 'paused':
                    raise check.PolicyFailure(cached.get('reason') or 'Agent check paused')
                return
            changed = [name for name in values if cached.get('inputs', {}).get(name) != values[name]]
            ledger, old, account = check.fixture(work)
            fixture_settings = replace(settings, ledger=ledger, old_ledger=old)
            try:
                check.check_offline(fixture_settings, work, stop=self.stop)
                try:
                    check.check_live(fixture_settings, work, account, stop=self.stop)
                except check.CheckCancelled:
                    raise
                except Exception:
                    # Expired login during the probe is recoverable on the next
                    # draft, just like signing in after an empty-home check.
                    if not check.signed_in(settings, work, stop=self.stop):
                        raise check.OfflineFailure([], signed_out=True) from None
                    raise
            except check.OfflineFailure as exc:
                if exc.signed_out:
                    raise
                self.pause(key, values, changed, str(exc))
            except check.PolicyFailure as exc:
                self.pause(key, values, changed, str(exc))
            self.save_cache({'key': key, 'inputs': values, 'state': 'ready', 'reason': None})
            self.set_state('ready')

    def pause(self, key, values, changed, detail):
        reason = 'Agent paused; changed: ' + ', '.join(changed or ['isolation check']) + '. ' + detail
        self.save_cache({'key': key, 'inputs': values, 'state': 'paused', 'reason': reason})
        raise check.PolicyFailure(reason)


@lru_cache(maxsize=32)
def get_gate(settings):
    return AgentGate(settings)
