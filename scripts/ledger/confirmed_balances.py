"""Structured partial-balance failures and ordered correction boundaries.

Balances come from exact store postings, never hledger's diagnostic prose. The
strict hledger check remains authoritative; its diagnostic accompanies a refusal.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from store import entries


class BalanceRefusal(ValueError):
    def __init__(self, refusal):
        self.refusal = refusal
        super().__init__(refusal['detail'] if refusal.get('restriction') else 'Not saved: a confirmed balance would change')


def assertion_info(documents, root, entry, posting):
    account = posting['account']
    label = documents[root/'data/accounts.json']['accounts'].get(account, {}).get('label') or account.split(':')[-1]
    statement = next((s for s in documents[root/'data/card-statements.json'] if s.get('assertion-id') == entry['id'] and s['account'] == account), None)
    return dict(account=account, account_label=label, date=entry['date'], entry_id=entry['id'],
                source=dict(kind='statement' if statement else 'balance', label=label,
                            date=entry['date'], **({'path': statement['source']} if statement else {})))


def failures(documents, root, *, candidate=None, detail=''):
    rows = entries(documents)
    balances, previous = {}, {}
    for index, entry in enumerate(rows):
        for posting in entry['postings']:
            key = (posting['account'], posting['commodity'])
            balances[key] = balances.get(key, Decimal(0)) + Decimal(posting['amount'])
            assertion = posting.get('assert')
            if not assertion:
                continue
            key = (posting['account'], assertion['commodity'])
            expected = Decimal(assertion['amount'])
            actual = balances.get(key, Decimal(0))
            start = previous.get(key, -1)
            if actual != expected:
                duplicates = []
                if candidate:
                    statement = next((s for s in documents[root/'data/card-statements.json'] if s.get('assertion-id') == entry['id']), None)
                    amounts = {abs(Decimal(p['amount'])) for p in candidate['postings'] if (p['account'], p['commodity']) == key}
                    day = date.fromisoformat(candidate['date'])
                    for other in rows[start+1:index+1]:
                        if other['id'] in (candidate.get('id'), entry['id']) or not any((p['account'], p['commodity']) == key and abs(Decimal(p['amount'])) in amounts for p in other['postings']):
                            continue
                        member = statement and ((other['statement'] or {}).get('end') == statement['end'] or any(m.get('entry-id') == other['id'] for m in statement.get('members', [])))
                        if (not statement or other['date'] >= statement['start'] or member) and abs((date.fromisoformat(other['date'])-day).days) <= 3:
                            duplicates.append(dict(entry_id=other['id'], date=other['date'], description=other['description'],
                                                   amount=next(p['amount'] for p in other['postings'] if (p['account'], p['commodity']) == key and abs(Decimal(p['amount'])) in amounts), commodity=key[1]))
                yield dict(kind='confirmed_balance', **assertion_info(documents, root, entry, posting),
                           commodity=key[1], asserted=format(expected, 'f'), calculated=format(actual, 'f'),
                           difference=format(actual-expected, 'f'), possible_duplicates=duplicates, detail=detail)
            previous[key] = index


def raise_failure(documents, root, *, candidate=None, detail='', problems=()):
    """Raise the first failing assertion as a BalanceRefusal; ``problems`` lists what else the save would break."""
    failure = next(failures(documents, root, candidate=candidate, detail=detail), None)
    if failure:
        raise BalanceRefusal({**failure, 'other_problems': list(problems)})


def correction_locks(documents, root, entry):
    rows = entries(documents)
    index = next(i for i, row in enumerate(rows) if row['id'] == entry['id'])
    locks = []
    for number, posting in enumerate(entry['postings']):
        key = (posting['account'], posting['commodity'])
        metadata = documents[root/'data/accounts.json']['accounts'].get(key[0], {})
        if metadata.get('kind') == 'card' and ((entry['statement'] or {}).get('end') or any(s.get('assertion-id') == entry['id'] or any(m.get('entry-id') == entry['id'] for m in s.get('members', [])) for s in documents[root/'data/card-statements.json'])):
            continue  # Filed cards retain their existing membership lock and wording.
        assertions = [(i, row, p) for i, row in enumerate(rows) for p in row['postings']
                      if p['account'] == key[0] and (p.get('assert') or {}).get('commodity') == key[1]]
        closing = next((a for a in assertions if a[0] >= index), None)
        if not closing:
            continue
        previous = next((a for a in reversed(assertions) if a[0] < index), None)
        # The previous assertion day is permitted only after its assertion, and
        # the closing day only before its assertion. Check ordered IDs on save.
        locks.append(dict(posting=number, **assertion_info(documents, root, closing[1], closing[2]),
                          start=previous[1]['date'] if previous else None,
                          previous_id=previous[1]['id'] if previous else None, end=closing[1]['date']))
    return locks


def refuse_lock(before, after, documents, root, lock, restriction, detail):
    # Compute the hypothetical running balance even when a locked edit is itself
    # balanced. Some edits (costs, or moving both sides together) preserve the
    # numeric assertion but must still respect its correction boundary.
    proposed = deepcopy(documents)
    for path, rows in proposed.items():
        if path.parent.name == 'expenses':
            rows[:] = [e for e in rows if e['id'] != before['id']]
    path = root/'data/expenses'/f"{after['date'][:4]}.json"
    rows = proposed.setdefault(path, [])
    index = next((i for i, e in enumerate(rows) if e['date'] > after['date']), len(rows))
    rows.insert(index, after)
    refusal = next((f for f in failures(proposed, root, candidate=after, detail=detail) if f['account'] == lock['account']), None)
    if refusal is None:
        closing = next(e for e in entries(documents) if e['id'] == lock['entry_id'])
        p = next(p for p in closing['postings'] if p['account'] == lock['account'] and p.get('assert'))
        expected = p['assert']['amount']
        refusal = dict(kind='confirmed_balance', **assertion_info(documents,root,closing,p),
                       commodity=p['assert']['commodity'],asserted=expected,calculated=expected,
                       difference='0',possible_duplicates=[],detail=detail)
    refusal['restriction'] = restriction
    raise BalanceRefusal(refusal)


def enforce_locks(before, after, documents, root):
    locks = correction_locks(documents, root, before)
    for lock in locks:
        index = lock['posting']
        old = before['postings'][index]
        new = after['postings'][index] if len(after['postings']) > index else {}
        if any(old.get(k) != new.get(k) for k in ('account', 'amount', 'commodity', 'cost', 'assert')):
            refuse_lock(before,after,documents,root,lock,'locked_posting','Amount and paying account are locked by a confirmed balance; reopen the correction form.')
        if after['date'] > lock['end'] or lock['start'] and after['date'] < lock['start']:
            refuse_lock(before,after,documents,root,lock,'date_window','The date must stay between the neighbouring confirmed balances.')
    return locks


def enforce_order(before, after, documents, root, locks):
    order = {row['id']: i for i, row in enumerate(entries(documents))}
    for lock in locks:
        if order[after['id']] > order[lock['entry_id']] or lock['previous_id'] and order[after['id']] <= order[lock['previous_id']]:
            refuse_lock(before,after,documents,root,lock,'same_day_order','The date must keep this entry between its neighbouring confirmed balances, including same-day order.')
