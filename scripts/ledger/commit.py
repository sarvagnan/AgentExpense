"""Shared in-process verification and jj commit, also used by terminal make commit."""
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_paths import ledger_root
from ledger_lock import ledger_lock
from step_timings import timed


def jj(root, *args):
    with timed('commit_jj'):
        return subprocess.check_output(['jj', '--config', 'snapshot.max-new-file-size=0', '-R', str(root), *args],
                                       text=True, stderr=subprocess.PIPE).strip()


def verify(root, *, index=None, canonical_cache=None, emit=print, hledger_mode=None):
    """One implementation for CLI and long-lived server; no make/uv/Python starts."""
    from check_store import _check
    from store import load_parsed
    from generate_ledger import render
    from second_check import verify as second_check, find_hledger
    with timed('verify'):
        with timed('store_check'):
            with timed('schema'):
                documents, parsed = load_parsed(root)
            _check(root, loaded=(documents, parsed), strict_checker=index.check if index else None,
                   membership_checker=index.check_membership if index else None,
                   canonical_cache=canonical_cache, emit=emit)
        with timed('journal'):
            path = root / 'expense_ledger.ledger'
            temporary = path.with_suffix('.ledger.tmp')
            try:
                temporary.write_text(render(documents, root), encoding='utf-8')
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        mode = hledger_mode or os.environ.get('EXPENSES_HLEDGER_CHECK') or 'background'
        if mode != 'background':
            with timed('hledger'):
                if second_check(root, find_hledger()):
                    raise ValueError('hledger second check failed')
    return documents


def commit(root, message, *, lock_fd=None, rollback_operation=None, index=None, canonical_cache=None,
           emit=print, hledger_mode=None, before_commit=None, after_commit=None):
    if not message.strip():
        raise ValueError('usage: make commit m="what changed, and on what evidence"')
    from ledger_manifest import read_manifest
    read_manifest(root)
    if lock_fd is None:
        with ledger_lock(root) as fd:
            return commit(root, message, lock_fd=fd, rollback_operation=rollback_operation, index=index,
                          canonical_cache=canonical_cache, emit=emit, hledger_mode=hledger_mode,
                          before_commit=before_commit, after_commit=after_commit)
    documents = verify(root, index=index, canonical_cache=canonical_cache, emit=emit, hledger_mode=hledger_mode)
    # Server transactions already own a durable operation/file rollback journal.
    # CLI callers must first snapshot their intended working-copy edits, so a
    # failed commit/bookmark restores those edits rather than their old parent.
    operation = rollback_operation
    if operation is None:
        jj(root, 'status')
        operation = jj(root, 'op', 'log', '--limit', '1', '--no-graph', '-T', 'id')
    if before_commit:
        before_commit()
    try:
        jj(root, 'commit', '-m', message)
        jj(root, 'bookmark', 'set', 'main', '-r', '@-')
        identity = jj(root, 'log', '-r', '@-', '--no-graph', '-T', 'commit_id')
        if after_commit:
            after_commit(identity, documents)
        return identity
    except BaseException:
        jj(root, 'op', 'restore', operation)
        raise


if __name__ == '__main__':
    print(commit(ledger_root(), os.getenv('EXPENSES_COMMIT_MESSAGE', '')))
