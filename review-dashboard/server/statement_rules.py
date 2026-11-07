"""Conservative statement decisions, learned from the actual store's precedents."""
from collections import Counter
from copy import deepcopy
from datetime import date
from difflib import SequenceMatcher
from decimal import ROUND_FLOOR, Decimal
import re
from reconcile_statement import activity, card_amount, reconcile, select_account
from store import entries, posting


def normal(value):
    return re.sub(r'[^a-z0-9]+', ' ', value.casefold()).strip()


def foreign(entry, currency):
    if entry.get('original-amount'):
        return entry['original-amount']
    totals = {}
    for p in entry['postings']:
        if p['commodity'] != currency and (p.get('cost') or {}).get('commodity') == currency:
            totals[p['commodity']] = totals.get(p['commodity'], Decimal(0)) + Decimal(p['amount'])
    if len(totals) == 1:
        commodity, amount = next(iter(totals.items()))
        return dict(commodity=commodity, amount=str(amount))
    return None


def distance(entry, row):
    return abs((date.fromisoformat(entry['date']) - date.fromisoformat(row['date'])).days)


def scaled_postings(entry, row, account):
    """Copy the known posting proportions, refusing complex or foreign-currency precedents."""
    currency = next(p['commodity'] for p in entry['postings'] if p['account'] == account)
    old = card_amount(entry, account)
    if not old or any(p['commodity'] != currency or p.get('cost') or p.get('assert') or p.get('date') for p in entry['postings']):
        return None
    total = Decimal(row['amount']) * (-1 if row['direction'] == 'Debit' else 1)
    result = []
    allocated = Decimal(0)
    noncard = [p for p in entry['postings'] if p['account'] != account]
    for i, p in enumerate(noncard):
        amount = -total - allocated if i == len(noncard)-1 else (Decimal(p['amount']) * total / old).quantize(Decimal('.01'))
        allocated += amount
        result.append(posting(p['account'], f'{amount:.2f}', currency))
    return result + [posting(account, f'{total:.2f}', currency)]


def plan(documents, statement, root):
    if statement.get('kind') == 'bank':
        from reconcile_bank import bank_plan
        from ledger_manifest import read_manifest
        return bank_plan(documents, statement, root, read_manifest(root).get('startDate'))
    account = select_account(documents, root, None if statement.get('accountAssigned') else statement.get('card_last4'), statement.get('account'))
    currency = documents[root / 'data/accounts.json']['accounts'][account]['currency']
    rows = statement['rows']
    report = reconcile(documents, rows, end=statement['periodEnd'], start=statement['periodStart'], total_due=statement['totalDue'], root=root, account=account, balance_style=False)
    actual = entries(documents)
    by_id = {e['id']: e for e in actual}
    available = [e for e in actual if not (e['statement'] or {}).get('end') and activity(e, account)]
    used, decisions = set(), {}
    buckets = {item['row']['serial']: item for key in ('alreadyMembers', 'serialMatches', 'candidateRows', 'newRows') for item in report[key]}
    def settle(row, rule, why, entry=None, postings=None):
        item = dict(row=row, rule=rule, why=why, entry_id=entry['id'] if entry else None,
                    entry_description=entry['description'] if entry else None, postings=postings, needs_user=False, decision=None, candidates=[])
        if rule == 'foreign_currency' and entry:
            item['conversion'] = dict(old=f'{abs(card_amount(entry, account)):.2f}', billed=row['amount'])
        decisions[row['serial']] = item
        if entry:
            used.add(entry['id'])
    # Reserve serials before any other matches.
    for row in rows:
        bucket = buckets[row['serial']]
        if bucket.get('entryId'):
            if not bucket.get('warnings'):
                settle(row, 'serial', 'The serial and signed account-currency amount match this entry.', by_id[bucket['entryId']])
    # Reserve original-currency matches before the provisional amount can look like
    # an unrelated purchase with a similar provisional amount.
    for row in rows:
        if row['serial'] in decisions or not row.get('foreignAmount'):
            continue
        candidates = [e for e in available if e['id'] not in used and distance(e, row) <= 3 and
                      (f := foreign(e, currency)) and f['commodity'] == row['foreignCurrency'] and abs(Decimal(f['amount'])) == Decimal(row['foreignAmount']) and
                      card_amount(e, account) is not None and (card_amount(e, account) < 0) == (row['direction'] == 'Debit')]
        if len(candidates) == 1:
            settle(row, 'foreign_currency', f"Same {row['foreignAmount']} {row['foreignCurrency']}; the billed {currency} replaces the provisional amount.", candidates[0])
    for row in rows:
        if row['serial'] in decisions:
            continue
        bucket = buckets[row['serial']]
        candidates = [c for c in bucket.get('candidates', []) if c['entryId'] not in used]
        # Widen only the uncertain bucket: equal signed amounts outside the safe
        # three-day window must be explicitly settled, never silently matched.
        if not candidates:
            signed = Decimal(row['amount']) * (-1 if row['direction'] == 'Debit' else 1)
            candidates = [dict(entryId=e['id'], date=e['date'], description=e['description'], dayDistance=distance(e, row),
                               descriptionSimilarity=SequenceMatcher(None, normal(e['description']), normal(row['description'])).ratio())
                          for e in available if e['id'] not in used and card_amount(e, account) == signed]
        # A weak merchant match needs confirmation even on the same day.
        if len(candidates) == 1 and candidates[0]['dayDistance'] <= 3 and candidates[0]['descriptionSimilarity'] >= .2:
            candidate = candidates[0]
            competing = [r for r in rows if r['serial'] not in decisions and r['serial'] != row['serial'] and any(c['entryId'] == candidate['entryId'] for c in buckets[r['serial']].get('candidates', []))]
            if not competing:
                payment = row['direction'] == 'Credit' and 'payment received' in row['description'].casefold()
                settle(row, 'payment' if payment else 'exact', 'Existing bank-to-card payment: same amount within three days.' if payment else 'Unique signed amount and printed date within three days.', by_id[candidate['entryId']])
                continue
        if candidates or bucket.get('warnings'):
            decisions[row['serial']] = dict(row=row, rule='needs_you', why='The amount is close, but the date or merchant needs your confirmation.' if len(candidates)==1 else 'Several entries could match this row. Choose the charge or leave it pending.', entry_id=None, entry_description=None, postings=None, needs_user=True, decision=None, candidates=candidates)
    history = [e for e in actual if ((e['statement'] or {}).get('end') or '9999') < statement['periodEnd'] and activity(e, account)]
    for row in rows:
        if row['serial'] in decisions:
            continue
        n = normal(row['description'])
        precedents = [e for e in history if normal(e['description'].split(' — ')[-1]) == n or normal(e['description']) == n]
        if precedents:
            last = max(precedents, key=lambda e: (e['date'], e['statement']['end']))
            postings = None if row.get('foreignAmount') else scaled_postings(last, row, account)
            if postings:
                settle(row, 'booked_before', f"Same description as {last['date']}: use its accounts.", postings=postings)
                continue
        # Evidenced reversal of a charge on this very statement, including a
        # charge already recorded before the statement arrived.
        opposite = [r for r in rows if r['serial'] != row['serial'] and normal(r['description']) == n and r['amount'] == row['amount'] and r['direction'] != row['direction']]
        pair = next((decisions[r['serial']] for r in opposite if r['serial'] in decisions), None)
        if pair and row['direction'] == 'Credit':
            template = by_id.get(pair['entry_id'])
            postings = scaled_postings(template, row, account) if template else None
            if postings:
                settle(row, 'reversal', 'Reverses the same merchant and amount on this statement, using the charge’s accounts.', postings=postings)
                continue
        merchants = [e for e in history if any(note.startswith('statement merchant: ') and normal(note.removeprefix('statement merchant: ')) == n for note in e.get('notes', []))]
        if merchants:
            last = max(merchants, key=lambda e: (e['date'], e['statement']['end']))
            postings = None if row.get('foreignAmount') else scaled_postings(last, row, account)
            if postings:
                settle(row, 'familiar_merchant', f"Statement merchant seen on {last['date']}; use the same posting proportions.", postings=postings)
                continue
        decisions[row['serial']] = dict(row=row, rule='needs_you', why='No safe precedent. Choose an account, or keep the statement description as pending.', entry_id=None, entry_description=None, postings=None, needs_user=True, decision=None, candidates=[])
    result = [decisions[r['serial']] for r in rows]
    matched = {item['entry_id'] for item in result if item['entry_id']}
    unmatched = [dict(entry_id=e['id'], date=e['date'], description=e['description'], amount=str(card_amount(e, account))) for e in available if e['id'] not in matched and e['date'] <= statement['periodEnd']]
    return dict(items=result, unmatched=unmatched, counts=dict(Counter(i['rule'] for i in result)))


def billed_shares(amounts, billed):
    """The billed total split across foreign postings in proportion to their signed amounts.

    Shares are rounded to the billed amount's places with the largest-remainder method,
    so they net to the billed amount exactly (with the sign of the postings' net); each
    keeps its posting's sign. A share that would round to zero or flip sign refuses.
    """
    net = sum(amounts, Decimal(0))
    if net == 0 or billed <= 0:
        raise ValueError('The billed amount can\'t be split across postings that net to zero. Use Edit entry.')
    step = Decimal(1).scaleb(min(billed.as_tuple().exponent, -2))
    exact = [amount * billed / abs(net) / step for amount in amounts]
    floors = [value.to_integral_value(rounding=ROUND_FLOOR) for value in exact]
    target = (billed / step) * (1 if net > 0 else -1)
    missing = int(target - sum(floors, Decimal(0)))
    order = sorted(range(len(exact)), key=lambda index: (-(exact[index] - floors[index]), index))
    for index in order[:missing]:
        floors[index] += 1
    shares = [units * step for units in floors]
    if sum(shares, Decimal(0)) != target * step or any(share == 0 or (share > 0) != (amount > 0) for share, amount in zip(shares, amounts)):
        raise ValueError('The billed amount can\'t be split across these postings exactly. Use Edit entry.')
    return shares


def convert(entry, row, account):
    currency = next(p['commodity'] for p in entry['postings'] if p['account'] == account)
    old = card_amount(entry, account)
    original = foreign(entry, currency)
    if original is None:
        raise ValueError('Original currency is missing')
    foreign_posts = [p for p in entry['postings'] if p['commodity'] == row['foreignCurrency']]
    if abs(sum((Decimal(p['amount']) for p in foreign_posts), Decimal(0))) != Decimal(row['foreignAmount']):
        raise ValueError('Foreign allocation changed since review')
    shares = billed_shares([Decimal(p['amount']) for p in foreign_posts], Decimal(row['amount']))
    net = sum((Decimal(p['amount']) for p in foreign_posts), Decimal(0))
    if (net > 0) != (row['direction'] == 'Debit'):
        raise ValueError('The billed amount can\'t balance these postings: the card charge and the foreign amount point opposite ways. Use Edit entry.')
    for p, share in zip(foreign_posts, shares):
        # A total cost is positive; hledger applies the posting's sign.
        p['cost'] = dict(amount=format(abs(share), 'f'), commodity=currency, per='total')
    for p in entry['postings']:
        if p['account'] == account:
            p['amount'] = '-' + row['amount'] if row['direction'] == 'Debit' else row['amount']
    entry['notes'].append(f"statement charge: {row['amount']} {currency} for {row['foreignAmount']} {row['foreignCurrency']}; replaces {abs(old):.2f} {currency} prior card posting")
