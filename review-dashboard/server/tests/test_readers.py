import os
"""Versioned readers on wholly fictional sources; never run an agent/model."""
from copy import deepcopy
from decimal import Decimal
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

from persona_guard import persona_sensitive
from support import APPLICATION, Fixture, meta, entry as store_entry, posting
from statement_readers import Registry, Reader, check_statement
from statements import Statements
from store import load, dump, entries
from audit import records
from transactions import clean
from check_store import check_card_statements


OPTIONS = dict(columns=dict(date='Date',description='Description',amount='Amount',balance='Balance',reference='Reference'),period=dict(start='2026-01-01',end='2026-01-28'))
BANK = 'Assets:Deposits:Main'
META = meta(kind='bank',currency='INR',label='Example Current',statement=dict(reader='generic-csv',options=OPTIONS))
CSV = b'Date,Description,Amount,Balance,Reference\n2026-01-02,Fable Shop,-10.00,90.00,REF-001\n2026-01-03,Fable refund,2.00,92.00,REF-002\n'


class ReaderContract(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp'),prefix='expense-readers-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.csv = self.root/'example.csv';self.csv.write_bytes(CSV)

    def test_builtin_registry_and_recognition_are_contained(self):
        registry = Registry(self.root)
        self.assertEqual(set(registry.readers),{'example-card-pdf','generic-csv','generic-sheet'})
        self.assertEqual(registry.get('example-card-pdf').module.recognise(self.root/'missing.pdf')['confidence'],0)
        reader = registry.get('generic-csv')
        self.assertGreater(reader.recognise(self.csv)['confidence'],0)
        with patch.object(reader.module,'recognise',side_effect=RuntimeError('unreadable')):
            self.assertEqual(reader.recognise(self.csv)['confidence'],0)
        with patch.object(reader.module,'recognise',return_value=dict(confidence=float('nan'),reasons=[])):
            self.assertEqual(reader.recognise(self.csv)['confidence'],0)

    def test_personal_reader_discovery_version_mismatch_and_duplicate_refusal(self):
        folder = self.root/'readers';folder.mkdir()
        code = "INTERFACE=1\nID='personal-export'\nNAME='Personal export'\nKINDS=('bank',)\nEXTENSIONS=('.txt',)\nCHECKS=()\ndef recognise(file): return dict(confidence=1,reasons=['Personal layout'])\ndef read(file,account,password=None): return {}\n"
        path = folder/'personal.py';path.write_text(code)
        self.assertTrue(Registry(self.root).get('personal-export').personal)
        path.write_text(code.replace('INTERFACE=1','INTERFACE=2'))
        with self.assertRaisesRegex(ValueError,'interface'):Registry(self.root)
        path.write_text(code.replace("ID='personal-export'","ID='generic-csv'"))
        with self.assertRaisesRegex(ValueError,'Duplicate'):Registry(self.root)

    def test_csv_decimal_mapping_citation_and_running_checks(self):
        statement = Registry().get('generic-csv').read(self.csv,META)
        tested = check_statement(statement,account=META,opening=dict(date='2026-01-01',balance='100.00'))
        self.assertTrue(all(t['ok'] for t in tested))
        self.assertEqual(statement['rows'][0]['citation'],dict(sheet='CSV',row=2))
        self.assertEqual(statement['rows'][0]['reference'],'REF-001')
        self.assertEqual(statement['closingBalance'],'92.00')
        statement['rows'][1]['balance']='92.01'
        with self.assertRaisesRegex(ValueError,'Running balance'):check_statement(statement,account=META,opening=dict(date='2026-01-01',balance='100.00'))

    def test_csv_encoding_separators_headers_skip_and_debit_positive(self):
        text='Invented export\nWhen;Payee;Value;Balance\n02/01/2026;Fable Café;1.234,50;8.765,50\nFooter;ignored;;\n'
        self.csv.write_bytes(text.encode('cp1252'))
        meta=deepcopy(META);meta['statement']['options']=dict(columns=dict(date='When',description='Payee',amount='Value',balance='Balance'),delimiter=';',encoding='cp1252',header_row=2,skip_rows=[4],date_format='%d/%m/%Y',decimal_separator=',',thousands_separator='.',sign='debit-positive')
        statement=Registry().get('generic-csv').read(self.csv,meta)
        check_statement(statement,account=meta,opening=dict(date='2026-01-02',balance='10000.00'))
        self.assertEqual(statement['rows'][0]['description'],'Fable Café')
        self.assertEqual(statement['rows'][0]['amount'],'1234.50')
        self.assertEqual(statement['rows'][0]['direction'],'Debit')
        self.assertEqual(statement['rows'][0]['citation']['row'],3)

    def test_debit_credit_columns_and_printed_summary(self):
        self.csv.write_text('Date,Payee,Debit,Credit\n2026-01-02,Fable Shop,10.00,\n2026-01-03,Fable refund,,2.00\n')
        meta=deepcopy(META);meta['statement']['options']=dict(columns=dict(date=1,description=2,debit=3,credit=4),summary=dict(openingBalance='100.00',closingBalance='92.00',debits='10.00',credits='2.00'))
        statement=Registry().get('generic-csv').read(self.csv,meta)
        self.assertEqual(statement['declaredChecks'],['printed_totals'])
        self.assertTrue(check_statement(statement,account=meta))
        statement['debits']='11.00'
        with self.assertRaisesRegex(ValueError,'Printed debits'):check_statement(statement,account=meta)

    def test_csv_no_summary_continuity_with_previous_and_no_balance(self):
        meta=deepcopy(META);meta['statement']['options']['columns'].pop('balance')
        statement=Registry().get('generic-csv').read(self.csv,meta)
        tested=check_statement(statement,account=meta,previous=dict(end='2025-12-31',balance='100.00'))
        self.assertEqual(statement['openingBalance'],'100.00');self.assertEqual(statement['closingBalance'],'92.00')
        self.assertIn('Continuity',tested[0]['text'])
        statement['openingBalance']='101.00';statement['closingBalance']='93.00'
        with self.assertRaisesRegex(ValueError,'Continuity'):check_statement(statement,account=meta,previous=dict(end='2025-12-31',balance='100.00'))

    def test_malformed_input_is_rejected_without_silent_skips(self):
        reader=Registry().get('generic-csv')
        for before,after in [(b'2026-01-02',b'2026-02-30'),(b'-10.00',b'NaN'),(b'-10.00',b'1,00.00'),(b'Fable Shop',b''),(b'Date,Description',b'Date,Date'),(b'90.00',b'Infinity')]:
            self.csv.write_bytes(CSV.replace(before,after))
            with self.subTest(after=after),self.assertRaises((ValueError,KeyError)):reader.read(self.csv,META)
        self.csv.write_bytes(CSV)
        for options in [dict(columns={}),dict(header_row=0),dict(sign='guess'),dict(decimal_separator='.',thousands_separator='.')]:
            meta=deepcopy(META);meta['statement']['options'].update(options)
            with self.assertRaises(ValueError):reader.read(self.csv,meta)

    def test_currency_and_every_source_citation_are_mandatory_checks(self):
        statement=Registry().get('generic-csv').read(self.csv,META)
        statement['currency']='USD'
        with self.assertRaisesRegex(ValueError,'currency'):check_statement(statement,account=META)
        statement['currency']=None;statement['rows'][0].pop('citation')
        with self.assertRaisesRegex(ValueError,'citation'):check_statement(statement,account=META)

    def test_every_persona_and_spreadsheet_format(self):
        for persona in ('asha','jordan','sam','empty'):
            folder=APPLICATION/'fixtures/statements'/persona
            meta=json.loads((folder/'account.json').read_text())['metadata']
            expected=None
            for extension,reader in (('csv','generic-csv'),('xlsx','generic-sheet'),('xls','generic-sheet')):
                with self.subTest(persona=persona,extension=extension):
                    statement=Registry().get(reader).read(folder/('bank.'+extension),meta)
                    check_statement(statement,account=meta)
                    rows=[{k:r[k] for k in ('date','description','amount','direction','balance','reference')} for r in statement['rows']]
                    if expected is None:expected=rows
                    else:self.assertEqual(expected,rows)
            card=Registry().get('example-card-pdf').read(folder/'card.pdf',dict(kind='card',currency=meta['currency']))
            self.assertTrue(check_statement(card,account=dict(currency=meta['currency'])))

    def test_workbook_sheet_selection_summary_cells_dates_and_formula_rejection(self):
        import openpyxl
        from datetime import datetime
        book=openpyxl.Workbook();book.active.title='Notes';sheet=book.create_sheet('Rows')
        sheet.append(['Opening','100.00','Closing','90.00'])
        sheet.append(['Date','Payee','Amount','Balance'])
        sheet.append([datetime(2026,1,2),'Fable Shop',-10,90])
        path=self.root/'example.xlsx';book.save(path)
        meta=deepcopy(META);meta['statement']['options']=dict(sheet='Rows',header_row=2,columns=dict(date=1,description=2,amount=3,balance=4))
        statement=Registry().get('generic-sheet').read(path,meta)
        check_statement(statement,account=meta)
        self.assertEqual(statement['rows'][0]['citation'],dict(sheet='Rows',row=3))
        meta['statement']['options'].update(decimal_separator=',',thousands_separator='.')
        self.assertEqual(Registry().get('generic-sheet').read(path,meta)['rows'],statement['rows'])
        meta['statement']['options']['sheet']=1
        self.assertEqual(Registry().get('generic-sheet').read(path,meta)['rows'],statement['rows'])
        sheet.cell(3,3,'=1-11');book.save(path)
        with self.assertRaisesRegex(ValueError,'Formula'):Registry().get('generic-sheet').read(path,meta)


class BankApply(Fixture):
    def setup_bank(self, *, start=None, options=None):
        declarations=load(self.ledger)[self.ledger/'data/accounts.json']
        meta=deepcopy(META)
        if options is not None:meta['statement']['options']=options
        declarations['accounts'].update({BANK:meta,'Equity:Opening Balances':{}})
        dump(self.ledger/'data/accounts.json',declarations)
        dump(self.ledger/'data/expenses/2026.json',[store_entry('a'*16,start or '2026-01-01','Opening balance',[posting(BANK,'100.00',**{'assert':dict(amount='100.00',commodity='INR')}),posting('Equity:Opening Balances','-100.00')])])
        if start:
            manifest=json.loads((self.ledger/'ledger.json').read_text());manifest['startDate']=start;dump(self.ledger/'ledger.json',manifest)
        self.jj('commit','-m','Fictional bank baseline')
        return Statements(self.settings)

    def read_bank(self,sessions,data=CSV,name='Example.csv'):
        session=sessions.create(data,name)
        with patch('agent.run') as run:
            sessions.read(session)
            run.assert_not_called()
        session=sessions.get(session['id'])
        self.assertEqual(session['state'],'found',session.get('error'))
        return session

    def apply_bank(self,sessions,session):
        import threading
        for item in session['plan']['items']:
            if item['needs_user'] and not item['decision']:sessions.decide(session['id'],item['row']['serial'],dict(action='pending'))
        sessions.queue_apply(session['id']);sessions.work(threading.Event())
        applied=sessions.get(session['id'])
        self.assertEqual(applied['state'],'applied',applied.get('error'))
        self.assertTrue(clean(self.ledger))
        return applied

    def test_bank_apply_summary_membership_assertion_evidence_audit_commit(self):
        sessions=self.setup_bank();session=self.read_bank(sessions)
        applied=self.apply_bank(sessions,session)
        docs=load(self.ledger);summary=docs[self.ledger/'data/card-statements.json'][0]
        self.assertEqual(summary['opening-balance'],'100.00');self.assertEqual(summary['closing-balance'],'92.00')
        self.assertEqual(len(summary['members']),2)
        self.assertEqual(applied['result']['added'],2)
        self.assertEqual(len(check_card_statements(docs,self.ledger)['statements']),1)
        self.assertEqual(records(self.ledger)[0]['kind'],'statement')
        self.assertIn('Reconcile Example Current statement',self.jj('log','-r','@-','--no-graph','-T','description'))
        with self.client(worker=False) as client:
            filed=client.get('/api/statements/filed/2026-01-28',params={'account':BANK})
            self.assertEqual(filed.status_code,200,filed.text)
            self.assertTrue(all(r['entry_id'] for r in filed.json()['rows']))

    def test_overlapping_reference_and_balance_skips_without_double_entries(self):
        sessions=self.setup_bank();first=self.apply_bank(sessions,self.read_bank(sessions))
        options=load(self.ledger)[self.ledger/'data/accounts.json']
        options['accounts'][BANK]['statement']['options']['period']['end']='2026-02-28'
        dump(self.ledger/'data/accounts.json',options);self.jj('commit','-m','Next fictional period')
        second=self.read_bank(sessions,CSV+b'2026-02-02,Lantern Printworks,-5.00,87.00,REF-003\n','Overlap.csv')
        self.assertEqual([i['rule'] for i in second['plan']['items']],['already_in_ledger','already_in_ledger','needs_you'])
        applied=self.apply_bank(sessions,second)
        self.assertEqual(applied['result']['added'],1);self.assertEqual(applied['result']['skipped'],2)
        check_card_statements(load(self.ledger),self.ledger)

    def test_ambiguous_overlap_stays_in_review_without_a_second_movement(self):
        from review import snapshot,apply
        options=deepcopy(OPTIONS);options['columns'].pop('reference');options['columns'].pop('balance')
        sessions=self.setup_bank(options=options)
        self.apply_bank(sessions,self.read_bank(sessions))
        declarations=load(self.ledger)[self.ledger/'data/accounts.json'];declarations['accounts'][BANK]['statement']['options']['period']['end']='2026-02-28';dump(self.ledger/'data/accounts.json',declarations);self.jj('commit','-m','Next period')
        session=self.read_bank(sessions,CSV+b'2026-02-02,Lantern Printworks,-5.00,87.00,REF-003\n','Ambiguous.csv')
        self.assertEqual(session['plan']['items'][0]['duplicate_reason'],'overlapping-statements')
        before=len(entries(load(self.ledger)));self.apply_bank(sessions,session)
        self.assertEqual(len(entries(load(self.ledger))),before+2) # One new row and closing assertion.
        state=snapshot(self.ledger);overlaps=[i for i in state['items'] if i['kind']=='overlap']
        self.assertEqual(len(overlaps),2);self.assertTrue(all(i['important'] for i in overlaps))
        item=overlaps[0];entry=item['entries'][0]
        apply(self.settings,dict(request_id='b'*32,action='acknowledge',entry_id=entry['id'],versions={entry['id']:entry['version']},statement_id=item['statement_id'],row_serial=item['row_serial']))
        self.assertEqual(len([i for i in snapshot(self.ledger)['items'] if i['kind']=='overlap']),1)

    def test_start_date_skips_covered_rows_and_continues_at_opening(self):
        sessions=self.setup_bank(start='2026-01-02')
        data=b'Date,Description,Amount,Balance,Reference\n2026-01-01,Old Fable purchase,-5.00,100.00,OLD-001\n2026-01-02,Fable Shop,-10.00,90.00,REF-001\n2026-01-03,Fable refund,2.00,92.00,REF-002\n'
        session=self.read_bank(sessions,data)
        self.assertEqual(session['plan']['items'][0]['rule'],'pre_start')
        applied=self.apply_bank(sessions,session)
        self.assertEqual(applied['result']['added'],2)
        self.assertFalse(any(e['date']<'2026-01-02' for e in entries(load(self.ledger))))
        check_card_statements(load(self.ledger),self.ledger)

    def test_bad_running_balance_and_continuity_never_reach_matching_or_agents(self):
        sessions=self.setup_bank()
        for value in (CSV.replace(b'90.00',b'91.00'),CSV.replace(b'90.00',b'80.00').replace(b'92.00',b'82.00')):
            session=sessions.create(value,'Broken.csv')
            with patch('statements.plan') as plan,patch('agent.run') as run:
                with self.assertRaises(ValueError):sessions.read(session)
                plan.assert_not_called();run.assert_not_called()

    def test_two_configured_accounts_require_selection_and_currency_mismatch_blocks(self):
        sessions=self.setup_bank();declarations=load(self.ledger)[self.ledger/'data/accounts.json'];declarations['accounts']['Assets:Deposits:Other']=deepcopy(META);dump(self.ledger/'data/accounts.json',declarations)
        session=sessions.create(CSV,'Example.csv');sessions.read(session)
        self.assertEqual(sessions.get(session['id'])['state'],'account_needed')
        session=sessions.assign(session['id'],BANK);sessions.read(session)
        self.assertEqual(sessions.get(session['id'])['statement']['account'],BANK)
        declarations['accounts'].pop('Assets:Deposits:Other');declarations['accounts'][BANK]['statement']['options']['currency']='USD';dump(self.ledger/'data/accounts.json',declarations)
        blocked=sessions.create(CSV,'Mismatch.csv');sessions.read(blocked)
        self.assertEqual(sessions.get(blocked['id'])['state'],'currency_mismatch')

    def test_apply_failure_rolls_back_summary_entries_assertion_and_evidence(self):
        sessions=self.setup_bank();session=self.read_bank(sessions)
        for item in session['plan']['items']:sessions.decide(session['id'],item['row']['serial'],dict(action='pending'))
        before={p:p.read_bytes() for p in self.ledger.glob('data/**/*.json')}
        sessions.queue_apply(session['id'])
        import threading
        with patch('commit.verify',side_effect=ValueError('Fictional verification failure')):
            sessions.work(threading.Event())
        self.assertEqual(sessions.get(session['id'])['state'],'failed')
        self.assertEqual(before,{p:p.read_bytes() for p in self.ledger.glob('data/**/*.json')})
        self.assertFalse(list((self.ledger/'Bills').glob('**/*.csv')))
        self.assertTrue(clean(self.ledger))
    def test_card_pdf_pre_start_rows_are_covered_and_later_rows_apply(self):
        from statement_fixtures import statement_pdf
        sessions=self.setup_bank(start='2026-01-02')
        declarations=load(self.ledger)[self.ledger/'data/accounts.json']
        declarations['accounts'][BANK]=meta(kind='card',currency='INR',label='Example Card',statement=dict(reader='example-card-pdf',identifier='5678'))
        dump(self.ledger/'data/accounts.json',declarations)
        opening=load(self.ledger)[self.ledger/'data/expenses/2026.json']
        opening[0]['postings'][0].update(amount='-110.00',**{'assert':dict(amount='-110.00',commodity='INR')});opening[0]['postings'][1]['amount']='110.00';dump(self.ledger/'data/expenses/2026.json',opening)
        self.jj('commit','-m','Fictional card start date')
        text='''Example Card Statement
Statement period: 2026-01-01 to 2026-01-28
Card last four digits: 5678
Previous balance: 100.00
Purchases: 20.00
Cash advances: 0.00
Credits: 0.00
Total due: 120.00
Minimum due: 12.00
Due date: 2026-02-15
Date | Serial | Description | Amount | Direction | Foreign amount | Currency
2026-01-01 | CLIP-001 | Old Fable Shop | 10.00 | Debit | |
2026-01-03 | CLIP-002 | Lantern Printworks | 10.00 | Debit | |
'''
        session=self.read_bank(sessions,statement_pdf(text),'Clipped.pdf')
        self.assertEqual(session['statement']['kind'],'bank')
        self.assertEqual(session['plan']['items'][0]['rule'],'pre_start')
        applied=self.apply_bank(sessions,session)
        self.assertEqual(applied['result']['added'],1)
        summary=load(self.ledger)[self.ledger/'data/card-statements.json'][0]
        self.assertEqual(summary['closing-balance'],'-120.00')
        self.assertFalse(any(e['date']<'2026-01-02' for e in entries(load(self.ledger))))
        check_card_statements(load(self.ledger),self.ledger)

    def test_generic_card_export_uses_signed_balance_continuity(self):
        sessions=self.setup_bank()
        declarations=load(self.ledger)[self.ledger/'data/accounts.json']
        declarations['accounts'][BANK]['kind']='card';dump(self.ledger/'data/accounts.json',declarations)
        opening=load(self.ledger)[self.ledger/'data/expenses/2026.json'];opening[0]['postings'][0].update(amount='-100.00',**{'assert':dict(amount='-100.00',commodity='INR')});opening[0]['postings'][1]['amount']='100.00';dump(self.ledger/'data/expenses/2026.json',opening)
        self.jj('commit','-m','Fictional generic card baseline')
        data=CSV.replace(b'90.00',b'-110.00').replace(b'92.00',b'-108.00')
        session=self.read_bank(sessions,data);applied=self.apply_bank(sessions,session)
        self.assertEqual(session['statement']['openingBalance'],'-100.00')
        self.assertEqual(session['statement']['closingBalance'],'-108.00')
        check_card_statements(load(self.ledger),self.ledger)

    def test_bank_foreign_estimate_requires_explicit_conversion_and_preserves_original(self):
        sessions=self.setup_bank()
        documents=load(self.ledger)
        documents[self.ledger/'data/accounts.json']['accounts'][BANK]['statement']['options']['columns'].update(foreign_amount='Foreign',foreign_currency='Currency')
        documents[self.ledger/'data/expenses/2026.json'].append(store_entry('b'*16,'2026-01-02','Fable Shop',[posting('Expenses:Food','2.00','USD',cost=dict(amount='9.00',commodity='INR',per='total')),posting(BANK,'-9.00')],**{'original-amount':dict(amount='2.00',commodity='USD')}))
        for path,value in documents.items():dump(path,value)
        self.jj('commit','-m','Fictional bank currency estimate')
        data=b'Date,Description,Amount,Balance,Reference,Foreign,Currency\n2026-01-02,Fable Shop,-10.00,90.00,REF-001,2.00,USD\n2026-01-03,Fable refund,2.00,92.00,REF-002,,\n'
        session=self.read_bank(sessions,data)
        first=session['plan']['items'][0]
        self.assertEqual(first['rule'],'foreign_currency');self.assertTrue(first['needs_user'])
        with self.assertRaisesRegex(ValueError,'Decide every row'):sessions.queue_apply(session['id'])
        sessions.decide(session['id'],first['row']['serial'],dict(action='conversion'))
        applied=self.apply_bank(sessions,sessions.get(session['id']))
        self.assertEqual(applied['result']['converted'],1)
        entry=next(e for e in entries(load(self.ledger)) if e['id']=='b'*16)
        self.assertEqual(entry['postings'][0]['amount'],'2.00');self.assertEqual(entry['postings'][0]['cost']['amount'],'10.00');self.assertEqual(entry['postings'][1]['amount'],'-10.00')
        self.assertIn('replaces 9.00',entry['notes'][-1])

    def test_printed_identifier_detects_account_and_mismatches_are_refused(self):
        sessions=self.setup_bank()
        declarations=load(self.ledger)[self.ledger/'data/accounts.json']
        first=declarations['accounts'][BANK]
        first['statement']['identifier']='ID-ONE';first['statement']['options']['columns']['identifier']='Account'
        second=deepcopy(first);second['statement']['identifier']='ID-TWO';declarations['accounts']['Assets:Deposits:Other']=second
        dump(self.ledger/'data/accounts.json',declarations)
        data=b'Date,Description,Amount,Balance,Reference,Account\n2026-01-02,Fable Shop,-10.00,90.00,REF-001,ID-ONE\n'
        session=self.read_bank(sessions,data)
        self.assertEqual(session['statement']['account'],BANK)
        source=self.base/'wrong.csv';source.write_bytes(data.replace(b'ID-ONE',b'ID-OTHER'))
        with self.assertRaisesRegex(ValueError,'identifier'):Registry().get('generic-csv').read(source,first)

    def test_generic_mismatch_reassigns_to_new_currency_account_and_keeps_mapping(self):
        sessions=self.setup_bank()
        documents=load(self.ledger);declarations=documents[self.ledger/'data/accounts.json']
        declarations['accounts'][BANK]['statement']['options']['currency']='USD'
        other='Assets:Deposits:Currency Account';declarations['accounts'][other]=meta(kind='bank',currency='USD',label='Currency Account')
        dump(self.ledger/'data/accounts.json',declarations);self.jj('commit','-m','Fictional currency account')
        session=sessions.create(CSV,'Currency.csv');sessions.read(session)
        self.assertEqual(sessions.get(session['id'])['state'],'currency_mismatch')
        assigned=sessions.assign(session['id'],other);sessions.read(assigned)
        found=sessions.get(session['id']);self.assertEqual(found['state'],'found');self.assertEqual(found['statement']['account'],other)
        self.apply_bank(sessions,found)
        actual=load(self.ledger)[self.ledger/'data/accounts.json']['accounts'][other]
        self.assertEqual(actual['statement']['reader'],'generic-csv')
        again=sessions.create(CSV,'Again.csv');sessions.read(again)
        self.assertEqual(sessions.get(again['id'])['state'],'already_filed')

    def test_completed_tabular_uploads_are_removed_and_records_expire(self):
        from datetime import datetime,timedelta,timezone
        sessions=self.setup_bank();session=self.read_bank(sessions);applied=self.apply_bank(sessions,session)
        self.assertFalse(list(sessions.folder(session['id']).glob('*.csv')))
        duplicate=sessions.create(CSV,'Duplicate.csv');sessions.read(duplicate)
        self.assertEqual(sessions.get(duplicate['id'])['state'],'already_filed')
        self.assertFalse(list(sessions.folder(duplicate['id']).glob('*.csv')))
        for item in (applied,sessions.get(duplicate['id'])):
            path=sessions.folder(item['id'])/'session.json';value=json.loads(path.read_text());value['updated_at']=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat();path.write_text(json.dumps(value))
        sessions.purge();self.assertEqual(sessions.all(),[])

    def test_new_opening_and_postings_keep_three_decimal_places(self):
        sessions=self.setup_bank();dump(self.ledger/'data/expenses/2026.json',[]);self.jj('commit','-m','Fictional unused precision account')
        data=b'Date,Description,Amount,Balance,Reference\n2026-01-02,Fable Shop,-0.005,50.000,REF-001\n'
        found=self.read_bank(sessions,data)
        self.assertEqual(found['statement']['openingBalance'],'50.005')
        self.assertEqual(found['opening']['postings'][0]['amount'],'50.005')
        self.apply_bank(sessions,found)
        docs=load(self.ledger);row=next(e for e in entries(docs) if (e['references'] or {}).get('reference')=='REF-001')
        self.assertEqual(row['postings'][1]['amount'],'-0.005')
        check_card_statements(docs,self.ledger)

    @persona_sensitive
    def test_every_persona_pdf_applies_with_preserved_existing_entries(self):
        from dataclasses import replace
        import shutil
        import threading
        for persona in ('asha','jordan','sam','empty'):
            with self.subTest(persona=persona):
                root=self.base/('card-'+persona)
                shutil.copytree(APPLICATION/'fixtures/ledgers'/persona,root,ignore=shutil.ignore_patterns('.git','.jj','drafts','.lock','expense_ledger.ledger'))
                (root/'.gitignore').write_text('/drafts/\n/.lock\n/expense_ledger.ledger\n')
                self.jj('git','init','--colocate',str(root),at=root)
                self.jj('commit','-m','Fictional card import baseline',at=root)
                self.jj('bookmark','set','main','-r','@-',at=root)
                settings=replace(self.settings,ledger=root)
                sessions=Statements(settings)
                before={e['id']:deepcopy(e['postings']) for e in entries(load(root))}
                source=APPLICATION/'fixtures/statements'/persona/'card.pdf'
                session=sessions.create(source.read_bytes(),'Fictional-Card.pdf')
                with patch('agent.run') as run:
                    sessions.read(session);run.assert_not_called()
                found=sessions.get(session['id']);self.assertEqual(found['state'],'found')
                for item in found['plan']['items']:
                    if not item['needs_user']:continue
                    if item.get('conversion'):
                        sessions.decide(found['id'],item['row']['serial'],dict(action='conversion'))
                        continue
                    latest=sessions.get(found['id'])
                    used={i['entry_id'] for i in latest['plan']['items'] if i['entry_id']}|{i['decision'].get('entry_id') for i in latest['plan']['items'] if i['decision']}
                    exact=[c for c in item['candidates'] if c['entryId'] not in used and c['date']==item['row']['date'] and c['description']==item['row']['description']]
                    sessions.decide(found['id'],item['row']['serial'],dict(action='match',entry_id=exact[0]['entryId']) if exact else dict(action='pending'))
                sessions.queue_apply(found['id']);sessions.work(threading.Event())
                applied=sessions.get(found['id']);self.assertEqual(applied['state'],'applied',applied.get('error'))
                after={e['id']:e['postings'] for e in entries(load(root))}
                self.assertTrue(all(after[identity]==postings for identity,postings in before.items()))
                check_card_statements(load(root),root)
                self.assertTrue(clean(root))

    def test_unconfigured_personal_pdf_reader_cannot_displace_builtin_or_new_opening(self):
        from statement_fixtures import statement_pdf
        sessions=self.setup_bank()
        declarations=load(self.ledger)[self.ledger/'data/accounts.json'];declarations['accounts'][BANK]=meta(kind='card',currency='INR',statement=dict(reader='example-card-pdf',identifier='5678'));dump(self.ledger/'data/accounts.json',declarations);dump(self.ledger/'data/expenses/2026.json',[])
        folder=self.ledger/'readers';folder.mkdir();(folder/'unused.py').write_text("INTERFACE=1\nID='unused-pdf'\nNAME='Unused'\nKINDS=('card',)\nEXTENSIONS=('.pdf',)\nCHECKS=('printed_totals',)\ndef recognise(file): return dict(confidence=1,reasons=['Fixture'])\ndef read(file,account,password=None): raise AssertionError('Unconfigured reader must not run')\n")
        self.jj('commit','-m','Fictional unused reader')
        session=self.read_bank(sessions,statement_pdf(),'Example.pdf')
        self.assertEqual(session['statement']['reader'],'example-card-pdf')
        self.assertIsNotNone(session['opening'])

    def test_configured_personal_card_reader_checks_and_applies_in_process(self):
        from statement_fixtures import statement_pdf
        sessions=self.setup_bank()
        declarations=load(self.ledger)[self.ledger/'data/accounts.json'];declarations['accounts'][BANK]=meta(kind='card',currency='INR',statement=dict(reader='personal-card',identifier='5678'));dump(self.ledger/'data/accounts.json',declarations);dump(self.ledger/'data/expenses/2026.json',[])
        folder=self.ledger/'readers';folder.mkdir();(folder/'personal.py').write_text("from statement_readers import Registry\nINTERFACE=1\nID='personal-card'\nNAME='Personal card export'\nKINDS=('card',)\nEXTENSIONS=('.pdf',)\nCHECKS=('printed_totals',)\ndef recognise(file): return dict(confidence=1,reasons=['Fixture layout'])\ndef read(file,account,password=None): return Registry().get('example-card-pdf').read(file,account)\n")
        self.jj('commit','-m','Fictional configured personal reader')
        session=self.read_bank(sessions,statement_pdf(),'Personal.pdf')
        self.assertTrue(session['statement']['readerPersonal'])
        self.assertEqual(session['statement']['reader'],'personal-card')
        self.assertEqual(session['statement']['kind'],'bank')
        self.apply_bank(sessions,session)
        check_card_statements(load(self.ledger),self.ledger)
