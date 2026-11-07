"""Unknown statement gates on fictional evidence and offline adapters only."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

import agent
from agent_pause import AgentPause
from statement_reading import extract, verify, learn_mapping, safe_source
from statement_readers import Registry, check_statement
from statements import Statements
from store import load, dump
from audit import records
from support import Fixture, meta, entry as store_entry, posting
from cited_statement_fixtures import answer, file_bytes

ACCOUNT='Assets:Deposits:Cited Export'


class StatementReading(Fixture):
    def setUp(self):
        super().setUp()
        self.currency=json.loads((self.ledger/'ledger.json').read_text())['defaultCurrency']
        self.meta=meta(kind='bank',currency=self.currency,label='Cited Export')
        docs=load(self.ledger);docs[self.ledger/'data/accounts.json']['accounts'][ACCOUNT]=self.meta
        dump(self.ledger/'data/accounts.json',docs[self.ledger/'data/accounts.json'])
        self.jj('commit','-m','Fictional statement reading baseline')
        self.work=self.base/'extraction';self.work.mkdir()

    def source(self,extension='csv'):
        path=self.ledger/('source.'+extension);path.write_bytes(file_bytes(self.currency,extension))
        return extract(path,self.work),path

    def test_all_formats_extraction_correct_citations_and_strict_checks(self):
        for extension in ('csv','xlsx','xls','pdf'):
            with self.subTest(extension=extension):
                source,path=self.source(extension)
                read=answer(self.currency,sheet='CSV' if extension=='csv' else 'Activity',pdf=extension=='pdf')
                result=verify(read,source,self.meta)
                self.assertEqual(result['closingBalance'],'92.00');self.assertTrue(check_statement(result,account=self.meta))
                self.assertEqual(len(source['images']),int(extension=='pdf'))
                if extension=='pdf': self.assertTrue(source['images'][0].is_file())

    def test_bad_citations_values_summary_totals_chain_duplicates_and_currency_stop(self):
        source,_=self.source()
        cases=[('address',lambda a:a['rows'][0]['amount'].update(source='CSV!Z999'),'Unknown source address'),
               ('fabrication',lambda a:a['rows'][0]['amount'].update(value='11.00'),'Source value mismatch'),
               ('summary',lambda a:a.update(debits=None),'Missing source citation for debits'),
               ('totals',lambda a:a['debits'].update(value='100.00',source='CSV!B3'),'Printed debits'),
               ('balance',lambda a:a['rows'][1]['balance'].update(value='90.00',source='CSV!D8'),'Duplicate balance|different source rows'),
               ('duplicate',lambda a:a['rows'].append(deepcopy(a['rows'][0])),'Duplicate date'),
               ('currency',lambda a:a['currency'].update(value='ZZZ'),'Source value mismatch')]
        for name,change,error in cases:
            with self.subTest(case=name):
                result=answer(self.currency);change(result)
                with self.assertRaisesRegex(ValueError,error): verify(result,source,self.meta)
        altered=deepcopy(source);altered['units']['CSV!D9']='93.00';result=answer(self.currency);result['rows'][1]['balance']['value']='93.00'
        with self.assertRaisesRegex(ValueError,'Running balance.*CSV!'):verify(result,altered,self.meta)
        other=next(c for c in load(self.ledger)[self.ledger/'data/accounts.json']['commodities'] if c!=self.currency)
        altered=deepcopy(source);altered['units']['CSV!B2']=other;result=answer(other)
        with self.assertRaisesRegex(ValueError,'currency.*sources:'):verify(result,altered,self.meta)
        result=answer(self.currency);result.update(read_ok=False)
        with self.assertRaisesRegex(ValueError,'certainty'):verify(result,source,self.meta)

    def test_locale_dates_numbers_native_cells_formula_and_spliced_rows(self):
        source,_=self.source();result=answer(self.currency)
        source['units']['CSV!A8']='02/01/2026';source['units']['CSV!C8']='-10,00'
        self.assertEqual(verify(result,source,self.meta,'de-DE')['rows'][0]['amount'],'10.00')
        with self.assertRaisesRegex(ValueError,'date'):verify(result,source,self.meta,'en-US')
        source['units']['CSV!A8']='2026-01-02';source['units']['CSV!C8']=-10
        self.assertEqual(verify(result,source,self.meta)['rows'][0]['amount'],'10.00')
        source['units']['CSV!C8']='=2-12'
        with self.assertRaisesRegex(ValueError,'Formula'):verify(result,source,self.meta)
        source,_=self.source();result['rows'][0]['description'].update(value='Fable refund',source='CSV!B9')
        with self.assertRaisesRegex(ValueError,'different source rows'):verify(result,source,self.meta)

    def test_escape_and_symlink_components_refused(self):
        source,path=self.source()
        link=self.ledger/'link';link.symlink_to(path)
        with self.assertRaisesRegex(ValueError,'symlink'):safe_source(self.ledger,link)
        folder=self.ledger/'linked';folder.symlink_to(self.ledger,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink'):safe_source(self.ledger,folder/path.name)
        with self.assertRaisesRegex(ValueError,'outside'):safe_source(self.ledger,self.ledger/'..'/path.name)
        with self.assertRaisesRegex(ValueError,'outside'):safe_source(self.ledger,self.base/'elsewhere')

    def test_learned_mapping_roundtrips_all_tabular_readers_without_fixed_period(self):
        for extension in ('csv','xlsx','xls'):
            source,path=self.source(extension);read=answer(self.currency,'CSV' if extension=='csv' else 'Activity')
            statement=verify(read,source,self.meta)
            mapping=learn_mapping(read,source,statement,self.meta,'en')
            self.assertIsNotNone(mapping)
            meta={**self.meta,'statement':mapping};parsed=Registry().get(mapping['reader']).read(path,meta)
            self.assertTrue(check_statement(parsed,account=meta));self.assertEqual(parsed['closingBalance'],'92.00')
            self.assertIsInstance(mapping['options']['period']['end'],dict)

    def session(self,extension='csv'):
        sessions=Statements(self.settings)
        session=sessions.create(file_bytes(self.currency,extension),'Unmapped.'+extension)
        session.update(agent_requested=True,assigned_account=ACCOUNT);sessions.put(session)
        return sessions,session

    def test_fifo_agent_read_apply_audit_and_opt_in_mapping(self):
        self.set_answers(answer(self.currency));sessions,session=self.session()
        sessions.work(threading.Event());result=sessions.get(session['id'])
        self.assertEqual(result['state'],'found',result.get('error'));self.assertTrue(result['agent_verified'])
        self.assertEqual(len(self.calls()),1);self.assertIsNotNone(result['learning'])
        self.assertIsNone(load(self.ledger)[self.ledger/'data/accounts.json']['accounts'][ACCOUNT]['statement'])
        sessions.learn(session['id'],True)
        for row in result['plan']['items']:
            if row['needs_user']:sessions.decide(session['id'],row['row']['serial'],dict(action='pending'))
        sessions.queue_apply(session['id']);sessions.work(threading.Event());result=sessions.get(session['id'])
        self.assertEqual(result['state'],'applied',result.get('error'))
        saved=load(self.ledger)[self.ledger/'data/accounts.json']['accounts'][ACCOUNT]['statement']
        self.assertEqual(saved['reader'],'generic-csv');self.assertEqual(records(self.ledger)[-1]['extraction'],'agent')
        self.assertFalse((sessions.folder(session['id'])/'read.csv').exists())

    def test_apply_rechecks_source_and_discard_does_not_learn(self):
        self.set_answers(answer(self.currency));sessions,s=self.session();sessions.work(threading.Event());s=sessions.get(s['id'])
        sessions.learn(s['id'],True)
        for item in s['plan']['items']:
            if item['needs_user']:sessions.decide(s['id'],item['row']['serial'],dict(action='pending'))
        path=sessions.folder(s['id'])/'read.csv';path.write_bytes(path.read_bytes()+b'Changed\n')
        sessions.queue_apply(s['id']);sessions.work(threading.Event())
        self.assertEqual(sessions.get(s['id'])['state'],'failed');self.assertIn('source changed',sessions.get(s['id'])['error'])
        sessions.discard(s['id'])
        self.assertIsNone(load(self.ledger)[self.ledger/'data/accounts.json']['accounts'][ACCOUNT]['statement'])
        self.assertEqual(records(self.ledger),[])

    def test_unknown_add_interpretation_is_read_only_and_queues_own_session(self):
        self.set_answers(answer(self.currency))
        with self.client(worker=False) as client:
            identity=self.create(client,'statement for Cited Export',upload=False)
            response=client.post(f'/api/drafts/{identity}/files',files={'files':('Unknown.csv',file_bytes(self.currency,'csv'),'text/csv')})
            fid=response.json()['files'][0]['id'];body=dict(choices={fid:dict(account=ACCOUNT)})
            preview=client.post(f'/api/drafts/{identity}/interpret',json=body).json()['files'][0]
            self.assertEqual(preview['kind'],'statement');self.assertNotIn('reader',preview);self.assertEqual(self.calls(),[])
            session=client.post(f'/api/drafts/{identity}/continue',json=body).json()['statements'][0]
            self.assertTrue(session['agent_requested']);self.assertEqual(self.calls(),[])
            client.app.state.statements.work(threading.Event());session=client.get('/api/statements/'+session['id']).json()
            self.assertEqual(session['state'],'found',session.get('error'))
            response=client.post('/api/statements/'+session['id']+'/mapping',json=dict(enabled=True))
            self.assertEqual(response.status_code,200,response.text)

    def test_pause_auth_timeout_resume_and_bad_answer_stop(self):
        sessions,s=self.session();sessions.agent_pause=AgentPause(self.ledger)
        for kind in ('auth','transient','usage_limit'):
            failure=agent.AgentFailure('Offline outage',dict(failure_kind=kind))
            with patch('agent.run',side_effect=failure):sessions.work(threading.Event())
            self.assertEqual(sessions.get(s['id'])['state'],'agent_waiting');self.assertTrue(sessions.agent_pause.blocked())
            sessions.agent_pause.success()
        self.set_answers(answer(self.currency));sessions.work(threading.Event());self.assertEqual(sessions.get(s['id'])['state'],'found')
        sessions,s=self.session();self.set_answers(dict(kind='timeout'))
        sessions.settings=replace(self.settings,timeout=.2);sessions.agent_pause=AgentPause(self.ledger)
        sessions.work(threading.Event());self.assertEqual(sessions.get(s['id'])['state'],'agent_waiting')
        sessions.agent_pause.success();self.set_answers({**answer(self.currency), 'read_ok':False})
        sessions.work(threading.Event());self.assertEqual(sessions.get(s['id'])['state'],'couldnt_read')
        self.assertIn('certainty',sessions.get(s['id'])['error'])

    def test_pdf_and_workbooks_use_fake_adapter_then_real_apply_without_learning(self):
        for extension in ('pdf','xlsx','xls'):
            with self.subTest(extension=extension):
                # Each format uses an independent account/period identity.
                account=ACCOUNT+' '+extension
                docs=load(self.ledger);docs[self.ledger/'data/accounts.json']['accounts'][account]=deepcopy(self.meta)
                dump(self.ledger/'data/accounts.json',docs[self.ledger/'data/accounts.json']);self.jj('commit','-m','Fictional format account')
                self.set_answers(answer(self.currency,'Activity',pdf=extension=='pdf'))
                sessions,s=self.session(extension);s['assigned_account']=account;sessions.put(s)
                sessions.work(threading.Event());s=sessions.get(s['id']);self.assertEqual(s['state'],'found',s.get('error'))
                for item in s['plan']['items']:
                    if item['needs_user']:sessions.decide(s['id'],item['row']['serial'],dict(action='pending'))
                sessions.queue_apply(s['id']);sessions.work(threading.Event());s=sessions.get(s['id'])
                self.assertEqual(s['state'],'applied',s.get('error'))
                self.assertIsNone(load(self.ledger)[self.ledger/'data/accounts.json']['accounts'][account]['statement'])

    def test_card_debt_figures_are_verified_before_signed_normalization(self):
        source,_=self.source();read=answer(self.currency)
        read['balanceStyle']='debt';read['closingBalance']['value']='108.00';source['units']['CSV!B4']='108.00'
        for row,printed in zip(read['rows'],('110.00','108.00')):
            row['balance']['value']=printed;source['units'][row['balance']['source']]=printed
        result=verify(read,source,{**self.meta,'kind':'card'})
        self.assertEqual(result['openingBalance'],'-100.00');self.assertEqual(result['closingBalance'],'-108.00')
        self.assertEqual(result['rows'][0]['balance'],'-110.00')
        with self.assertRaisesRegex(ValueError,'card account'):verify(read,source,self.meta)

    def test_recognised_parse_failure_never_calls_agent(self):
        from statement_fixtures import statement_pdf
        card='Liabilities:Plastic:Recognised'
        docs=load(self.ledger);docs[self.ledger/'data/accounts.json']['accounts'][card]=meta(kind='card',currency=self.currency,statement=dict(reader='example-card-pdf',identifier='5678'))
        dump(self.ledger/'data/accounts.json',docs[self.ledger/'data/accounts.json'])
        sessions=Statements(self.settings);s=sessions.create(statement_pdf(),'Recognised.pdf')
        with patch('statement_readers.Reader.read',side_effect=ValueError('Invalid printed layout')),patch('agent.run') as run,patch('statements.plan') as plan:
            sessions.work(threading.Event());result=sessions.get(s['id'])
            self.assertEqual(result['state'],'couldnt_read');self.assertIn('recognised reader',result['error'])
            run.assert_not_called();plan.assert_not_called()

    def test_explicit_direction_column_headers_and_missing_bank_balance(self):
        source,_=self.source();read=answer(self.currency)
        for i,direction in ((8,'Debit'),(9,'Credit')):
            source['units'][f'CSV!F{i}']=direction
            read['rows'][i-8]['direction']['source']=f'CSV!F{i}'
        self.assertTrue(verify(read,source,self.meta))
        read['rows'][0]['balance']=None
        with self.assertRaisesRegex(ValueError,'Missing running balance citation at CSV!C8'):verify(read,source,self.meta)
        read=answer(self.currency);source['units']['CSV!C7']='Debit';source['units']['CSV!C8']='10.00'
        source['units']['CSV!G7']='Credit';source['units']['CSV!G9']='2.00'
        read['rows'][1]['amount']['source']='CSV!G9';read['rows'][1]['direction']['source']='CSV!G9'
        self.assertTrue(verify(read,source,self.meta))
        read['rows'][0]['direction'].update(value='Credit',source='CSV!G7')
        with self.assertRaisesRegex(ValueError,'Direction column'):verify(read,source,self.meta)
        read['rows'][0]['direction'].update(value='Debit',source='CSV!C7')
        read['rows'][1]['direction'].update(value='Debit',source='CSV!C7')
        with self.assertRaisesRegex(ValueError,'Duplicate direction citation at CSV!C7'):verify(read,source,self.meta)

    def test_multi_page_and_sheet_addresses_evident_dates_and_whitespace(self):
        source,path=self.source('pdf')
        read=answer(self.currency,pdf=True)
        # Move the second movement to a separately numbered page/line.
        source['units']['p2:l1']=source['units'].pop('p1:l9')
        for fact in read['rows'][1].values():
            if fact: fact['source']='p2:l1'
        read['rows'][0]['description']['value']='Fable   Shop'
        self.assertEqual(verify(read,source,self.meta)['rows'][1]['page'],2)
        source,_=self.source();read=answer(self.currency)
        source['units']['CSV!D1']='01/28/2026';source['units']['CSV!B1']='01/01/2026'
        source['units']['CSV!A8']='01/02/2026';source['units']['CSV!A9']='01/03/2026'
        self.assertTrue(verify(read,source,self.meta,'en-GB'))
        for key in ('openingBalance','closingBalance','debits','credits'):
            fact=read[key];old=fact['source'];fact['source']='Summary!'+old.split('!')[1];source['units'][fact['source']]=source['units'].pop(old)
        self.assertTrue(verify(read,source,self.meta,'en-GB'))

    def test_summary_cannot_be_invented_from_movement_cells(self):
        source,_=self.source();read=answer(self.currency)
        read['debits'].update(value='10.00',source='CSV!C8')
        with self.assertRaisesRegex(ValueError,'Printed summary citation.*movement source'):verify(read,source,self.meta)

    def test_learned_csv_uses_next_files_period_currency_and_extra_rows(self):
        source,path=self.source();read=answer(self.currency);statement=verify(read,source,self.meta)
        mapping=learn_mapping(read,source,statement,self.meta,'en')
        text=path.read_text().replace('2026-01-','2026-02-').replace('Closing,92.00','Closing,91.00').replace('Debits,10.00','Debits,11.00')
        path.write_text(text+'2026-02-04,Additional Fable,-1.00,91.00,CITED-003\n')
        parsed=Registry().get(mapping['reader']).read(path,{**self.meta,'statement':mapping})
        self.assertEqual(parsed['periodStart'],'2026-02-01');self.assertEqual(parsed['periodEnd'],'2026-02-28')
        self.assertTrue(check_statement(parsed,account=self.meta))
        self.assertEqual(mapping['options']['skip_rows'],[])
        self.assertEqual(len(parsed['rows']),3);self.assertEqual(parsed['closingBalance'],'91.00')

    def test_workbook_sheet_named_csv_retains_the_workbook_reader(self):
        import openpyxl
        source,path=self.source('xlsx')
        book=openpyxl.load_workbook(path);book.active.title='CSV';book.save(path);book.close()
        source=extract(path,self.work);read=answer(self.currency,'CSV')
        statement=verify(read,source,self.meta);mapping=learn_mapping(read,source,statement,self.meta,'en')
        self.assertEqual(mapping['reader'],'generic-sheet')
        parsed=Registry().get(mapping['reader']).read(path,{**self.meta,'statement':mapping})
        self.assertTrue(check_statement(parsed,account=self.meta))

    def test_pdf_phrases_and_references_cannot_be_shortened(self):
        source,_=self.source('pdf')
        for name,value in (('description','Fable'),('reference','CITED')):
            read=answer(self.currency,pdf=True);read['rows'][0][name]['value']=value
            with self.subTest(field=name),self.assertRaisesRegex(ValueError,'Source value mismatch for '+name):verify(read,source,self.meta)

    def test_reading_reloads_concurrent_ledger_changes_and_locks_checklist_snapshot(self):
        from ledger_lock import ledger_lock
        from statements import fingerprint
        import fcntl, os
        self.set_answers(answer(self.currency));sessions,s=self.session()
        real_run=agent.run
        from statement_rules import plan as real_plan
        def concurrent_writer(*args,**kwargs):
            result=real_run(*args,**kwargs)
            with ledger_lock(self.ledger):
                docs=load(self.ledger);docs[self.ledger/'data/accounts.json']['accounts']['Equity:Opening Balances']={}
                docs[self.ledger/'data/expenses/2026.json']=[
                    store_entry('a'*16,'2026-01-01','Concurrent opening',[posting(ACCOUNT,'100.00',self.currency),posting('Equity:Opening Balances','-100.00',self.currency)]),
                    store_entry('b'*16,'2026-01-02','Fable Shop',[posting('Expenses:Food','10.00',self.currency),posting(ACCOUNT,'-10.00',self.currency)],references={'reference':'CITED-001','order-id':[]})]
                for path,value in docs.items():dump(path,value)
                self.jj('commit','-m','Fictional concurrent addition')
            return result
        def locked_plan(*args,**kwargs):
            fd=os.open(self.ledger/'.lock',os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:os.close(fd)
            return real_plan(*args,**kwargs)
        with patch('agent.run',side_effect=concurrent_writer),patch('statements.plan',side_effect=locked_plan):
            sessions.work(threading.Event())
        result=sessions.get(s['id']);self.assertEqual(result['state'],'found',result.get('error'))
        first=next(i for i in result['plan']['items'] if i['row']['reference']=='CITED-001')
        self.assertEqual(first['entry_id'],'b'*16);self.assertIsNone(result['opening'])
        self.assertEqual(result['fingerprint'],fingerprint(self.ledger));self.assertEqual(len(self.calls()),1)

    def test_account_or_locale_change_during_reading_stops_before_matching(self):
        from ledger_lock import ledger_lock
        original_manifest=json.loads((self.ledger/'ledger.json').read_text())
        real_run=agent.run
        for field in ('currency','locale'):
            with self.subTest(field=field):
                with ledger_lock(self.ledger):
                    docs=load(self.ledger);docs[self.ledger/'data/accounts.json']['accounts'][ACCOUNT]=deepcopy(self.meta)
                    dump(self.ledger/'data/accounts.json',docs[self.ledger/'data/accounts.json']);dump(self.ledger/'ledger.json',original_manifest)
                    self.jj('commit','-m','Fictional reading context baseline')
                self.set_answers(answer(self.currency));sessions,s=self.session()
                def changed_context(*args,**kwargs):
                    result=real_run(*args,**kwargs)
                    with ledger_lock(self.ledger):
                        if field=='currency':
                            docs=load(self.ledger);declarations=docs[self.ledger/'data/accounts.json']
                            declarations['accounts'][ACCOUNT]['currency']=next(c for c in declarations['commodities'] if c!=self.currency)
                            dump(self.ledger/'data/accounts.json',declarations)
                        else:dump(self.ledger/'ledger.json',{**original_manifest,'locale':'de-DE'})
                        self.jj('commit','-m','Fictional concurrent reading context change')
                    return result
                with patch('agent.run',side_effect=changed_context),patch('statements.plan') as plan:
                    sessions.work(threading.Event());plan.assert_not_called()
                result=sessions.get(s['id']);self.assertEqual(result['state'],'couldnt_read');self.assertIn('changed during the reading',result['error'])
