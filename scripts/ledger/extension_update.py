"""Bring stored entries in line with one extension's definition and ledger.json: one verified commit.

    make extension-update NAME=<extension>

Run it after listing a new extension in ledger.json, after changing an
extension's fields, or after removing an extension from ledger.json:

- a listed extension that entries don't have yet is added to every entry as null;
- an added field is null in every entry;
- a removed or renamed field is refused while any entry holds a value in it (the
  count says how many); with no values it goes;
- an extension removed from ledger.json is refused while any entry holds its
  values; with none it is removed from every entry.

The groups follow ledger.json's order. Everything runs under the ledger lock and
ends in one verified commit with a reviewable message; on any failure every
file is restored. Trash entries change the same way. docs/EXTENSIONS.md.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ledger_lock import LOCK_FD_ENV, ledger_lock
from ledger_manifest import read_manifest
from store import ROOT, dump, read_json, store_paths, validate
import store_models as m


def plan(root: Path, name: str) -> tuple[dict, dict]:
    """The changed documents and a summary; raises ValueError when values would be lost."""
    if not re.fullmatch(r'[a-z][a-z0-9]*(-[a-z0-9]+)*', name or ''):
        raise ValueError('NAME must be an extension name, such as NAME=work.')
    manifest = read_manifest(root)
    listed = list(manifest['extensions'])
    definitions = {item.name: item.definition for item in m.ledger_extensions(root, manifest['extensions'])}
    fields = list(definitions[name].fields) if name in definitions else []
    documents = {path: read_json(path) for path in store_paths(root)}
    summary = dict(listed=name in definitions, added=set(), removed=set(), changed=0)
    held = {}
    for path, document in documents.items():
        if path.parent.name == 'expenses':
            items = document
        elif path.name == 'trash.json':
            items = [item['entry'] for item in document]
        else:
            continue
        for entry in items:
            groups = entry.get('extensions') if isinstance(entry.get('extensions'), dict) else {}
            before = repr(groups)
            group = groups.get(name)
            if name in definitions:
                if group is None:
                    if name not in groups:
                        summary['added'].add('(the extension)')
                    groups[name] = None
                else:
                    for key in group.keys() - set(fields):
                        summary['removed'].add(key)
                        if group[key] is not None:
                            held[key] = held.get(key, 0) + 1
                    for key in set(fields) - group.keys():
                        summary['added'].add(key)
                    values = {key: group.get(key) for key in fields}
                    groups[name] = values if any(value is not None for value in values.values()) else None
            elif name in groups:
                if group is not None and any(value is not None for value in group.values()):
                    held['(the extension)'] = held.get('(the extension)', 0) + 1
                groups.pop(name)
                summary['removed'].add('(the extension)')
            ordered = {key: groups[key] for key in listed if key in groups}
            ordered.update({key: value for key, value in groups.items() if key not in ordered})
            entry['extensions'] = ordered
            if repr(ordered) != before:
                summary['changed'] += 1
    if held:
        what = '; '.join(f'{count} entries hold values in {key}' for key, count in sorted(held.items()))
        raise ValueError(f'Not changed: {what}. Clear those values (Edit entry) or keep the field, then run this again.')
    return documents, summary


def message(name: str, summary: dict) -> str:
    listing = lambda values: ', '.join(sorted(values)) or 'none'
    return '\n'.join([f'Update extension {name} via make extension-update', '',
                      f"Listed: {'yes' if summary['listed'] else 'no (removed from ledger.json)'}",
                      f"Fields added: {listing(summary['added'] - {'(the extension)'})}",
                      f"Fields removed: {listing(summary['removed'] - {'(the extension)'})}",
                      f"Entries changed: {summary['changed']}",
                      'Added-by: user (extension update)'])


def update(root: Path, name: str, *, commit: bool = True) -> dict:
    root = root.resolve()
    with ledger_lock(root) as fd:
        documents, summary = plan(root, name)
        if not summary['changed']:
            # No entry changes, but ledger.json or the definition may have (listing on an empty
            # ledger, a field added while every group is null, only `ask` changed): commit those.
            from commit import jj
            if not commit or not jj(root, 'diff', '--summary'):
                return {**summary, 'commit': None}
        paths = [path for path in documents if path.parent.name == 'expenses' or path.name == 'trash.json']
        before = {path: path.read_bytes() for path in paths}
        try:
            validate(documents, root)
            for path in paths:
                dump(path, documents[path], root)
            commit_id = None
            if commit:
                from commit import commit as verified_commit
                previous = os.environ.get(LOCK_FD_ENV)
                os.environ[LOCK_FD_ENV] = str(fd)
                try:
                    commit_id = verified_commit(root, message(name, summary))
                finally:
                    if previous is None:
                        os.environ.pop(LOCK_FD_ENV, None)
                    else:
                        os.environ[LOCK_FD_ENV] = previous
        except BaseException:
            for path, data in before.items():
                path.write_bytes(data)
            raise
    return {**summary, 'commit': commit_id}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('name', help='the extension name')
    parser.add_argument('--root', type=Path, default=ROOT, help='Ledger repository root')
    args = parser.parse_args(argv)
    try:
        result = update(args.root, args.name)
    except ValueError as error:
        parser.exit(1, f'{error}\n')
    if result['commit'] is None:
        print(f'Extension {args.name}: every entry already matches and nothing else changed; nothing to commit.')
    else:
        print(f"Extension {args.name}: {result['changed']} entries updated; committed {result['commit'][:12]}.")


if __name__ == '__main__':
    main()
