"""Format 0 -> 1. Intended report/journal/dashboard changes: none."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import uuid

from account_metadata import infer_kind, is_balance_sheet
from store import dump, read_json

SOURCE_FORMAT = 0
TARGET_FORMAT = 1
PRICE_SOURCE = 'mutual-fund-nav'


def plan(root, options):
    declarations = read_json(root / 'data/accounts.json')
    postings = [p for directory in ['expenses'] for file in sorted((root / 'data' / directory).glob('*.json'))
                for entry in read_json(file) for p in entry['postings']]
    postings += [p for entry in read_json(root / 'data/forecast.json') for p in entry['postings']]
    currencies = defaultdict(set)
    counts = Counter()
    for posting in postings:
        account = posting['account']
        if is_balance_sheet(account):
            currencies[account].add(posting['commodity'])
            counts[posting.get('cost', posting)['commodity']] += 1
    default = options.default_currency or (counts.most_common(1)[0][0] if counts else None)
    if not default:
        raise ValueError('No balance-sheet postings; supply --default-currency.')
    overrides = read_json(options.accounts) if options.accounts else {}
    if not isinstance(overrides, dict) or set(overrides) - set(declarations['accounts']):
        raise ValueError('--accounts must contain overrides keyed by declared account name.')
    accounts, unknown, multiple = {}, [], []
    for account in declarations['accounts']:
        override = overrides.get(account, {})
        if not isinstance(override, dict):
            raise ValueError('--accounts values must be metadata objects.')
        if not is_balance_sheet(account):
            accounts[account] = override
            continue
        units = currencies[account]
        if len(units) > 1:
            multiple.append(account)
        metadata = {'kind': infer_kind(account), 'currency': next(iter(units)) if len(units) == 1 else default}
        metadata.update(override)
        if not metadata['kind']:
            unknown.append(account)
        accounts[account] = metadata
    if multiple:
        raise ValueError('Multiple posting commodities: ' + ', '.join(multiple))
    if unknown:
        raise ValueError('Cannot infer account kinds: ' + ', '.join(unknown) + '. Supply --accounts PATH with per-account metadata overrides.')
    manifest = dict(format=1, id=str(uuid.uuid4()), name=options.name, defaultCurrency=default,
                    modules=dict(fuel=(root / 'data/fuel-log.json').is_file(),
                                 investments=dict(prices=PRICE_SOURCE if (root / 'data/prices/mutual-funds.prices').is_file() and (root / 'data/prices/fund-sources.json').is_file() else None),
                                 history=dict(oldLedger=None), weeklyReview=False),
                    review=dict(storageKey=options.review_storage_key))
    for key, value in [('locale', options.locale), ('startDate', options.start_date)]:
        if value is not None:
            manifest[key] = value
    return manifest, {**declarations, 'accounts': accounts}


def apply(root, result):
    manifest, accounts = result
    dump(root / 'ledger.json', manifest)
    dump(root / 'data/accounts.json', accounts)
