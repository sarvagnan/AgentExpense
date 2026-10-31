"""Append-only live-page audit records and weekly review commands."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from jsonschema import Draft202012Validator, FormatChecker
from store import ROOT, read_json, _unique_object
from ledger_lock import LOCK_FD_ENV, ledger_lock
from project_paths import APPLICATION_ROOT

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


def records(root=ROOT):
    validator = Draft202012Validator(read_json(SCHEMA), format_checker=FORMATS)
    used = set()
    result = []
    for path in sorted((root / 'data/additions').glob('*.jsonl')):
        if not re.fullmatch(r'\d{4}\.jsonl', path.name):
            raise ValueError(f'Invalid audit year file: {path.name}')
        for number, line in enumerate(path.read_text().splitlines(), 1):
            try:
                record = json.loads(line, object_pairs_hook=_unique_object)
                validator.validate(record)
                if record['id'] in used:
                    raise ValueError('Duplicate audit ID ' + record['id'])
                if datetime.fromisoformat(record['time']).year != int(path.stem):
                    raise ValueError('Audit event is in the wrong year')
            except Exception as exc:
                raise ValueError(f'{path.relative_to(root)}:{number}: {exc}') from exc
            used.add(record['id'])
            result.append(record)
    return result


def audit_path(root, record):
    return root / 'data/additions' / (record['time'][:4] + '.jsonl')


def append(root, record):
    """Caller holds the ledger lock; O_APPEND never rewrites an existing line."""
    Draft202012Validator(read_json(SCHEMA), format_checker=FORMATS).validate(record)
    existing = next((r for r in records(root) if r['id'] == record['id']), None)
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
    data = (json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(',', ':')) + '\n').encode()
    with path.open('ab') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def missing_entries(root, entry_ids):
    return [(r['id'], identity) for r in records(root) for identity in
            ([r['entry_id']] if r['kind'] == 'draft' and r.get('entry_id') else r.get('entry_ids', []) if r['kind'] == 'statement' else []) if identity not in entry_ids]


def unreviewed(lines):
    reviewed = {r['line_id'] for r in lines if r['kind'] == 'review'}
    return [r for r in lines if r['kind'] != 'review' and r['id'] not in reviewed]


def ledger_clean(root):
    return not subprocess.check_output(['jj', '-R', str(root), 'diff', '--summary'], text=True).strip()


def make_commit(root, message, lock_fd):
    env = {**os.environ, 'LEDGER_DIR': str(root), LOCK_FD_ENV: str(lock_fd), 'EXPENSES_COMMIT_MESSAGE': message}
    completed = subprocess.run(['make', 'commit'], cwd=APPLICATION_ROOT, env=env, pass_fds=(lock_fd,),
                               text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError('Ledger commit failed: ' + (completed.stdout + completed.stderr)[-5000:])
    return subprocess.check_output(['jj', '-R', str(root), 'log', '-r', '@-', '--no-graph', '-T', 'commit_id'], text=True).strip()


def review(root, verdicts, reviewer, message, *, clean=ledger_clean, commit=make_commit):
    """Append one verdict per line and commit them as one ledger change; on any failure change nothing.

    A half-written review would leave the ledger dirty, and the live server waits for a clean one.
    """
    with ledger_lock(root) as lock_fd:
        lines = {r['id']: r for r in records(root)}
        if not verdicts:
            raise ValueError('No verdicts given')
        if not reviewer.strip():
            raise ValueError('The reviewer must be named')
        additions, seen = [], set()
        for item in verdicts:
            if not isinstance(item, dict) or set(item) != {'line', 'verdict', 'note'}:
                raise ValueError('Each verdict needs exactly line, verdict and note')
            line = lines.get(item['line']) if isinstance(item['line'], str) else None
            if not line or line['kind'] == 'review':
                raise ValueError('--line must identify a draft or statement audit line: ' + str(item['line']))
            if item['line'] in seen:
                raise ValueError('A line has a verdict more than once: ' + item['line'])
            if item['verdict'] not in ('ok', 'issue'):
                raise ValueError('A verdict must be ok or issue')
            if not isinstance(item['note'], str) or not item['note'].strip():
                raise ValueError('Each verdict needs a note')
            seen.add(item['line'])
            additions.append(event('review', line_id=item['line'], verdict=item['verdict'], note=item['note'], reviewer=reviewer))
        if not clean(root):
            raise ValueError('The ledger has uncommitted changes; leave them for the user rather than committing them with verdicts')
        sizes = {path: path.stat().st_size if path.exists() else None for path in {audit_path(root, r) for r in additions}}
        try:
            for record in additions:
                append(root, record)
            return commit(root, message, lock_fd), [r['id'] for r in additions]
        except BaseException:
            # Back to the exact earlier bytes: truncation keeps every committed line untouched.
            for path, size in sizes.items():
                if size is None:
                    path.unlink(missing_ok=True)
                else:
                    os.truncate(path, size)
            raise


def since_time(value, at=None):
    at = at or datetime.now(timezone.utc)
    if re.fullmatch(r'\d+d', value):
        return at - timedelta(days=int(value[:-1]))
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    sub = parser.add_subparsers(dest='command', required=True)
    listing = sub.add_parser('list')
    listing.add_argument('--since', help='7d by default, unless --unreviewed')
    listing.add_argument('--unreviewed', action='store_true', help='draft and statement lines without a verdict, of any age')
    listing.add_argument('--json', action='store_true')
    reviewing = sub.add_parser('review', help='record every verdict and commit them together, or change nothing')
    reviewing.add_argument('--verdicts', type=Path, required=True, help='JSON list of {"line", "verdict": "ok" or "issue", "note"}')
    reviewing.add_argument('--reviewer', required=True)
    reviewing.add_argument('--message', default='Review the dashboard audit log')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    # A checkout without the ledger (such as an application worktree) must not read as an empty log.
    if not (root / 'data').is_dir():
        parser.error(f'No ledger at {root}: run from the project root, or set LEDGER_DIR')
    if args.command == 'list':
        lines = records(root)
        if args.unreviewed:
            lines = unreviewed(lines)
        if args.since or not args.unreviewed:
            lines = [r for r in lines if datetime.fromisoformat(r['time']) >= since_time(args.since or '7d')]
        if args.json:
            print(json.dumps(lines, ensure_ascii=False, indent=2))
        else:
            for r in lines:
                print(f"{r['time']} {r['id']} {r['kind']} {r.get('outcome', r.get('verdict', r.get('statement', {}).get('periodEnd', '')))}")
                print(json.dumps(r, ensure_ascii=False, indent=2))
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
