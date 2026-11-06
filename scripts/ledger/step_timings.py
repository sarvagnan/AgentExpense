"""Step durations of one save, for the server log only; never written into a ledger.

The server names a temporary file in EXPENSES_STEP_TIMINGS when it runs `make commit`.
Each verification step in that process tree appends one JSON line, and the server
adds them to the save's timing line. Without the variable nothing is recorded.

As a wrapper: `python3 step_timings.py STEP -- COMMAND [ARG ...]` runs COMMAND and
records its wall time as STEP, exiting with its status.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time

ENV = 'EXPENSES_STEP_TIMINGS'


def record(step: str, seconds: float) -> None:
    path = os.environ.get(ENV)
    if path:
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps({'step': step, 'seconds': round(seconds, 6)}) + '\n')


@contextmanager
def timed(step: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        record(step, time.perf_counter() - start)


def read(path) -> dict:
    """Seconds per step, summed over repeated steps, in first-seen order."""
    totals = {}
    path = Path(path)
    for line in path.read_text(encoding='utf-8').splitlines() if path.exists() else []:
        try:
            item = json.loads(line)
            totals[item['step']] = totals.get(item['step'], 0.0) + float(item['seconds'])
        except (ValueError, KeyError, TypeError):
            continue
    return totals


if __name__ == '__main__':
    import subprocess
    import sys
    if len(sys.argv) < 4 or sys.argv[2] != '--':
        sys.exit('usage: step_timings.py STEP -- COMMAND [ARG ...]')
    with timed(sys.argv[1]):
        status = subprocess.run(sys.argv[3:]).returncode
    sys.exit(status)
