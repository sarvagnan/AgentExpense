"""Regenerate fictional reader inputs; never alter a persona's financial store.

Run: uv run --directory review-dashboard/server python ../../fixtures/generate_statements.py
All names/amounts below and in the source personas are invented. account.json
contains the metadata to use in a disposable import ledger, including exact mappings.
"""
from datetime import datetime, date, timedelta
from decimal import Decimal
import csv
import io
import json
import re
from pathlib import Path
import sys

APPLICATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APPLICATION/'review-dashboard/server/tests'))
from statement_fixtures import statement_pdf

BANKS = {'asha':'Nila Current','jordan':'Alder Checking','sam':'Birken Current','empty':'Alder Checking'}


def generate(destination=None):
    import openpyxl
    import xlwt
    destination = Path(destination) if destination else APPLICATION/'fixtures/statements'
    for persona, label in BANKS.items():
        ledger = APPLICATION/'fixtures/ledgers'/persona
        metadata = json.loads((ledger/'data/accounts.json').read_text())['accounts']
        bank = next(a for a,m in metadata.items() if m.get('label') == label)
        currency = metadata[bank]['currency']
        opening = sum((Decimal(p['amount']) for file in (ledger/'data/expenses').glob('*.json') for e in json.loads(file.read_text()) for p in e['postings'] if p['account']==bank), Decimal(0))
        # The new-ledger fixture has no opening entry. Its source export explicitly
        # starts from zero; Apply will propose that opening with the statement.
        rows = [['Example account export',None,None,None,None,None],['Date','Description','Debit','Credit','Balance','Reference'],
                ['2026-11-02','Paper Kite Market','24.50','',f'{opening-Decimal("24.50"):.2f}','DEMO-001'],
                ['2026-11-03','Lantern Printworks','15.00','',f'{opening-Decimal("39.50"):.2f}','DEMO-002'],
                ['2026-11-04','Fable refund','','4.50',f'{opening-Decimal("35.00"):.2f}','DEMO-003']]
        folder = destination/persona;folder.mkdir(parents=True, exist_ok=True)
        options = dict(columns=dict(date='Date',description='Description',debit='Debit',credit='Credit',balance='Balance',reference='Reference'), header_row=2, date_format='%Y-%m-%d', period=dict(start='2026-11-01',end='2026-11-28'))
        reader = 'generic-sheet' if persona=='asha' else 'generic-csv'
        if reader == 'generic-sheet':
            options['sheet'] = 'Activity'
        account = dict(account=bank, metadata={**metadata[bank], 'statement':dict(reader=reader,options=options)})
        (folder/'account.json').write_text(json.dumps(account,indent=2)+'\n')
        with (folder/'bank.csv').open('w', newline='',encoding='utf-8') as file:
            csv.writer(file).writerows(rows)
        book = openpyxl.Workbook();sheet = book.active;sheet.title='Activity'
        for row in rows:sheet.append(row)
        book.save(folder/'bank.xlsx')
        legacy = xlwt.Workbook();sheet = legacy.add_sheet('Activity')
        for r,row in enumerate(rows):
            for c,value in enumerate(row):
                if value is not None:sheet.write(r,c,value)
        legacy.save(str(folder/'bank.xls'))
        # Each persona's example PDF is independently usable in an unused card
        # account; no real identifier or source statement is copied.
        card = next((a for a,m in metadata.items() if m.get('kind')=='card' and m.get('currency')==currency), next(a for a,m in metadata.items() if m.get('kind')=='card'))
        identifier = metadata[card]['statement']['identifier']
        card_currency = metadata[card]['currency']
        actual = sorted((e for file in sorted((ledger/'data/expenses').glob('*.json')) for e in json.loads(file.read_text())),key=lambda e:e['date'])
        filed = [s for s in json.loads((ledger/'data/card-statements.json').read_text()) if s['account']==card]
        prior = max(filed,key=lambda s:s['end']) if filed else None
        openings = [e for e in actual if any(p['account'].startswith('Equity:Opening Balances') for p in e['postings']) and any(p['account']==card for p in e['postings'])]
        previous = Decimal(prior['total-due']) if prior else -sum((Decimal(p['amount']) for e in openings for p in e['postings'] if p['account']==card),Decimal(0))
        start = (date.fromisoformat(prior['end'])+timedelta(days=1)).isoformat() if prior else min((e['date'] for e in openings),default=json.loads((ledger/'ledger.json').read_text()).get('startDate','2026-11-01'))
        card_rows = []
        for entry in actual:
            postings = [p for p in entry['postings'] if p['account']==card]
            if not postings or entry in openings or (entry['statement'] or {}).get('end') or all(Decimal(p['amount'])==0 and p.get('assert') for p in entry['postings']):
                continue
            if len(postings)!=1:
                raise ValueError('Fixture card needs one account posting')
            amount = Decimal(postings[0]['amount'])
            serial = entry.get('serial-number') or 'DEMO-'+entry['id']
            if not re.fullmatch('[A-Za-z0-9-]+',serial):serial='DEMO-'+entry['id']
            original = entry.get('original-amount')
            foreign = format(abs(Decimal(original['amount'])),'.2f') if original else ''
            foreign_currency = original['commodity'] if original else ''
            card_rows.append(dict(date=entry['date'],serial=serial,description=entry['description'].replace('|',' '),amount=abs(amount),direction='Debit' if amount<0 else 'Credit',foreign=foreign,currency=foreign_currency))
        card_rows += [dict(date='2026-11-02',serial='DEMO-PDF-001',description='Paper Kite Market',amount=Decimal('24.50'),direction='Debit',foreign='',currency=''),dict(date='2026-11-04',serial='DEMO-PDF-002',description='Fable refund',amount=Decimal('4.50'),direction='Credit',foreign='',currency='')]
        card_rows.sort(key=lambda r:r['date'])
        start=min(start,card_rows[0]['date'])
        purchases=sum((r['amount'] for r in card_rows if r['direction']=='Debit'),Decimal(0));credits=sum((r['amount'] for r in card_rows if r['direction']=='Credit'),Decimal(0));due=previous+purchases-credits
        heading='Date | Serial | Description | Amount | Direction | Foreign amount | Currency'
        header=f"Example Card Statement\nStatement currency: {card_currency}\nStatement period: {start} to 2026-11-28\nCard last four digits: {identifier}\nPrevious balance: {previous:.2f}\nPurchases: {purchases:.2f}\nCash advances: 0.00\nCredits: {credits:.2f}\nTotal due: {due:.2f}\nMinimum due: {due/10:.2f}\nDue date: 2026-12-15\n"
        lines=[f"{r['date']} | {r['serial']} | {r['description']} | {r['amount']:.2f} | {r['direction']} | {r['foreign']} | {r['currency']}" for r in card_rows]
        pages=[(header if offset==0 else '')+heading+'\n'+'\n'.join(lines[offset:offset+35])+'\n' for offset in range(0,len(lines),35)]
        (folder/'card.pdf').write_bytes(statement_pdf('\f'.join(pages)))
        print('Generated fictional statement inputs:', persona)


if __name__ == '__main__':
    generate()
