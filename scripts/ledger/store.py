"""Shared JSON store validation and canonical serialization (no financial imports)."""
from __future__ import annotations

import argparse
from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path
import re
import secrets
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

# Direct script execution puts this directory on sys.path; share the application helper.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_paths import SCHEMA_PATH, ledger_root
from ledger_manifest import ordered_manifest, read_manifest

ROOT = ledger_root()
# Field order is also journal comment order. The right side is the original label.
FIELD_LABELS = {
    "serial-number": "serial-number",
    "source-row": "source-row",
    "source-page": "source-page",
    "statement-page": "statement-page",
    "statement-end": "statement-end",
    "reference": "reference",
    "order-id": "order-id",
    "classification": "classification",
    "original-amount": "original amount",
    "fuel-filled-litres": "fuel-filled-litres",
    "trip-km": "trip-km",
    "reimbursement-group": "reimbursement-group",
    "reimbursement-label": "reimbursement-label",
    "variable-estimate": "variable-estimate",
    "policy-term": "policy-term",
}
ENTRY_KEYS = ("id", "date", "status", "description", "schedule", "sources", *FIELD_LABELS, "notes", "postings")
STATEMENT_KEYS = ("id", "account", "start", "end", "source", "previous-balance", "purchases", "cash-advances", "credits", "total-due", "rows")
KEY_ORDER = {name: index for index, name in enumerate((
    *ENTRY_KEYS, "account", "amount", "commodity", "cost", "assert", "every", "from", "interval", "per",
    "commodities", "accounts", "reader", "identifier",
))}


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


def ordered(value: Any) -> Any:
    if isinstance(value, dict):
        if {'format', 'id', 'name', 'defaultCurrency'} <= value.keys():
            return ordered_manifest(value)
        if set(value) == {'commodities', 'accounts'}:
            return {'commodities': value['commodities'],
                    'accounts': {name: {key: ordered(meta[key]) for key in sorted(meta, key=lambda key: (
                        ('kind', 'currency', 'label', 'aliases', 'statement').index(key) if key in
                        ('kind', 'currency', 'label', 'aliases', 'statement') else 999, key))}
                        for name, meta in value['accounts'].items()}}
        if {"previous-balance", "total-due", "rows"} <= value.keys():
            return {key: ordered(value[key]) for key in STATEMENT_KEYS if key in value} | {key: ordered(val) for key, val in value.items() if key not in STATEMENT_KEYS}
        if {"every", "from"} <= value.keys() and value.get("interval") == 1:
            value = {key: item for key, item in value.items() if key != "interval"}
        if {"account", "amount", "commodity"} <= value.keys():
            keys = ("account", "amount", "commodity", "cost", "assert", "notes")
            return {key: ordered(value[key]) for key in keys if key in value} | {key: ordered(val) for key, val in value.items() if key not in keys}
        return {key: ordered(value[key]) for key in sorted(value, key=lambda k: (KEY_ORDER.get(k, 999), k))}
    if isinstance(value, list):
        return [ordered(item) for item in value]
    return value


def canonical_text(value: Any) -> str:
    """Two-space layout, except each complete posting stays on one compact line."""
    def render(item: Any, level: int, compact: bool = False) -> str:
        if compact or not isinstance(item, (dict, list)) or not item:
            return json.dumps(item, ensure_ascii=False, separators=(", ", ": "), allow_nan=False)
        pad, inner = "  " * level, "  " * (level + 1)
        if isinstance(item, list):
            return "[\n" + ",\n".join(inner + render(x, level + 1) for x in item) + "\n" + pad + "]"
        parts = []
        for key, val in item.items():
            if key == "postings":
                text = "[\n" + ",\n".join("  " * (level + 2) + render(x, 0, True) for x in val) + "\n" + inner + "]"
            else:
                text = render(val, level + 1)
            parts.append(inner + json.dumps(key) + ": " + text)
        return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
    return render(ordered(value), 0) + "\n"


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_text(value), encoding="utf-8")


def store_paths(root: Path = ROOT) -> list[Path]:
    return [root / "data/accounts.json", *sorted((root / "data/expenses").glob("*.json")), root / "data/forecast.json", root / "data/card-statements.json"]


@lru_cache(maxsize=1)
def validators():
    """Application schemas are immutable within a process; ledger files are always fresh."""
    schema = read_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    result = {}
    for kind in ('accounts', 'expenses', 'forecast', 'card-statements'):
        result[kind] = Draft202012Validator(
            {**{k: v for k, v in schema.items() if k != 'oneOf'}, '$ref': f'#/$defs/{kind}'},
            format_checker=FormatChecker())
    result['currency-codes'] = set(read_json(SCHEMA_PATH.with_name('currencies.json')))
    result['manifest'] = Draft202012Validator(read_json(SCHEMA_PATH.with_name('ledger.schema.json')),
                                             format_checker=FormatChecker())
    return result


def validate(documents: dict[Path, Any], root: Path = ROOT) -> None:
    read_manifest(root)
    checks = validators()
    checks['manifest'].validate(read_json(root / 'ledger.json'))
    ids: dict[str, Path] = {}
    for path, document in documents.items():
        kind = {"accounts.json": "accounts", "forecast.json": "forecast", "card-statements.json": "card-statements"}.get(path.name, "expenses")
        # The file chooses its root type. The generic oneOf is ambiguous for empty arrays.
        errors = sorted(checks[kind].iter_errors(document), key=lambda e: str(list(e.absolute_path)))
        if errors:
            error = errors[0]
            raise ValueError(f"{path.relative_to(root)}:{list(error.absolute_path)}: {error.message}")
        if kind == "accounts":
            import shlex
            commodities = set()
            for directive in document['commodities']:
                token = shlex.split(directive)[0]
                # A numeric quantity in a declaration specifies display precision.
                if re.fullmatch(r'[0-9.,]+', token):
                    token = shlex.split(directive)[1]
                commodities.add(token)
            for name, metadata in document['accounts'].items():
                if metadata.get('currency') and metadata['currency'] not in commodities:
                    raise ValueError(f'{name}: account currency is not a declared commodity')
                if any(alias != alias.lower() for alias in metadata.get('aliases', [])):
                    raise ValueError(f'{name}: aliases must be lowercase')
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
            declarations = documents[root / 'data/accounts.json']['accounts']
            for posting in entry['postings']:
                metadata = declarations.get(posting['account'], {})
                currency = metadata.get('currency')
                if not currency:
                    continue
                unit_holding = metadata.get('kind') == 'investment' and posting['commodity'] not in checks['currency-codes']
                if posting['commodity'] != currency and not unit_holding:
                    raise ValueError(f"{identity}: {posting['account']} must use its account currency {currency}, got {posting['commodity']}")
                if unit_holding and Decimal(posting['amount']) != 0 and (not posting.get('cost') or posting['cost']['commodity'] not in checks['currency-codes']):
                    raise ValueError(f"{identity}: investment units need a cost in a currency")
                assertion = posting.get('assert')
                if assertion and assertion['commodity'] != currency and not (
                        metadata.get('kind') == 'investment' and assertion['commodity'] not in checks['currency-codes']):
                    raise ValueError(f"{identity}: assertion must use its account currency {currency} or investment units")
            if kind == "expenses":
                if entry["date"][:4] != path.stem:
                    raise ValueError(f"{identity}: date {entry['date']} does not belong in {path.name}")
                if entry["date"] < previous:
                    raise ValueError(f"{path.name}: dates out of order at {identity}")
                previous = entry["date"]
            elif entry["date"] != entry["schedule"]["from"]:
                raise ValueError(f"{identity}: forecast date must equal schedule.from")


def load(root: Path = ROOT) -> dict[Path, Any]:
    read_manifest(root)
    paths = store_paths(root)
    documents = {path: read_json(path) for path in paths}
    validate(documents, root)
    return documents


def entries(documents: dict[Path, Any]) -> list[dict[str, Any]]:
    # Sorting only the date preserves the original sequence among same-day entries.
    return sorted((entry for path, items in documents.items() if path.parent.name == "expenses" for entry in items), key=lambda e: e["date"])


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
                dump(path, document)
            dump(args.root / 'ledger.json', read_json(args.root / 'ledger.json'))
            print(f"Formatted {len(documents) + 1} JSON store files")
    else:
        load(args.root)


if __name__ == "__main__":
    main()
