"""Check balance-style summaries, explicit membership, overlap and assertions."""
from decimal import Decimal
from statement_readers import signed, card_debt, check_row_date, continuity_entries
from ledger_manifest import read_manifest
from reconcile_statement import card_amount
from reconcile_bank import possible_overlap
from store import entries


def check_bank_statements(documents, root):
    actual = {e['id']: e for e in entries(documents)}
    declarations = documents[root/'data/accounts.json']['accounts']
    start_date = read_manifest(root).get('startDate')
    keys = [(s['account'],s['end']) for s in documents[root/'data/card-statements.json']]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate statement for the same account and period end')
    results, previous, seen = [], {}, []
    for summary in documents[root/'data/card-statements.json']:
        if summary.get('kind') != 'bank':
            previous[summary['account']] = dict(end=summary['end'], **{'closing-balance':str(-Decimal(summary['total-due']))})
            continue
        account, start, end = summary['account'], summary['start'], summary['end']
        if declarations.get(account,{}).get('kind') not in ('bank','card'):
            raise ValueError('Balance-style statement needs a declared bank or card account')
        currency = declarations[account]['currency']
        members = summary['members']
        debt = card_debt(summary)
        if debt and declarations[account]['kind'] != 'card':
            raise ValueError('Debt-style statement needs a card account')
        if len(members) != summary['rows']:
            raise ValueError('Statement membership row count differs')
        used, serials, last = set(), set(), start
        prior = previous.get(account)
        for row in members:
            check_row_date(row, start, end, debt=debt)
            if not debt and row['date'] < last:
                raise ValueError('Statement membership is outside its period or out of date order')
            last = row['date']
            if row['serial'] in serials:
                raise ValueError('Duplicate statement row serial')
            serials.add(row['serial'])
            if row['disposition'] == 'pre-start':
                if not start_date or row['date'] >= start_date or 'entry-id' in row:
                    raise ValueError('Pre-start membership must precede startDate and have no entry')
                continue
            if start_date and row['date'] < start_date:
                raise ValueError('Pre-start rows cannot be applied')
            if row['disposition'] == 'overlap-pending':
                if debt:
                    raise ValueError('Billed card rows cannot be overlapping membership')
                candidates = row.get('review-candidates', [])
                if not candidates or not prior:
                    raise ValueError('Unresolved overlap must identify previous members')
                for identity in candidates:
                    if identity not in actual or not any(m.get('entry-id') == identity and possible_overlap(row,m) for old in seen if old['account']==account for m in old['members']):
                        raise ValueError('Overlap review candidate is not an identical earlier statement row')
                continue
            identity = row.get('entry-id')
            if identity not in actual or identity in used:
                raise ValueError('Missing or repeated statement member entry')
            used.add(identity)
            entry = actual[identity]
            if debt and (entry.get('statement-end') or any(m.get('entry-id') == identity for old in seen if old['account']==account for m in old['members'])):
                raise ValueError('Billed card entry is already a member of another statement')
            if card_amount(entry, account) != signed(row):
                raise ValueError('Statement member amount/direction differs')
            if summary['source'] not in entry.get('sources',[]):
                raise ValueError('Statement member is missing its source')
            if row['disposition'] == 'already-in-ledger' and not any(m.get('entry-id') == identity and possible_overlap(row,m) for old in seen if old['account']==account for m in old['members']):
                raise ValueError('Already-in-ledger row must match earlier membership')
        debits = sum((Decimal(r['amount']) for r in members if r['direction']=='Debit'), Decimal(0))
        credits = sum((Decimal(r['amount']) for r in members if r['direction']=='Credit'), Decimal(0))
        if debits != Decimal(summary['debits']) or credits != Decimal(summary['credits']):
            raise ValueError('Statement debit/credit totals differ from membership')
        opening, closing = Decimal(summary['opening-balance']), Decimal(summary['closing-balance'])
        balance = opening
        for row in members:
            balance += signed(row)
            if row.get('balance') is not None and balance != Decimal(row['balance']):
                raise ValueError('Statement running balance differs at ' + row['serial'])
        if balance != closing:
            raise ValueError('Statement opening − debits + credits differs from closing balance')
        if prior:
            overlap = sum((signed(r) for r in members if r['date'] <= prior['end']), Decimal(0)) if not debt else Decimal(0)
            if opening + overlap != Decimal(prior['closing-balance']):
                raise ValueError('Broken statement closing-balance continuity')
        else:
            _, before = continuity_entries(actual.values(), summary | {'periodStart':start}, start_date)
            anchor = sum((Decimal(p['amount']) for e in before for p in e['postings'] if p['account']==account), Decimal(0))
            overlap = sum((signed(r) for r in members if start_date and r['date'] < start_date), Decimal(0))
            if opening + overlap != anchor:
                raise ValueError('Statement does not continue from ledger opening balance')
        assertion = actual.get(summary.get('assertion-id'))
        expected = dict(account=account,amount='0.00',commodity=currency,**{'assert':dict(amount=summary['closing-balance'],commodity=currency)})
        if not debt and (not assertion or assertion['date'] != end or assertion['postings'] != [expected] or summary['source'] not in assertion.get('sources',[])):
            raise ValueError('Statement closing balance assertion is missing or differs')
        previous[account] = summary
        seen.append(summary)
        results.append(dict(account=account,start=start,end=end,rows=len(members),debits=str(debits),credits=str(credits),closingBalance=str(closing),result='PASS'))
    return results
