"""Single-input routing, without any agent or network service."""
from copy import deepcopy
import io
import json
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from support import Fixture, png, meta
from store import dump, load
from test_readers import CSV, OPTIONS
from test_statements import statement_pdf


class AddIntake(Fixture):
    def setUp(self):
        super().setUp()
        self.currency=json.loads((self.ledger/'ledger.json').read_text())['defaultCurrency']
        self.bank='Assets:Bank:Example Current'
        self.meta=meta(kind='bank',currency=self.currency,label='Example Current',aliases=['everyday'],statement=dict(reader='generic-csv',options=deepcopy(OPTIONS)))
        data=load(self.ledger)[self.ledger/'data/accounts.json'];data['accounts'][self.bank]=self.meta
        dump(self.ledger/'data/accounts.json',data)

    def attach(self,client,text,files):
        identity=self.create(client,text,upload=False)
        result=client.post(f'/api/drafts/{identity}/files',files=[('files',(name,content,media)) for name,content,media in files])
        self.assertEqual(result.status_code,201,result.text)
        return identity,result.json()['files']

    def interpretation(self,client,identity,**body):
        response=client.post(f'/api/drafts/{identity}/interpret',json=body)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_words_and_alias_plus_month_are_server_owned(self):
        with self.client(worker=False) as client:
            for text in ('bank statement for October','card statement','everyday October'):
                response=client.post('/api/add/interpret',json=dict(text=text))
                self.assertEqual(response.json()['kind'],'statement',text)
            self.assertEqual(client.post('/api/add/interpret',json=dict(text='everyday lunch 12')).json()['kind'],'expense')

    def test_csv_recognition_and_continue_are_separate_and_idempotent(self):
        with self.client(worker=False) as client:
            identity,files=self.attach(client,'everyday January',[('Export.csv',CSV,'text/csv')])
            preview=self.interpretation(client,identity)['files'][0]
            self.assertEqual(preview['kind'],'statement');self.assertTrue(preview['builtin'])
            self.assertEqual(preview['account'],self.bank);self.assertEqual(preview['currency'],self.currency)
            self.assertEqual(client.get('/api/statements').json()['sessions'],[])
            result=client.post(f'/api/drafts/{identity}/continue',json={})
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(len(result.json()['statements']),1);self.assertEqual(result.json()['drafts'],[])
            self.assertEqual(client.post(f'/api/drafts/{identity}/continue',json={}).json(),result.json())
            self.assertEqual(client.get('/api/drafts').json(),[])
            self.assertEqual(len(client.get('/api/statements').json()['sessions']),1)
            self.assertEqual(self.calls(),[])
            self.assertEqual(client.patch(f'/api/drafts/{identity}',json=dict(text='late edit')).status_code,409)

    def test_workbook_extensions_use_registry_and_retain_mapping(self):
        import openpyxl, xlwt
        for extension in ('xlsx','xls'):
            if extension=='xlsx':
                workbook=openpyxl.Workbook();sheet=workbook.active
                for row in CSV.decode().splitlines(): sheet.append(row.split(','))
                output=io.BytesIO();workbook.save(output)
            else:
                workbook=xlwt.Workbook();sheet=workbook.add_sheet('Sheet')
                for i,row in enumerate(CSV.decode().splitlines()):
                    for j,value in enumerate(row.split(',')): sheet.write(i,j,value)
                output=io.BytesIO();workbook.save(output)
            data=load(self.ledger)[self.ledger/'data/accounts.json'];data['accounts'][self.bank]['statement']['reader']='generic-sheet';dump(self.ledger/'data/accounts.json',data)
            with self.client(worker=False) as client:
                identity,_=self.attach(client,'everyday January',[(f'Export.{extension}',output.getvalue(),'application/octet-stream')])
                preview=self.interpretation(client,identity)['files'][0]
                self.assertEqual(preview['reader'],'generic-sheet');self.assertEqual(preview['account'],self.bank)

    def test_tabular_files_cannot_be_routed_to_the_expense_agent(self):
        with self.client(worker=False) as client:
            identity,files=self.attach(client,'everyday January',[('Export.csv',CSV,'text/csv')])
            fid=files[0]['id']
            self.assertFalse(self.interpretation(client,identity)['files'][0]['can_choose_kind'])
            response=client.post(f'/api/drafts/{identity}/continue',json=dict(choices={fid:dict(kind='receipt')}))
            self.assertEqual(response.status_code,409)
            self.assertEqual(client.get(f'/api/drafts/{identity}').json()['state'],'editing')
            self.assertEqual(self.calls(),[])

    def test_csv_encoding_is_owned_by_the_configured_reader(self):
        data=load(self.ledger)[self.ledger/'data/accounts.json']
        data['accounts'][self.bank]['statement']['options']['encoding']='cp1252'
        dump(self.ledger/'data/accounts.json',data)
        content=CSV.decode().replace('Fable Shop','Fable Café').encode('cp1252')
        with self.client(worker=False) as client:
            identity,_=self.attach(client,'everyday January',[('Encoded.csv',content,'text/csv')])
            preview=self.interpretation(client,identity)['files'][0]
            self.assertEqual(preview['account'],self.bank)
            self.assertEqual(preview['start'],'2026-01-01')

    def test_ambiguous_pdf_has_two_explicit_choices(self):
        writer=PdfWriter();writer.add_blank_page(width=200,height=200);out=io.BytesIO();writer.write(out)
        with self.client(worker=False) as client:
            identity,files=self.attach(client,'',[('Document.pdf',out.getvalue(),'application/pdf')]);fid=files[0]['id']
            self.assertEqual(self.interpretation(client,identity)['files'][0]['kind'],'ambiguous')
            self.assertTrue(client.post(f'/api/drafts/{identity}/continue',json={}).json()['blocked'])
            for kind in ('statement','receipt'):
                self.assertEqual(self.interpretation(client,identity,choices={fid:dict(kind=kind)})['files'][0]['kind'],kind)

    def test_passwords_are_not_retained_or_echoed(self):
        writer=PdfWriter();writer.add_blank_page(width=200,height=200);writer.encrypt('fictional-secret');out=io.BytesIO();writer.write(out)
        with self.client(worker=False) as client:
            identity,files=self.attach(client,'card statement',[('Locked.pdf',out.getvalue(),'application/pdf')]);fid=files[0]['id']
            self.assertTrue(self.interpretation(client,identity)['files'][0]['locked'])
            bad=self.interpretation(client,identity,passwords={fid:'incorrect'})['files'][0]
            self.assertEqual(bad['error'],"That password didn't open the file. Try again.")
            good=self.interpretation(client,identity,passwords={fid:'fictional-secret'})['files'][0]
            self.assertFalse(good['locked'])
            result=client.post(f'/api/drafts/{identity}/continue',json=dict(passwords={fid:'fictional-secret'})).json()
            session=result['statements'][0];self.assertEqual(session['state'],'reading')
            for path in (self.ledger/'drafts').rglob('*.json'):
                self.assertNotIn('fictional-secret',path.read_text());self.assertNotIn('incorrect',path.read_text())
            self.assertTrue(client.app.state.statements.folder(session['id']).joinpath('original.pdf').is_file())

    def test_several_statements_plus_receipt_dispatch_separately_and_failure_is_local(self):
        with self.client(worker=False) as client:
            identity,_=self.attach(client,'lunch 12', [('One.csv',CSV,'text/csv'),('Bad.csv',b'Unmapped\nwrong\n','text/csv'),('Receipt.png',png(),'image/png')])
            result=client.post(f'/api/drafts/{identity}/continue',json={}).json()
            self.assertEqual(len(result['statements']),2);self.assertEqual(len(result['drafts']),1)
            draft=result['drafts'][0];self.assertEqual(draft['text'],'lunch 12');self.assertEqual(len(draft['files']),1)
            sessions=client.app.state.statements
            for session in result['statements']:
                import threading
                sessions.work(threading.Event())
            bad=next(sessions.get(s['id']) for s in result['statements'] if s['original_name']=='Bad.csv')
            good=next(sessions.get(s['id']) for s in result['statements'] if s['original_name']=='One.csv')
            self.assertEqual(bad['state'],'couldnt_read');self.assertEqual(good['state'],'found')
            self.assertEqual(self.calls(),[])

    def test_currency_mismatch_is_blocked_and_account_creation_is_only_staged(self):
        other=next(c for c in load(self.ledger)[self.ledger/'data/accounts.json']['commodities'] if c!=self.currency)
        data=load(self.ledger)[self.ledger/'data/accounts.json'];data['accounts'][self.bank]['statement']['options']['currency']=other;dump(self.ledger/'data/accounts.json',data)
        with self.client(worker=False) as client:
            identity,files=self.attach(client,'everyday January',[('Other.csv',CSV,'text/csv')]);fid=files[0]['id']
            before=(self.ledger/'data/accounts.json').read_bytes()
            preview=self.interpretation(client,identity)['files'][0];self.assertTrue(preview['mismatch'])
            self.assertTrue(client.post(f'/api/drafts/{identity}/continue',json={}).json()['blocked'])
            staged=self.interpretation(client,identity,choices={fid:dict(new_account=dict(name='New Current',kind='bank'))})['files'][0]
            self.assertFalse(staged['mismatch']);self.assertEqual(staged['new_account']['currency'],other)
            self.assertEqual(before,(self.ledger/'data/accounts.json').read_bytes())
