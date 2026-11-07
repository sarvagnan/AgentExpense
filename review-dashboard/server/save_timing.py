"""One structured timing line per save, in the server log only (never the ledger).

The worker, the settings writer and statement Apply open an `operation`. Steps
inside it add their wall time; `make commit` hands its own steps back through a
temporary file named in EXPENSES_STEP_TIMINGS (`scripts/ledger/step_timings.py`).
When the operation ran a commit, successful or not, the logger `expenses.timing`
writes one line:

    save-timing {"operation": "edit", "outcome": "ok", "total": 2.91, "steps": {...}}

Steps, in seconds (a step absent from the line did not run):
- `pickup`: from the Queue position (`queued_at`) to the worker starting the item;
- `agent`: the agent's reading (Add only);
- `prepare`: the proposal check, including the JSON strict check in scratch;
- `lock_wait`: waiting for the ledger lock;
- `commit`: all of `make commit`, which contains `verify` (all of `make
  verify-data`, including its `uv run` starts), and within that `journal` (journal
  rebuild), `store_check` (with `schema`, the store's load and schema validation,
  and `strict_check`, the JSON strict check with every recurring rule) and
  `hledger` (the strict check of the whole journal); `commit_jj` is the jj calls
  inside `make commit` (snapshot, commit, bookmark);
- `jj`: the server's own jj calls (clean check, rollback snapshot, commit ID);
- `data`: the dashboard data rebuild (`npm run data`).

`total` runs from the worker starting the operation (or the request, for direct
writes) to its end, including the data rebuild; `pickup` lies before it.
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
import tempfile
import time

from settings import APPLICATION

sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
import ledger_lock as _ledger_lock  # noqa: E402
import step_timings  # noqa: E402

LOG = logging.getLogger('expenses.timing')
STEPS = ('pickup', 'agent', 'prepare', 'lock_wait', 'commit', 'verify', 'journal', 'store_check', 'schema',
         'strict_check', 'hledger', 'commit_jj', 'jj', 'data')
_current = contextvars.ContextVar('expenses_save_timing', default=None)


class Timing:
    def __init__(self, name):
        self.name, self.steps, self.active, self.start = name, {}, set(), time.perf_counter()

    def add(self, step, seconds):
        self.steps[step] = self.steps.get(step, 0.0) + seconds

    def line(self, outcome):
        steps = {name: round(self.steps[name], 4) for name in (*STEPS, *sorted(set(self.steps) - set(STEPS))) if name in self.steps}
        return dict(operation=self.name, outcome=outcome, total=round(time.perf_counter() - self.start, 4), steps=steps)


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
    """Run `make commit` exactly as before, timing it with the steps it reports."""
    timing = _current.get()
    if timing is None:
        return subprocess.run(['make', 'commit'], cwd=application, env=env, pass_fds=(lock_fd,), capture_output=True, text=True)
    handle, path = tempfile.mkstemp(prefix='expense-steps-', suffix='.jsonl')
    os.close(handle)
    try:
        with step('commit'):
            completed = subprocess.run(['make', 'commit'], cwd=application, env={**env, step_timings.ENV: path},
                                       pass_fds=(lock_fd,), capture_output=True, text=True)
        for name, seconds in step_timings.read(path).items():
            timing.add(name, seconds)
        return completed
    finally:
        os.unlink(path)
