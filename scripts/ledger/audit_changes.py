"""Read a ledger's own jj diffs without snapshotting; build hand-change audit lines."""
from __future__ import annotations

from contextvars import ContextVar
import json
import os
import select
import time
from pathlib import Path
import re
import subprocess

from store import _unique_object


class HistoryUnavailable(ValueError):
    pass


read_deadline = ContextVar('audit_read_deadline', default=float('inf'))


def require_repository(root):
    root = Path(root).resolve()
    if not (root / '.jj').is_dir() or (root / '.jj').is_symlink():
        raise HistoryUnavailable(f'No own jj repository at {root}; initialize the ledger separately (an enclosing repository is never used).')


def read_jj(root, *args, limit=None):
    require_repository(root)
    try:
        command = ['jj', '--ignore-working-copy', '--no-pager', '--color=never', '-R', str(root), *args]
        if limit is not None or read_deadline.get() != float('inf'):
            return bounded_read(command, root, limit if limit is not None else 16 * 1024**2).decode()
        return subprocess.check_output(command, cwd=root, text=True, stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HistoryUnavailable('Could not read the ledger jj history: ' +
                                 (getattr(exc, 'stderr', None) or str(exc)).strip()) from exc


def bounded_read(command, root, limit):
    deadline = min(time.monotonic() + 12, read_deadline.get())
    with subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        output = bytearray()
        try:
            while len(output) <= limit and select.select([process.stdout], [], [], max(0, deadline-time.monotonic()))[0]:
                block = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(output)))
                if not block:
                    if process.wait(timeout=max(0, deadline-time.monotonic())) == 0:
                        return bytes(output)
                    raise HistoryUnavailable('history_unavailable')
                output.extend(block)
            raise HistoryUnavailable('history_limit')
        except subprocess.TimeoutExpired:
            raise HistoryUnavailable('history_limit') from None
        finally:
            if process.poll() is None:
                process.kill()


IDENTITY = ('json({"commit" => commit_id, "change_id" => change_id, "message" => description, '
            '"parents" => parents.map(|p| p.commit_id())}) ++ "\\n"')
PATHS = ('json({"path" => path, "status" => status, "source" => source.path()}) ++ "\\n"')


def identities(root, revision):
    return [json.loads(line) for line in read_jj(root, 'log', '-r', revision, '--no-graph', '-T', IDENTITY).splitlines()]


def identity(root, revision):
    rows = identities(root, revision)
    if len(rows) != 1:
        raise ValueError('Select exactly one commit.')
    return rows[0]


def paths(root, revision, *filesets):
    rows = [json.loads(line) for line in read_jj(root, 'diff', '-r', revision, '-T', PATHS, *filesets).splitlines()]
    return [{key: value for key, value in row.items() if key != 'source' or row['status'] in ('renamed', 'copied')}
            for row in rows]


def audit_path(path):
    return path.startswith('data/additions/')


def covered(path):
    return (path.startswith('Bills/') or path.startswith('data/') and
            not path.startswith(('data/additions/', 'data/prices/')))


def touches(row, predicate):
    return predicate(row['path']) or predicate(row.get('source', ''))


def file_text(root, revision, path):
    return read_jj(root, 'file', 'show', '-r', revision, path)


def own_lines(root, info, changed=None):
    """Inspect historical backfill candidates; declared saves use append receipts."""
    changed = paths(root, info['commit'], 'glob:data/additions/**') if changed is None else changed
    result = []
    for row in changed:
        if not touches(row, audit_path):
            continue
        if row['status'] not in ('added', 'modified') or not audit_path(row['path']):
            raise ValueError('An audit log may only be appended to.')
        before = '' if row['status'] == 'added' else file_text(root, info['parents'][0], row['path'])
        after = file_text(root, info['commit'], row['path'])
        if not after.startswith(before) or before and not before.endswith('\n'):
            raise ValueError('An audit log may only be appended to.')
        result.extend(json.loads(line, object_pairs_hook=_unique_object) for line in after[len(before):].splitlines())
    return result


def audit_restore_hint(root, names=None):
    import shlex
    if names is None and not (root / '.jj').is_dir(): return ''
    names = names if names is not None else read_jj(root, 'diff', '--name-only', 'glob:data/additions/**').splitlines()
    if not names: return ''
    return (' Remove the leftover audit edits with: ' +
            shlex.join(['jj', '-R', str(root), 'restore', '--from', '@-', '--', *names]) +
            '. This leaves the other edits in place; then retry.')


def snapshots(root, revision, names):
    result = {}
    for name in sorted(names):
        items = json.loads(file_text(root, revision, name), object_pairs_hook=_unique_object)
        if not isinstance(items, list):
            raise ValueError(f'Expected a list in {name}')
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or item['id'] in result:
                raise ValueError(f'Invalid or duplicate snapshot ID in {name}')
            result[item['id']] = item
    return result


def item_changes(before, after, actions):
    result = []
    for key in sorted(before.keys() | after.keys()):
        if key not in before:
            result.append(dict(id=key, action=actions[0], entry=after[key]))
        elif key not in after:
            result.append(dict(id=key, action=actions[2], entry=before[key]))
        elif before[key] != after[key]:
            fields = {field: dict(before=before[key].get(field), after=after[key].get(field))
                      for field in sorted(before[key].keys() | after[key].keys())
                      if before[key].get(field) != after[key].get(field) or
                      (field in before[key]) != (field in after[key])}
            result.append(dict(id=key, action=actions[1], changed_fields=fields))
    return result


def change_line(root, revision, actor, *, message=None, recorded=False, note=None):
    from audit import event
    if actor not in ('user', 'agent'):
        raise ValueError('Use make commit m="what changed" by=user or by=agent.')
    info = identity(root, revision)
    if len(info['parents']) != 1 or info['parents'][0] == '0' * 40:
        raise ValueError('Merges, the root commit and the ledger baseline are not recorded.')
    changed = paths(root, info['commit'])
    if not any(touches(row, covered) for row in changed):
        raise ValueError('The commit changes no covered ledger data.')
    if any(touches(row, audit_path) for row in changed):
        raise ValueError('The commit touches the audit log; it already carries its own line or is not a hand change.')
    groups = []
    for predicate, actions in ((lambda p: bool(re.fullmatch(r'data/expenses/[^/]+\.json', p)), ('added', 'changed', 'removed')),
                               (lambda p: p == 'data/forecast.json', ('made', 'edited', 'removed'))):
        old, new = set(), set()
        for row in changed:
            source = row.get('source', row['path'])
            if row['status'] not in ('added', 'copied') and predicate(source):
                old.add(source)
            if row['status'] != 'removed' and predicate(row['path']):
                new.add(row['path'])
        groups.append(item_changes(snapshots(root, info['parents'][0], old), snapshots(root, info['commit'], new), actions))
    return event('change', actor=actor, message=info['message'].rstrip('\n') if message is None else message,
                 change_id=info['change_id'], commit=info['commit'] if recorded else None,
                 entries=groups[0], rules=groups[1], paths=[row for row in changed if not touches(row, audit_path)],
                 **({'note': note} if note is not None else {}))


def main_history(root):
    # jj returns reverse topological order; reverse it to append ancestors first.
    return list(reversed(identities(root, '::main')))


# One process reads committed path metadata and audit additions; no expense blobs.
HISTORY = ('commit_id ++ " " ++ change_id ++ " " ++ json({"commit" => commit_id, '
           '"change_id" => change_id, "message" => description, '
           '"parents" => parents.map(|p| p.commit_id()), '
           '"paths" => self.diff().files().map(|f| json({"path" => f.path(), "status" => f.status(), '
           '"source" => f.source().path()})), '
           '"audit_patch" => stringify(self.diff("glob:data/additions/**").git(0))}) ++ "\\n"')


def batched_history(root, revision='main'):
    rows = []
    for raw in read_jj(root, 'log', '-r', '::' + revision, '--no-graph', '-T', HISTORY).splitlines():
        prefix = re.match(r'^([0-9a-f]{40}) ([k-z]{32}) (.*)$', raw)
        if not prefix:
            if rows and rows[-1].get('reason'):
                rows[-1]['reason'] += ' ' + raw
                continue
            raise HistoryUnavailable('Cannot identify a historical commit in jj output; preserve the history and inspect main')
        commit, change, payload = prefix.groups()
        try:
            info = json.loads(payload)
            from commit import _normal_paths
            info['paths'] = _normal_paths(info['paths'])
        except (ValueError, TypeError, KeyError) as exc:
            info = dict(commit=commit, change_id=change, message='', reason='Cannot describe historical commit: ' + str(exc))
        rows.append(info)
    return list(reversed(rows))


def _ancestors(history, revision):
    parents = {info['commit']: info.get('parents', []) for info in history}
    found, waiting = set(), [revision]
    while waiting:
        key = waiting.pop()
        if key not in found:
            found.add(key)
            waiting.extend(parents.get(key, []))
    return found


def _audit_additions(patch):
    return [json.loads(line[1:], object_pairs_hook=_unique_object) for line in patch.splitlines()
            if line.startswith('+') and not line.startswith('+++ ')]


def unrecorded(root, lines, *, snapshot=None, revision='main'):
    from ledger_manifest import read_manifest
    start = read_manifest(snapshot or root).get('auditStart')
    if start is None:
        raise HistoryUnavailable('No auditStart in ledger.json; run make upgrade-ledger to start checking hand changes.')
    history = batched_history(root, revision) if revision != 'main' else batched_history(root)
    after = start['after']
    if after is not None and after not in {row['commit'] for row in history}:
        raise HistoryUnavailable('auditStart.after is not on main; inspect the boundary and ledger history.')
    excluded = _ancestors(history, after) if after else set()
    changes = {line['change_id'] for line in lines if line['kind'] == 'change'}
    result = []
    for info in history:
        if info['commit'] in excluded:
            continue
        if info.get('reason'):
            result.append(info)
            continue
        if len(info['parents']) != 1 or info['parents'][0] == '0' * 40:
            continue
        try:
            if (any(touches(row, covered) for row in info['paths']) and
                    info['change_id'] not in changes and not _audit_additions(info['audit_patch'])):
                result.append(info)
        except (ValueError, KeyError, TypeError) as exc:
            result.append({**info, 'reason':'Cannot read historical audit additions: ' + str(exc)})
    return result


def document_change(root, documents, journal, message, time):
    """Settings already own these before-images and writes; no jj diff is needed."""
    import base64
    from audit import event
    changed, before, after = [], {}, {}
    for path in sorted(documents):
        rel = str(path.relative_to(root))
        saved = journal['files'][rel]
        old = base64.b64decode(saved['bytes']) if saved else None
        new = path.read_bytes()
        if old == new:
            continue
        changed.append(dict(path=rel, status='added' if old is None else 'modified'))
        before[rel] = json.loads(old) if old is not None else []
        after[rel] = json.loads(new)
    groups = []
    for predicate, actions in ((lambda p:p.startswith('data/expenses/'), ('added','changed','removed')),
                               (lambda p:p=='data/forecast.json', ('made','edited','removed'))):
        old = {item['id']:item for name, items in before.items() if predicate(name) for item in items}
        new = {item['id']:item for name, items in after.items() if predicate(name) for item in items}
        groups.append(item_changes(old,new,actions))
    return event('change', actor='user', message=message, change_id=journal['change_id'], commit=None,
                 entries=groups[0], rules=groups[1], paths=changed, note='Settings via dashboard') | {'time':time}
