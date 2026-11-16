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
        result = subprocess.check_output(['jj', '--config', 'snapshot.max-new-file-size=0', '-R', str(root), *args],
                                         text=True, stderr=subprocess.PIPE).strip()
    return result


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
                data = render(documents, root)
                temporary.write_text(data, encoding='utf-8')
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        mode = hledger_mode or os.environ.get('EXPENSES_HLEDGER_CHECK') or 'background'
        if mode != 'background':
            with timed('hledger'):
                if second_check(root, find_hledger()):
                    raise ValueError('hledger second check failed')
    return documents


# The result lookup is also the pre-publication path check: still three jj
# processes on a declared save (commit, this lookup, bookmark).
COMMITTED = ('json({"commit" => commit_id, "change_id" => change_id, "message" => description, '
             '"paths" => self.diff().files().map(|f| json({"path" => f.path(), "status" => f.status(), '
             '"source" => f.source().path()}))})')


def _normal_paths(rows):
    import json
    rows = [json.loads(row) if isinstance(row, str) else row for row in rows]
    return [{k: v for k, v in row.items() if k != 'source' or row['status'] in ('renamed', 'copied')}
            for row in rows]


def rollback_commit(root, operation):
    """Undo only this writer's unpublished commit, keeping the current tree."""
    from audit_changes import identity, read_jj
    origin = read_jj(root, '--at-op=' + operation, 'log', '-r', '@', '--no-graph', '-T', 'change_id').strip()
    if identity(root, 'main')['change_id'] == origin:
        return False  # On main already; the caller owns the success-receipt boundary.
    parent = identity(root, '@-')
    if parent['change_id'] == origin:
        jj(root, 'squash', '--from', parent['commit'], '--into', '@', '--use-destination-message')
    return True


def commit(root, message, *, lock_fd=None, rollback_operation=None, index=None, canonical_cache=None,
           emit=print, hledger_mode=None, before_commit=None, after_commit=None,
           actor=None, carries_audit=False, audit_receipts=(), snapshot=None, audit_before=None):
    import json
    from audit import append, audit_path as log_path, reset_appends, validate_appends
    from audit_changes import require_repository, identity, paths, touches, covered, audit_path, change_line, read_jj
    from ledger_manifest import read_manifest
    if not isinstance(message, str) or not message.strip():
        raise ValueError('usage: make commit m="what changed, and on what evidence" by=user|agent')
    root = Path(root).resolve()
    require_repository(root)
    if actor is not None and actor not in ('user', 'agent'):
        raise ValueError('Use make commit m="what changed" by=user or by=agent.')
    if lock_fd is None:
        with ledger_lock(root) as fd:
            return commit(root, message, lock_fd=fd, rollback_operation=rollback_operation, index=index,
                          canonical_cache=canonical_cache, emit=emit, hledger_mode=hledger_mode,
                          before_commit=before_commit, after_commit=after_commit,
                          actor=actor, carries_audit=carries_audit, audit_receipts=audit_receipts,
                          snapshot=snapshot, audit_before=audit_before)
    read_manifest(root)
    fast = carries_audit and validate_appends(root, rollback_operation, audit_receipts)
    additions, expected = None, [] if snapshot is not None else None
    operation = rollback_operation
    if not fast:
        jj(root, 'status')
        operation = operation or jj(root, 'op', 'log', '--limit', '1', '--no-graph', '-T', 'id')
        info = identity(root, '@')
        snapshot = info
        if len(info['parents']) != 1 or info['parents'][0] == '0' * 40:
            raise ValueError('Use make new-ledger for the baseline; merges and the root commit are not hand changes.')
        changed = paths(root, info['commit'])
        if read_jj(root, 'file', 'list', '-r', info['commit'], 'expense_ledger.ledger').strip():
            raise ValueError('Generated expense_ledger.ledger must be ignored; untrack it before saving (see docs/SAVES.md)')
        if carries_audit:
            if any(touches(row, covered) for row in changed):
                raise ValueError('A declared commit changes ledger data without adding a new audit line.')
        else:
            if any(touches(row, audit_path) for row in changed):
                from audit_changes import audit_restore_hint
                names = sorted({name for row in changed for name in (row['path'], row.get('source')) if name and audit_path(name)})
                if (root / 'expense_ledger.ledger.tmp').exists(): names.append('expense_ledger.ledger.tmp')
                raise ValueError('A hand commit must not touch the audit log.' + audit_restore_hint(root, names))
            if any(touches(row, covered) for row in changed):
                additions = change_line(root, info['commit'], actor, message=message)
            expected = [row for row in changed if not touches(row, audit_path)]
    else:
        # Receipts come from validated append(), bound to the transaction's own
        # rollback operation. No snapshot, identity, diff or audit blob reads.
        for receipt in audit_receipts:
            line = json.loads(receipt.data)
            if line['kind'] == 'change' and line.get('commit') is None:
                expected = line['paths']
    audit_before = dict(audit_before or {})
    if additions:
        path = log_path(root, additions)
        audit_before[path] = path.read_bytes() if path.exists() else None
    prepared, result = None, None
    journal = root / 'expense_ledger.ledger'
    journal_before = journal.read_bytes() if journal.exists() else None
    try:
        receipts_for_commit = audit_receipts
        if additions:
            reset_appends(root, operation)
            receipts_for_commit = (append(root, additions),)
        documents = verify(root, index=index, canonical_cache=canonical_cache, emit=emit, hledger_mode=hledger_mode)
        if before_commit:
            before_commit()
        if receipts_for_commit:
            validate_appends(root, operation, receipts_for_commit)
        if snapshot is not None:
            from audit_changes import read_jj
            jj(root, 'status')
            current = identity(root, '@')
            expected_audit = {path: before or b'' for path, before in audit_before.items()}
            for receipt in receipts_for_commit:
                expected_audit[receipt.path] += receipt.data
            changed = read_jj(root, 'diff', '--from', snapshot['commit'], '--to', current['commit'], '--name-only').splitlines()
            unexpected = [name for name in changed if name not in {str(path.relative_to(root)) for path in expected_audit}]
            unexpected += [str(path.relative_to(root)) for path, data in expected_audit.items() if path.read_bytes() != data]
            actual = [row for row in paths(root, current['commit']) if not touches(row, audit_path)]
            if sorted(actual, key=lambda row: row['path']) != sorted(expected, key=lambda row: row['path']):
                unexpected += [row['path'] for row in actual if row not in expected] + [row['path'] for row in expected if row not in actual]
            if unexpected:
                raise ValueError('Working-copy paths changed during verification: ' + ', '.join(sorted(set(unexpected))) + '; nothing was committed; files were kept')
            if any(current[key] != snapshot[key] for key in ('change_id', 'parents')):
                raise ValueError('Working revision changed during verification; nothing was committed; files were kept')
            prepared = current['commit']
        jj(root, 'commit', '-m', message)
        result = json.loads(jj(root, 'log', '-r', '@-', '--no-graph', '-T', COMMITTED))
        if prepared:
            from audit_changes import read_jj
            drift = read_jj(root, 'diff', '--from', prepared, '--to', result['commit'], '--name-only').strip()
            if drift:
                raise ValueError('Commit candidate changed before publication: ' + drift + '; main was not moved; files were kept')
        committed_paths = _normal_paths(result['paths'])
        audit_rows = [row for row in committed_paths if touches(row, audit_path)]
        expected_logs = {str(receipt.path.relative_to(root)) for receipt in receipts_for_commit}
        if ({row['path'] for row in audit_rows} != expected_logs or
                any(row['status'] not in ('added', 'modified') for row in audit_rows)):
            raise ValueError('Audit paths changed during verification; rolled back before moving main')
        actual = [row for row in committed_paths if not touches(row, audit_path)]
        if expected is not None and sorted(actual, key=lambda row: row['path']) != sorted(expected, key=lambda row: row['path']):
            if any(row['path'] == 'expense_ledger.ledger' for row in actual):
                raise ValueError('Generated expense_ledger.ledger must be ignored; untrack it before saving (see docs/SAVES.md)')
            unexpected = sorted(row['path'] for row in actual if row not in expected)
            missing = sorted(row['path'] for row in expected if row not in actual)
            raise ValueError('Commit paths changed during verification; unexpected or changed: ' +
                             (', '.join(unexpected) or 'none') + '; missing or changed: ' +
                             (', '.join(missing) or 'none') + '; rolled back before moving main')
        jj(root, 'bookmark', 'set', 'main', '-r', result['commit'])
        if after_commit:
            after_commit(result['commit'], documents)
        return result['commit']
    except BaseException:
        if not rollback_commit(root, operation):
            # Published with its line; callback failure only delays cached/page data.
            if result is not None: return result['commit']
            raise
        for path, before in audit_before.items():
            if before is None: path.unlink(missing_ok=True)
            else: path.write_bytes(before)
        if journal_before is None: journal.unlink(missing_ok=True)
        else: journal.write_bytes(journal_before)
        raise


if __name__ == '__main__':
    try:
        print(commit(ledger_root(), os.getenv('EXPENSES_COMMIT_MESSAGE', ''),
                     actor=os.getenv('EXPENSES_COMMIT_ACTOR') or None))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
