"""Confirmed balances across every writer, with real strict hledger checks."""
from copy import deepcopy
import json
import os
from pathlib import Path
import secrets
from unittest.mock import patch

from support import Fixture, ready, entry as store_entry, posting, meta, rule as store_rule
from store import dump, load, entries
from add_entry import add_entry
from confirmed_balances import BalanceRefusal, correction_locks, failures
from corrections import open_form, wire_entry, apply
from drafts import Drafts
from hand_finish import preview
from transactions import Transaction
from worker import Worker
from ledger_lock import ledger_lock
from audit import records
from rules_service import queue_usual


# Reads the selected ledger manifest to exercise its native currency.
PERSONA_SENSITIVE = True


class ConfirmedBalanceTests(Fixture):
    def seed(self):
        source=Path(os.environ['LEDGER_DIR'])/'ledger.json'
        code=json.loads(source.read_text())['defaultCurrency']
        self.code=code
        manifest=json.loads((self.ledger/'ledger.json').read_text());manifest.update(defaultCurrency=code)
        dump(self.ledger/'ledger.json',manifest)
        dump(self.ledger/'data/accounts.json',dict(commodities=[code],accounts={
            'Assets:Cash':meta(kind='bank',currency=code,label='Cedar Current'),
            'Expenses:Food':{'category':None},'Expenses:Learning':{'category':None},'Equity:Opening Balances':{}}))
        p=lambda a,n:posting(a,n,code)
        opening=store_entry('0000000000000001','2026-09-30','Opening balance',[p('Assets:Cash','1000.00'),p('Equity:Opening Balances','-1000.00')])
        opening['postings'][0]['assert']=dict(amount='1000.00',commodity=code)
        paid=store_entry('0000000000000002','2026-10-29','Cedar learning payment',[p('Expenses:Food','100.00'),p('Assets:Cash','-100.00')])
        close=store_entry('0000000000000003','2026-10-31','Monthly confirmed balance',[p('Assets:Cash','0.00')])
        close['postings'][0]['assert']=dict(amount='900.00',commodity=code)
        dump(self.ledger/'data/expenses/2026.json',[opening,paid,close])
        self.jj('commit','-m','Fictional monthly bank balance')
        proposal=wire_entry(self.settings,paid);proposal['entry']['date']='2026-10-30'
        return paid,close,proposal

    def test_addition_exact_balances_duplicate_period_currency_and_diagnostics(self):
        paid,close,proposal=self.seed()
        entry=deepcopy(paid);entry.pop('id');entry['date']='2026-10-30'
        with self.assertRaises(BalanceRefusal) as caught:add_entry(entry,root=self.ledger,dry_run=True)
        reason=caught.exception.refusal
        self.assertEqual((reason['asserted'],reason['calculated'],reason['difference']),('900.00','800.00','-100.00'))
        self.assertEqual(reason['commodity'],self.code);self.assertEqual(reason['entry_id'],close['id'])
        self.assertEqual(reason['source']['kind'],'balance');self.assertIn('Balance assertion',reason['detail'])
        self.assertEqual([d['entry_id'] for d in reason['possible_duplicates']],[paid['id']])
        # Outside the previous assertion's period, even near and equal, is excluded.
        docs=load(self.ledger);rows=docs[self.ledger/'data/expenses/2026.json']
        rows[0]['date']='2026-10-28';rows[0]['postings'][0]['amount']='100.00';rows[0]['postings'][1]['amount']='-100.00';rows[0]['postings'][0]['assert']['amount']='100.00'
        rows[1]['date']='2026-10-29'
        rows[2]['postings'][0]['assert']['amount']='0.00'
        extra=deepcopy(paid);extra.update(id='0000000000000004',date='2026-10-30');rows.insert(2,extra)
        reasons=list(failures(docs,self.ledger,candidate=extra))
        self.assertEqual([d['entry_id'] for d in reasons[0]['possible_duplicates']],[paid['id']])

    def test_human_preview_and_usual_refusal_keep_words_and_audit(self):
        paid,close,proposal=self.seed();drafts=Drafts(self.ledger);d=drafts.create('Cedar learning paid again')
        checks=preview(self.settings,drafts,d,proposal)
        self.assertFalse(checks['ok']);self.assertEqual(checks['refusal']['calculated'],'800.00')
        self.assertEqual(drafts.get(d['id'])['text'],'Cedar learning paid again')
        rule=store_rule('f000000000000001','2026-10-30',paid['description'],deepcopy(paid['postings']))
        dump(self.ledger/'data/forecast.json',[rule]);self.jj('commit','-m','Fictional usual rule')
        with self.assertRaises(BalanceRefusal):queue_usual(self.settings,drafts,d,dict(rule_id=rule['id'],date='2026-10-30'))
        refused=drafts.get(d['id']);self.assertEqual(refused['state'],'failed');self.assertEqual(refused['audit_pending'][0]['refusal'],refused['refusal'])
        Worker(self.settings,drafts).flush_audits()
        self.assertEqual(records(self.ledger)[-1]['refusal'],refused['refusal'])

    def test_agent_refusal_has_one_run_no_retry_no_pause_and_audited_reason(self):
        paid,close,proposal=self.seed();proposal['attachment-names']=[]
        self.set_answers(proposal)
        with self.client() as client:
            d=client.post('/api/drafts',json={'text':'Cedar learning paid again'}).json()
            client.post('/api/drafts/'+d['id']+'/save')
            drafts=client.app.state.drafts
            with drafts.changed:self.assertTrue(drafts.changed.wait_for(lambda:drafts.get(d['id'])['state']=='failed',timeout=30))
            failed=client.get('/api/drafts/'+d['id']).json()
            self.assertEqual(len(failed['runs']),1);self.assertEqual(failed['refusal']['calculated'],'800.00')
            self.assertIsNone(client.app.state.worker.pause.status());self.assertEqual(len(self.calls()),1)
            self.assertEqual(client.post('/api/drafts/'+d['id']+'/retry').status_code,409)
            answer=client.post('/api/drafts/'+d['id']+'/resolve-refusal',json={'action':'answer'})
            self.assertEqual(answer.json()['state'],'question')
        Worker(self.settings,drafts).flush_audits()
        self.assertEqual(records(self.ledger)[-1]['refusal'],failed['refusal'])

    def test_api_usual_reason_and_edit_recovery(self):
        paid,close,proposal=self.seed()
        rule=store_rule('f000000000000001','2026-10-30',paid['description'],deepcopy(paid['postings']))
        dump(self.ledger/'data/forecast.json',[rule]);self.jj('commit','-m','Fictional usual rule')
        with self.client(worker=False) as client:
            d=client.post('/api/drafts',json={'text':'Cedar learning paid again'}).json()
            response=client.post('/api/drafts/'+d['id']+'/usual',json=dict(rule_id=rule['id'],date='2026-10-30'))
            self.assertEqual(response.status_code,200);self.assertEqual(response.json()['refusal']['kind'],'confirmed_balance')
            changed=client.post('/api/drafts/'+d['id']+'/resolve-refusal',json={'action':'edit'}).json()
            self.assertEqual(changed['state'],'editing');self.assertEqual(changed['text'],d['text']);self.assertNotIn('refusal',changed)
            self.assertEqual(self.calls(),[])

    def test_correction_locks_range_metadata_and_same_day_boundary(self):
        paid,close,proposal=self.seed();drafts=Drafts(self.ledger);d=open_form(self.settings,drafts,paid['id'])
        locks=d['correction']['balance_locks'];self.assertEqual(len(locks),1)
        self.assertEqual((locks[0]['posting'],locks[0]['start'],locks[0]['end']),(1,'2026-09-30','2026-10-31'))
        def body():return dict(version=__import__('replacements').entry_hash(paid),proposal=wire_entry(self.settings,paid),approved_accounts=[])
        for key,value in [('amount','-90.00'),('account','Equity:Opening Balances')]:
            b=body();b['proposal']['entry']['postings'][1][key]=value
            with self.assertRaisesRegex(ValueError,'locked'):apply(self.settings,paid['id'],secrets.token_hex(16),b)
        for day in ('2026-09-29','2026-11-01','2026-10-31'):
            b=body();b['proposal']['entry']['date']=day
            with self.assertRaisesRegex(ValueError,'neighbouring confirmed balances'):apply(self.settings,paid['id'],secrets.token_hex(16),b)
        # Metadata and category are still editable on the original date.
        b=body();b['proposal']['entry'].update(description='Corrected learning label',notes=['Owner note'])
        b['proposal']['entry']['postings'][0]['account']='Expenses:Learning'
        result=apply(self.settings,paid['id'],secrets.token_hex(16),b)
        self.assertIn('description',result['changed'])
        after=next(e for e in entries(load(self.ledger)) if e['id']==paid['id'])
        self.assertEqual(after['postings'][1],paid['postings'][1])

    def test_new_date_into_confirmed_period_and_receipt_choice_return_reason(self):
        paid,close,proposal=self.seed()
        extra=deepcopy(paid);extra.update(id='0000000000000004',date='2026-11-02')
        docs=load(self.ledger);docs[self.ledger/'data/expenses/2026.json'].append(extra)
        dump(self.ledger/'data/expenses/2026.json',docs[self.ledger/'data/expenses/2026.json']);self.jj('commit','-m','Fictional later payment')
        with self.client(worker=False) as client:
            d=client.post('/api/entries/'+extra['id']+'/correction-form').json()
            form=wire_entry(self.settings,extra);form['entry']['date']='2026-10-30'
            response=client.post('/api/entries/'+extra['id']+'/corrections/'+secrets.token_hex(16),json=dict(version=d['correction']['version'],proposal=form,approved_accounts=[]))
            self.assertEqual(response.status_code,409);self.assertEqual(response.json()['detail']['calculated'],'800.00')
            from receipts import choose
            drafts=client.app.state.drafts;r=drafts.create('Later payment receipt')
            answer=dict(read_ok=True,references=[],payment_datetime=None,payee=None,date='2026-10-30',amount=None,currency=None,paying_account=None,notes=[])
            r.update(state='added',receipt_reading=dict(state='question',entry=extra,entry_id=extra['id'],version=__import__('replacements').entry_hash(extra),answer=answer,operation_id=secrets.token_hex(16),entry_totals={self.code:'100.00'}))
            drafts.put(r)
            with self.assertRaises(BalanceRefusal):choose(self.settings,drafts,r,'use')
            self.assertEqual(drafts.get(r['id'])['receipt_reading']['state'],'question')

    def test_same_day_existing_order_is_kept_and_other_currency_is_independent(self):
        paid,close,proposal=self.seed()
        docs=load(self.ledger);rows=docs[self.ledger/'data/expenses/2026.json']
        rows[1]['date']='2026-10-31'
        other=next(code for code in json.loads((self.settings.application/'schema/currencies.json').read_text()) if code!=self.code)
        declarations=docs[self.ledger/'data/accounts.json'];declarations['commodities'].append(other)
        declarations['accounts']['Assets:Bank:Other']=meta(kind='bank',currency=other)
        extra=store_entry('0000000000000005','2026-10-31','Other currency balance',[posting('Assets:Bank:Other','0.00',other,**{'assert':dict(amount='0.00',commodity=other)})])
        rows.append(extra)
        for path,value in docs.items():dump(path,value)
        self.jj('commit','-m','Fictional same-day and separate currency balances')
        paid=rows[1];form=wire_entry(self.settings,paid);form['entry']['notes']=['Same-day order retained']
        result=apply(self.settings,paid['id'],secrets.token_hex(16),dict(version=__import__('replacements').entry_hash(paid),proposal=form,approved_accounts=[]))
        self.assertEqual(result['entry_id'],paid['id'])
        self.assertEqual([e['id'] for e in entries(load(self.ledger)) if e['date']=='2026-10-31'],[paid['id'],close['id'],extra['id']])
