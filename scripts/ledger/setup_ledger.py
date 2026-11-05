"""Add accounts and opening balances to a ledger during setup, under the ledger lock.

    make add-account ARGS='--kind bank --label "Example Checking" [--currency USD]
                           [--alias checking] [--reader example-card-pdf --identifier 1234]'
    make add-account ARGS='--account "Expenses:Groceries"'
    make opening-balances ARGS='--date 2026-11-05 "Example Checking=2400.00" "Example Card=312.40"'

Accounts get their hledger name from their kind and label unless --account names one.
Opening balances are positive amounts: what is in a bank, cash, wallet or investment
account, what is owed on a card or loan (including unbilled charges), and for a person
what they owe you (negative when you owe them). Each becomes a balance assertion on the
start date against Equity:Opening Balances, and the start date is saved as startDate.
Nothing is committed: run `make commit m="…"` afterwards.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ledger_lock import ledger_lock
from project_paths import APPLICATION_ROOT
from store import ROOT, dump, load, new_id, read_json, validate

KINDS = ('bank', 'card', 'cash', 'wallet', 'loan', 'investment', 'person')
PARENTS = {'bank': 'Assets:Bank', 'card': 'Liabilities:Credit Cards', 'cash': 'Assets:Cash', 'wallet': 'Assets:Wallets',
           'loan': 'Liabilities:Loans', 'investment': 'Assets:Investments', 'person': 'Assets:People'}
OWED = ('card', 'loan')  # amounts typed as owed; the ledger holds them as negative
OPENING = 'Equity:Opening Balances'
ROOTS = ('Assets', 'Liabilities', 'Equity', 'Income', 'Expenses')


def currencies():
    return set(json.loads((APPLICATION_ROOT / 'schema/currencies.json').read_text()))


def minor_units(code):
    return json.loads((APPLICATION_ROOT / 'schema/currency-minor-units.json').read_text()).get(code, 2)


def account_name(kind, label):
    if kind == 'cash' and label.strip().lower() == 'cash':
        return 'Assets:Cash'
    return f'{PARENTS[kind]}:{label.strip()}'


def check_account_name(name):
    parts = name.split(':')
    if parts[0] not in ROOTS or len(parts) < 2 or any(not p.strip() or p != p.strip() for p in parts) \
            or re.search(r'[\r\n;]|  |\t', name):
        raise ValueError(f'Account names look like "Assets:Bank:Example Checking": {name!r} is not one.')


def account_changes(root, *, kind=None, label=None, currency=None, aliases=(), reader=None, identifier=None,
                    account=None, readers=None):
    """The changed accounts document for one new account, validated, not yet written."""
    documents = load(root)
    declared = deepcopy(documents[root / 'data/accounts.json'])
    manifest = read_json(root / 'ledger.json')
    if kind is None:
        if not account or label or currency or aliases or reader or identifier:
            raise ValueError('Give --kind and --label for a balance-sheet account, or only --account for a category.')
        if account.startswith(('Assets:', 'Liabilities:')):
            raise ValueError('Assets and Liabilities accounts need --kind and --label.')
        name, meta = account, {}
    else:
        if kind not in KINDS:
            raise ValueError('--kind must be one of ' + ', '.join(KINDS))
        if not label or not label.strip() or '\n' in label:
            raise ValueError('--label is the name you use for the account, such as "Example Checking".')
        name = account or account_name(kind, label)
        if not name.startswith(('Assets:', 'Liabilities:')):
            raise ValueError('Balance-sheet accounts start with Assets: or Liabilities:.')
        currency = (currency or manifest['defaultCurrency']).strip()
        if kind != 'investment':
            currency = currency.upper()
            if currency not in currencies():
                raise ValueError(f'--currency must be an ISO 4217 code such as USD; {currency!r} is not one.')
        meta = dict(kind=kind, currency=currency, label=label.strip())
        words = [a.strip().lower() for a in aliases if a.strip()]
        if words:
            meta['aliases'] = list(dict.fromkeys(words))
        if identifier and not reader:
            raise ValueError('--identifier needs --reader: the reader that reads this account\'s statements.')
        if reader:
            if kind not in ('bank', 'card'):
                raise ValueError('Statement readers apply to bank and card accounts.')
            known = readers if readers is not None else reader_ids(root)
            if reader not in known:
                raise ValueError(f'Unknown statement reader {reader!r}; known readers: {", ".join(sorted(known))}.')
            meta['statement'] = {'reader': reader, **({'identifier': identifier.strip()} if identifier else {})}
    check_account_name(name)
    if name in declared['accounts']:
        raise ValueError(f'{name} is already declared.')
    if meta and any(existing.startswith(name + ':') or name.startswith(existing + ':')
                    for existing, other in declared['accounts'].items() if other.get('kind')):
        raise ValueError(f'{name} would nest inside or around another balance-sheet account.')
    declared['accounts'][name] = meta
    if meta.get('currency') and meta['currency'] not in declared['commodities']:
        declared['commodities'].append(meta['currency'])
    checked = dict(documents)
    checked[root / 'data/accounts.json'] = declared
    validate(checked, root)
    return {root / 'data/accounts.json': declared}, name


def reader_ids(root):
    from statement_readers import Registry
    return set(Registry(root).readers)


def find_account(accounts, key):
    if key in accounts:
        return key
    matches = [name for name, meta in accounts.items()
               if meta.get('kind') and key.strip().lower() in {(meta.get('label') or '').lower(), *meta.get('aliases', [])}]
    if len(matches) != 1:
        raise ValueError(f'{key!r} matches {"no" if not matches else "more than one"} declared account; use its full name.')
    return matches[0]


def amount_text(value, currency):
    try:
        number = Decimal(value.replace(',', '').strip())
    except InvalidOperation:
        raise ValueError(f'{value!r} is not an amount.') from None
    if not number.is_finite():
        raise ValueError(f'{value!r} is not an amount.')
    places = minor_units(currency)
    if number != number.quantize(Decimal(1).scaleb(-places)):
        raise ValueError(f'{currency} amounts have at most {places} decimal places: {value!r}.')
    return number, format(number.quantize(Decimal(1).scaleb(-max(places, 2))), 'f')


def opening_changes(root, start, balances):
    """Changed documents for opening balances on `start`; validated, not yet written."""
    date.fromisoformat(start)
    documents = deepcopy(load(root))
    accounts_path = root / 'data/accounts.json'
    declared = documents[accounts_path]
    manifest = read_json(root / 'ledger.json')
    if manifest.get('startDate') not in (None, start):
        raise ValueError(f'This ledger already starts on {manifest["startDate"]}; opening balances use that date.')
    used = {e['id'] for items in documents.values() if isinstance(items, list) for e in items}
    opened = {p['account'] for items in documents.values() if isinstance(items, list) for e in items
              if any(q['account'] == OPENING for q in e.get('postings', [])) for p in e['postings']}
    if not balances:
        raise ValueError('Give at least one ACCOUNT=AMOUNT.')
    path = root / 'data/expenses' / f'{start[:4]}.json'
    entries = documents.setdefault(path, [])
    seen = set()
    for item in balances:
        if '=' not in item:
            raise ValueError(f'{item!r}: write ACCOUNT=AMOUNT, for example "Example Checking=2400.00".')
        key, value = item.rsplit('=', 1)
        name = find_account(declared['accounts'], key.strip())
        meta = declared['accounts'][name]
        if not meta.get('kind'):
            raise ValueError(f'{name} is not a balance-sheet account.')
        if name in opened or name in seen:
            raise ValueError(f'{name} already has an opening balance.')
        seen.add(name)
        currency = meta['currency']
        if meta['kind'] == 'investment' and currency not in currencies():
            raise ValueError(f'{name} holds units of {currency}; record its holdings as a purchase with their cost.')
        number, _ = amount_text(value, currency)
        if meta['kind'] in OWED:
            number = -number
        text = amount_text(str(number), currency)[1]
        negated = amount_text(str(-number), currency)[1]
        identity = new_id(used)
        used.add(identity)
        held = {'account': name, 'amount': text, 'commodity': currency, 'assert': {'amount': text, 'commodity': currency}}
        entries.append(dict(id=identity, date=start, status='confirmed', description='Opening balance', postings=[
            held, dict(account=OPENING, amount=negated, commodity=currency)]))
    entries.sort(key=lambda entry: entry['date'])
    if OPENING not in declared['accounts']:
        declared['accounts'][OPENING] = {}
    validate(documents, root)
    manifest['startDate'] = start
    from ledger_manifest import ordered_manifest
    return {accounts_path: declared, path: entries, root / 'ledger.json': ordered_manifest(manifest)}


def apply(root, compute):
    """Compute and write changes under the ledger lock, restoring every file on failure."""
    with ledger_lock(root):
        changes = compute()
        changes, result = changes if isinstance(changes, tuple) else (changes, None)
        before = {path: path.read_bytes() if path.exists() else None for path in changes}
        try:
            for path, value in changes.items():
                dump(path, value)
            load(root)
        except BaseException:
            for path, value in before.items():
                path.unlink(missing_ok=True) if value is None else path.write_bytes(value)
            raise
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=ROOT, help='ledger root (default: LEDGER_DIR)')
    commands = parser.add_subparsers(dest='command', required=True)
    add = commands.add_parser('account', help='declare one account')
    add.add_argument('--kind', choices=KINDS)
    add.add_argument('--label')
    add.add_argument('--currency', help="default: the ledger's main currency")
    add.add_argument('--alias', action='append', default=[], help='a word the person uses for it (repeatable)')
    add.add_argument('--reader', help='statement reader ID (bank and card accounts)')
    add.add_argument('--identifier', help='what the statement prints for this account, such as the last four digits')
    add.add_argument('--account', help='full hledger account name (default: from kind and label)')
    opening = commands.add_parser('opening', help='record opening balances on the start date')
    opening.add_argument('--date', required=True, help='the start date, YYYY-MM-DD (usually today)')
    opening.add_argument('balances', nargs='+', metavar='ACCOUNT=AMOUNT')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == 'account':
            name = apply(root, lambda: account_changes(root, kind=args.kind, label=args.label, currency=args.currency,
                                                        aliases=args.alias, reader=args.reader,
                                                        identifier=args.identifier, account=args.account))
            print(f'Declared {name}. Commit with make commit m="Add account {name}".')
        else:
            apply(root, lambda: opening_changes(root, args.date, args.balances))
            print(f'Recorded {len(args.balances)} opening balance(s) on {args.date} and set startDate. '
                  'Commit with make commit m="Record opening balances".')
    except ValueError as exc:
        raise SystemExit(str(exc))


if __name__ == '__main__':
    main()
