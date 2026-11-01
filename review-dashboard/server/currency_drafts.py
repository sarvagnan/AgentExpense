"""Resolve an agent's incomplete currency proposal without giving it write access."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import re
from settings import APPLICATION
from store import load


def positive(value):
    if not isinstance(value, str) or len(value)>100 or not re.fullmatch(r'(0|[1-9][0-9]*)(\.[0-9]+)?', value):
        raise ValueError('Enter a positive decimal amount or rate')
    number = Decimal(value)
    if not number.is_finite() or number <= 0 or number > Decimal('1e12'):
        raise ValueError('Enter a positive decimal amount or rate')
    return number


def currency_request(answer, root):
    entry = answer.get('entry')
    if answer.get('status') != 'question' or not entry or len(entry['postings']) != 2:
        return None
    accounts = load(root)[root / 'data/accounts.json']['accounts']
    postings = entry['postings']
    try:
        if any(not Decimal(p['amount']).is_finite() for p in postings):
            return None
    except (InvalidOperation, TypeError, ValueError, KeyError):
        return None
    expense = next((p for p in postings if p['account'].startswith('Expenses:')), None)
    if expense:
        target = next(p for p in postings if p is not expense)
        meta = accounts.get(target['account'], {})
        source, kind = expense, 'expense'
        source_currency, target_currency = source['commodity'], meta.get('currency')
        if source.get('cost') or Decimal(source['amount']) <= 0:
            return None
    else:
        source = next((p for p in postings if Decimal(p['amount']) < 0), None)
        if not source:
            return None
        target = next(p for p in postings if p is not source)
        source_currency = accounts.get(source['account'], {}).get('currency')
        target_currency = accounts.get(target['account'], {}).get('currency')
        kind = 'transfer'
    # Only the prompt's explicit unknown-amount marker selects this form. A
    # question about an otherwise priced entry still needs its ordinary answer.
    if (Decimal(target['amount']) != 0 or source.get('cost') or target.get('cost')
            or target['commodity'] != target_currency
            or kind == 'transfer' and source['commodity'] != source_currency):
        return None
    supported = json.loads((APPLICATION / 'schema/currencies.json').read_text())
    if source_currency not in supported or target_currency not in supported or source_currency == target_currency:
        return None
    try:
        original = positive(format(abs(Decimal(source['amount'])), 'f'))
    except (ValueError, InvalidOperation):
        return None
    return dict(kind=kind, amount=str(original), sourceCurrency=source_currency, currency=target_currency,
                sourceAccount=source['account'], account=target['account'],
                accountLabel=accounts[target['account']].get('label') or target['account'].split(':')[-1],
                sourceLabel=accounts.get(source['account'], {}).get('label') or source['account'].split(':')[-1])


def resolve(draft, root, amount=None, rate=None, estimate=True):
    request = draft.get('currency_request')
    if not request or not draft.get('currency_proposal'):
        raise ValueError('This draft does not need a currency amount')
    if currency_request(draft['currency_proposal'], root) != request:
        raise ValueError('The account currencies changed. Answer the question again before saving.')
    if (amount is None) == (rate is None):
        raise ValueError('Enter either an amount or a rate')
    # Kept in sync with the browser currency formatter by the currency tests.
    digits = json.loads((APPLICATION / 'schema/currency-minor-units.json').read_text())[request['currency']]
    quantum = Decimal(1).scaleb(-digits)
    charged = (positive(amount) if amount is not None else positive(rate) * positive(request['amount'])).quantize(quantum, rounding=ROUND_HALF_UP)
    positive(str(charged))
    answer = deepcopy(draft['currency_proposal'])
    answer.update(status='ready', question=None)
    entry = answer['entry']
    source = next(p for p in entry['postings'] if p['account'] == request['sourceAccount'])
    target = next(p for p in entry['postings'] if p['account'] == request['account'])
    if request['kind'] == 'expense':
        source['cost'] = dict(amount=str(charged), commodity=request['currency'])
        target.update(amount=str(-charged), commodity=request['currency'], cost=None)
        if estimate:
            entry['notes'].append('Currency estimate: statement will replace with the amount actually charged.')
    else:
        source['cost'] = dict(amount=str(charged), commodity=request['currency'])
        target.update(amount=str(charged), commodity=request['currency'], cost=None)
    draft['questions'][-1].update(answer=f"{charged} {request['currency']} {'received' if request['kind']=='transfer' else 'charged'}" + (' (estimate)' if estimate and request['kind']=='expense' else ''))
    draft.pop('currency_request', None)
    draft.pop('currency_proposal', None)
    draft['proposal'] = answer
