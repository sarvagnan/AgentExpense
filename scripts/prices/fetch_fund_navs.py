"""Dispatch the ledger's configured price source. Called under the shared lock.

The public entry point is update_fund_prices.sh; --ledger-locked is internal.
"""
from datetime import date, timedelta
from decimal import Decimal
import argparse
import importlib.util
from pathlib import Path
import re
import shlex
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_paths import ledger_root
from ledger_manifest import read_manifest, price_source
from ledger.ledger_lock import ledger_lock

INTERFACE_VERSION = 1
PRICE_PATH = 'data/prices/mutual-funds.prices'


def load_source(identity, root):
    if not re.fullmatch(r'[a-z][a-z0-9-]*', identity):
        raise ValueError('Invalid price source id')
    path = Path(__file__).with_name('sources') / (identity + '.py')
    if not path.is_file():
        raise ValueError('Unknown price source: ' + identity)
    spec = importlib.util.spec_from_file_location('expense_price_source', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if getattr(module, 'INTERFACE_VERSION', None) != INTERFACE_VERSION:
        raise ValueError('Unsupported price source interface')
    return module.Source(root)


def parse_directives(text, commodities=None):
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(';'):
            continue
        parts = shlex.split(line)
        if len(parts) != 5 or parts[0] != 'P':
            raise ValueError('Price source must return hledger P directives')
        _, day, commodity, quantity, currency = parts
        date.fromisoformat(day)
        value = Decimal(quantity)
        if not value.is_finite() or value <= 0 or not all(re.fullmatch(r'[^\s";=@]+', token) for token in (commodity, currency)):
            raise ValueError('Invalid price directive')
        if commodities is not None and commodity not in commodities:
            raise ValueError('Price source returned an undeclared commodity')
        key = (day, commodity, currency)
        if key in result and Decimal(result[key]) != value:
            raise ValueError('Conflicting price directives')
        result[key] = quantity
    return result


def update(root, start=None, end=None):
    identity = price_source(read_manifest(root))
    if identity is None:
        return False
    source = load_source(identity, root)
    if not source.commodities:
        return False
    path = root / PRICE_PATH
    text = path.read_text() if path.exists() else ''
    prices = parse_directives(text)
    if start is None:
        latest = [max((day for day, item, _ in prices if item == commodity), default=None) for commodity in source.commodities]
        if not all(latest):
            raise ValueError('--from is required when configured commodities have no stored prices')
        start = date.fromisoformat(min(latest)) - timedelta(days=7)
    end = end or date.today()
    if start > end:
        raise ValueError('Price start date is after end date')
    fetched = parse_directives(source.fetch(start, end), set(source.commodities))
    if any(not start.isoformat() <= day <= end.isoformat() for day, _, _ in fetched):
        raise ValueError('Price source returned a date outside the requested range')
    changed = any(key not in prices or Decimal(prices[key]) != Decimal(value) for key, value in fetched.items())
    if not changed:
        return False
    prices.update(fetched)
    header = [line for line in text.splitlines() if line.startswith(';')]
    if not header:
        header = ['; Price source: ' + identity, '; Written by scripts/prices/fetch_fund_navs.py.']
    body = header + [''] + [f'P {day} "{commodity}" {value} {currency}' for (day, commodity, currency), value in sorted(prices.items())]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    try:
        temporary.write_text('\n'.join(body) + '\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from', dest='start', type=date.fromisoformat)
    parser.add_argument('--to', dest='end', type=date.fromisoformat)
    parser.add_argument('--ledger-locked', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--configured', action='store_true', help='Exit 0 if a price source is configured, otherwise 1')
    args = parser.parse_args()
    root = ledger_root()
    if args.configured:
        return 0 if price_source(read_manifest(root)) else 1
    if args.ledger_locked:
        update(root, args.start, args.end)
    else:
        with ledger_lock(root):
            update(root, args.start, args.end)
    return 0


if __name__ == '__main__':
    sys.exit(main())
