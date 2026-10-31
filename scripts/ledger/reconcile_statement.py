"""Read-only card-row reconciliation proposals; never mutate the store or evidence."""
from __future__ import annotations

import argparse
from datetime import date, datetime
from decimal import Decimal
from difflib import SequenceMatcher
import json
from pathlib import Path
import re

from store import ROOT, entries, load, read_json

CARD = 'Liabilities:Credit Cards:Rewards Card'
DECIMAL = re.compile(r'^(0|[1-9][0-9]*)(\.[0-9]+)?$')


def validate_rows(rows: list) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError('Rows must be a JSON array')
    serials = set()
    for row in rows:
        if not isinstance(row['date'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['date']):
            raise ValueError('Row date must be YYYY-MM-DD')
        date.fromisoformat(row['date'])
        if not isinstance(row['serial'], str) or not row['serial'] or row['serial'] in serials:
            raise ValueError('Missing or duplicate row serial')
        serials.add(row['serial'])
        if not isinstance(row['description'], str) or not row['description']:
            raise ValueError('Missing row description')
        if not isinstance(row['amount'], str) or not DECIMAL.fullmatch(row['amount']):
            raise ValueError('Row amount must be a nonnegative decimal string')
        if row['direction'] not in ('Debit', 'Credit'):
            raise ValueError('Row direction must be Debit or Credit')
        if type(row['page']) is not int or row['page'] < 1:
            raise ValueError('Row page must be a positive integer')
    return rows


def extract_rows(root: Path, end: str) -> tuple[list[dict], dict]:
    filed = root / f"data/statements/{datetime.fromisoformat(end).strftime('%B%Y')}-card-reconciliation.json"
    if filed.exists():
        data = read_json(filed)
        return validate_rows(data['rows']), dict(source=data['source'], start=data['periodStart'])
    data = read_json(root / 'data/statements/extracted.json')
    statements = [s for s in data['statements'] if s['periodEnd'][:10] == end]
    if len(statements) != 1:
        raise ValueError(f'Expected one extracted statement for {end}, found {len(statements)}')
    statement = statements[0]
    # Accept both decimal strings and older numeric extracts without float arithmetic.
    rows = [dict(date=r['date'][:10], serial=r['serialNumber'], description=r['description'],
                 amount=format(Decimal(str(r['amountInr'])), '.2f'), direction=r['direction'], page=r['pageNumber'])
            for r in data['transactions'] if r['statementEnd'][:10] == end and r['sourceFile'] == statement['sourceFile']]
    return validate_rows(rows), dict(source=statement['sourceFile'], start=statement['periodStart'][:10])


def card_amount(entry: dict, account: str) -> Decimal | None:
    postings = [p for p in entry['postings'] if p['account'] == account]
    if not postings:
        return None
    if len(postings) != 1 or postings[0]['commodity'] != 'INR':
        raise ValueError(f"{entry['id']}: reconciliation needs exactly one INR card posting")
    return Decimal(postings[0]['amount'])


def activity(entry: dict, account: str) -> bool:
    return (any(p['account'] == account for p in entry['postings'])
            and not any(p['account'].startswith('Equity:Opening Balances') for p in entry['postings'])
            and not all(Decimal(p['amount']) == 0 and 'assert' in p for p in entry['postings']))


def reconcile(documents: dict, rows: list, *, end: str, start: str, account: str = CARD,
              total_due: str | None = None, source: str | None = None, root: Path = ROOT) -> dict:
    validate_rows(rows)
    date.fromisoformat(start)
    date.fromisoformat(end)
    if start > end:
        raise ValueError('Statement start is after end')
    if total_due is not None and not DECIMAL.fullmatch(total_due):
        raise ValueError('Total due must be a nonnegative decimal string')
    actuals = entries(documents)
    statements = [s for s in documents[root / 'data/card-statements.json'] if s['account'] == account]
    filed = next((s for s in statements if s['end'] == end), None)
    source = source or (filed['source'] if filed else None)
    previous_statements = [s for s in statements if s['end'] < end]
    if previous_statements:
        previous = Decimal(max(previous_statements, key=lambda s: s['end'])['total-due'])
    else:
        openings = [e for e in actuals if e['date'] <= start and any(p['account'].startswith('Equity:Opening Balances') for p in e['postings']) and any(p['account'] == account for p in e['postings'])]
        if len(openings) != 1:
            raise ValueError('No unique previous statement or opening balance for draft')
        previous = -card_amount(openings[0], account)
    serials = {}
    for entry in actuals:
        if entry.get('serial-number'):
            serials.setdefault(entry['serial-number'], []).append(entry)
    unbilled = [e for e in actuals if 'statement-end' not in e and activity(e, account)]
    matched_ids = set()
    serial_ids = set()
    result = dict(alreadyMembers=[], serialMatches=[], candidateRows=[], newRows=[], warnings=[])
    rest = []
    for row in rows:
        signed = Decimal(row['amount']) * (-1 if row['direction'] == 'Debit' else 1)
        matches = [e for e in serials.get(row['serial'], []) if activity(e, account)]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous store serial: {row['serial']}")
        fields = {'statement-end': end, 'serial-number': row['serial'], 'statement-page': row['page']}
        if not matches:
            rest.append((row, signed, fields))
            continue
        entry = matches[0]
        matched_ids.add(entry['id'])
        serial_ids.add(entry['id'])
        proposal = dict(row=row, entryId=entry['id'], fields=fields, statementSource=source)
        problems = []
        if card_amount(entry, account) != signed:
            problems.append('Serial matches but amount/direction differs; do not apply membership')
        if entry.get('statement-end') == end:
            if entry.get('statement-page') != row['page']:
                problems.append('Stored statement-page differs from extracted row')
            if source and source not in entry.get('sources', []):
                problems.append('Stored member is missing the statement PDF source')
            bucket = 'alreadyMembers'
        else:
            bucket = 'serialMatches'
            if 'statement-end' in entry:
                proposal['existingStatementEnd'] = entry['statement-end']
                problems.append('Different statement membership requires owner review')
        if problems:
            proposal['warnings'] = problems
            result['warnings'].extend(f"{row['serial']}: {problem}" for problem in problems)
        result[bucket].append(proposal)
    # Reserve every serial match before amount/date matching. Never reuse an entry silently.
    for row, signed, fields in rest:
        candidates = []
        for entry in unbilled:
            if entry['id'] in serial_ids or card_amount(entry, account) != signed:
                continue
            distance = abs((date.fromisoformat(entry['date']) - date.fromisoformat(row['date'])).days)
            if distance > 3:
                continue
            similarity = SequenceMatcher(None, entry['description'].casefold(), row['description'].casefold()).ratio()
            candidates.append(dict(entryId=entry['id'], date=entry['date'], description=entry['description'],
                                   dayDistance=distance, descriptionSimilarity=round(similarity, 6)))
        candidates.sort(key=lambda c: (c['dayDistance'], -c['descriptionSimilarity'], c['entryId']))
        if candidates:
            result['candidateRows'].append(dict(row=row, fields=fields, statementSource=source, candidates=candidates, uniqueMatch=False))
        else:
            amount = format(signed, 'f')
            expense_amount = format(-signed, 'f')
            # Posting amounts require a decimal point; retain all supplied precision.
            amount += '' if '.' in amount else '.00'
            expense_amount += '' if '.' in expense_amount else '.00'
            proposal = dict(date=row['date'], status='pending', description=row['description'], **fields,
                            notes=['Pending classification from statement row; allocate an id and cite the filed PDF before adding.'],
                            postings=[dict(account='Expenses:Uncategorized', amount=expense_amount, commodity='INR'),
                                      dict(account=account, amount=amount, commodity='INR')])
            if source:
                proposal['sources'] = [source]
            result['newRows'].append(dict(row=row, proposedEntry=proposal))
    candidate_usage = {}
    for item in result['candidateRows']:
        for candidate in item['candidates']:
            candidate_usage[candidate['entryId']] = candidate_usage.get(candidate['entryId'], 0) + 1
    for item in result['candidateRows']:
        if len(item['candidates']) == 1 and candidate_usage[item['candidates'][0]['entryId']] == 1:
            item['uniqueMatch'] = True
            matched_ids.add(item['candidates'][0]['entryId'])
    result['unmatchedUnbilled'] = [dict(entryId=e['id'], date=e['date'], description=e['description'], amount=str(card_amount(e, account)))
                                   for e in unbilled if e['date'] <= end and e['id'] not in matched_ids]
    purchases = sum((Decimal(r['amount']) for r in rows if r['direction'] == 'Debit'), Decimal(0))
    credits = sum((Decimal(r['amount']) for r in rows if r['direction'] == 'Credit'), Decimal(0))
    result['draftStatement'] = dict(account=account, start=start, end=end, source=source,
                                   **{'previous-balance': str(previous), 'purchases': str(purchases), 'credits': str(credits),
                                      'total-due': total_due if total_due is not None else str(previous + purchases - credits), 'rows': len(rows)})
    result['draftNotes'] = ['Allocate a fresh id only when accepting this draft.']
    if total_due is None:
        result['draftNotes'].append('total-due is computed; check it against the statement, including any cash advances.')
    if source is None:
        result['draftNotes'].append('source is missing; supply the filed statement PDF path before accepting.')
    return result


def text_report(report: dict) -> str:
    labels = [('alreadyMembers', 'Already members'), ('serialMatches', 'Serial matches: propose membership fields'),
              ('candidateRows', 'Amount/direction/date candidates (ranked; uniqueMatch is conservative)'),
              ('newRows', 'No candidate: propose pending entries'), ('unmatchedUnbilled', 'Unmatched unbilled entries through statement end')]
    lines = []
    for key, label in labels:
        lines.append(f'{label}: {len(report[key])}')
        lines.extend('  ' + json.dumps(item, ensure_ascii=False) for item in report[key])
    lines.extend(['Draft card-statements.json object:', json.dumps(report['draftStatement'], ensure_ascii=False, indent=2)])
    lines.extend(report['draftNotes'])
    lines.extend('WARNING: ' + warning for warning in report['warnings'])
    return '\n'.join(lines)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--rows', type=Path)
    inputs.add_argument('--from-extract', metavar='END')
    parser.add_argument('--end')
    parser.add_argument('--start')
    parser.add_argument('--account', default=CARD)
    parser.add_argument('--total-due')
    parser.add_argument('--source', help='Filed statement PDF path for a new statement draft')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--root', type=Path, default=ROOT, help='Ledger repository root')
    args = parser.parse_args(argv)
    try:
        metadata = {}
        if args.from_extract:
            rows, metadata = extract_rows(args.root, args.from_extract)
            if args.end and args.end != args.from_extract:
                raise ValueError('--end differs from --from-extract')
        else:
            rows = read_json(args.rows)
        end, start = args.end or args.from_extract, args.start or metadata.get('start')
        if not start or not end:
            raise ValueError('--start and --end are required for --rows')
        report = reconcile(load(args.root), rows, end=end, start=start, account=args.account,
                           total_due=args.total_due, source=args.source or metadata.get('source'), root=args.root)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else text_report(report))


if __name__ == '__main__':
    main()
