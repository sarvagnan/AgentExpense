"""Format 1 -> 2: every store document in its typed shape (docs/STORE.md), fuel as an extension.

Every entry, posting and rule is written out in full, with related facts grouped;
fuel readings move into the fuel extension (docs/EXTENSIONS.md) and policy terms
into a ledger extension, ``policy``, made only when an entry has one. A per-unit
cost that doesn't balance exactly becomes the total cost that does, and a
repeated order-id becomes a list, and an empty optional string becomes null. Anything
else the models can't hold stops the migration with a count per kind; the ledger stays
unchanged.

The journal stays byte-identical except, as declared and counted: converted unit
costs (``@`` becomes ``@@``), policy terms (``policy-term: 5`` in the extension
slot instead of ``5 years``), repeated order-ids whose comment lines move and cleared
empty values whose empty comment lines go.
Native-commodity hledger reports stay identical. Every output, the plan and the
counts name no account, amount, date, description or path.
"""
from collections import Counter
import json
from pathlib import Path
import shutil
import tempfile
import os

import format1
from store import dump, write_text, load, canonical_text

SOURCE_FORMAT = 1
TARGET_FORMAT = 2
# Dashboard paths that change with the typed store (docs/STORE.md, "Dashboard data"): the manifest
# and account metadata the dashboard copies, the extension definitions and values, and the
# rules (and their account metadata) Add reads.
OUTPUT_CHANGES = ('$.meta.ledger', '$.meta.accounts', '$.meta.extensions', '$.transactions.*.extensions',
                  '$.rules', '$.payments')


class Plan(tuple):
    """(manifest, accounts) like every migration, plus the converted documents and counts."""

    def __new__(cls, manifest, documents, definitions, counts, allowed):
        plan = super().__new__(cls, (manifest, documents['data/accounts.json']))
        plan.documents, plan.definitions, plan.counts, plan.allowed = documents, definitions, counts, allowed
        return plan


def _allowed(root, documents, counts):
    """Entries whose journal lines may change, by the kinds the migration declares."""
    allowed = {'unit-cost': set(), 'policy-term': set(), 'order-id': set(), 'empty-value': set()}
    old = {e['id']: e for path in sorted((root / 'data/expenses').glob('*.json')) for e in format1.read(path)}
    for path, items in documents.items():
        if not path.startswith('data/expenses/'):
            continue
        for entry in items:
            before = old[entry['id']]
            if any(p.get('cost', {}).get('per') == 'unit' for p in before['postings']) and \
                    [p['cost'] for p in entry['postings']] != [format1.posting(p)['cost'] for p in before['postings']]:
                allowed['unit-cost'].add(entry['id'])
            if 'policy-term' in before:
                allowed['policy-term'].add(entry['id'])
            if any(note.startswith('order-id: ') for note in before.get('notes', [])) and 'order-id' in before:
                allowed['order-id'].add(entry['id'])
            # An empty label printed an empty comment line; the cleared value prints none.
            if any(before.get(key) in ('', []) for key in format1.EMPTY_CLEARED) or '' in before.get('sources', []):
                allowed['empty-value'].add(entry['id'])
    return allowed


def plan(root, options):
    from ledger_manifest import read_manifest
    manifest = read_manifest(root, check_format=False)
    upgraded, documents, definitions, counts = format1.convert(root, manifest)
    if (root / 'extensions').exists():
        counts['problem:existing-extensions-folder'] += 1
    problems = {key: value for key, value in counts.items() if key.startswith('problem:')}
    if problems:
        lines = [f'{value} {format1.PROBLEMS.get(key[8:], key[8:])}' for key, value in sorted(problems.items())]
        raise ValueError('The ledger needs changes before format 2 can hold it: ' + '; '.join(lines) + '.')
    # The runner calls check() when this is the last step; a later format's checks would refuse format 2.
    return Plan(upgraded, documents, definitions, counts, _allowed(root, documents, counts))


def write(root, result):
    write_text(root / 'ledger.json', canonical_text(__import__('ledger_manifest').ordered_manifest(result[0]), flat=False))
    for name, definition in result.definitions.items():
        write_text(root / 'extensions' / f'{name}.json', json.dumps(definition, indent=2, ensure_ascii=False) + '\n')
    for relative, document in result.documents.items():
        dump(root / relative, document, root)


KINDS = (('data/expenses/', 'entries'), ('data/forecast.json', 'rules'), ('data/card-statements.json', 'statements'),
         ('data/accounts.json', 'accounts'), ('data/trash.json', 'trash'), ('data/recurring-settings.json', 'recurring settings'),
         ('ledger.json', 'ledger settings'), ('extensions/', 'extension definitions'))


def document_kind(message):
    """The kind of store document a check failure is about, from the message's leading path."""
    head = message.split(':', 1)[0].strip()
    return next((kind for prefix, kind in KINDS if head.startswith(prefix) or prefix.rstrip('/') in head), 'store documents')


def check(result):
    """Write the converted ledger into scratch space and load it with every format 2 check."""
    with tempfile.TemporaryDirectory(prefix='expense-migration-2-', dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp')) as folder:
        scratch = Path(folder)
        try:
            write(scratch, result)
            load(scratch)
        except ValueError as error:
            # Name the kind of document only: never a file name, a year or a value.
            raise ValueError(f'The converted ledger does not pass the format 2 checks ({document_kind(str(error))}). '
                             'Run make verify on a copy after upgrading it by hand to see details.') from None


def describe(result):
    """The plan as counts only."""
    counts = result.counts
    lines = [f'  {key}: {value}' for key, value in sorted(counts.items()) if not key.startswith('problem:')]
    lines += [f'  journal entries that change as declared ({kind}): {len(ids)}' for kind, ids in sorted(result.allowed.items())]
    return lines


def targets(root):
    """Every path this migration may write, for rollback."""
    from store import store_paths
    return [*(str(path.relative_to(root)) for path in store_paths(root)), 'extensions/policy.json']


def apply(root, result):
    write(root, result)
