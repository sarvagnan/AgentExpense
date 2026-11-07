"""Account-currency changes relabel opening balances, never convert transactions."""
from decimal import Decimal
from copy import deepcopy
import re
from store import commodities, commodity_directive, load, read_json


def check_code(code):
    if not isinstance(code, str) or not re.fullmatch(r'[A-Z]{3}', code):
        raise ValueError('Currency must be a three-letter uppercase code')


def currency_changes(root, code, account=None):
    check_code(code)
    documents = deepcopy(load(root))
    manifest = read_json(root / 'ledger.json')
    if account is None:
        manifest['defaultCurrency'] = code
        return {root / 'ledger.json': manifest}
    declared = documents[root / 'data/accounts.json']
    metadata = declared['accounts'].get(account)
    if not metadata or not metadata.get('currency'):
        raise ValueError('Choose a declared balance-sheet account')
    old = metadata['currency']
    changed = {}
    for path, rows in documents.items():
        if path.name not in ('forecast.json',) and path.parent.name != 'expenses':
            continue
        for entry in rows:
            postings = [p for p in entry['postings'] if p['account'] == account]
            if not postings:
                continue
            opening = path.parent.name == 'expenses' and (
                any(p['account'] == 'Equity:Opening Balances' for p in entry['postings'])
                or (entry['date'] == manifest.get('startDate') and all(p.get('assert') and p['amount'].strip('-0.') == '' for p in entry['postings'])))
            if not opening:
                raise ValueError(f"{account}: currency is fixed by transaction {entry['id']} on {entry['date']}")
            equity = [p for p in entry['postings'] if p['account'] == 'Equity:Opening Balances' and p['commodity'] == old and not p.get('cost')]
            delta = Decimal(0)
            for posting in postings:
                if posting['commodity'] == old:
                    delta += Decimal(posting['amount'])
                    posting['commodity'] = code
                elif (posting.get('cost') or {}).get('commodity') == old:
                    cost = posting['cost']
                    quantity = Decimal(posting['amount'])
                    value = abs(Decimal(cost['amount'])) * (abs(quantity) if cost['per'] == 'unit' else 1)
                    delta += value * (-1 if quantity < 0 else 1)
                    cost['commodity'] = code
                if (posting.get('assert') or {}).get('commodity') == old:
                    posting['assert']['commodity'] = code
            if delta:
                if not equity:
                    raise ValueError(f"{account}: opening balance has no matching equity currency {old}")
                # A shared opening entry may also open other accounts. Split only
                # this account's equity counterpart; keep every other amount/unit.
                remainder = Decimal(equity[0]['amount']) + delta
                if remainder == 0 and not equity[0].get('assert'):
                    equity[0]['commodity'] = code
                else:
                    equity[0]['amount'] = format(remainder, '.2f')
                    entry['postings'].append({'account': 'Equity:Opening Balances', 'amount': format(-delta, '.2f'), 'commodity': code,
                                              'cost': None, 'assert': None, 'notes': []})
            changed[path] = rows
    if any(s['account'] == account for s in documents[root / 'data/card-statements.json']):
        raise ValueError(f'{account}: currency is fixed by a filed statement')
    metadata['currency'] = code
    if code not in commodities(declared):
        declared['commodities'].append(commodity_directive(code))
    return {root / 'data/accounts.json': declared, **changed}
