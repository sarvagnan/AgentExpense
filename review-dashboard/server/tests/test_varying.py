"""Offline checked bill saves and varying estimates in disposable repositories."""
from copy import deepcopy
from decimal import Decimal
import json
import secrets
from unittest.mock import patch
from support import Fixture
from test_rules import payment_rule
from store import load,dump,entries,read_json,validate
from audit import records
from drafts import Drafts
from ledger_lock import ledger_lock
from rules_service import operate,queue_usual,snapshot,AmountCheck
from transactions import Transaction,clean

class VaryingTests(Fixture):
    def seed(self,*,bill=False,fixed=False):
        r=payment_rule(day='2026-11-15',amount='110.00');r['description']='Willow Energy';r['notes']=['Keep the original notes.']
        if not fixed:r['amount']='varies'
        if bill:r['bill']=dict(amount='125.00',received='2026-11-01',pay_by='2026-11-20',occurrence=r['date'],sources=[])
        rows=[]
        for i,(day,value) in enumerate([('2026-08-15','100.00'),('2026-09-15','600.00'),('2026-10-15','110.00')],1):
            e=dict(id=f'{i:016x}',date=day,status='confirmed',description=r['description'],postings=deepcopy(r['postings']))
            e['postings'][0]['amount']=value;e['postings'][1]['amount']='-'+value
            if i==3:e['rule']=dict(id=r['id'],occurrence=day)
            rows.append(e)
        dump(self.ledger/'data/forecast.json',[r]);dump(self.ledger/'data/expenses/2026.json',rows)
        self.jj('commit','-m','Fictional varying baseline');return r

    def rule(self):return read_json(self.ledger/'data/forecast.json')[0]
    def usual(self,**body):
        drafts=Drafts(self.ledger);d=drafts.create('Willow Energy payment')
        day=body.pop('date','2026-11-15')
        queue_usual(self.settings,drafts,d,dict(rule_id=self.rule()['id'],date=day,**body))
        with ledger_lock(self.ledger) as fd:result=Transaction(self.settings,drafts,d).apply(d['proposal'],fd)
        drafts.completed(d,result)
        return drafts,d,result

    def test_switch_to_varies_and_linked_correction_share_recompute(self):
        r=self.seed(fixed=True)
        operate(self.settings,secrets.token_hex(16),dict(action='edited',rule_id=r['id'],rule={'amount':'varies'}))
        self.assertEqual(self.rule()['postings'][0]['amount'],'110.00')
        _,d,result=self.usual(amount='120.00')
        from corrections import open_form,apply
        drafts=Drafts(self.ledger);form=open_form(self.settings,drafts,result['entry_id']);proposal=deepcopy(form['hand_form'])
        proposal['entry']['postings'][0]['amount']='130.00';proposal['entry']['postings'][1]['amount']='-130.00'
        changed=apply(self.settings,result['entry_id'],secrets.token_hex(16),dict(version=form['correction']['version'],proposal=proposal))
        self.assertEqual(changed['rule']['status'],'kept');self.assertEqual(self.rule()['postings'][0]['amount'],'130.00')
        self.assertEqual(records(self.ledger)[-1]['rule_changes'][0]['after']['postings'][0]['amount'],'130.00')

    def test_bill_save_retry_remove_and_no_expense_or_undo(self):
        r=self.seed()
        with self.client(worker=False) as client:
            identity=self.create(client);body=dict(rule_id=r['id'],amount='125.00',date='2026-11-01',pay_by='2026-11-20')
            before=len(entries(load(self.ledger)));response=client.post(f'/api/drafts/{identity}/bill',json=body)
            self.assertEqual(response.status_code,200,response.text);client.app.state.worker.process(client.app.state.drafts.get(identity));draft=client.get('/api/drafts/'+identity).json();self.assertEqual(draft['result']['outcome'],'bill');self.assertNotIn('can_undo',draft['result'])
            self.assertEqual(len(entries(load(self.ledger))),before);self.assertEqual(self.rule()['date'],r['date']);self.assertTrue(self.rule()['bill']['sources'])
            for source in self.rule()['bill']['sources']:self.assertTrue((self.ledger/source).is_file())
            count=len(records(self.ledger));replay=client.post(f'/api/drafts/{identity}/bill',json=body);self.assertEqual(replay.status_code,200,replay.text);self.assertEqual(len(records(self.ledger)),count)
            message=self.jj('log','-r','@-','--no-graph','-T','description');self.assertIn('Record Willow Energy bill via dashboard',message);self.assertIn('Added-by: user (bill, rule '+r['id']+')',message)
            op=secrets.token_hex(16);payload=dict(action='bill-removed',rule_id=r['id']);operate(self.settings,op,payload);self.assertTrue(operate(self.settings,op,payload)['replayed'])
            self.assertNotIn('bill',self.rule());self.assertEqual(records(self.ledger)[-1]['rule_action'],'bill-removed');self.assertTrue(clean(self.ledger));self.assertEqual(self.calls(),[])

    def test_paying_bill_attaches_sources_clears_links_advances_and_audits(self):
        r=self.seed(bill=True);source='Bills/2026-11-01/Willow-bill.txt';(self.ledger/source).parent.mkdir(parents=True,exist_ok=True);(self.ledger/source).write_text('Fictional bill evidence.')
        r['bill']['sources']=[source];dump(self.ledger/'data/forecast.json',[r]);self.jj('commit','-m','Fictional filed bill')
        _,d,result=self.usual()
        entry=next(e for e in entries(load(self.ledger)) if e['id']==result['entry_id'])
        self.assertEqual(entry['postings'][0]['amount'],'125.00');self.assertIn(source,entry['sources']);self.assertIn(source,result['sources']);self.assertNotIn('bill',self.rule());self.assertEqual(self.rule()['date'],'2026-12-15');self.assertEqual(self.rule()['postings'][0]['amount'],'125.00')
        self.assertEqual(entry['rule']['occurrence'],'2026-11-15');line=records(self.ledger)[-1];self.assertEqual(line['rule_action'],'bill-paid');self.assertEqual(line['text'],d['text']);self.assertEqual(line['bill']['sources'],[source]);self.assertTrue(clean(self.ledger))

    def test_amount_check_explicit_doubts_on_payment_and_bill(self):
        r=self.seed()
        drafts=Drafts(self.ledger);d=drafts.create('Willow Energy 1300')
        with self.assertRaises(AmountCheck):queue_usual(self.settings,drafts,d,dict(rule_id=r['id'],date='2026-11-15',amount='1300.00'))
        _,d,result=self.usual(amount='1300.00',amount_anyway=True)
        self.assertTrue(records(self.ledger)[-1]['doubts']);self.assertIn('Amount check:',self.jj('log','-r','@-','--no-graph','-T','description'))
        body=dict(action='bill-saved',rule_id=r['id'],bill=dict(amount='1.00',received='2026-12-01',occurrence=self.rule()['date'],sources=[]),text='Willow Energy bill 1')
        with self.assertRaises(AmountCheck):operate(self.settings,secrets.token_hex(16),body)
        operate(self.settings,secrets.token_hex(16),{**body,'amount_anyway':True});self.assertTrue(records(self.ledger)[-1]['doubts']);self.assertIn('amount_check',records(self.ledger)[-1])

    def test_bill_rollback_and_invalid_shapes_and_needs_amount(self):
        r=self.seed();before=self.bytes()
        payload=dict(action='bill-saved',rule_id=r['id'],bill=dict(amount='120.00',received='2026-11-01',occurrence=r['date'],sources=[]))
        with patch.object(Transaction,'verified_commit',side_effect=RuntimeError('injected bill failure')):
            with self.assertRaisesRegex(RuntimeError,'injected'):operate(self.settings,secrets.token_hex(16),payload)
        self.assertEqual(self.bytes(),before);self.assertTrue(clean(self.ledger))
        for change in ({'amount':'-1.00'},{'received':'2026-02-30'},{'occurrence':'2026-12-15'},{'extra':'invalid'}):
            with self.assertRaises(ValueError):operate(self.settings,secrets.token_hex(16),{**payload,'bill':{**payload['bill'],**change}})
        drafts=Drafts(self.ledger)
        with self.assertRaisesRegex(ValueError,'Needs an amount'):queue_usual(self.settings,drafts,drafts.create('Willow Energy'),dict(rule_id=r['id']))
        invalid=deepcopy(r);invalid['postings'].append(dict(account='Expenses:Food',amount='1.00',commodity='INR'))
        docs=load(self.ledger);docs[self.ledger/'data/forecast.json']=[invalid]
        with self.assertRaisesRegex(ValueError,'two-posting'):validate(docs,self.ledger)
        self.assertEqual(self.bytes(),before)

    def test_bill_retry_after_completion_receipt_loss_and_after_payment(self):
        r=self.seed()
        with self.client(worker=False) as client:
            identity=self.create(client,upload=False);body=dict(rule_id=r['id'],amount='125.00',date='2026-11-01')
            response=client.post(f'/api/drafts/{identity}/bill',json=body);self.assertEqual(response.status_code,200,response.text)
            client.app.state.worker.process(client.app.state.drafts.get(identity));self.usual();count=len(records(self.ledger))
            # A retry uses the frozen operation even though the rule has advanced.
            again=client.post(f'/api/drafts/{identity}/bill',json=body);self.assertEqual(again.status_code,200,again.text);self.assertEqual(len(records(self.ledger)),count);self.assertNotIn('bill',self.rule())
            changed=client.post(f'/api/drafts/{identity}/bill',json={**body,'amount':'126.00'});self.assertEqual(changed.status_code,409)

    def test_fifo_bill_waits_for_clean_ledger_and_runs_while_agent_is_paused(self):
        r=self.seed()
        with self.client() as client:
            worker=client.app.state.worker;worker.pause.fail('usage_limit',reason='Fictional offline pause')
            unrelated=self.ledger/'owner-note.txt';unrelated.write_text('Fictional unfinished work.')
            identity=self.create(client,upload=False);body=dict(rule_id=r['id'],amount='125',date='2026-11-01')
            response=client.post(f'/api/drafts/{identity}/bill',json=body);self.assertEqual(response.status_code,200,response.text);position=response.json()['queued_at']
            waiting=self.wait_condition(client,identity,lambda d:bool(d.get('waiting')));self.assertEqual(waiting['state'],'working');self.assertNotIn('bill',self.rule())
            with ledger_lock(self.ledger):self.jj('commit','-m','Finish fictional unrelated work')
            done=self.wait(client,identity,'added');self.assertEqual(done['result']['outcome'],'bill');self.assertEqual(done['queued_at'],position);self.assertEqual(self.calls(),[]);self.assertTrue(clean(self.ledger))

    def test_bill_operation_survives_crash_after_commit_and_recovers_without_duplicate(self):
        r=self.seed()
        with self.client(worker=False) as client:
            identity=self.create(client,upload=False);body=dict(rule_id=r['id'],amount='125.00',date='2026-11-01')
            response=client.post(f'/api/drafts/{identity}/bill',json=body);self.assertEqual(response.status_code,200,response.text)
            drafts=client.app.state.drafts;queued=drafts.get(identity)
            from rules_service import RuleRecords
            original_put=RuleRecords.put
            def crash_after_commit(store,draft):
                if draft.get('committed_result'):raise SystemExit('simulated process exit after verified commit')
                return original_put(store,draft)
            # A process exit is simulated outside the in-process rollback handler
            # by leaving the result journal exactly as a terminated process would.
            with patch.object(RuleRecords,'put',crash_after_commit),patch.object(Transaction,'rollback',lambda *args:None):
                with self.assertRaises(SystemExit):client.app.state.worker.process(queued)
            self.assertIn('bill',self.rule());count=len(records(self.ledger));self.assertEqual(count,1)
            client.app.state.worker.process(drafts.get(identity));done=client.get('/api/drafts/'+identity).json();self.assertEqual(done['state'],'added');self.assertEqual(done['result']['outcome'],'bill');self.assertEqual(len(records(self.ledger)),count);self.assertFalse((RuleRecords(self.ledger).folder(identity)/'transaction.json').exists());self.assertTrue(clean(self.ledger))
            # Permanent audit replay remains sufficient after the ignored receipt expires.
            import shutil
            shutil.rmtree(RuleRecords(self.ledger).folder(identity))
            replay=operate(self.settings,identity,queued['bill_request']['operation']);self.assertTrue(replay['replayed']);self.assertEqual(replay['bill'],self.rule()['bill'])

    def test_plain_addition_links_varying_rule_and_recomputes_without_usual_path(self):
        r=self.seed()
        from rules_service import wire_proposal
        drafts=Drafts(self.ledger);draft=drafts.create('Paid Willow Energy 120');drafts.save_time(draft)
        answer,_=wire_proposal(r,'2026-11-15',[],self.ledger,{'amount':'120.00'})
        draft['proposal']=answer
        with ledger_lock(self.ledger) as fd:result=Transaction(self.settings,drafts,draft).apply(answer,fd)
        self.assertEqual(result['rule']['id'],r['id']);self.assertEqual(self.rule()['postings'][0]['amount'],'120.00')

    def test_bill_payment_consumes_only_its_occurrence_when_early_or_late(self):
        for day in ('2026-10-10','2026-12-20'):
            with self.subTest(payment_date=day):
                self.seed(bill=True)
                _,draft,result=self.usual(date=day)
                self.assertEqual(result['rule']['occurrence'],'2026-11-15')
                self.assertEqual(self.rule()['date'],'2026-12-15')
                self.assertNotIn('bill',self.rule())
                from corrections import open_form,apply
                drafts=Drafts(self.ledger);form=open_form(self.settings,drafts,result['entry_id']);proposal=deepcopy(form['hand_form'])
                proposal['entry']['postings'][0]['amount']='130.00';proposal['entry']['postings'][1]['amount']='-130.00'
                apply(self.settings,result['entry_id'],secrets.token_hex(16),dict(version=form['correction']['version'],proposal=proposal))
                entry=next(e for e in entries(load(self.ledger)) if e['id']==result['entry_id'])
                self.assertEqual(entry['rule']['occurrence'],'2026-11-15')
                self.assertEqual(self.rule()['date'],'2026-12-15')
                self.assertTrue(clean(self.ledger))

    def test_bill_and_typed_varying_amount_keep_every_decimal_place(self):
        r=self.seed()
        bill=dict(amount='125.005',received='2026-11-01',occurrence=r['date'],sources=[])
        operate(self.settings,secrets.token_hex(16),dict(action='bill-saved',rule_id=r['id'],bill=bill))
        _,draft,result=self.usual()
        entry=next(e for e in entries(load(self.ledger)) if e['id']==result['entry_id'])
        self.assertEqual(entry['postings'][0]['amount'],'125.005')
        self.assertEqual(entry['postings'][1]['amount'],'-125.005')
        self.assertEqual(self.rule()['postings'][0]['amount'],'125.005')
        _,draft,result=self.usual(date='2026-12-15',amount='130.005')
        entry=next(e for e in entries(load(self.ledger)) if e['id']==result['entry_id'])
        self.assertEqual(entry['postings'][0]['amount'],'130.005')
