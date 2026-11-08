"""Cross-process ledger lock, including a safe handoff to a child make commit.

The inherited descriptor is checked against .lock before it can bypass acquisition.
Never unlink a lock file: waiters must all lock the same inode.

Server lock order is ledger lock → drafts.mutex → statements.mutex. Acquire only
the locks needed, in that order; release a draft/statement mutex before beginning
a ledger operation. The checker's condition must never wait for either mutex.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time

LOCK_FD_ENV = 'EXPENSES_LEDGER_LOCK_FD'
# An optional callable that receives each blocking acquisition's wait in seconds.
# The server's step timings set it; it never changes how the lock behaves.
wait_observer = None


@contextmanager
def ledger_lock(root: Path, *, blocking=True):
    path = root / '.lock'
    inherited = os.environ.get(LOCK_FD_ENV)
    valid = False
    if inherited:
        try:
            fd = int(inherited)
            held, expected = os.fstat(fd), path.stat()
            valid = (held.st_dev, held.st_ino) == (expected.st_dev, expected.st_ino)
        except (OSError, ValueError):
            pass
    if valid:
        yield fd
        return
    with path.open('a+b') as handle:
        start = time.perf_counter()
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        if blocking and wait_observer is not None:
            wait_observer(time.perf_counter() - start)
        try:
            yield handle.fileno()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


if __name__ == '__main__':
    import subprocess
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from project_paths import ledger_root
    command = sys.argv[1:]
    if command[:1] == ['--']:
        command = command[1:]
    if not command:
        sys.exit('usage: ledger_lock.py -- COMMAND [ARG ...]')
    with ledger_lock(ledger_root()) as lock_fd:
        result = subprocess.run(command, env={**os.environ, LOCK_FD_ENV: str(lock_fd)}, pass_fds=(lock_fd,))
    sys.exit(result.returncode)
