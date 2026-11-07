"""Read, check and write the format 2 JSON store through the pydantic models (no financial imports).

``load`` reads every store document, validates each against the ledger's models
(the core composed with the extensions ``ledger.json`` lists) and then checks
what spans documents. ``dump`` validates one whole document against its model
and writes it in the canonical layout, so every write is checked and every file
stays byte-stable. In memory a document is the plain JSON value, in format 2's
key order. docs/STORE.md describes the shape.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path
import re
import secrets
import stat
from typing import Any

# Direct script execution puts this directory on sys.path; share the application helper.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_paths import APPLICATION_ROOT, ledger_root
from ledger_manifest import ordered_manifest, read_manifest
from pydantic import ValidationError
import store_models as m

ROOT = ledger_root()
KINDS = {'accounts.json': 'accounts', 'forecast.json': 'forecast', 'card-statements.json': 'card-statements',
         'recurring-settings.json': 'recurring-settings', 'trash.json': 'trash'}


def new_id(used: set[str]) -> str:
    """Allocate random IDs across both the actual and forecast namespaces."""
    while (value := secrets.token_hex(8)) in used:
        pass
    used.add(value)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def ledger_of(path: Path) -> Path:
    """The ledger root that holds a store document."""
    path = Path(path)
    return path.parent if path.name == 'ledger.json' else path.parents[2] if path.parent.name == 'expenses' else path.parents[1]


def kind_of(path: Path) -> str | None:
    """Which store document a path is: manifest, a KINDS value, expenses, or None for any other JSON."""
    path = Path(path)
    if path.name == 'ledger.json':
        return 'manifest'
    if path.parent.name == 'expenses' and path.parent.parent.name == 'data':
        return 'expenses'
    return KINDS.get(path.name) if path.parent.name == 'data' else None


@lru_cache(maxsize=32)
def _models(listed: str, texts: tuple[str, ...]) -> m.LedgerModels:
    names = json.loads(listed)
    extensions = tuple(m.LedgerExtension(name, bool(settings['ask']), text)
                       for (name, settings), text in zip(names.items(), texts))
    return m.models_for(extensions)


def extensions_of(root: Path = ROOT) -> tuple[m.LedgerExtension, ...]:
    """The definitions of the extensions ``ledger.json`` lists, in order, checked for clashes.

    The format number isn't checked here, so a migration can write a typed store while a
    later format's migration is still to run; ``load`` and ``validate`` check it."""
    return m.ledger_extensions(root, read_manifest(root, check_format=False).get('extensions') or {})


def ledger_models(root: Path = ROOT) -> m.LedgerModels:
    """The models of one ledger: the core composed with its listed extensions."""
    extensions = extensions_of(root)
    listed = json.dumps({item.name: {'ask': item.ask} for item in extensions})
    return _models(listed, tuple(item.text for item in extensions))


def _error(path: str, error: ValidationError) -> ValueError:
    first = error.errors()[0]
    where = '.'.join(str(part) for part in first['loc'])
    more = f' (and {error.error_count() - 1} more)' if error.error_count() > 1 else ''
    return ValueError(f'{path}:{where}: {first["msg"]}{more}')


def check_document(path: Path, value: Any, root: Path | None = None) -> None:
    """Validate one whole document against its model."""
    root = ledger_of(path) if root is None else root
    models = ledger_models(root)
    try:
        models.document(kind_of(path)).model_validate(value)
    except ValidationError as error:
        raise _error(str(Path(path).relative_to(root)) if Path(path).is_relative_to(root) else Path(path).name, error) from None


def canonical_text(value: Any, *, flat: bool = True) -> str:
    """The canonical layout: two-space indentation and a final newline.

    Each posting, and (with ``flat``) each object that holds only plain values,
    sits on one line. A list of strings has one item per line.
    """
    def plain(item: Any) -> bool:
        return not isinstance(item, (dict, list)) or not item

    def render(item: Any, level: int, compact: bool = False) -> str:
        if compact or plain(item) or flat and isinstance(item, dict) and all(plain(v) for v in item.values()):
            return json.dumps(item, ensure_ascii=False, separators=(", ", ": "), allow_nan=False)
        pad, inner = "  " * level, "  " * (level + 1)
        if isinstance(item, list):
            return "[\n" + ",\n".join(inner + render(x, level + 1) for x in item) + "\n" + pad + "]"
        parts = []
        for key, val in item.items():
            if key == "postings" and isinstance(val, list) and val:
                text = "[\n" + ",\n".join("  " * (level + 2) + render(x, 0, True) for x in val) + "\n" + inner + "]"
            else:
                text = render(val, level + 1)
            parts.append(inner + json.dumps(key, ensure_ascii=False) + ": " + text)
        return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
    return render(value, 0) + "\n"


def serialize(path: Path, value: Any, root: Path | None = None) -> str:
    """The canonical bytes of one document, validated and in the model's key order."""
    path = Path(path)
    kind = kind_of(path)
    if kind == 'manifest':
        return canonical_text(ordered_manifest(value), flat=False)
    if kind is None:
        # Not a store document (a reconciliation record, for example): plain layout, no model.
        return canonical_text(value, flat=False)
    root = ledger_of(path) if root is None else root
    model = ledger_models(root).document(kind_of(path))
    try:
        parsed = model.model_validate(value)
    except ValidationError as error:
        raise _error(str(path.relative_to(root)) if path.is_relative_to(root) else path.name, error) from None
    return canonical_text(parsed.model_dump(mode='json', by_alias=True))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{secrets.token_hex(8)}.tmp')
    try:
        temporary.write_text(text, encoding="utf-8")
        if path.exists():
            temporary.chmod(stat.S_IMODE(path.stat().st_mode))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def dump(path: Path, value: Any, root: Path | None = None) -> None:
    """Validate a whole document against its model and write it canonically."""
    write_text(Path(path), serialize(Path(path), value, root))


def store_paths(root: Path = ROOT) -> list[Path]:
    return [root / "data/accounts.json", *sorted((root / "data/expenses").glob("*.json")), root / "data/forecast.json", root / "data/card-statements.json",
            *([root / 'data/recurring-settings.json'] if (root / 'data/recurring-settings.json').exists() else []),
            *([root / 'data/trash.json'] if (root / 'data/trash.json').exists() else [])]


def settings_paths(root: Path) -> list[Path]:
    """The ledger files that decide how the store is read: ledger.json and the ledger's own extensions."""
    folder = root / 'extensions'
    return [root / 'ledger.json', *(sorted(folder.glob('*.json')) if folder.is_dir() else [])]


def copy_settings(source: Path, target: Path) -> None:
    """Copy ledger.json and the ledger's extensions into a scratch ledger."""
    import shutil
    for path in settings_paths(source):
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


@lru_cache(maxsize=1)
def currency_codes() -> frozenset[str]:
    return frozenset(read_json(APPLICATION_ROOT / 'schema/currencies.json'))


def commodities(declarations: dict) -> set[str]:
    import shlex
    result = set()
    for directive in declarations['commodities']:
        token = shlex.split(directive)[0]
        # A numeric quantity in a declaration specifies display precision.
        if re.fullmatch(r'[0-9.,]+', token):
            token = shlex.split(directive)[1]
        result.add(token)
    return result


def extension_mismatch(documents: dict[Path, Any], root: Path, listed: list[str]) -> None:
    """Entries whose extension groups don't match ledger.json, counted, with what to run."""
    held, differing, names = Counter(), 0, set()
    for path, document in documents.items():
        items = document if path.parent.name == 'expenses' else [item.get('entry', {}) for item in document] if path.name == 'trash.json' else []
        for entry in items:
            groups = entry.get('extensions') if isinstance(entry, dict) else None
            if not isinstance(groups, dict) or list(groups) == listed:
                continue
            differing += 1
            names |= groups.keys() ^ set(listed)
            for name in groups.keys() - set(listed):
                held[name] += groups[name] is not None
    for name in sorted(held):
        if held[name]:
            raise ValueError(f'{held[name]} entries hold values of the extension "{name}", which ledger.json does not list. '
                             'List it again, or clear those values first.')
    if differing:
        raise ValueError(f'{differing} entries have extension groups that differ from ledger.json; run make extension-update '
                         'NAME=' + (sorted(names)[0] if names else '<name>'))


def validate(documents: dict[Path, Any], root: Path = ROOT) -> None:
    """Validate every document against the models, then the checks that span documents."""
    manifest = read_manifest(root)
    models = ledger_models(root)
    extension_mismatch(documents, root, list(manifest['extensions']))
    for path, document in documents.items():
        if path.name == "ledger.json":
            continue
        kind = kind_of(path)
        try:
            models.document(kind).model_validate(document)
        except ValidationError as error:
            raise _error(str(path.relative_to(root)), error) from None
    accounts = documents[root / 'data/accounts.json']
    declared_commodities = commodities(accounts)
    for name, metadata in accounts['accounts'].items():
        if metadata.get('currency') and metadata['currency'] not in declared_commodities:
            raise ValueError(f'{name}: account currency is not a declared commodity')
    declarations = accounts['accounts']
    codes = currency_codes()
    ids: dict[str, Path] = {}
    for path, document in documents.items():
        kind = kind_of(path)
        if kind in ('manifest', 'accounts', 'recurring-settings', 'trash'):
            continue
        if kind == "expenses" and not re.fullmatch(r"\d{4}\.json", path.name):
            raise ValueError(f"Not a calendar-year filename: {path.name}")
        previous = ""
        for entry in document:
            identity = entry["id"]
            if identity in ids:
                raise ValueError(f"Duplicate id {identity}: {ids[identity]} and {path}")
            ids[identity] = path
            if kind == "card-statements":
                if entry["start"] > entry["end"]:
                    raise ValueError(f"{identity}: statement start is after end")
                if entry["end"] < previous:
                    raise ValueError(f"{path.name}: statements out of end-date order at {identity}")
                previous = entry["end"]
                continue
            for posting in entry['postings']:
                metadata = declarations.get(posting['account'], {})
                currency = metadata.get('currency')
                if not currency:
                    continue
                unit_holding = metadata.get('kind') == 'investment' and posting['commodity'] not in codes
                if posting['commodity'] != currency and not unit_holding:
                    raise ValueError(f"{identity}: {posting['account']} must use its account currency {currency}, got {posting['commodity']}")
                if unit_holding and Decimal(posting['amount']) != 0 and (not posting['cost'] or posting['cost']['commodity'] not in codes):
                    raise ValueError(f"{identity}: investment units need a cost in a currency")
                assertion = posting['assert']
                if assertion and assertion['commodity'] != currency and not (
                        metadata.get('kind') == 'investment' and assertion['commodity'] not in codes):
                    raise ValueError(f"{identity}: assertion must use its account currency {currency} or investment units")
            if kind == "expenses":
                if entry["date"][:4] != path.stem:
                    raise ValueError(f"{identity}: date {entry['date']} does not belong in {path.name}")
                if entry["date"] < previous:
                    raise ValueError(f"{path.name}: dates out of order at {identity}")
                previous = entry["date"]
            else:
                from recurring import occurrence, simple_payment, period_for
                if occurrence(entry) != entry['date']:
                    raise ValueError(f'{identity}: schedule.from must agree with anchor-day')
                if entry['amount'] == 'varies' and not simple_payment(entry, declarations):
                    raise ValueError(f'{identity}: only simple two-posting payments in one currency can vary')
                bill = entry['bill']
                if bill and period_for(entry, bill['occurrence']) != bill['occurrence']:
                    raise ValueError(f'{identity}: bill must name the current occurrence')

    actual = {e['id']: e for e in entries(documents)}
    rules = {r['id']: r for r in documents.get(root / 'data/forecast.json', [])}
    for identity, entry in actual.items():
        if entry['rule'] and entry['rule']['id'] not in rules:
            raise ValueError(f'{identity}: rule link must name an existing rule')
        for link in entry['possible-duplicate-of']:
            if link['id'] not in actual:
                raise ValueError(f'{identity}: duplicate link must name an existing, greater actual entry ID')
    trash = documents.get(root / 'data/trash.json', [])
    trashed = {item['entry']['id'] for item in trash}
    if len(trashed) != len(trash) or trashed & actual.keys():
        raise ValueError('Trash entries must be unique and absent from the ledger')
    if len({item['id'] for item in trash}) != len(trash):
        raise ValueError('Trash operation IDs must be unique')
    stored_paths = set()
    for item in trash:
        originals = [file['original'] for file in item['files']]
        kept = [file['path'] for file in item['kept']]
        sources = {source for source in item['entry']['evidence'] if source.startswith('Bills/')}
        if len(set(originals + kept)) != len(originals + kept) or set(originals + kept) != sources:
            raise ValueError('Trash evidence must map every Bills source exactly once')
        for file in item['files']:
            if not file['stored'].startswith('data/trash-files/' + item['id'] + '/') or '..' in Path(file['original']).parts or file['stored'] in stored_paths:
                raise ValueError('Trash files must stay inside their operation folder')
            stored_paths.add(file['stored'])
        if any('..' in Path(source).parts for source in kept):
            raise ValueError('Kept Trash evidence must stay inside Bills')
    for rule in rules.values():
        evidence = [*((rule['origin'] or {}).get('entry-ids', [])), *((rule['removal'] or {}).get('entry-ids', []))]
        if any(identity not in actual and identity not in trashed for identity in evidence):
            raise ValueError(f"{rule['id']}: rule evidence must name existing actual entries")


def load(root: Path = ROOT) -> dict[Path, Any]:
    read_manifest(root)
    paths = store_paths(root)
    documents = {path: read_json(path) for path in paths}
    validate(documents, root)
    return documents


def entries(documents: dict[Path, Any]) -> list[dict[str, Any]]:
    # Sorting only the date preserves the original sequence among same-day entries.
    return sorted((entry for path, items in documents.items() if path.parent.name == "expenses" for entry in items), key=lambda e: e["date"])


def empty_entry(root: Path = ROOT, **values: Any) -> dict[str, Any]:
    """A format 2 entry with every field empty, then ``values`` (by store name, kebab-case)."""
    entry = {'id': None, 'date': None, 'status': 'confirmed', 'description': None, 'evidence': [], 'statement': None,
             'references': None, 'classification': None, 'original-amount': None, 'reimbursement': None,
             'excluded-from-estimate': None, 'extensions': ledger_models(root).empty_extensions(), 'rule': None,
             'possible-duplicate-of': [], 'notes': [], 'postings': []}
    unknown = set(values) - set(entry)
    if unknown:
        raise ValueError('Unknown entry fields: ' + ', '.join(sorted(unknown)))
    entry.update(values)
    return entry


def posting(account: str, amount: str, commodity: str, *, cost: dict | None = None, assertion: dict | None = None,
            notes: list[str] | None = None) -> dict[str, Any]:
    """A format 2 posting with every field present."""
    return {'account': account, 'amount': amount, 'commodity': commodity, 'cost': cost, 'assert': assertion, 'notes': notes or []}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", action="store_true", help="Rewrite all store files canonically")
    parser.add_argument("--root", type=Path, default=ROOT, help="Ledger repository root")
    args = parser.parse_args()
    read_manifest(args.root)
    if args.format:
        from ledger_lock import ledger_lock
        with ledger_lock(args.root):
            documents = {path: read_json(path) for path in store_paths(args.root)}
            for path, document in documents.items():
                if path.parent.name == "expenses" and isinstance(document, list):
                    document.sort(key=lambda entry: entry["date"])
                elif path.name == "card-statements.json" and isinstance(document, list):
                    document.sort(key=lambda statement: statement["end"])
            validate(documents, args.root)
            for path, document in documents.items():
                dump(path, document, args.root)
            dump(args.root / 'ledger.json', read_json(args.root / 'ledger.json'))
            print(f"Formatted {len(documents) + 1} JSON store files")
    else:
        load(args.root)


if __name__ == "__main__":
    main()
