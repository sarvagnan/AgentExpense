"""Validate and append one actual expense entry with a fresh permanent id."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from store import ROOT, _unique_object, dump, entries, extensions_of, load, new_id, validate
from generate_ledger import render_entry


def add_entry(entry: dict, *, root: Path = ROOT, dry_run: bool = False) -> str:
    if not isinstance(entry, dict) or entry.get('id') is not None:
        raise ValueError('Supply one format 2 entry with "id": null')
    if 'schedule' in entry:
        raise ValueError('This helper adds actual entries, not forecast rules')
    documents = load(root)
    used = {e['id'] for items in documents.values() if isinstance(items, list) for e in items}
    entry = {**entry, 'id': new_id(used)}
    day = entry.get('date')
    if not isinstance(day, str):
        raise ValueError('Entry needs a date')
    path = root / 'data/expenses' / f'{day[:4]}.json'
    items = documents.setdefault(path, [])
    index = next((i for i, existing in enumerate(items) if existing['date'] > day), len(items))
    items.insert(index, entry)
    validate(documents, root)
    declared = set(documents[root / 'data/accounts.json']['accounts'])
    for posting in entry['postings']:
        if posting['account'] not in declared:
            raise ValueError(f"Undeclared account: {posting['account']}")
    for source in entry['evidence']:
        if source.startswith('Bills/') and ('..' in Path(source).parts or not (root / source).is_file()):
            raise ValueError(f'Missing or invalid evidence path: {source}')
    # Check the complete proposed actual journal so balance assertions retain their history.
    declarations = documents[root / 'data/accounts.json']
    lines = [*("commodity " + c for c in declarations['commodities']),
             *("account " + a for a in declarations['accounts']), '']
    extensions = extensions_of(root)
    for actual in entries(documents):
        lines.extend(render_entry(actual, extensions))
    hledger = os.environ.get('HLEDGER_BIN') or shutil.which('hledger') or '/opt/homebrew/bin/hledger'
    checked = subprocess.run([hledger, '-f', '-', 'check', '-s', 'ordereddates'],
                             input='\n'.join(lines) + '\n', text=True, capture_output=True)
    if checked.returncode:
        from confirmed_balances import raise_failure
        raise_failure(documents, root, candidate=entry, detail=checked.stderr)
        raise ValueError('Proposed entry fails strict hledger validation:\n' + checked.stderr)
    # All input checks precede writes, including dry runs. Same-day order remains stable.
    if not dry_run:
        for file, document in documents.items():
            dump(file, document)
    return entry['id']


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', nargs='?', type=Path, help='Entry JSON file; omit or use - for stdin')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--root', type=Path, default=ROOT, help='Ledger repository root')
    args = parser.parse_args(argv)
    try:
        text = sys.stdin.read() if args.file is None or str(args.file) == '-' else args.file.read_text(encoding='utf-8')
        entry = json.loads(text, object_pairs_hook=_unique_object)
        from ledger_manifest import read_manifest
        from ledger_lock import ledger_lock
        read_manifest(args.root)
        if args.dry_run:
            print(add_entry(entry, root=args.root, dry_run=True))
        else:
            with ledger_lock(args.root):
                print(add_entry(entry, root=args.root))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
