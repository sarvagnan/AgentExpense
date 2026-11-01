"""Offline multi-currency proposals, settings commits and statement application."""
from copy import deepcopy
from decimal import Decimal
import json
import subprocess
import threading
from unittest.mock import patch
from support import Fixture, ready
from transactions import to_store, check, ProposalError, clean
from store import dump, load, read_json, entries
from statement_pdf import parse_text, account_currency
from statement_fixtures import TEXT, statement_pdf
from statements import Statements
from statement_rules import convert, foreign
from agent import PREFACE, statement_result

CARD='Liabilities:Plastic:Lumen'
BANK='Assets:Checking:Alder'
TRAVEL='Assets:Travel:Willow'


class CurrencyTests(Fixture):
    def setup_currencies(self):
        path=self.ledger/'data/accounts.json';value=read_json(path)
        value['commodities']=['INR','USD','EUR','GBP']
        value['accounts'].update({CARD:dict(kind='card',currency='USD',label='Lumen Card',statement=dict(reader='example-card-pdf',identifier='5678')),
                                  BANK:dict(kind='bank',currency='USD'),TRAVEL:dict(kind='bank',currency='EUR'),'Equity:Opening Balances':{}})
        dump(path,value)

    def proposal(self, original='EUR', charged='USD', cost=True):
        answer=ready(new=False);answer['attachment_names']=[]
        answer['entry']['original_amount']=dict(amount='46.80',commodity=original)
        answer['entry']['postings']=[dict(account='Expenses:Food',amount='46.80',commodity=original,cost=dict(amount='51.48',commodity=charged) if cost else None),dict(account=CARD,amount='-51.48',commodity=charged,cost=None)]
        return answer

    def test_foreign_expense_requires_charge_and_same_currency_needs_no_conversion(self):
        self.setup_currencies()
        from currency_drafts import currency_request
        question=self.proposal(cost=False);question.update(status='question',question='Whose expense is this?')
        self.assertIsNone(currency_request(question,self.ledger),'A known charge must retain its ordinary question')
        question['question']='How much was charged?'
        question['entry']['postings'][1]['amount']='0'
        self.assertEqual(currency_request(question,self.ledger)['kind'],'expense')
        question['entry']['postings'][1]['commodity']='EUR'
        self.assertIsNone(currency_request(question,self.ledger),'The marker must use the declared paying currency')
        question['entry']['postings'][0]['amount']='NaN'
        self.assertIsNone(currency_request(question,self.ledger))
        malformed=self.proposal(cost=False);malformed.update(status='question')
        malformed['entry']['postings'][0]['commodity']='not-a-currency'
        self.assertIsNone(currency_request(malformed,self.ledger))
        with self.assertRaisesRegex(ProposalError,'needs the amount charged or a rate'):
            to_store(self.proposal(cost=False),self.ledger,0)
        entry,decl,_=to_store(self.proposal(),self.ledger,0);check(entry,decl,self.ledger)
        self.assertEqual(entry['postings'][0]['cost'],dict(amount='51.48',commodity='USD',per='total'))
        # INR is a valid foreign commodity on a USD account too.
        entry,decl,_=to_store(self.proposal(original='INR'),self.ledger,0);check(entry,decl,self.ledger)
        answer=self.proposal(original='USD',cost=False);answer['entry']['original_amount']=None;answer['entry']['postings'][1]['amount']='-46.80'
        entry,decl,_=to_store(answer,self.ledger,0);check(entry,decl,self.ledger)
        self.assertNotIn('cost',entry['postings'][0])
        answer['entry']['original_amount']=dict(amount='46.80',commodity='USD')
        with self.assertRaisesRegex(ProposalError,'must differ'):
            to_store(answer,self.ledger,0)
        self.assertIn('ask for the amount charged',PREFACE)
        self.assertIn('both the sent and received amounts',PREFACE)

    def test_cross_currency_transfer_keeps_sent_received_and_cost(self):
        self.setup_currencies();answer=ready(new=False);answer['attachment_names']=[];answer['entry']['original_amount']=None
        answer['entry']['postings']=[dict(account=BANK,amount='-1000.00',commodity='USD',cost=dict(amount='920.00',commodity='EUR')),dict(account=TRAVEL,amount='920.00',commodity='EUR',cost=None)]
        entry,decl,_=to_store(answer,self.ledger,0);check(entry,decl,self.ledger)
        self.assertEqual([p['amount'] for p in entry['postings']],['-1000.00','920.00'])
        self.assertEqual([p['commodity'] for p in entry['postings']],['USD','EUR'])
        answer['entry']['postings'][1]['commodity']='USD'
        entry,decl,_=to_store(answer,self.ledger,0)
        with self.assertRaisesRegex(ProposalError,'account currency EUR'):
            check(entry,decl,self.ledger)

    def test_settings_api_commits_verified_change_under_write_rules_and_locks_activity(self):
        self.setup_currencies()
        opening=dict(id='1'*16,date='2026-10-01',status='confirmed',description='Opening',postings=[dict(account=TRAVEL,amount='650.00',commodity='EUR',assert_=None),dict(account='Equity:Opening Balances',amount='-650.00',commodity='EUR')])
        opening['postings'][0].pop('assert_');opening['postings'][0]['assert']=dict(amount='650.00',commodity='EUR')
        dump(self.ledger/'data/expenses/2026.json',[opening]);self.jj('commit','-m','Currency settings baseline')
        with self.client(worker=False) as client:
            before=(self.ledger/'data/accounts.json').read_bytes()
            response=client.post('/api/settings/default-currency',json={'currency':'GBP'})
            self.assertEqual(response.status_code,200,response.text);self.assertTrue(clean(self.ledger))
            self.assertEqual((self.ledger/'data/accounts.json').read_bytes(),before)
            self.assertIn('Change default currency to GBP',self.jj('log','-r','@-','--no-graph','-T','description'))
            response=client.post('/api/accounts/currency',json={'account':TRAVEL,'currency':'USD'})
            self.assertEqual(response.status_code,200,response.text);self.assertTrue(clean(self.ledger))
            row=entries(load(self.ledger))[0];self.assertEqual([p['commodity'] for p in row['postings']],['USD','USD']);self.assertEqual(row['postings'][0]['amount'],'650.00')
            denied=client.post('/api/settings/default-currency',json={'currency':'EUR'},headers={'origin':'https://foreign.invalid'})
            self.assertEqual(denied.status_code,403)
            (self.ledger/'uncommitted.txt').write_text('Unfinished work')
            response=client.post('/api/settings/default-currency',json={'currency':'EUR'})
            self.assertEqual(response.status_code,409);self.assertIn('uncommitted',response.text)
        (self.ledger/'uncommitted.txt').unlink()
        row['postings'][1]['account']='Expenses:Food';dump(self.ledger/'data/expenses/2026.json',[row]);self.jj('commit','-m','Activity fixes currency')
        with self.client(worker=False) as client:
            response=client.post('/api/accounts/currency',json={'account':TRAVEL,'currency':'EUR'})
            self.assertEqual(response.status_code,409);self.assertIn('fixed by transaction',response.text)

    def test_failed_settings_verification_rolls_back(self):
        self.setup_currencies();self.jj('commit','-m','Currency baseline')
        before=self.bytes()
        real_run=subprocess.run
        def run(args,*a,**kw):
            if args==['make','commit']:
                return subprocess.CompletedProcess(args,1,'','Injected verification failure')
            return real_run(args,*a,**kw)
        from currency_settings import update_currency
        with patch('currency_settings.subprocess.run',side_effect=run),self.assertRaisesRegex(RuntimeError,'failed verification'):
            update_currency(self.settings,'EUR')
        self.assertEqual(self.bytes(),before);self.assertTrue(clean(self.ledger))

    def test_usd_statement_applies_foreign_cost_and_replaces_estimate(self):
        self.setup_currencies()
        opening=dict(id='1'*16,date='2026-01-01',status='confirmed',description='Opening',postings=[dict(account=CARD,amount='0.00',commodity='USD'),dict(account='Equity:Opening Balances',amount='0.00',commodity='USD')])
        dump(self.ledger/'data/expenses/2026.json',[opening]);self.jj('commit','-m','USD statement baseline')
        text=TEXT.replace('Statement period:', 'Statement currency: USD\nStatement period:').replace('2.00 | USD','2.00 | EUR')
        sessions=Statements(self.settings);session=sessions.create(statement_pdf(text),'Fictional-USD.pdf')
        with patch('agent.run') as agent:
            sessions.read(session);agent.assert_not_called()
        session=sessions.get(session['id']);self.assertEqual(session['statement']['currency'],'USD');self.assertTrue(all(c['ok'] for c in session['checks']))
        self.assertIn('USD',session['checks'][0]['text']);self.assertNotIn('INR',session['checks'][0]['text'])
        sessions.decide(session['id'],'EX-001',dict(action='account',account='Expenses:Food'))
        sessions.decide(session['id'],'EX-002',dict(action='account',account=BANK))
        sessions.queue_apply(session['id']);sessions.work(threading.Event())
        session=sessions.get(session['id']);self.assertEqual(session['state'],'applied',session.get('error'));self.assertTrue(clean(self.ledger))
        entry=next(e for e in entries(load(self.ledger)) if e.get('serial-number')=='EX-001')
        self.assertEqual(entry['postings'][0]['cost'],dict(amount='120.00',commodity='USD',per='total'))
        self.assertEqual(entry['postings'][1]['commodity'],'USD')
        row={**parse_text(text)['rows'][0],'amount':'121.00'}
        self.assertEqual(foreign(entry,'USD')['commodity'],'EUR')
        convert(entry,row,CARD)
        self.assertEqual(entry['postings'][0]['cost']['amount'],'121.00');self.assertEqual(entry['postings'][0]['cost']['commodity'],'USD')
        self.assertIn('replaces 120.00 USD',entry['notes'][-1])

    def test_printed_mismatch_and_missing_foreign_currency_stop(self):
        self.setup_currencies();sessions=Statements(self.settings)
        session=sessions.create(statement_pdf(TEXT.replace('Statement period:','Statement currency: EUR\nStatement period:')),'Mismatch.pdf')
        with patch('agent.run') as agent:
            sessions.read(session)
        agent.assert_not_called()
        self.assertEqual(sessions.get(session['id'])['state'], 'currency_mismatch')
        self.assertEqual(sessions.get(session['id'])['file_currency'], 'EUR')
        with self.assertRaisesRegex(ValueError,'explicit currency'):
            parse_text(TEXT.replace('2.00 | USD','2.00 |'))
        from statement_fixtures import statement_answer
        answer=statement_answer();answer['transactions'][0]['foreign_currency']=None
        with self.assertRaisesRegex(ValueError,'explicit currency'):
            statement_result(answer)

    def test_currency_form_validates_rounds_commits_and_records_estimate_without_another_agent(self):
        self.setup_currencies()
        path=self.ledger/'data/accounts.json';declared=read_json(path);declared['commodities'].remove('EUR');declared['accounts'].pop(TRAVEL);dump(path,declared)
        self.jj('commit','-m','Currency form baseline')
        answer=self.proposal(cost=False);answer.update(status='question',question='How much was charged?')
        answer['entry']['postings'][1]['amount']='0'
        self.set_answers(answer)
        with self.client(timeout=30) as client:
            identity=self.create(client,upload=False);self.save(client,identity)
            draft=self.wait(client,identity,'question');self.assertEqual(draft['currency_request']['currency'],'USD')
            self.assertIn('unknown amount marker',self.calls()[0]['argv'][-1])
            for value in ('','0','-1','NaN','Infinity','abc','1e4'):
                response=client.post(f'/api/drafts/{identity}/currency',json={'rate':value})
                self.assertEqual(response.status_code,422,response.text)
                self.assertEqual(client.get(f'/api/drafts/{identity}').json()['state'],'question')
            response=client.post(f'/api/drafts/{identity}/currency',json={'rate':'1.1001','estimate':True})
            self.assertEqual(response.status_code,200,response.text)
            result=self.wait(client,identity,'added')
            entry=entries(load(self.ledger))[0]
            self.assertEqual(entry['postings'][0]['cost'],dict(amount='51.48',commodity='USD',per='total'))
            self.assertEqual(entry['postings'][1]['amount'],'-51.48')
            self.assertIn('EUR',read_json(self.ledger/'data/accounts.json')['commodities'])
            self.assertIn('estimate',entry['notes'][-1]);self.assertEqual(len(self.calls()),1)
            self.assertEqual(result['questions'][-1]['answer'],'51.48 USD charged (estimate)')
            self.assertTrue(clean(self.ledger))

    def test_transfer_form_saves_both_amounts(self):
        self.setup_currencies();self.jj('commit','-m','Transfer form baseline')
        answer=self.proposal(cost=False);answer.update(status='question',question='How much arrived?')
        answer['entry']['original_amount']=None
        answer['entry']['postings']=[dict(account=BANK,amount='-1000',commodity='USD',cost=None),dict(account=TRAVEL,amount='0',commodity='EUR',cost=None)]
        self.set_answers(answer)
        with self.client(timeout=30) as client:
            identity=self.create(client,upload=False);self.save(client,identity)
            self.assertEqual(self.wait(client,identity,'question')['currency_request']['kind'],'transfer')
            response=client.post(f'/api/drafts/{identity}/currency',json={'amount':'920.125','estimate':False})
            self.assertEqual(response.status_code,200,response.text);self.wait(client,identity,'added')
            entry=entries(load(self.ledger))[0]
            self.assertEqual(entry['postings'][1]['amount'],'920.13')
            self.assertEqual(entry['postings'][0]['cost']['amount'],'920.13')
            self.assertEqual(entry['postings'][0]['amount'],'-1000.00')

    def test_statement_mismatch_reassign_and_create_account_through_verified_endpoint(self):
        self.setup_currencies()
        path=self.ledger/'data/accounts.json';declared=read_json(path);declared['commodities'].remove('GBP');dump(path,declared)
        self.jj('commit','-m','Statement account baseline')
        sessions=Statements(self.settings)
        text=TEXT.replace('Statement period:','Statement currency: EUR\nStatement period:').replace('2.00 | USD','2.00 | GBP')
        session=sessions.create(statement_pdf(text),'Fictional-EUR.pdf');sessions.read(session)
        with self.assertRaisesRegex(ValueError,'not ready'):
            sessions.queue_apply(session['id'])
        with self.assertRaisesRegex(ValueError,'statement currency'):
            sessions.assign(session['id'],CARD)
        with self.client(worker=False) as client:
            denied=client.post('/api/accounts',json=dict(kind='card',name='Willow Card',currency='EUR'),headers={'origin':'https://foreign.invalid'})
            self.assertEqual(denied.status_code,403)
            for kind,name,currency in [('invalid','Willow','EUR'),('card','../bad:name','EUR'),('card','Willow','ZZZ')]:
                self.assertEqual(client.post('/api/accounts',json=dict(kind=kind,name=name,currency=currency)).status_code,409)
            response=client.post('/api/accounts',json=dict(kind='card',name='Willow Card',currency='EUR'))
            self.assertEqual(response.status_code,201,response.text);account=response.json()['account'];self.assertTrue(clean(self.ledger))
            self.assertIn('Add Willow Card account (EUR)',self.jj('log','-r','@-','--no-graph','-T','description'))
            self.assertEqual(client.post('/api/accounts',json=dict(kind='card',name='Willow Card',currency='EUR')).status_code,409)
        sessions.assign(session['id'],account);sessions.read(sessions.get(session['id']))
        saved=sessions.get(session['id']);self.assertEqual(saved['state'],'found');self.assertEqual(saved['statement']['account'],account)
        self.assertTrue(all(c['ok'] for c in saved['checks']))
        self.assertIsNotNone(saved['opening'])
        self.assertFalse(entries(load(self.ledger)), 'An opening is only proposed before Apply')
        for item in saved['plan']['items']:
            sessions.decide(session['id'],item['row']['serial'],dict(action='account',account='Expenses:Food'))
        sessions.queue_apply(session['id']);sessions.work(threading.Event())
        applied=sessions.get(session['id']);self.assertEqual(applied['state'],'applied',applied.get('error'))
        opening=next(e for e in entries(load(self.ledger)) if e['description']=='Statement starting balance')
        self.assertEqual(opening['date'],'2026-01-01');self.assertIn(opening['id'],applied['result']['entry_ids'])
        self.assertTrue(clean(self.ledger))

    def test_account_creation_rolls_back_verification_failure_and_rejects_dirty_ledger(self):
        from currency_settings import create_account
        before=self.bytes()
        with patch('currency_settings.subprocess.run',return_value=subprocess.CompletedProcess([],1,'','injected failure')):
            with self.assertRaisesRegex(RuntimeError,'failed verification'):
                create_account(self.settings,'card','Willow Card','EUR')
        self.assertEqual(self.bytes(),before);self.assertTrue(clean(self.ledger))
        (self.ledger/'unfinished.txt').write_text('Human work')
        with self.assertRaisesRegex(ValueError,'uncommitted'):
            create_account(self.settings,'bank','Willow Current','EUR')
