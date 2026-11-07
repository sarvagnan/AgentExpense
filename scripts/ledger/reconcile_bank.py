"""Balance-style statement proposals and persistent overlap identity, with no writes."""
from collections import Counter
from decimal import Decimal
from difflib import SequenceMatcher
from datetime import date
import re
from statement_readers import signed, money_text, card_debt
from store import entries, posting
from reconcile_statement import activity, card_amount


def same_row(one, two):
    if any(one.get(k) != two.get(k) for k in ('date', 'direction', 'description')) or Decimal(one['amount']) != Decimal(two['amount']):
        return False
    if one.get('reference') != two.get('reference'):
        return False
    if one.get('balance') is None or two.get('balance') is None:
        return one.get('balance') == two.get('balance')
    return Decimal(one['balance']) == Decimal(two['balance'])


def possible_overlap(one, two):
    if any(one.get(k) != two.get(k) for k in ('date','direction','description')) or Decimal(one['amount']) != Decimal(two['amount']):
        return False
    if one.get('reference') and two.get('reference') and one['reference'] != two['reference']:
        return False
    if one.get('balance') is not None and two.get('balance') is not None and Decimal(one['balance']) != Decimal(two['balance']):
        return False
    return True


def identifiers(entry):
    """The printed reference and statement serial an entry carries (None when absent)."""
    return ((entry['references'] or {}).get('reference'), (entry['statement'] or {}).get('serial'))


def bank_plan(documents, statement, root, start_date=None):
    account, rows = statement['account'], statement['rows']
    actual = {e['id']: e for e in entries(documents)}
    summaries = [s for s in documents[root/'data/card-statements.json'] if s['account'] == account]
    previous = [m for s in summaries for m in s.get('members', []) if m.get('entry-id')]
    current = {m['serial']:m for s in summaries if s['end'] == statement['periodEnd'] for m in s.get('members', []) if m.get('entry-id')}
    available = [e for e in actual.values() if activity(e, account)]
    filed_ids = {m['entry-id'] for m in previous}
    currency = documents[root/'data/accounts.json']['accounts'][account]['currency']
    used, result = set(), []
    ordered = sorted(rows, key=lambda r: (not (r.get('reference') and any(r['reference'] in identifiers(e) for e in available)), not bool(r.get('foreignAmount'))))
    for row in ordered:
        item = dict(row=row, rule='needs_you', why='No safe precedent. Choose an account, or keep this row pending for Review.', entry_id=None, entry_description=None, postings=None, needs_user=True, decision=None, candidates=[])
        member = current.get(row['serial'])
        if member and same_row(row, member) and card_amount(actual[member['entry-id']], account) == signed(row):
            identity = member['entry-id']
            item.update(rule='serial', why='Member of this filed statement.', entry_id=identity, entry_description=actual[identity]['description'], needs_user=False)
            used.add(identity)
            result.append(item)
            continue
        if start_date and row['date'] < start_date:
            item.update(rule='pre_start', why='Covered by opening balances before the ledger start date.', needs_user=False)
            result.append(item)
            continue
        overlaps = {m['entry-id'] for m in previous if possible_overlap(row, m)} if not card_debt(statement) else set()
        exact_overlaps = {m['entry-id'] for m in previous if same_row(row, m)} if not card_debt(statement) else set()
        distinct = bool(row.get('reference') or row.get('balance') is not None)
        if len(overlaps) == 1 and overlaps == exact_overlaps and distinct and next(iter(overlaps)) not in used:
            identity = next(iter(overlaps));entry = actual[identity]
            item.update(rule='already_in_ledger', why='Already in the ledger from an earlier statement.', entry_id=identity, entry_description=entry['description'], needs_user=False)
            used.add(identity)
            result.append(item)
            continue
        if not overlaps and row.get('foreignAmount'):
            foreign_matches = []
            for e in available:
                if e['id'] in used or e['id'] in filed_ids or (e['statement'] or {}).get('end') or abs((date.fromisoformat(e['date'])-date.fromisoformat(row['date'])).days) > 3 or (card_amount(e,account) < 0) != (row['direction']=='Debit'):
                    continue
                original = e.get('original-amount')
                if original is None:
                    totals = {}
                    for p in e['postings']:
                        if p['commodity'] != currency and (p.get('cost') or {}).get('commodity') == currency:
                            totals[p['commodity']] = totals.get(p['commodity'],Decimal(0))+Decimal(p['amount'])
                    if len(totals)==1:
                        code,value = next(iter(totals.items()))
                        original = dict(commodity=code,amount=str(abs(value)))
                if original and original['commodity']==row['foreignCurrency'] and abs(Decimal(original['amount']))==Decimal(row['foreignAmount']):
                    foreign_matches.append(e)
            if len(foreign_matches)==1:
                entry=foreign_matches[0]
                item.update(rule='foreign_currency',why='Same original-currency amount; explicitly confirm the billed amount that replaces the estimate.',entry_id=entry['id'],entry_description=entry['description'],conversion=dict(old=money_text(abs(card_amount(entry,account))),billed=row['amount']))
                used.add(entry['id']);result.append(item)
                continue
        candidates = []
        for e in available:
            if e['id'] in used or card_amount(e, account) != signed(row):
                continue
            if card_debt(statement) and (e['id'] in filed_ids or (e['statement'] or {}).get('end')):
                continue
            distance = abs((date.fromisoformat(e['date']) - date.fromisoformat(row['date'])).days)
            reference = row.get('reference') and row['reference'] in identifiers(e)
            if e['id'] in filed_ids and e['id'] not in overlaps and not reference:
                continue
            if distance > 31 and not reference and e['id'] not in overlaps:
                continue
            similarity = SequenceMatcher(None, e['description'].casefold(), row['description'].casefold()).ratio()
            candidates.append(dict(entryId=e['id'], date=e['date'], description=e['description'], dayDistance=distance, descriptionSimilarity=similarity, reference=bool(reference)))
        candidates.sort(key=lambda c: (not c['reference'], c['dayDistance'], -c['descriptionSimilarity'], c['entryId']))
        if overlaps:
            item.update(rule='overlap', why='Identical overlapping rows have no unique reference or running balance. Confirm the membership, or leave it for Review.', duplicate_reason='overlapping-statements')
            candidates = [c for c in candidates if c['entryId'] in overlaps]
        else:
            references = [c for c in candidates if c['reference']]
            safe = references or [c for c in candidates if c['dayDistance'] <= 3 and c['descriptionSimilarity'] >= .2]
            competing = not references and any(other is not row and signed(other) == signed(row) and other['date'] == row['date'] for other in rows)
            if len(safe) == 1 and not competing:
                candidate = safe[0]
                item.update(rule='serial' if candidate['reference'] else 'exact', why='Reference matches this entry.' if candidate['reference'] else 'Unique signed amount, description and date within three days.', entry_id=candidate['entryId'], entry_description=candidate['description'], needs_user=False)
                used.add(candidate['entryId'])
            elif candidates and start_date:
                item['duplicate_reason'] = 'imported-after-start'
        item['candidates'] = candidates
        if not candidates and item['needs_user'] and not row.get('foreignAmount'):
            normal = lambda value: re.sub(r'[\W_]+', ' ', value.casefold()).strip()
            precedent = [e for e in available if normal(row['description']) and e['status']=='confirmed' and e['date'] < row['date'] and normal(e['description']) == normal(row['description'])]
            if precedent:
                template = max(precedent,key=lambda e:e['date'])
                postings = None
                if len(template['postings']) == 2 and all(p['commodity'] == documents[root/'data/accounts.json']['accounts'][account]['currency'] and not p['cost'] and not p['assert'] for p in template['postings']):
                    other = next(p for p in template['postings'] if p['account'] != account)
                    currency = other['commodity']
                    postings = [posting(other['account'],money_text(-signed(row)),currency),posting(account,money_text(signed(row)),currency)]
                if postings:
                    item.update(rule='booked_before',why='Same description as a confirmed earlier entry; use its accounts.',postings=postings,needs_user=False)
        result.append(item)
    result.sort(key=lambda item: next(i for i,r in enumerate(rows) if r['serial']==item['row']['serial']))
    # A card's earlier billed purchases are settled by their own statement,
    # whatever their dates; only unbilled entries can be missing from this one.
    billed = filed_ids | {e['id'] for e in available if (e['statement'] or {}).get('end')} if card_debt(statement) else set()
    return dict(items=result, unmatched=[dict(entry_id=e['id'],date=e['date'],description=e['description'],amount=str(card_amount(e, account))) for e in available if e['id'] not in used and e['id'] not in billed and e['date'] <= statement['periodEnd']], counts=dict(Counter(i['rule'] for i in result)))
