"""Regular contributions use the same checked writers as other usual payments."""
from copy import deepcopy
import json
import secrets
import threading
import subprocess
from unittest.mock import patch
from support import Fixture, ready, meta, posting, entry as store_entry
from test_rules import payment_rule
from store import load, dump, entries, read_json
from rules_service import queue_usual, operate, snapshot
from audit import records
from drafts import Drafts
from ledger_lock import ledger_lock
from transactions import Transaction

DESTINATION='Assets:Retirement'
PAYER='Assets:Contribution Bank'

class InvestmentRules(Fixture):
    def seed(self, varying=False):
        docs=load(self.ledger);accounts=docs[self.ledger/'data/accounts.json']
        accounts['accounts'].update({DESTINATION:meta(kind='investment',currency='INR'),PAYER:meta(kind='bank',currency='INR')})
        dump(self.ledger/'data/accounts.json',accounts)
        r=payment_rule(day='2026-10-15',amount='5000.00');r['description']='SIP contribution'
        r['postings']=[posting(DESTINATION,'5000.00'),posting(PAYER,'-5000.00')]
        if varying:r['amount']='varies'
        dump(self.ledger/'data/forecast.json',[r]);self.jj('commit','-m','Fictional contribution baseline')
        return r

    def proposal(self, day='2026-10-15', amount='5000.00'):
        p=ready(new=False);p['attachment-names']=[]
        p['entry'].update(date=day,description='SIP contribution',postings=[dict(account=DESTINATION,amount=amount,commodity='INR',cost=None),dict(account=PAYER,amount='-'+amount,commodity='INR',cost=None)])
        return p

    def bank_statement(self, rows, *, start='2026-10-01', end='2026-12-31', opening='20000.00'):
        """Read a fictional statement through its real reader and Apply queue."""
        from decimal import Decimal
        from statements import Statements
        docs=load(self.ledger);accounts=docs[self.ledger/'data/accounts.json']
        total=sum((Decimal(amount) for _,_,amount in rows),Decimal(0));closing=Decimal(opening)-total
        accounts['accounts'][PAYER]['statement']=dict(reader='generic-csv',identifier=None,options=dict(currency='INR',date_format='%Y-%m-%d',period={'start':start,'end':end},opening_balance=opening,closing_balance=str(closing),debits=str(total),credits='0.00',columns={'date':'Date','description':'Description','amount':'Amount','balance':'Balance'}))
        dump(self.ledger/'data/accounts.json',accounts);self.jj('commit','-m','Fictional bank statement baseline')
        balance=Decimal(opening);csv='Date,Description,Amount,Balance\n'
        for day,description,amount in rows:
            balance-=Decimal(amount);csv+=f'{day},{description},-{amount},{balance}\n'
        sessions=Statements(self.settings);s=sessions.create(csv.encode(),'Contribution.csv',client_time='2026-11-01T12:00:00+00:00',client_zone='UTC');s['assigned_account']=PAYER;sessions.put(s);sessions.read(s)
        s=sessions.get(s['id']);self.assertIn(s['state'],('found','review'),s.get('error'))
        for item in s['plan']['items']:
            if item['needs_user']:sessions.decide(s['id'],item['row']['serial'],dict(action='account',account=DESTINATION))
        return sessions,s['id']

    def assert_statement_pays_bill(self, day):
        r=self.seed(varying=True);r.update(date='2026-11-01');r['schedule']['from']=r['date']
        dump(self.ledger/'data/forecast.json',[r]);self.jj('commit','-m','Fictional November occurrence')
        bill=dict(amount='5000.00',received='2026-10-01',**{'pay-by':'2026-11-01','occurrence':r['date'],'evidence':[]})
        operate(self.settings,secrets.token_hex(16),dict(action='bill-saved',rule_id=r['id'],bill=bill))
        sessions,identity=self.bank_statement([(day,'SIP contribution','5000.00')]);sessions.queue_apply(identity);sessions.work(threading.Event());s=sessions.get(identity)
        self.assertEqual(s['state'],'applied',s.get('error'))
        payment=next(e for e in entries(load(self.ledger)) if e['description']=='SIP contribution')
        self.assertEqual(payment['rule'],dict(id=r['id'],occurrence='2026-11-01'))
        current=read_json(self.ledger/'data/forecast.json')[0];self.assertIsNone(current['bill']);self.assertEqual(current['date'],'2026-12-01')
        paid=next(line for line in records(self.ledger) if line.get('rule_action')=='bill-paid')
        self.assertEqual(paid['bill'],bill);self.assertEqual(paid['files'],[dict(original_name='Contribution.csv',filed_path=s['result']['source'])]);self.assertEqual(paid['doubts'],[])

    def test_statement_pays_saved_bill_early(self):
        self.assert_statement_pays_bill('2026-10-31')

    def test_statement_pays_saved_bill_on_time(self):
        self.assert_statement_pays_bill('2026-11-01')

    def test_statement_pays_saved_bill_late(self):
        self.assert_statement_pays_bill('2026-12-02')

    def test_statement_preserves_preexisting_rule_link_and_absence(self):
        r=self.seed()
        existing=[store_entry(f'{n:016x}',day,description,deepcopy(r['postings']),rule=link)
                  for n,day,description,link in [
                      (1,'2026-10-15','SIP contribution declined',None),
                      (2,'2026-11-15','SIP contribution corrected',None),
                      (3,'2026-12-15','SIP contribution linked',dict(id=r['id'],occurrence='2026-09-15'))]]
        dump(self.ledger/'data/expenses/2026.json',existing)
        sessions,identity=self.bank_statement([(e['date'],e['description'],'5000.00') for e in existing],opening='0.00')
        sessions.queue_apply(identity);sessions.work(threading.Event());s=sessions.get(identity)
        self.assertEqual(s['state'],'applied',s.get('error'))
        actual={e['id']:e['rule'] for e in entries(load(self.ledger))}
        self.assertEqual({e['id']:actual[e['id']] for e in existing},{e['id']:e['rule'] for e in existing})
        self.assertEqual(read_json(self.ledger/'data/forecast.json'),[r])

    def test_statement_never_creates_an_automatic_rule(self):
        r=self.seed();dump(self.ledger/'data/forecast.json',[])
        dump(self.ledger/'data/expenses/2026.json',[store_entry('0000000000000001','2026-09-15','SIP contribution',deepcopy(r['postings']))])
        sessions,identity=self.bank_statement([('2026-10-15','SIP contribution','5000.00')],end='2026-10-31',opening='-5000.00')
        sessions.queue_apply(identity);sessions.work(threading.Event());s=sessions.get(identity)
        self.assertEqual(s['state'],'applied',s.get('error'))
        self.assertEqual(read_json(self.ledger/'data/forecast.json'),[])
        self.assertTrue(all(e['rule'] is None for e in entries(load(self.ledger))))

    def test_statement_leaves_ambiguous_bills_and_wrong_amount_unlinked(self):
        r=self.seed(varying=True)
        r['bill']=dict(amount='5000.00',received='2026-10-01',**{'pay-by':None,'occurrence':r['date'],'evidence':[]})
        other=deepcopy(r);other['id']='f000000000000002'
        dump(self.ledger/'data/forecast.json',[r,other])
        sessions,identity=self.bank_statement([('2026-10-15','SIP contribution','5000.00'),('2026-11-15','SIP contribution','6000.00')])
        sessions.queue_apply(identity);sessions.work(threading.Event());s=sessions.get(identity)
        self.assertEqual(s['state'],'applied',s.get('error'));self.assertEqual(read_json(self.ledger/'data/forecast.json'),[r,other])
        self.assertTrue(all(e['rule'] is None for e in entries(load(self.ledger))))

    def test_statement_wrong_amount_does_not_consume_a_unique_bill(self):
        r=self.seed(varying=True)
        r['bill']=dict(amount='5000.00',received='2026-10-01',**{'pay-by':None,'occurrence':r['date'],'evidence':[]})
        dump(self.ledger/'data/forecast.json',[r])
        sessions,identity=self.bank_statement([('2026-10-15','SIP contribution','6000.00')])
        sessions.queue_apply(identity);sessions.work(threading.Event());s=sessions.get(identity)
        self.assertEqual(s['state'],'applied',s.get('error'));self.assertEqual(read_json(self.ledger/'data/forecast.json'),[r])
        self.assertTrue(all(e['rule'] is None for e in entries(load(self.ledger))))

    def test_two_statement_rows_on_one_rule_consume_bill_then_next_occurrence(self):
        r=self.seed(varying=True)
        r['bill']=dict(amount='5000.00',received='2026-10-01',**{'pay-by':None,'occurrence':r['date'],'evidence':[]})
        dump(self.ledger/'data/forecast.json',[r])
        sessions,identity=self.bank_statement([('2026-10-15','SIP contribution','5000.00'),('2026-11-15','SIP contribution','5500.00')])
        sessions.queue_apply(identity);sessions.work(threading.Event());s=sessions.get(identity)
        self.assertEqual(s['state'],'applied',s.get('error'))
        payments=[e for e in entries(load(self.ledger)) if e['rule']]
        self.assertEqual([e['rule'] for e in payments],[dict(id=r['id'],occurrence='2026-10-15'),dict(id=r['id'],occurrence='2026-11-15')])
        current=read_json(self.ledger/'data/forecast.json')[0]
        self.assertEqual(current['date'],'2026-12-15');self.assertIsNone(current['bill']);self.assertEqual(current['postings'][0]['amount'],'5250.00')
        self.assertEqual(sum(line.get('rule_action')=='bill-paid' for line in records(self.ledger)),1)

    def test_legacy_undone_payment_is_skipped_and_direct_link_refusal_is_plain(self):
        from audit import append, event
        from rules_service import addition_effects, statement_effects
        r=self.seed();payment=store_entry('0000000000000001','2026-10-15','SIP contribution',deepcopy(r['postings']))
        dump(self.ledger/'data/expenses/2026.json',[payment])
        made=event('rule',rule_action='made',rule_id=r['id'],request_id=secrets.token_hex(16),request_hash='a'*64,rule_changes=[],entry_id=payment['id'])
        undone=event('rule',rule_action='undone',rule_id=r['id'],request_id=secrets.token_hex(16),request_hash='b'*64,rule_changes=[],undoes=made['id'])
        with ledger_lock(self.ledger):
            append(self.ledger,made);append(self.ledger,undone)
            docs=load(self.ledger)
            with self.assertRaisesRegex(ValueError,'undone.*cannot be linked'):
                addition_effects(self.ledger,payment['id'],dict(client_time='2026-11-01T00:00:00+00:00',text=''),documents=docs)
            self.assertEqual(statement_effects(self.ledger,[payment['id']],dict(id='c'*32),documents=docs),[])
        self.assertIsNone(entries(load(self.ledger))[0]['rule'])

    def test_statement_matching_work_is_bounded_by_batch_and_relevant_history(self):
        from rules_service import statement_effects
        class CountedAccounts(dict):
            reads=0
            def get(self,*args):
                self.reads+=1
                return super().get(*args)
        r=self.seed(varying=True);docs=load(self.ledger)
        unrelated=[store_entry(f'{n+1000:016x}','2026-09-01','Unrelated food',[posting('Expenses:Food','1.00'),posting(PAYER,'-1.00')]) for n in range(400)]
        payments=[store_entry(f'{n+2000:016x}','2026-10-15','SIP contribution',deepcopy(r['postings'])) for n in range(20)]
        docs[self.ledger/'data/expenses/2026.json']=unrelated+payments
        accounts=CountedAccounts(docs[self.ledger/'data/accounts.json']['accounts']);docs[self.ledger/'data/accounts.json']['accounts']=accounts
        with ledger_lock(self.ledger):
            statement_effects(self.ledger,[e['id'] for e in payments],dict(id='c'*32,client_time='2026-11-01T00:00:00+00:00'),documents=docs)
        self.assertTrue(all(e['rule']==dict(id=r['id'],occurrence='2026-10-15') for e in payments))
        self.assertLess(accounts.reads,30*len(unrelated)+100*len(payments),'Unrelated history must be indexed once, not revisited per statement row')

    def test_unit_contribution_usual_amount_is_its_money_cost(self):
        r=self.seed();accounts=read_json(self.ledger/'data/accounts.json');accounts['accounts'][DESTINATION]['currency']='UNITS';accounts['commodities'].append('UNITS')
        dump(self.ledger/'data/accounts.json',accounts)
        r['postings'][0].update(amount='5.00',commodity='UNITS',cost=dict(amount='5000.00',commodity='INR',per='total'))
        dump(self.ledger/'data/forecast.json',[r]);self.jj('commit','-m','Fictional unit contribution')
        drafts=Drafts(self.ledger);d=drafts.create('SIP contribution')
        queue_usual(self.settings,drafts,d,dict(rule_id=r['id'],date='2026-10-15',amount='5000.00'))
        self.assertEqual(d['proposal']['entry']['postings'][0]['amount'],'5.00')
        self.assertEqual(d['proposal']['entry']['postings'][0]['cost']['amount'],'5000.00')
        self.assertEqual(d['proposal']['entry']['postings'][1]['amount'],'-5000.00')

    def test_typed_contribution_fake_agent_add_links_and_advances(self):
        r=self.seed();self.set_answers(self.proposal())
        with self.client() as client:
            identity=self.create(client,'SIP 5000 from bank',upload=False);self.save(client,identity)
            result=self.wait(client,identity,'added')['result']
        self.assertEqual(len(self.calls()),1)
        self.assertEqual(result['rule'],dict(id=r['id'],occurrence='2026-10-15'))
        self.assertEqual(result['next_due'],'2026-11-15')

    def test_hand_finish_links_and_advances_without_agent(self):
        r=self.seed()
        with self.client() as client:
            client.app.state.worker.pause.fail('usage_limit')
            identity=self.create(client,'SIP 5000 from bank',upload=False);self.save(client,identity)
            url='/api/drafts/'+identity
            token=client.post(url+'/claim',json={}).json()['claim_token']
            checked=client.post(url+'/hand-form',json=dict(claim_token=token,proposal=self.proposal())).json()
            self.assertTrue(checked['ok'],checked)
            response=client.post(url+'/hand-add',json=dict(claim_token=token,proposal_hash=checked['proposal_hash'],approved_accounts=[]))
            self.assertEqual(response.status_code,200,response.text)
            result=self.wait(client,identity,'added')['result']
        self.assertEqual(result['rule']['id'],r['id']);self.assertEqual(result['next_due'],'2026-11-15');self.assertEqual(self.calls(),[])

    def test_varying_usual_and_bill_retain_exact_contributions(self):
        r=self.seed(varying=True);drafts=Drafts(self.ledger)
        for day,amount in [('2026-10-15','5200.00'),('2026-11-15','4800.00')]:
            d=drafts.create('SIP contribution');queue_usual(self.settings,drafts,d,dict(rule_id=r['id'],date=day,amount=amount))
            with ledger_lock(self.ledger) as fd:result=Transaction(self.settings,drafts,d).apply(d['proposal'],fd)
            drafts.completed(d,result)
            self.assertEqual(result['rule']['id'],r['id'])
        current=read_json(self.ledger/'data/forecast.json')[0]
        self.assertEqual(current['postings'][0]['amount'],'5000.00')
        bill=dict(amount='5100.00',received='2026-12-01',**{'pay-by':'2026-12-20','occurrence':current['date'],'evidence':[]})
        operate(self.settings,secrets.token_hex(16),dict(action='bill-saved',rule_id=r['id'],bill=bill))
        self.assertEqual(len(entries(load(self.ledger))),2)
        d=drafts.create('SIP bill paid');queue_usual(self.settings,drafts,d,dict(rule_id=r['id'],date='2026-12-20'))
        with ledger_lock(self.ledger) as fd:result=Transaction(self.settings,drafts,d).apply(d['proposal'],fd)
        current=read_json(self.ledger/'data/forecast.json')[0]
        self.assertIsNone(current['bill']);self.assertEqual(current['date'],'2027-01-15');self.assertEqual(current['postings'][0]['amount'],'5100.00')
        self.assertEqual(len(snapshot(self.settings)['rules'][0]['varying']['history']),3)

    def test_statement_row_links_and_advances_in_same_audited_commit(self):
        from statements import Statements
        r=self.seed();docs=load(self.ledger);a=docs[self.ledger/'data/accounts.json']
        a['accounts'][PAYER]['statement']=dict(reader='generic-csv',identifier=None,options=dict(currency='INR',date_format='%Y-%m-%d',period={'start':'2026-10-01','end':'2026-10-31'},opening_balance='10000.00',closing_balance='5000.00',debits='5000.00',credits='0.00',columns={'date':'Date','description':'Description','amount':'Amount','balance':'Balance'}))
        dump(self.ledger/'data/accounts.json',a);self.jj('commit','-m','Fictional statement reader')
        sessions=Statements(self.settings)
        s=sessions.create(b'Date,Description,Amount,Balance\n2026-10-15,SIP contribution,-5000.00,5000.00\n','Contribution.csv')
        s['assigned_account']=PAYER;sessions.put(s);sessions.read(s)
        s=sessions.get(s['id']);self.assertIn(s['state'],('found','review'),s.get('error'))
        for item in s['plan']['items']:
            sessions.decide(s['id'],item['row']['serial'],dict(action='account',account=DESTINATION))
        before={str(p):p.read_bytes() for p in (self.ledger/'data').rglob('*') if p.is_file()}
        with patch('save_timing.make_commit',return_value=subprocess.CompletedProcess([],1,'','injected commit refusal')):
            sessions.queue_apply(s['id']);sessions.work(threading.Event())
        self.assertEqual(sessions.get(s['id'])['state'],'failed')
        self.assertEqual({str(p):p.read_bytes() for p in (self.ledger/'data').rglob('*') if p.is_file()},before)
        sessions.queue_apply(s['id']);sessions.work(threading.Event());s=sessions.get(s['id'])
        self.assertEqual(s['state'],'applied',s.get('error'))
        e=next(e for e in entries(load(self.ledger)) if e['description']=='SIP contribution')
        self.assertEqual(e['rule'],dict(id=r['id'],occurrence='2026-10-15'))
        self.assertEqual(read_json(self.ledger/'data/forecast.json')[0]['date'],'2026-11-15')
        self.assertEqual(records(self.ledger)[-1]['rule_changes'][0]['id'],r['id'])

    def test_card_statement_contribution_links_and_advances(self):
        from statements import Statements
        from statement_fixtures import statement_pdf, TEXT
        r=self.seed();card='Liabilities:Contribution Card'
        docs=load(self.ledger);a=docs[self.ledger/'data/accounts.json']
        a['accounts'][card]=meta(kind='card',currency='INR',statement=dict(reader='example-card-pdf',identifier='5678'))
        r.update(date='2026-01-02',amount='varies',description='Fable Shop');r['schedule']['from']=r['date'];r['postings']=[posting(DESTINATION,'120.00'),posting(card,'-120.00')]
        r['bill']=dict(amount='120.00',received='2026-01-01',**{'pay-by':'2026-01-02','occurrence':r['date'],'evidence':[]})
        dump(self.ledger/'data/accounts.json',a);dump(self.ledger/'data/forecast.json',[r]);self.jj('commit','-m','Fictional card contribution')
        sessions=Statements(self.settings);s=sessions.create(statement_pdf(TEXT.replace('2.00 | USD',' | ')),'Contribution.pdf');sessions.read(s)
        s=sessions.get(s['id']);self.assertIn(s['state'],('found','review'),s.get('error'))
        for item in s['plan']['items']:
            sessions.decide(s['id'],item['row']['serial'],dict(action='account',account=DESTINATION if item['row']['direction']=='Debit' else PAYER))
        before={str(p):p.read_bytes() for p in (self.ledger/'data').rglob('*') if p.is_file()}
        with patch('save_timing.make_commit',return_value=subprocess.CompletedProcess([],1,'','injected card commit refusal')):
            sessions.queue_apply(s['id']);sessions.work(threading.Event())
        self.assertEqual(sessions.get(s['id'])['state'],'failed')
        self.assertEqual({str(p):p.read_bytes() for p in (self.ledger/'data').rglob('*') if p.is_file()},before)
        self.assertFalse(any(line.get('rule_action')=='bill-paid' for line in records(self.ledger)))
        sessions.queue_apply(s['id']);sessions.work(threading.Event());s=sessions.get(s['id'])
        self.assertEqual(s['state'],'applied',s.get('error'))
        linked=[e for e in entries(load(self.ledger)) if e['rule']]
        self.assertEqual(len(linked),1);self.assertEqual(linked[0]['rule'],dict(id=r['id'],occurrence='2026-01-02'))
        self.assertEqual(read_json(self.ledger/'data/forecast.json')[0]['date'],'2026-02-02')
        self.assertIsNone(read_json(self.ledger/'data/forecast.json')[0]['bill'])
        self.assertEqual(sum(line.get('rule_action')=='bill-paid' for line in records(self.ledger)),1)
        self.assertEqual(records(self.ledger)[-1]['rule_changes'][0]['id'],r['id'])
