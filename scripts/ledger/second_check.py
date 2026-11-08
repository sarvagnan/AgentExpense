"""hledger as the second check: its strict check of an exported journal, in plain words.

The JSON strict check (`strict_check.py`) is the gate every save waits for. hledger is
optional. When it is installed it checks the exported journal a second time:

- `make verify` and `make verify-data` run it at once (`verify` below) and stop on a failure;
- `make commit` (and so every server save) sets EXPENSES_HLEDGER_CHECK=background, which
  skips it in `make verify-data`; the server then checks each commit in the background
  (`snapshot`, called by `review-dashboard/server/ledger_check.py`).

`finding()` reads hledger's error text and says what it found in plain words, with the
entry and accounts it names. The verify path uses only the standard library, so `make
verify-data` runs it with python3; the snapshot path loads the store with the models.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ENV = 'EXPENSES_HLEDGER_CHECK'
COMMAND = ('check', '-s', 'ordereddates')
JOURNAL = 'expense_ledger.ledger'
# How long hledger may take on one journal before the check counts as failed.
TIMEOUT = 120

TITLES = {
    'unbalanced': 'An entry doesn’t balance',
    'conversion': 'An entry mixes currencies without a conversion',
    'assertion': 'A confirmed balance doesn’t hold',
    'account': 'An account isn’t declared',
    'commodity': 'A currency isn’t declared',
    'dates': 'Entries are out of date order',
    'parse': 'hledger couldn’t read the ledger',
    'other': 'hledger found a problem',
}
APP_PROBLEM = ('AgentExpense’s own check passed this ledger, but hledger refused it, so the app and hledger '
               'disagree. This is a problem in AgentExpense, not in your entries. Copy the details and report it.')


def find_hledger(environ=None) -> str | None:
    """The hledger to run, or None when it isn't installed (it is optional)."""
    environ = os.environ if environ is None else environ
    for name in ('HLEDGER_BIN', 'HLEDGER'):
        value = environ.get(name)
        if value and (shutil.which(value, path=environ.get('PATH')) or (Path(value).is_file() and os.access(value, os.X_OK))):
            return shutil.which(value, path=environ.get('PATH')) or value
    return shutil.which('hledger', path=environ.get('PATH'))


def run_hledger(hledger: str, journal: Path) -> tuple[bool, str]:
    """hledger's strict check of one journal: passed, and its error text."""
    try:
        done = subprocess.run([hledger, '-f', str(journal), *COMMAND], capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f'hledger did not finish within {TIMEOUT} seconds.'
    except OSError as exc:
        return False, f'hledger could not start: {exc}'
    return done.returncode == 0, (done.stderr or done.stdout).strip()


LOCATION = re.compile(r'^hledger: Error: (?P<path>.+?):(?P<line>\d+)(?:-(?P<end>\d+))?(?::(?P<column>\d+))?:\s*$', re.M)
HEADER = re.compile(r'^(?P<date>\d{4}-\d{2}-\d{2})\s')


def blocks(journal_text: str) -> list[dict]:
    """Each transaction or rule in a journal: first and last line, date, id and posting accounts."""
    found, current = [], None
    for number, line in enumerate(journal_text.splitlines(), 1):
        if line and not line[0].isspace():
            current = None
            if HEADER.match(line) or line.startswith('~ '):
                match = HEADER.match(line)
                current = dict(start=number, end=number, date=match['date'] if match else None, id=None, accounts=[],
                               rule=line.startswith('~ '), description=line.split(' ', 2)[2] if match and line.count(' ') >= 2 else None)
                found.append(current)
            continue
        if current is None:
            continue
        if not line.strip():
            current = None
            continue
        current['end'] = number
        text = line.strip()
        if text.startswith(';'):
            tag = re.match(r';\s*id:\s*(\S+)', text)
            if tag and current['id'] is None:
                current['id'] = tag.group(1)
            continue
        account = re.split(r'\s{2,}|\t', text, maxsplit=1)[0]
        if account and account not in current['accounts']:
            current['accounts'].append(account)
    return found


def finding(output: str, journal_text: str = '', journal: str | None = None) -> dict:
    """What hledger found, in plain words, with the entry, accounts and commodity it names."""
    text = output or ''
    location = LOCATION.search(text)
    line = int(location['line']) if location else None
    in_journal = bool(location) and (journal is None or Path(location['path']).name == Path(journal).name)
    block = None
    if line and in_journal:
        block = next((b for b in blocks(journal_text) if b['start'] <= line <= b['end']), None)
    value = dict(kind='other', title=TITLES['other'], summary='hledger’s strict check failed. Details has its own words.',
                 line=line if in_journal else None, file=None if in_journal or not location else Path(location['path']).name,
                 entry_id=None, rule_id=None, date=None, description=None, accounts=[], account=None, commodity=None)
    if block:
        value.update(date=block['date'], description=block['description'], accounts=list(block['accounts']),
                     **({'rule_id': block['id']} if block['rule'] else {'entry_id': block['id']}))
    if re.search(r'multi-commodity transaction is unbalanced', text):
        value.update(kind='conversion', title=TITLES['conversion'],
                     summary='An entry has amounts in different currencies and no cost that converts one into the other, so it doesn’t balance.')
    elif re.search(r'transaction is unbalanced', text):
        residue = re.search(r'sum should be 0 but is: (.+)', text)
        value.update(kind='unbalanced', title=TITLES['unbalanced'],
                     summary='An entry doesn’t add up to zero' + (f': it is off by {residue.group(1).strip()}.' if residue else '.'))
    elif (match := re.search(r'Balance assertion failed in (.+)', text)):
        account = match.group(1).strip()
        asserted = re.search(r'asserted balance is:\s+(.+)', text)
        calculated = re.search(r'calculated balance is:\s+(.+)', text)
        commodity = re.search(r'In commodity (\S+)', text)
        value.update(kind='assertion', title=TITLES['assertion'], account=account,
                     commodity=commodity.group(1) if commodity else None,
                     accounts=[account, *[a for a in value['accounts'] if a != account]],
                     summary=f'The confirmed balance of {account} doesn’t match the entries before it'
                             + (f': confirmed {asserted.group(1).strip()}, calculated {calculated.group(1).strip()}.' if asserted and calculated else '.'))
    elif (match := re.search(r'account "(.+?)" has not been declared', text)):
        account = match.group(1)
        value.update(kind='account', title=TITLES['account'], account=account,
                     accounts=[account, *[a for a in value['accounts'] if a != account]],
                     summary=f'An entry uses {account}, which isn’t in the ledger’s list of accounts.')
    elif (match := re.search(r'commodity "(.+?)" has not been declared', text)):
        value.update(kind='commodity', title=TITLES['commodity'], commodity=match.group(1),
                     summary=f'{match.group(1)} is used{" by an entry" if block else ""} but isn’t in the ledger’s list of currencies.')
    elif re.search(r'out of order with the previous transaction', text):
        value.update(kind='dates', title=TITLES['dates'],
                     summary='An entry comes after a later-dated entry in the ledger file, so the dates are out of order.')
    elif location:
        where_line = f'line {line} of {value["file"]}' if value['file'] else f'line {line} of the exported ledger'
        value.update(kind='parse', title=TITLES['parse'],
                     summary=f'hledger couldn’t read {where_line}' + (', in an entry' if block else '') + '.')
    return value


def describe(found: dict) -> str:
    """Plain words for a terminal: the title, the summary and what to do."""
    lines = [f'hledger second check failed: {found["title"]}.', found['summary']]
    if found['entry_id']:
        lines.append(f'Entry: {found["entry_id"]}')
    return '\n'.join(lines)


def verify(root: Path, hledger: str | None) -> int:
    """`make verify-data`'s hledger step: run now, unless saves deferred it to the background."""
    if os.environ.get(ENV) == 'background':
        print('hledger second check: in the background (the server checks each commit; make verify-data runs it now)')
        return 0
    if not hledger:
        print('hledger is not installed: the second check is skipped (it is optional; see make doctor)')
        return 0
    journal = root / JOURNAL
    ok, output = run_hledger(hledger, journal)
    if ok:
        print('hledger second check: passed')
        return 0
    text = journal.read_text(encoding='utf-8') if journal.is_file() else ''
    print(describe(finding(output, text, str(journal))), file=sys.stderr)
    print('\nhledger said:\n' + output, file=sys.stderr)
    return 1


# The snapshot: the store at one commit, read without the working copy or the ledger lock.

SNAPSHOT_FILES = re.compile(r'^(ledger\.json|extensions/[^/]+\.json|data/(accounts|forecast|card-statements|recurring-settings|trash)\.json'
                            r'|data/expenses/[^/]+\.json|data/prices/[^/]+\.prices)$')


def jj(root: Path, *args: str) -> str:
    """A read-only jj command: it never snapshots the working copy or waits for a save."""
    # Run from the ledger root: jj prints paths relative to the working directory.
    return subprocess.check_output(['jj', '--ignore-working-copy', '--color=never', '-R', str(root), *args],
                                   cwd=root, text=True, stderr=subprocess.PIPE)


def committed(root: Path) -> str | None:
    """The commit the ledger's main bookmark names (or the working copy's parent), or None."""
    for revision in ('main', '@-'):
        try:
            value = jj(root, 'log', '-r', revision, '--no-graph', '-T', 'commit_id').strip()
        except (OSError, subprocess.CalledProcessError):
            continue
        if re.fullmatch(r'[0-9a-f]{40}', value):
            return value
    return None


def show(root: Path, commit: str, path: str) -> bytes:
    return subprocess.check_output(['jj', '--ignore-working-copy', '-R', str(root), 'file', 'show', '-r', commit,
                                    'root-file:' + json.dumps(path)], cwd=root, stderr=subprocess.PIPE)


def export(root: Path, commit: str, target: Path) -> list[str]:
    """Write the store files of one commit under target (no Bills, audit or drafts)."""
    paths = [p for p in jj(root, 'file', 'list', '-r', commit).splitlines() if SNAPSHOT_FILES.match(p)]
    for path in paths:
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(show(root, commit, path))
    return paths


class BaseMissing(LookupError):
    """The last good check's commit is no longer in the ledger's history."""


def changed_entries(root: Path, base: str | None, commit: str, target: Path) -> list[dict] | None:
    """Entries added or changed between the last good check and this commit, newest first;
    None before any good check. Raises BaseMissing when that commit has left the history."""
    if not base or base == commit:
        return None if not base else []
    try:
        names = [p for p in jj(root, 'diff', '--from', base, '--to', commit, '--name-only').splitlines()
                 if re.fullmatch(r'data/expenses/[^/]+\.json', p)]
    except subprocess.CalledProcessError:
        raise BaseMissing(base) from None
    before, after = {}, {}
    for name in names:
        try:
            for entry in json.loads(show(root, base, name)):
                before[entry['id']] = entry
        except (subprocess.CalledProcessError, ValueError, KeyError, TypeError):
            pass
        path = target / name
        if path.is_file():
            for entry in json.loads(path.read_text(encoding='utf-8')):
                after[entry['id']] = entry
    changed = [dict(id=e['id'], date=e['date'], description=e['description'],
                    accounts=sorted({p['account'] for p in e['postings']}), added=e['id'] not in before)
               for identity, e in after.items() if before.get(identity) != e]
    return sorted(changed, key=lambda e: (e['date'], e['id']), reverse=True)


def changes(root: Path, base: str | None, commit: str, target: Path) -> dict:
    """`changed` (list, or None when unknown) and `base_missing` for a failure's record."""
    try:
        return dict(changed=changed_entries(root, base, commit, target), base_missing=False)
    except BaseMissing:
        return dict(changed=None, base_missing=True)


def json_check(target: Path) -> str | None:
    """The JSON strict check of the snapshot: None when it passes, else its reason."""
    from store import load
    from strict_check import check
    try:
        check(load(target), target)
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        return str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return None


def snapshot(root: Path, commit: str, base: str | None, hledger: str, work: Path, *, hledger_result=None) -> dict:
    """Export the store at `commit`, render its journal and run hledger on it.

    `hledger_result(journal_path, journal_text)` replaces the hledger run (a test hook
    that feeds a result). On a failure the JSON strict check runs on the same snapshot:
    when it passes, the app and hledger disagree (an app problem)."""
    import time
    started = time.perf_counter()
    target = work / 'ledger'
    target.mkdir(parents=True)
    export(root, commit, target)
    from generate_ledger import render
    from store import load
    journal = target / JOURNAL
    try:
        text = render(load(target), target)
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        # The models refused the committed store: the JSON check fails too, so it's the data.
        reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return dict(commit=commit, ok=False, output=reason, finding=dict(finding(''), kind='other', title='The ledger’s data can’t be read',
                    summary=f'The committed data doesn’t pass AgentExpense’s own checks: {reason}'),
                    json_check=reason, app_problem=False, seconds=round(time.perf_counter() - started, 3),
                    **changes(root, base, commit, target))
    journal.write_text(text, encoding='utf-8')
    ok, output = hledger_result(journal, text) if hledger_result else run_hledger(hledger, journal)
    result = dict(commit=commit, ok=ok, output=output, seconds=None)
    if not ok:
        found = finding(output, text, str(journal))
        reason = json_check(target)
        result.update(finding=found, json_check=reason, app_problem=reason is None, **changes(root, base, commit, target))
    result['seconds'] = round(time.perf_counter() - started, 3)
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('verify', help='hledger strict check of the ledger journal now (make verify-data)')
    check.add_argument('--root', type=Path)
    check.add_argument('--hledger')
    snap = commands.add_parser('snapshot', help='check one commit from a snapshot; prints the result as JSON')
    snap.add_argument('--root', type=Path, required=True)
    snap.add_argument('--commit', required=True)
    snap.add_argument('--base')
    snap.add_argument('--hledger', required=True)
    snap.add_argument('--work', type=Path, required=True)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if args.command == 'verify':
        from project_paths import ledger_root
        root = (args.root or ledger_root()).resolve()
        return verify(root, find_hledger({**os.environ, 'HLEDGER_BIN': args.hledger or os.environ.get('HLEDGER_BIN', '')}))
    print(json.dumps(snapshot(args.root.resolve(), args.commit, args.base, args.hledger, args.work)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
