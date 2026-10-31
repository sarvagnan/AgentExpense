"""Verify and commit only the selected ledger, holding its shared file lock."""
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_paths import APPLICATION_ROOT, ledger_root
from ledger_lock import ledger_lock


def jj(root, *args):
    # Verified evidence must enter the commit even above jj's default file limit.
    return subprocess.check_output(['jj', '--config', 'snapshot.max-new-file-size=0', '-R', str(root), *args], text=True).strip()


def commit(root, message):
    if not message.strip():
        raise ValueError('usage: make commit m="what changed, and on what evidence"')
    with ledger_lock(root):
        subprocess.run(['make', 'verify'], cwd=APPLICATION_ROOT, check=True)
        jj(root, 'status')  # snapshot before recording the operation to restore
        operation = jj(root, 'op', 'log', '--limit', '1', '--no-graph', '-T', 'id')
        try:
            jj(root, 'commit', '-m', message)
            jj(root, 'bookmark', 'set', 'main', '-r', '@-')
            return jj(root, 'log', '-r', '@-', '--no-graph', '-T', 'commit_id')
        except BaseException:
            # The caller can then restore files without leaving a half-made commit.
            jj(root, 'op', 'restore', operation)
            raise


if __name__ == '__main__':
    print(commit(ledger_root(), os.getenv('EXPENSES_COMMIT_MESSAGE', '')))
