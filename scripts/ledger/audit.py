"""Append-only operation and hand-change audit records and weekly review commands."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from store import ROOT, read_json, _unique_object
from ledger_lock import ledger_lock
from proposal_consent import validate_consent

SCHEMA = Path(__file__).resolve().parents[2] / 'schema/additions.schema.json'
FORMATS = FormatChecker()


@FORMATS.checks('date-time')
def valid_timestamp(value):
    if not isinstance(value, str):
        return True
    try:
        return bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})', value)) and datetime.fromisoformat(value.replace('z','Z')).tzinfo is not None
    except ValueError:
        return False



def stamp():
    return datetime.now(timezone.utc).isoformat()


def event(kind, **fields):
    return dict(version=1, id=secrets.token_hex(16), kind=kind, time=stamp(), **fields)


class UnknownAuditField(ValueError):
    """An unsupported field whose name and location can be shown during upgrade."""


def _older_audit_view(record):
    """Read old audit facts in the current shape, leaving the original JSONL intact."""
    if not isinstance(record, dict):
        return record
    if 'legacy' in record:
        raise UnknownAuditField("Unexpected audit field 'legacy'; it is reserved for the read view")
    kind = record.get('kind')
    legacy = {}
    if kind in ('draft', 'correction'):
        fields = ('declined_rules', 'rule_events', 'usual') if kind == 'draft' else ('rule_note', 'receipt_details')
        legacy.update((field, record.pop(field)) for field in fields if field in record)
        consent = record.get('account_consent')
        if isinstance(consent, dict) and 'method' not in consent and {'source', 'time'} & consent.keys():
            legacy['account_consent'] = record.pop('account_consent')
        runs = record.get('runs')
        if isinstance(runs, list):
            errors = {str(index): run.pop('error_events') for index, run in enumerate(runs)
                      if isinstance(run, dict) and 'error_events' in run}
            if errors:
                legacy['run_error_events'] = errors
    if 'rule_events' in legacy:
        record.setdefault('rule_changes', [dict(id=row['rule_id'], before=row['before'], after=row['after'])
                                           for row in legacy['rule_events']])
    if kind == 'correction' and isinstance(record.get('changed_fields'), dict):
        legacy['changed_fields'] = record['changed_fields']
        record['changed_fields'] = list(record['changed_fields'])
    receipt = legacy.get('receipt_details')
    if isinstance(receipt, dict) and isinstance(receipt.get('answer'), dict):
        answer = dict(receipt['answer'])
        if 'commodity' in answer and 'currency' not in answer:
            answer['currency'] = answer.pop('commodity')
        record.setdefault('receipt', dict(receipt, answer=answer))
    if kind == 'rule' and 'rule_action' not in record and record.get('action') in ('edited', 'undone', 'made'):
        legacy['action'] = record['rule_action'] = record.pop('action')
        snapshots = {key: record.pop(key) for key in ('before', 'after')}
        if 'rule_changes' in record:
            legacy.update(snapshots)
        else:
            record['rule_changes'] = [dict(id=record['rule_id'], **snapshots)]
    if kind == 'review' and record.get('verdict') == 'confirm':
        record['verdict'] = 'ok'
    if kind == 'review' and record.get('verdict') == 'propose removal':
        # The older app's reviewer proposed removing the added entry: an issue, with the original verdict kept.
        legacy['verdict'] = record['verdict']
        record['verdict'] = 'issue'
    if legacy:
        record['legacy'] = legacy
    return record


def records(root=ROOT):
    from store import _model_memos
    memo = _model_memos.get(root.resolve())
    schema = read_json(SCHEMA)
    schema_key = hashlib.sha256(json.dumps(schema).encode()).digest() if memo else None
    validator = Draft202012Validator(schema, format_checker=FORMATS)
    used = set()
    changes, commits = set(), set()
    result = []
    for path in sorted((root / 'data/additions').glob('*.jsonl')):
        if not re.fullmatch(r'\d{4}\.jsonl', path.name):
            raise ValueError(f'Invalid audit year file: {path.name}')
        for number, line in enumerate(path.read_text().splitlines(), 1):
            try:
                record = _older_audit_view(json.loads(line, object_pairs_hook=_unique_object))
                key = (schema_key, hashlib.sha256(line.encode()).digest()) if memo else None
                if memo is not None:
                    with memo.lock: checked = key in memo.audit_lines
                else: checked = False
                if not checked:
                    validator.validate(record)
                    validate_consent(record)
                    if memo is not None:
                        with memo.lock:
                            if len(memo.audit_lines) > 100000: memo.audit_lines.clear()
                            memo.audit_lines.add(key)
                if record['kind'] == 'change':
                    if record['change_id'] in changes or record['commit'] and record['commit'] in commits:
                        raise ValueError('Duplicate recorded change')
                    changes.add(record['change_id'])
                    if record['commit']: commits.add(record['commit'])
                if record['id'] in used:
                    raise ValueError('Duplicate audit ID ' + record['id'])
                if datetime.fromisoformat(record['time']).year != int(path.stem):
                    raise ValueError('Audit event is in the wrong year')
            except ValidationError as exc:
                if exc.validator == 'additionalProperties':
                    raise UnknownAuditField(f'{path.relative_to(root)}:{number}: {exc.json_path}: {exc.message}') from None
                raise ValueError(f'{path.relative_to(root)}:{number}: {exc}') from exc
            except UnknownAuditField as exc:
                raise UnknownAuditField(f'{path.relative_to(root)}:{number}: {exc}') from None
            except Exception as exc:
                raise ValueError(f'{path.relative_to(root)}:{number}: {exc}') from exc
            used.add(record['id'])
            result.append(record)
    return result


def audit_path(root, record):
    return root / 'data/additions' / (record['time'][:4] + '.jsonl')


def line_bytes(record):
    return (json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(',', ':')) + '\n').encode()


def append(root, record, *, lines=None):
    """Caller holds the lock; optional lines are its current locked audit snapshot."""
    if isinstance(record, dict) and 'legacy' in record:
        raise ValidationError("'legacy' is reserved for the older audit read view")
    Draft202012Validator(read_json(SCHEMA), format_checker=FORMATS).validate(record)
    validate_consent(record)
    current = records(root) if lines is None else lines
    if record['kind'] == 'change' and any(r['kind'] == 'change' and r['id'] != record['id'] and
            (r['change_id'] == record['change_id'] or record['commit'] and r['commit'] == record['commit']) for r in current):
        raise ValueError('Duplicate recorded change')
    existing = next((r for r in current if r['id'] == record['id']), None)
    if existing:
        if existing != record:
            raise ValueError('Audit ID already contains a different record')
        return
    path = audit_path(root, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Audit log must not be a symlink')
    if path.exists() and path.stat().st_size and not path.read_bytes().endswith(b'\n'):
        raise ValueError('Audit log has an incomplete final line')
    data = line_bytes(record)
    offset = path.stat().st_size if path.exists() else 0
    with path.open('ab') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if lines is not None: lines.append(record)
    key = str(Path(root).resolve())
    operation, receipts = _APPENDS.get().get(key, (None, ()))
    receipt = AppendReceipt(Path(root).resolve(), operation, path.resolve(), offset, data, _receipt_stamp(path))
    _APPENDS.set({**_APPENDS.get(), key: (operation, (*receipts, receipt))})
    return receipt


@dataclass(frozen=True)
class AppendReceipt:
    root: Path
    operation: str
    path: Path
    offset: int
    data: bytes
    stamp: tuple


_APPENDS = ContextVar('validated_audit_appends', default={})


def reset_appends(root, operation):
    _APPENDS.set({**_APPENDS.get(), str(Path(root).resolve()): (operation, ())})


def take_appends(root):
    key = str(Path(root).resolve())
    state = _APPENDS.get()
    operation, receipts = state.get(key, (None, ()))
    _APPENDS.set({name: value for name, value in state.items() if name != key})
    return receipts


def _receipt_stamp(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def validate_appends(root, operation, receipts):
    """Validate sealed, in-process appends using only their new byte ranges."""
    if not receipts:
        return False
    root = Path(root).resolve()
    last = {}
    for receipt in receipts:
        if not isinstance(receipt, AppendReceipt) or receipt.root != root or receipt.operation != operation:
            raise ValueError('Audit append receipt belongs to another locked transaction')
        if receipt.path in last and receipt.offset != last[receipt.path].offset + len(last[receipt.path].data):
            raise ValueError('Audit append receipts do not form a contiguous append')
        if receipt.path.is_symlink():
            raise ValueError('Audit log must not be a symlink')
        with receipt.path.open('rb') as handle:
            handle.seek(receipt.offset)
            if handle.read(len(receipt.data)) != receipt.data:
                raise ValueError('Validated audit append changed before commit')
        last[receipt.path] = receipt
    if any(_receipt_stamp(path) != receipt.stamp for path, receipt in last.items()):
        raise ValueError('Audit file changed after its validated append')
    return True


def missing_entries(root, entry_ids, *, lines=None):
    return [(r['id'], identity) for r in (records(root) if lines is None else lines) for identity in
            ([r['entry_id']] if r['kind'] == 'draft' and r.get('entry_id') else r.get('entry_ids', []) if r['kind'] == 'statement' else []) if identity not in entry_ids]


REVIEW_INTERNAL = {'review', 'rule-review', 'weekly-review-run', 'weekly-review-summary',
                   'review-message', 'review-carry-over', 'review-resolution'}


def unreviewed(lines):
    reviewed = {r['line_id'] for r in lines if r['kind'] == 'review'}
    return [r for r in lines if r['kind'] not in REVIEW_INTERNAL and r['id'] not in reviewed]


def ledger_clean(root):
    from audit_changes import require_repository
    require_repository(root)
    # A writer's explicit cleanliness check snapshots; history readers never do.
    return not subprocess.check_output(['jj', '-R', str(root), 'diff', '--summary'], cwd=root, text=True).strip()


def make_commit(root, message, lock_fd, *, snapshot=None, audit_before=None, operation=None):
    from commit import commit
    return commit(root, message, lock_fd=lock_fd, carries_audit=True, snapshot=snapshot,
                  audit_before=audit_before, rollback_operation=operation, audit_receipts=take_appends(root))


def _append_commit(root, additions, message, fd, callback):
    """Restore this append batch in memory on failure; kills leave a clear refusal."""
    from commit import jj
    from audit_changes import identity
    before = {path: path.read_bytes() if path.exists() else None for path in {audit_path(root, line) for line in additions}}
    snapshot, operation = None, None
    if callback is make_commit:
        jj(root, 'status')
        snapshot = identity(root, '@')
        operation = jj(root, 'op', 'log', '--limit', '1', '--no-graph', '-T', 'id')
        reset_appends(root, operation)
    try:
        for line in additions:
            append(root, line)
        result = (callback(root, message, fd, snapshot=snapshot, audit_before=before, operation=operation)
                  if callback is make_commit else callback(root, message, fd))
        return result, [line['id'] for line in additions]
    except BaseException:
        for path, data in before.items():
            if data is None: path.unlink(missing_ok=True)
            else: path.write_bytes(data)
        raise


def record(root, changes, *, commit=make_commit):
    """Backfill one or more ancestors of main as one atomic, record-only commit."""
    from audit_changes import identity, main_history, change_line, own_lines, audit_restore_hint
    with ledger_lock(root) as fd:
        if not isinstance(changes, list) or not changes:
            raise ValueError('Changes must be a nonempty JSON list of {commit, actor, note?}.')
        lines = records(root)
        if not ledger_clean(root):
            raise ValueError('The ledger has uncommitted changes; record requires a clean working copy.' + audit_restore_hint(root))
        history = main_history(root)
        order = {row['commit']: position for position, row in enumerate(history)}
        known_changes = {r['change_id'] for r in lines if r['kind'] == 'change'}
        known_commits = {r['commit'] for r in lines if r['kind'] == 'change' and r['commit']}
        additions = []
        for item in changes:
            if (not isinstance(item, dict) or not {'commit', 'actor'} <= item.keys() or
                    item.keys() - {'commit', 'actor', 'note'} or
                    not isinstance(item['commit'], str) or not re.fullmatch('[0-9a-f]{4,40}|[k-z]{4,32}', item['commit']) or
                    item['actor'] not in ('user', 'agent') or
                    'note' in item and not isinstance(item['note'], str)):
                raise ValueError('Each change needs commit, actor user|agent and optional text note.')
            info = identity(root, item['commit'])
            if info['commit'] not in order:
                raise ValueError('The commit is not on main.')
            if info['change_id'] in known_changes or info['commit'] in known_commits:
                raise ValueError('The change is already recorded (or repeated in this request).')
            if len(info['parents']) == 1 and own_lines(root, info):
                raise ValueError('The commit already carries its own audit line.')
            line = change_line(root, info['commit'], item['actor'], recorded=True, note=item.get('note'))
            additions.append(line)
            known_changes.add(info['change_id']); known_commits.add(info['commit'])
        additions.sort(key=lambda r: order[r['commit']])
        return _append_commit(root, additions, 'Record earlier hand changes in the audit log', fd, commit)


def warn_unrecorded(root, lines):
    from audit_changes import unrecorded, HistoryUnavailable
    try:
        missing = unrecorded(root, lines)
    except (HistoryUnavailable, ValueError, OSError) as exc:
        print(f"WARNING: couldn't check unrecorded commits: {exc}", file=sys.stderr)
        return
    import shlex
    for info in missing:
        if info.get('reason'):
            print(f"WARNING: ledger commit {info['commit']} (change {info['change_id']}): {info['reason']}", file=sys.stderr)
            continue
        print(f"WARNING: unrecorded ledger commit {info['commit']} (change {info['change_id']}): "
              + json.dumps(info['message'].rstrip('\n')), file=sys.stderr)
        print('Ask the owner who made it; use --actor agent instead if they identify an agent. Then run:', file=sys.stderr)
        print('uv run --locked --project review-dashboard/server python '
              f"scripts/ledger/audit.py --root {shlex.quote(str(root))} record --commit {info['commit']} --actor user", file=sys.stderr)


def review(root, verdicts, reviewer, message, *, clean=ledger_clean, commit=make_commit,
           lock_fd=None, marker=None, rule_sources=None, publish=None):
    """Validate verdicts, using the caller's audit-only transaction for weekly batches."""
    if lock_fd is None:
        with ledger_lock(root) as fd:
            return review(root, verdicts, reviewer, message, clean=clean, commit=commit, lock_fd=fd)
    if marker:
        held, expected = os.fstat(lock_fd), (root / '.lock').stat()
        if (held.st_dev, held.st_ino) != (expected.st_dev, expected.st_ino) or publish is None:
            raise ValueError('Weekly review requires the held ledger lock and an audit transaction')
    if not verdicts:
        raise ValueError('No verdicts given')
    if not reviewer.strip():
        raise ValueError('The reviewer must be named')
    lines = records(root)
    sources = {r['id'] for r in lines if r['kind'] not in REVIEW_INTERNAL}
    additions, seen = [], set()
    linkage = {key: marker[key] for key in ('run_id', 'batch')} if marker else {}
    for item in verdicts:
        if isinstance(item,dict) and set(item)=={'rule','verdict','reason'}:
            rule_id=item['rule']
            if not isinstance(rule_id,str) or not re.fullmatch('[0-9a-f]{16}',rule_id) or not any(r['id']==rule_id for r in read_json(root/'data/forecast.json')):
                raise ValueError('A rule verdict must identify an existing rule')
            if ('rule',rule_id) in seen: raise ValueError('A rule has a verdict more than once: '+rule_id)
            if item['verdict'] not in ('confirm','propose-removal'): raise ValueError('A rule verdict must be confirm or propose-removal')
            if not isinstance(item['reason'],str) or not item['reason'].strip(): raise ValueError('A rule verdict needs a concrete reason')
            seen.add(('rule',rule_id))
            additions.append(event('rule-review',rule_id=rule_id,rule_verdict=item['verdict'],reason=item['reason'],reviewer=reviewer,
                               **(dict(rule_sources=rule_sources[rule_id]) if marker else {}), **linkage))
            continue
        details = {k: item[k] for k in ('decide', 'evidence', 'choices', 'suggestion') if marker and isinstance(item, dict) and k in item}
        if not isinstance(item, dict) or item.keys() - details.keys() != {'line', 'verdict', 'note'}:
            raise ValueError('Each outcome verdict needs exactly line, verdict and note; a rule verdict needs rule, verdict and reason')
        if not isinstance(item['line'], str) or item['line'] not in sources:
            raise ValueError('--line must identify a draft or statement audit line, or a rule operation: ' + str(item['line']))
        if item['line'] in seen:
            raise ValueError('A line has a verdict more than once: ' + item['line'])
        if item['verdict'] not in ('ok', 'issue'):
            raise ValueError('A verdict must be ok or issue')
        if not isinstance(item['note'], str) or not item['note'].strip():
            raise ValueError('Each verdict needs a note')
        seen.add(item['line'])
        additions.append(event('review', line_id=item['line'], verdict=item['verdict'], note=item['note'], reviewer=reviewer, **details, **linkage))
    if not clean(root):
        from audit_changes import audit_restore_hint
        raise ValueError('The ledger has uncommitted changes; review requires a clean working copy.' + audit_restore_hint(root))
    if any(row['kind'] == 'review' and row['line_id'] in seen for row in lines):
        raise ValueError('The audit line already has a verdict')
    if marker:
        additions.append(marker)
        return publish(additions, message, lock_fd)
    return _append_commit(root, additions, message, lock_fd, commit)


def since_time(value, at=None):
    at = at or datetime.now().astimezone()
    if re.fullmatch(r'\d+d', value):
        return at - timedelta(days=int(value[:-1]))
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.astimezone()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    sub = parser.add_subparsers(dest='command', required=True)
    listing = sub.add_parser('list')
    listing.add_argument('--since', help='7d by default, unless --unreviewed')
    listing.add_argument('--unreviewed', action='store_true', help='operation and hand-change lines without a verdict, of any age')
    listing.add_argument('--json', action='store_true')
    reviewing = sub.add_parser('review', help='record every verdict and commit them together, or change nothing')
    reviewing.add_argument('--verdicts', type=Path, required=True, help='JSON list of outcome {line,verdict,note} and/or rule {rule,verdict,reason} verdicts')
    reviewing.add_argument('--reviewer', required=True)
    reviewing.add_argument('--message', default='Review the dashboard audit log')
    recording = sub.add_parser('record', help='record earlier hand commits together, or change nothing')
    choice = recording.add_mutually_exclusive_group(required=True)
    choice.add_argument('--commit')
    choice.add_argument('--changes', type=Path, help='JSON list of {commit, actor, note?}')
    recording.add_argument('--actor', choices=('user', 'agent'))
    recording.add_argument('--note')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    # A checkout without the ledger (such as an application worktree) must not read as an empty log.
    if not (root / 'data').is_dir():
        parser.error(f'No ledger at {root}: run from the project root, or set LEDGER_DIR')
    from ledger_manifest import read_manifest
    try:
        read_manifest(root)
    except ValueError as exc:
        parser.error(str(exc))
    if args.command == 'list':
        try:
            lines = records(root)
        except (ValueError, OSError) as exc:
            print('WARNING: cannot read audit records: ' + str(exc), file=sys.stderr)
            if args.json: print('[]')
            return
        if args.unreviewed:
            warn_unrecorded(root, lines)
            lines = unreviewed(lines)
        if args.since or not args.unreviewed:
            lines = [r for r in lines if datetime.fromisoformat(r['time']) >= since_time(args.since or '7d')]
        if args.json:
            print(json.dumps(lines, ensure_ascii=False, indent=2))
        else:
            for r in lines:
                summary = r['message'].splitlines()[0] if r.get('message') else r.get('action', r.get('outcome', r.get('verdict', r.get('statement', {}).get('periodEnd', ''))))
                if r['kind'] == 'change':
                    summary = f"{r['actor']}: {summary} ({len(r['entries'])} entries, {len(r['rules'])} rules, {len(r['paths'])} paths)"
                print(f"{r['time']} {r['id']} {r['kind']} {summary}")
                print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.command == 'record':
        try:
            if args.changes:
                if args.actor is not None or args.note is not None:
                    raise ValueError('--actor and --note belong with --commit, not --changes.')
                changes = json.loads(args.changes.read_text(), object_pairs_hook=_unique_object)
            else:
                if args.actor is None:
                    raise ValueError('--commit requires --actor user|agent.')
                changes = [dict(commit=args.commit, actor=args.actor, **({'note': args.note} if args.note is not None else {}))]
            commit_id, ids = record(root, changes)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(f'Committed {len(ids)} change records as {commit_id}')
    else:
        try:
            verdicts = json.loads(args.verdicts.read_text(), object_pairs_hook=_unique_object)
            if not isinstance(verdicts, list):
                raise ValueError('--verdicts must hold a JSON list')
            commit_id, ids = review(root, verdicts, args.reviewer, args.message)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(f'Committed {len(ids)} verdicts as {commit_id}')


if __name__ == '__main__':
    main()
