"""Structured server save timings, never ledger data.

The shared in-process verifier reports journal/store/schema/strict/jj steps through
step_timings.observer. `committed` pins the real commit boundary; operation total
ends at the saved response. Dashboard builds log separate data-timing lines.
The bench joins those builds outside its response measurement. Terminal callers
can still use EXPENSES_STEP_TIMINGS without importing the server.
"""
from __future__ import annotations

from contextlib import contextmanager
import contextvars
from datetime import datetime
import json
import logging
import os
import subprocess
import sys
import time

from settings import APPLICATION

sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
import ledger_lock as _ledger_lock  # noqa: E402
import step_timings  # noqa: E402

LOG = logging.getLogger('expenses.timing')
STEPS = ('pickup', 'agent', 'prepare', 'lock_wait', 'snapshot', 'commit', 'verify', 'journal', 'store_check', 'schema',
         'strict_check', 'hledger', 'commit_jj', 'jj', 'data_snapshot', 'index', 'data')
_current = contextvars.ContextVar('expenses_save_timing', default=None)


class Timing:
    def __init__(self, name):
        self.name, self.steps, self.active, self.start = name, {}, set(), time.perf_counter()

    def add(self, step, seconds):
        self.steps[step] = self.steps.get(step, 0.0) + seconds

    def line(self, outcome):
        steps = {name: round(self.steps[name], 4) for name in (*STEPS, *sorted(set(self.steps) - set(STEPS))) if name in self.steps}
        return dict(operation=self.name, outcome=outcome, total=round(time.perf_counter() - self.start, 4), steps=steps,
                    **({'committed': round(self.committed, 4)} if hasattr(self, 'committed') else {}))


def configure_log():
    """Send timing lines to stderr (the service log) unless a handler exists already.

    EXPENSES_TIMING_LOG=0 keeps them out of stderr; the logger still runs, so a
    caller can attach its own handler (the offline tests and `make bench-save`).
    """
    LOG.setLevel(logging.INFO)
    LOG.propagate = False
    if os.environ.get('EXPENSES_TIMING_LOG', '1') == '0' or any(getattr(h, 'expenses_timing', False) for h in LOG.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter('%(message)s'))
    handler.expenses_timing = True
    LOG.addHandler(handler)


def _lock_wait(seconds):
    record('lock_wait', seconds)


_ledger_lock.wait_observer = _lock_wait


@contextmanager
def operation(name, *, queued_at=None):
    """Time one save; nested operations belong to the outer one."""
    if _current.get() is not None:
        yield _current.get()
        return
    timing = Timing(name)
    if queued_at:
        try:
            timing.add('pickup', max(time.time() - datetime.fromisoformat(queued_at).timestamp(), 0.0))
        except (TypeError, ValueError):
            pass
    token = _current.set(timing)
    outcome = 'ok'
    try:
        yield timing
    except BaseException:
        outcome = 'error'
        raise
    finally:
        _current.reset(token)
        if 'commit' in timing.steps:
            payload = timing.line(outcome)
            LOG.info('save-timing %s', json.dumps(payload, separators=(', ', ': ')), extra={'save_timing': payload})


def rename(name):
    timing = _current.get()
    if timing is not None:
        timing.name = name


def record(step, seconds):
    timing = _current.get()
    if timing is not None:
        timing.add(step, seconds)


@contextmanager
def step(name):
    """Add this block's wall time to `name`; an inner block of the same name adds nothing."""
    timing = _current.get()
    if timing is None or name in timing.active:
        yield
        return
    timing.active.add(name)
    start = time.perf_counter()
    try:
        yield
    finally:
        timing.active.discard(name)
        timing.add(name, time.perf_counter() - start)


def make_commit(application, env, lock_fd):
    """Call the shared locked verifier and commit in this long-lived process.

    Request the independent hledger check after success; never wait for it.
    """
    completed = _make_commit(application, env, lock_fd)
    if completed.returncode == 0 and env.get('LEDGER_DIR'):
        import ledger_check
        ledger_check.committed(env['LEDGER_DIR'])
    return completed


def _make_commit(application, env, lock_fd):
    from pathlib import Path
    import commit as ledger_commit
    from save_service import get
    from data_refresh import get as data_job
    root = Path(env['LEDGER_DIR']).resolve()
    service, data = get(root), data_job(root)
    output = []
    token = step_timings.observer.set(record)
    started, boundary, candidate = time.perf_counter(), None, None
    def accepted(identity, documents):
        nonlocal boundary, candidate
        at = time.perf_counter()
        boundary = at
        if timing := _current.get(): timing.committed = at - timing.start
        if service:
            with step('index'): service.accepted(identity, documents)
        if data: candidate = data.committed(identity, at=at)
    def prepare_data():
        if data:
            try:
                with step('data_snapshot'): data.prepare()
            except Exception as exc: data.failed_prepare(str(exc))
    try:
        identity = ledger_commit.commit(root, env['EXPENSES_COMMIT_MESSAGE'], lock_fd=lock_fd,
            rollback_operation=service.rollback_operation if service else None,
            index=service.index if service else None, canonical_cache=service.canonical if service else None,
            emit=lambda *args: output.append(' '.join(map(str, args))),
            hledger_mode=env.get('EXPENSES_HLEDGER_CHECK'),
            before_commit=prepare_data if data else None,
            after_commit=accepted)
        completed = subprocess.CompletedProcess(['in-process', 'commit'], 0, identity+'\n', '')
        completed.data_candidate = candidate
        return completed
    except Exception as exc:
        return subprocess.CompletedProcess(['in-process', 'commit'], 1, '\n'.join(output), str(exc))
    finally:
        record('commit', (boundary if boundary is not None else time.perf_counter()) - started)
        step_timings.observer.reset(token)
