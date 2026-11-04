"""Verified replacements, receipt follow-ups and draft declines in fictional ledgers."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import shutil
import subprocess
from unittest.mock import patch

from support import Fixture, ready, png
from store import load, entries, dump
from drafts import Drafts
from corrections import apply, open_form, wire_entry
from replacements import entry_hash, enforce_statement, move_evidence, safe_evidence
from transactions import Transaction, payment, clean
from rules_service import queue_usual
from ledger_lock import ledger_lock
from audit import records
from worker import Worker
from receipts import read, choose


class CorrectionTests(Fixture):
    def seed(self, *, day='2026-10-29', evidence=True, rule=False):
        self.currency=json.loads((self.ledger/'ledger.json').read_text())['defaultCurrency']
        e=deepcopy(ready(new=False)['entry']);e={k:v for k,v in e.items() if v is not None};e['id']='a'*16;e['date']=day
        e.pop('fuel_filled_litres',None);e.pop('trip_km',None)
        for p in e['postings']: p['amount']+='.00';p.pop('cost')
        e['classification']='owner classification';e['reference']='original-ref'
        if evidence:
            source=f'Bills/{day}/Willow.png';path=self.ledger/source;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(png());e['sources']=[source]
        if rule:
            r=dict(id='f'*16,date='2026-11-29',status='confirmed',description=e['description'],postings=deepcopy(e['postings']),schedule={'every':'month','from':'2026-11-29','anchor-day':29})
            e['rule']=dict(id=r['id'],occurrence=day);dump(self.ledger/'data/forecast.json',[r])
        dump(self.ledger/f'data/expenses/{day[:4]}.json',[e]);self.jj('commit','-m','Fictional correction baseline')
        return e

    def body(self,e):
        return dict(version=entry_hash(e),proposal=wire_entry(self.settings,e),sources=e.get('sources',[]),approved_accounts=[],reason='Corrected from the receipt')

    def save(self,e,body=None,op=None):
        return apply(self.settings,e['id'],op or secrets.token_hex(16),body or self.body(e))

    def test_identity_year_metadata_evidence_commit_and_audit_replay(self):
        before=self.seed(rule=True);body=self.body(before);body['proposal']['entry'].update(date='2027-01-02',description='Willow corrected')
        op=secrets.token_hex(16);result=self.save(before,body,op)
        current=entries(load(self.ledger))[0]
        self.assertEqual(current['id'],before['id']);self.assertEqual(current['reference'],before['reference']);self.assertEqual(current['classification'],before['classification'])
        self.assertEqual(current['postings'],before['postings']);self.assertEqual(current['sources'],['Bills/2027-01-02/Willow.png'])
        self.assertFalse((self.ledger/before['sources'][0]).exists());self.assertTrue((self.ledger/current['sources'][0]).is_file())
        self.assertEqual(load(self.ledger)[self.ledger/'data/expenses/2026.json'],[])
        line=records(self.ledger)[-1];self.assertEqual(line['kind'],'correction');self.assertEqual(line['before'],before);self.assertEqual(line['after'],current)
        message=self.jj('log','-r','@-','--no-graph','-T','description')
        for field in ['Edit Willow corrected via dashboard','Entry: '+before['id'],'Changed: ','Reason: "','Rule: kept','Evidence: Bills/2027-01-02/Willow.png','Draft: '+op,'Added-by: user (correction)']: self.assertIn(field,message)
        shutil.rmtree(self.ledger/'drafts/corrections')
        self.assertTrue(self.save(before,body,op)['replayed']);self.assertTrue(clean(self.ledger))
        with self.assertRaisesRegex(ValueError,'different correction'): self.save(before,{**body,'reason':'different'},op)

    def test_stale_and_untouched_forms_are_refused(self):
        e=self.seed();body=self.body(e)
        with self.assertRaisesRegex(ValueError,'No changes yet'): self.save(e,body)
        first=deepcopy(body);first['proposal']['entry']['description']='First change';self.save(e,first)
        body['proposal']['entry']['description']='Stale change'
        with self.assertRaisesRegex(ValueError,'changed since'): self.save(e,body)

    def test_rollback_restores_both_evidence_ends_and_year_files(self):
        e=self.seed();body=self.body(e);body['proposal']['entry']['date']='2027-01-02'
        with ledger_lock(self.ledger): pass
        before={str(p.relative_to(self.ledger)):p.read_bytes() for p in self.ledger.rglob('*') if p.is_file() and not any(x in p.parts for x in ('.git','.jj','drafts'))}
        with patch.object(Transaction,'verified_commit',side_effect=RuntimeError('injected verification failure')):
            with self.assertRaisesRegex(RuntimeError,'injected'): self.save(e,body)
        after={str(p.relative_to(self.ledger)):p.read_bytes() for p in self.ledger.rglob('*') if p.is_file() and not any(x in p.parts for x in ('.git','.jj','drafts'))}
        self.assertEqual(before,after);self.assertTrue(clean(self.ledger))

    def test_exclusive_moves_shared_citations_and_collision(self):
        e=self.seed();docs=load(self.ledger);after=deepcopy(e);after['date']='2027-01-02'
        target=self.ledger/'Bills/2027-01-02/Willow.png';target.parent.mkdir(parents=True);target.write_bytes(b'collision')
        self.assertEqual(move_evidence(docs,self.ledger,e,after)[0][1].name,'Willow-2.png')
        for value in [dict(sources=e['sources']),dict(notes=e['sources'])]:
            docs[self.ledger/'data/forecast.json']=[value];candidate=deepcopy(e);candidate['date']='2027-01-02'
            self.assertEqual(move_evidence(docs,self.ledger,e,candidate),[])
        docs[self.ledger/'data/forecast.json']=[];docs[self.ledger/'data/card-statements.json']=[dict(source=e['sources'][0])]
        self.assertEqual(move_evidence(docs,self.ledger,e,deepcopy(after)),[])

    def test_statement_financial_fields_lock_and_metadata_retention(self):
        e=self.seed();docs=load(self.ledger);docs[self.ledger/'data/card-statements.json']=[dict(members=[{'entry-id':e['id']}])]
        after=deepcopy(e);after.update(date='2027-01-02',description='Corrected description',notes=['New note'])
        enforce_statement(e,after,docs,self.ledger)
        for mutate in [lambda x:x['postings'].append(deepcopy(x['postings'][0])),lambda x:x['postings'][0].update(amount='200.00'),lambda x:x['postings'][1].update(account='Assets:Other'),lambda x:x['postings'][0].update(commodity='OTHER'),lambda x:x['postings'][0].update(cost=dict(amount='1.00',commodity=self.currency,per='total'))]:
            after=deepcopy(e);mutate(after)
            with self.assertRaisesRegex(ValueError,'filed statement'): enforce_statement(e,after,docs,self.ledger)

    def test_rule_kept_dropped_and_older_payment_does_not_move_it(self):
        e=self.seed(rule=True);body=self.body(e);body['proposal']['entry']['notes'].append('A corrected note');result=self.save(e,body)
        self.assertEqual(result['rule']['status'],'kept');self.assertEqual(result['rule']['next_due'],'2026-11-29')
        e=entries(load(self.ledger))[0];body=self.body(e);body['proposal']['entry']['postings'][0]['amount']='90.00';body['proposal']['entry']['postings'][1]['amount']='-90.00'
        result=self.save(e,body);self.assertEqual(result['rule']['status'],'dropped');self.assertNotIn('rule',entries(load(self.ledger))[0])

    def test_new_account_consent_and_cli_same_service(self):
        e=self.seed();body=self.body(e);body['proposal']['entry']['postings'][0]['account']='Expenses:Learning';body['proposal']['new_accounts']=['Expenses:Learning']
        with self.assertRaisesRegex(ValueError,'Tick every'):self.save(e,body)
        body['approved_accounts']=['Expenses:Learning'];self.save(e,body)
        path=self.base/'correction.json';e=entries(load(self.ledger))[0];body=self.body(e);body['proposal']['entry']['notes'].append('Operator correction');path.write_text(json.dumps(body))
        command=[__import__('sys').executable,str(Path(__file__).resolve().parents[3]/'scripts/ledger/correct.py'),'--root',str(self.ledger),e['id'],'--file',str(path)]
        run=subprocess.run(command,text=True,capture_output=True,env={**__import__('os').environ,'LEDGER_DIR':str(self.ledger)})
        self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(records(self.ledger)[-1]['actor'],'user')

    def test_http_form_claim_checks_attachments_and_save(self):
        e=self.seed()
        with self.client(worker=False) as client:
            opened=client.post(f'/api/entries/{e["id"]}/correction-form');self.assertEqual(opened.status_code,200,opened.text);draft=opened.json()
            claimed=client.post(f'/api/drafts/{draft["id"]}/claim',json={}).json();form=claimed['form'];token=claimed['claim_token']
            response=client.post(f'/api/drafts/{draft["id"]}/hand-form',json=dict(claim_token=token,proposal=form));self.assertTrue(response.json()['unchanged'])
            form['entry']['description']='Corrected by hand'
            upload=client.post(f'/api/drafts/{draft["id"]}/files',data=dict(claim_token=token),files=[('files',('More.png',png(),'image/png'))]);self.assertEqual(upload.status_code,201,upload.text)
            form['attachment_names']=['More']
            response=client.post(f'/api/drafts/{draft["id"]}/hand-form',json=dict(claim_token=token,proposal=form));self.assertTrue(response.json()['ok'],response.text)
            refused=client.post(f'/api/drafts/{draft["id"]}/hand-add',json=dict(claim_token=token,proposal_hash=response.json()['proposal_hash'],approved_accounts=[]));self.assertEqual(refused.status_code,409)
            with ledger_lock(self.ledger) as fd:
                with self.assertRaisesRegex(ValueError,'correction service'):Transaction(self.settings,drafts=client.app.state.drafts,draft=client.app.state.drafts.get(draft['id'])).apply(form,fd)
            body=dict(version=draft['correction']['version'],proposal=form,sources=e['sources'],approved_accounts=[],draft_id=draft['id'])
            result=client.post(f'/api/entries/{e["id"]}/corrections/{secrets.token_hex(16)}',json=body);self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(len(entries(load(self.ledger))[0]['sources']),2)

    def test_typed_date_declines_monotonic_and_no_rule_effects(self):
        e=self.seed(rule=True,evidence=False);rule=load(self.ledger)[self.ledger/'data/forecast.json'][0]
        with self.client(worker=False) as client:
            draft=client.post('/api/drafts',json=dict(text='Willow yesterday',pieces={'fields':{'date':{'value':'2026-10-28','source':'typed'}}})).json()
            for declines in [[rule['id']],[]]:
                result=client.patch('/api/drafts/'+draft['id'],json=dict(text='Changed words',declined_rules=declines));self.assertEqual(result.json()['declined_rules'],[rule['id']])
            self.assertEqual(client.get('/api/drafts/'+draft['id']).json()['state'],'editing')
            response=client.post('/api/drafts/'+draft['id']+'/usual',json={'rule_id':rule['id']});self.assertEqual(response.status_code,409)
        drafts=Drafts(self.ledger);d=drafts.create('Willow monthly membership',pieces={'fields':{'date':{'value':'2026-09-29'}}});queue_usual(self.settings,drafts,d,dict(rule_id=rule['id']))
        self.assertEqual(d['proposal']['entry']['date'],'2026-09-29')
        d=drafts.create('Willow monthly membership');d['declined_rules']=[rule['id']];d['finished_by']=dict(actor='user',reason='hand');d['hand_form']=wire_entry(self.settings,e);d['hand_form']['entry']['date']='2026-12-29';d['hand_form']['attachment_names']=[];drafts.save_time(d)
        with ledger_lock(self.ledger) as fd: result=Transaction(self.settings,drafts,d).apply(d['hand_form'],fd)
        self.assertIsNone(result.get('rule'));self.assertEqual(result.get('resume_rules',[]),[]);self.assertNotIn('rule',next(e for e in entries(load(self.ledger)) if e['id']==result['entry_id']));self.assertEqual(len(load(self.ledger)[self.ledger/'data/forecast.json']),1)

    def test_statement_correction_preserves_membership_and_refuses_money_changes(self):
        from test_review import ReviewTests
        rows=ReviewTests.seed(self,statement=True);e=next(r for r in rows if r.get('statement-end'))
        body=self.body(e);body['proposal']['entry'].update(description='Corrected statement workshop',date='2027-01-02')
        result=self.save(e,body);after=next(r for r in entries(load(self.ledger)) if r['id']==e['id'])
        for key in ('statement-end','serial-number','statement-page','sources','postings'): self.assertEqual(after[key],e[key])
        body=self.body(after);body['proposal']['entry']['postings'][0]['amount']='99.00';body['proposal']['entry']['postings'][1]['amount']='-99.00'
        with self.assertRaisesRegex(ValueError,'filed statement'):self.save(after,body)

    def test_unit_cost_hidden_fields_and_account_metadata_survive(self):
        e=self.seed();declarations=load(self.ledger)[self.ledger/'data/accounts.json'];foreign=next(c for c in declarations['commodities'] if c!=self.currency)
        e['postings'][0].update(amount='20.00',commodity=foreign,cost=dict(amount='5.00',commodity=self.currency,per='unit'))
        e['original-amount']=dict(amount='20.00',commodity=foreign);e['order-id']='original-order';e['source-row']=7;e['source-page']=2
        dump(self.ledger/'data/expenses/2026.json',[e]);self.jj('commit','-m','Fictional unit conversion baseline')
        body=self.body(e);body['proposal']['entry']['notes'].append('A receipt note');self.save(e,body)
        after=entries(load(self.ledger))[0]
        for key in ('postings','original-amount','order-id','source-row','source-page'):self.assertEqual(after[key],e[key])
        self.assertEqual(load(self.ledger)[self.ledger/'data/accounts.json'],declarations)
        after.update({'fuel-filled-litres':'5.00','trip-km':'50.00'});dump(self.ledger/'data/expenses/2026.json',[after])
        manifest=json.loads((self.ledger/'ledger.json').read_text());manifest['modules']['fuel']=False;dump(self.ledger/'ledger.json',manifest);self.jj('commit','-m','Historical optional module metadata')
        body=self.body(after);body['proposal']['entry']['description']='Corrected history label';self.save(after,body)
        updated=entries(load(self.ledger))[0]
        for key in ('fuel-filled-litres','trip-km'):self.assertEqual(updated.get(key),after[key])

    def test_old_addition_and_old_correction_never_advance_latest_rule(self):
        e=self.seed(rule=True);drafts=Drafts(self.ledger);d=drafts.create('Usual payment');queue_usual(self.settings,drafts,d,dict(rule_id='f'*16,date='2026-11-29'))
        with ledger_lock(self.ledger) as fd:Transaction(self.settings,drafts,d).apply(d['proposal'],fd)
        current_rule=deepcopy(load(self.ledger)[self.ledger/'data/forecast.json'][0]);self.assertEqual(current_rule['date'],'2026-12-29')
        d=drafts.create('Older payment');queue_usual(self.settings,drafts,d,dict(rule_id='f'*16,date='2026-09-29'))
        with ledger_lock(self.ledger) as fd:Transaction(self.settings,drafts,d).apply(d['proposal'],fd)
        self.assertEqual(load(self.ledger)[self.ledger/'data/forecast.json'][0],current_rule)
        body=self.body(e);body['proposal']['entry']['date']='2026-08-29';result=self.save(e,body)
        self.assertEqual(result['rule']['status'],'kept');self.assertEqual(load(self.ledger)[self.ledger/'data/forecast.json'][0],current_rule)

    def test_declined_removed_rule_still_covers_its_payment(self):
        from rules_service import addition_effects
        e=self.seed(rule=True);docs=load(self.ledger);rule=docs[self.ledger/'data/forecast.json'][0]
        rule.update(lifecycle='removed',removal=dict(date='2026-10-01',evidence=[]))
        entry=deepcopy(e);entry.update(id='b'*16,date='2026-11-29');entry.pop('rule');docs[self.ledger/'data/expenses/2026.json'].append(entry)
        for path,value in docs.items():dump(path,value)
        drafts=Drafts(self.ledger);draft=drafts.create('Willow monthly membership');draft['declined_rules']=[rule['id']];draft['client_time']='2026-11-30T12:00:00+00:00'
        result=addition_effects(self.ledger,entry['id'],draft)
        self.assertEqual(result['made_rules'],[]);self.assertEqual(result['resume_rules'],[])
        self.assertEqual(len(load(self.ledger)[self.ledger/'data/forecast.json']),1)

    def test_fifo_correction_waits_for_clean_ledger_without_a_model(self):
        import threading
        e=self.seed();body=self.body(e);body['proposal']['entry']['description']='Checked FIFO correction'
        dirty=self.ledger/'owner-work.txt';dirty.write_text('Unfinished owner work')
        drafts=Drafts(self.ledger);worker=Worker(self.settings,drafts);worker.retry_interval=.01
        observed=threading.Event();results=[];errors=[]
        original_clean=clean
        def check(root):
            result=original_clean(root)
            if not result: observed.set()
            return result
        def run():
            try:results.append(worker.call(lambda:apply(self.settings,e['id'],secrets.token_hex(16),body)))
            except Exception as exc:errors.append(exc)
        with patch('corrections.clean',side_effect=check),patch.object(worker.daily_refresh,'tick'):
            worker.start();thread=threading.Thread(target=run);thread.start()
            try:
                self.assertTrue(observed.wait(5));self.assertTrue(thread.is_alive());dirty.unlink();thread.join(180)
                self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(results[0]['entry_id'],e['id']);self.assertEqual(self.calls(),[])
            finally:
                dirty.unlink(missing_ok=True);worker.close();thread.join(5)

    def test_total_outgoing_split_refund_and_incoming_repayment(self):
        code=json.loads((self.ledger/'ledger.json').read_text())['defaultCurrency']
        p=lambda a,n:dict(account=a,amount=n,commodity=code)
        result=payment(dict(postings=[p('Expenses:Food','100'),p('Expenses:Discount','-10'),p('Assets:Cash','-40'),p('Assets:Cash','-50')]))
        self.assertEqual(result,dict(total='90 '+code,payment_account='Assets:Cash'))
        self.assertEqual(payment(dict(postings=[p('Expenses:Food','-100'),p('Assets:Cash','100')]))['payment_account'],'Assets:Cash')
        accounts={'Assets:Cash':{'kind':'cash'},'Assets:Owed':{'kind':'person'}}
        self.assertEqual(payment(dict(postings=[p('Assets:Owed','-100'),p('Assets:Cash','100')]),accounts)['payment_account'],'Assets:Cash')


class ReceiptTests(Fixture):
    seed=CorrectionTests.seed

    def receipt(self, e, **values):
        answer=dict(read_ok=True,references=['receipt-reference'],payment_datetime=None,payee='Willow recipient',date=e['date'],amount='100.00',currency=self.currency,paying_account='Assets:Cash',notes=['Printed receipt detail'])
        answer.update(values)
        self.set_answers(answer)
        drafts=Drafts(self.ledger);draft=drafts.create('Usual payment')
        draft.update(state='added',completed_at=__import__('agent').now(),result=dict(outcome='ready',entry_id=e['id'],sources=e.get('sources',[])),receipt_reading=dict(state='queued',queued_at=__import__('agent').now(),entry_id=e['id'],sources=e.get('sources',[])))
        drafts.put(draft);worker=Worker(self.settings,drafts)
        read(worker,draft)
        return drafts,drafts.get(draft['id']),worker

    def test_agreement_adds_details_through_correction_and_reads_filed_files(self):
        e=self.seed();drafts,d,worker=self.receipt(e)
        self.assertEqual(d['receipt_reading']['state'],'done')
        after=entries(load(self.ledger))[0]
        self.assertEqual(after['postings'],e['postings']);self.assertEqual(after['reference'],'original-ref');self.assertIn('Receipt reference: receipt-reference',after['notes'])
        self.assertIn('Willow recipient',' '.join(after['notes']));self.assertEqual(records(self.ledger)[-1]['receipt']['choice'],'agree')
        self.assertIn('(receipt details for '+e['id']+')',self.jj('log','-r','@-','--no-graph','-T','description'))
        self.assertTrue(self.calls());self.assertFalse((drafts.folder(d['id'])/'files').exists() and list((drafts.folder(d['id'])/'files').iterdir()))

    def test_disagreement_use_or_keep_audits_choice_and_keeps_pending_after_expiry(self):
        for choice in ('keep','use'):
            with self.subTest(choice=choice):
                # Independent entry state, same disposable repository.
                e=entries(load(self.ledger))[0] if (self.ledger/'data/expenses/2026.json').read_text().strip()!='[]' else self.seed()
                if not hasattr(self,'currency'):self.currency=json.loads((self.ledger/'ledger.json').read_text())['defaultCurrency']
                drafts,d,worker=self.receipt(e,date='2026-10-28',amount='120.00')
                self.assertEqual(d['receipt_reading']['state'],'question');self.assertEqual(entries(load(self.ledger))[0],e)
                drafts.purge(datetime.now(timezone.utc)+timedelta(days=2));self.assertTrue(drafts.folder(d['id']).exists())
                result=choose(self.settings,drafts,d,choice);self.assertEqual(result['receipt_reading']['state'],'done')
                after=entries(load(self.ledger))[0]
                self.assertEqual(after['date'],'2026-10-28' if choice=='use' else e['date']);self.assertEqual(after['postings'][0]['amount'],'120.00' if choice=='use' else e['postings'][0]['amount'])
                self.assertEqual(records(self.ledger)[-1]['receipt']['choice'],choice)
                self.assertEqual(choose(self.settings,drafts,result,choice)['receipt_reading']['state'],'done')

    def test_unreadable_and_unsafe_evidence_change_nothing(self):
        e=self.seed();drafts,d,worker=self.receipt(e,read_ok=False)
        self.assertEqual(d['receipt_reading']['state'],'unreadable');self.assertEqual(records(self.ledger),[])
        for source in ['../outside.png','Bills/../outside.png','/tmp/outside.png']:
            with self.assertRaises(ValueError):safe_evidence(self.ledger,source)
        (self.ledger/'Bills/link.png').symlink_to(self.ledger/e['sources'][0])
        with self.assertRaisesRegex(ValueError,'symlink'):safe_evidence(self.ledger,'Bills/link.png')
        (self.ledger/'Bills/link.png').unlink()
        outside=self.base/'outside';outside.mkdir();(self.ledger/'Bills/linked').symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink'):safe_evidence(self.ledger,'Bills/linked/file.png')

    def test_split_refusal_and_statement_locks(self):
        e=self.seed();e['postings'][0]['amount']='50.00';e['postings'].insert(1,deepcopy(e['postings'][0]));dump(self.ledger/'data/expenses/2026.json',[e]);self.jj('commit','-m','Split fixture')
        drafts,d,worker=self.receipt(e,amount='120.00')
        with self.assertRaisesRegex(ValueError,'split payment'):choose(self.settings,drafts,d,'use')
        choose(self.settings,drafts,d,'keep');self.assertEqual(entries(load(self.ledger))[0]['postings'],e['postings'])

    def test_conversion_refusal_and_agreement_resume_without_another_read(self):
        e=self.seed();declarations=load(self.ledger)[self.ledger/'data/accounts.json'];foreign=next(c for c in declarations['commodities'] if c!=self.currency)
        e['postings'][0].update(amount='20.00',commodity=foreign,cost=dict(amount='100.00',commodity=self.currency,per='total'))
        dump(self.ledger/'data/expenses/2026.json',[e]);self.jj('commit','-m','Fictional conversion')
        drafts,d,worker=self.receipt(e,amount='120.00')
        with self.assertRaisesRegex(ValueError,'currency conversion'):choose(self.settings,drafts,d,'use')
        choose(self.settings,drafts,d,'keep')
        e=entries(load(self.ledger))[0]
        with patch('receipts.choose',side_effect=RuntimeError('interrupted before correction')):
            with self.assertRaisesRegex(RuntimeError,'interrupted'):self.receipt(e,amount='100.00')
        queued=next(x for x in drafts.all() if x['receipt_reading']['state']=='ready')
        with patch('agent.run',side_effect=AssertionError('Receipt was read twice')): read(worker,queued)
        self.assertEqual(drafts.get(queued['id'])['receipt_reading']['state'],'done')

    def test_active_receipt_cannot_be_discarded_and_missing_record_keeps_worker_alive(self):
        import threading
        e=self.seed()
        with self.client(worker=False) as client:
            drafts=client.app.state.drafts;d=drafts.create('Receipt lifecycle')
            d.update(state='added',completed_at=__import__('agent').now(),result=dict(outcome='ready',entry_id=e['id']),receipt_reading=dict(state='reading',queued_at=__import__('agent').now(),entry_id=e['id'],sources=e['sources']))
            drafts.put(d)
            response=client.delete('/api/drafts/'+d['id']);self.assertEqual(response.status_code,409)
            d['receipt_reading']['state']='question';drafts.put(d)
            # Financial entries leave the Queue, including receipt follow-ups.
            self.assertEqual(client.delete('/api/drafts/'+d['id']).status_code,409)
            self.assertFalse(drafts.get(d['id']).get('trashed_at'))
        vanished=threading.Event()
        def missing(worker,draft):
            shutil.rmtree(worker.drafts.folder(draft['id']));vanished.set();raise FileNotFoundError('removed temporary receipt record')
        with patch('receipts.read',side_effect=missing),self.client() as client:
            drafts=client.app.state.drafts;d=drafts.create('Receipt recovery')
            d.update(state='added',completed_at=__import__('agent').now(),result=dict(outcome='ready',entry_id=e['id']),receipt_reading=dict(state='queued',queued_at=__import__('agent').now(),entry_id=e['id'],sources=e['sources']))
            drafts.put(d);client.app.state.worker.wake.set();self.assertTrue(vanished.wait(5));client.app.state.worker.thread.join(.2)
            self.assertTrue(client.app.state.worker.thread.is_alive())

    def test_usual_saves_without_agent_then_queues_reading_and_pause_blocks_it(self):
        e=self.seed(rule=True);drafts=Drafts(self.ledger);d=drafts.create('Usual payment')
        folder=drafts.folder(d['id'])/'files';file=folder/'receipt.png';file.write_bytes(png());d['files']=[dict(id='b'*32,name=file.name,extension='.png',original_name='Receipt.png',media_type='image/png',size=file.stat().st_size)]
        queue_usual(self.settings,drafts,d,dict(rule_id='f'*16,date='2026-11-29'))
        with patch('agent.run',side_effect=AssertionError('Addition called agent')):
            with ledger_lock(self.ledger) as fd:result=Transaction(self.settings,drafts,d).apply(d['proposal'],fd)
            drafts.completed(d,result)
        self.assertEqual(d['receipt_reading']['state'],'queued');self.assertTrue(all((self.ledger/p).is_file() for p in d['receipt_reading']['sources']))
        worker=Worker(self.settings,drafts);worker.pause.fail('auth');self.assertTrue(worker.pause.blocked());self.assertEqual(drafts.get(d['id'])['receipt_reading']['state'],'queued')
        worker.pause.success();worker.retry_interval=.01
        reading=d['receipt_reading'];answer=dict(read_ok=True,references=['saved-reference'],payment_datetime=None,payee=None,date='2026-11-29',amount='100.00',currency=self.currency,paying_account='Assets:Cash',notes=None)
        dirty=self.ledger/'owner-work.txt';dirty.write_text('Unfinished owner work')
        with patch('agent.run',return_value=(answer,{})) as run,patch.object(worker.daily_refresh,'tick'):
            worker.start()
            try:
                with drafts.changed:
                    waiting=drafts.changed.wait_for(lambda:drafts.get(d['id'])['receipt_reading']['state']=='ready' and bool(drafts.get(d['id']).get('waiting')),timeout=5)
                self.assertTrue(waiting,'Retain the extracted answer and wait for a clean ledger')
                dirty.unlink()
                with drafts.changed:
                    done=drafts.changed.wait_for(lambda:drafts.get(d['id'])['receipt_reading']['state']=='done',timeout=180)
                self.assertTrue(done);self.assertEqual(run.call_count,1);self.assertIsNone(drafts.get(d['id'])['waiting'])
            finally:
                dirty.unlink(missing_ok=True);worker.close()

