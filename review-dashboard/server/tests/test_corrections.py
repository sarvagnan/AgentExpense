"""Verified replacements, receipt follow-ups and draft declines in fictional ledgers."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import shutil
import subprocess
from unittest.mock import patch

from support import Fixture, ready, png, entry as store_entry, posting, rule as store_rule
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
        e=store_entry('a'*16,day,'Cafe — lunch — cash',[posting('Expenses:Food','100.00'),posting('Assets:Cash','-100.00')],
                      classification='owner classification',references={'reference':'original-ref','order-id':[]},notes=['User confirmed payment'])
        if evidence:
            source=f'Bills/{day}/Willow.png';path=self.ledger/source;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(png());e['evidence']=[source]
        if rule:
            r=store_rule('f'*16,'2026-11-29',e['description'],deepcopy(e['postings']),schedule={'every':'month','from':'2026-11-29','interval':1,'anchor-day':29})
            e['rule']=dict(id=r['id'],occurrence=day);dump(self.ledger/'data/forecast.json',[r])
        dump(self.ledger/f'data/expenses/{day[:4]}.json',[e]);self.jj('commit','-m','Fictional correction baseline')
        return e

    def body(self,e):
        return dict(version=entry_hash(e),proposal=wire_entry(self.settings,e),sources=e['evidence'],approved_accounts=[],reason='Corrected from the receipt')

    def save(self,e,body=None,op=None):
        return apply(self.settings,e['id'],op or secrets.token_hex(16),body or self.body(e))

    def test_reload_can_reclaim_with_the_token_kept_before_a_lost_claim_reply(self):
        e=self.seed()
        with self.client(worker=False) as client:
            draft=client.post('/api/entries/'+e['id']+'/correction-form').json()
            token='f'*64
            first=client.post('/api/drafts/'+draft['id']+'/claim',json={'claim_token':token})
            self.assertEqual(first.status_code,200,first.text)
            self.assertEqual(first.json()['claim_token'],token)
            second=client.post('/api/drafts/'+draft['id']+'/claim',json={'claim_token':token})
            self.assertEqual(second.status_code,200,second.text)
            self.assertEqual(second.json()['claim_token'],token)

    def test_new_editor_rotates_claim_and_obsolete_or_foreign_writes_are_refused(self):
        e=self.seed()
        with self.client(worker=False) as client:
            draft=client.post('/api/entries/'+e['id']+'/correction-form').json();url='/api/drafts/'+draft['id']
            first=client.post(url+'/claim',json={'claim_token':'a'*64}).json()
            second=client.post(url+'/claim',json={'claim_token':'b'*64,'previous_token':first['claim_token']})
            self.assertEqual(second.status_code,200,second.text)
            for token in [first['claim_token'],'c'*64,'bad','🍃',None,1,{'malformed':True}]:
                response=client.post(url+'/release',json={'claim_token':token,'proposal':first['form']})
                self.assertEqual(response.status_code,409,response.text)
            duplicate=client.post(url+'/claim',json={'claim_token':'d'*64,'previous_token':first['claim_token']})
            self.assertEqual(duplicate.status_code,409,duplicate.text)
            kept=Drafts(self.ledger).get(draft['id'])
            self.assertEqual(kept['claim']['token'],'b'*64)
            # Reordered reopen: D arrives before its predecessor C can transfer B.
            self.assertEqual(client.post(url+'/claim',json={'claim_token':'d'*64,'previous_token':'c'*64}).status_code,409)
            self.assertEqual(client.post(url+'/claim',json={'claim_token':'c'*64,'previous_token':'b'*64}).status_code,200)
            self.assertEqual(client.post(url+'/release',json={'claim_token':'c'*64}).status_code,200)
            self.assertEqual(client.post(url+'/claim',json={'claim_token':'e'*64,'previous_token':'d'*64}).status_code,200)

    def test_cancel_refuses_a_save_already_accepted_by_the_fifo(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        e=self.seed()
        with self.client(worker=False) as client:
            d=client.post('/api/entries/'+e['id']+'/correction-form').json();url='/api/drafts/'+d['id']
            claim=client.post(url+'/claim',json={}).json();body={**self.body(e),'draft_id':d['id']}
            body['proposal']['entry']['description']='Save held in FIFO'
            started=threading.Event();release=threading.Event()
            def held(*args,**kwargs):
                started.set();self.assertTrue(release.wait(10));raise ValueError('Known refusal')
            with patch.object(client.app.state.worker,'call',side_effect=held),ThreadPoolExecutor() as pool:
                saving=pool.submit(client.post,'/api/entries/'+e['id']+'/corrections/'+secrets.token_hex(16),json=body)
                try:
                    self.assertTrue(started.wait(5))
                    cancel=client.post(url+'/cancel-edit',json={'claim_token':claim['claim_token']})
                    self.assertEqual(cancel.status_code,409,cancel.text)
                    self.assertTrue((self.ledger/'drafts'/d['id']).exists())
                finally:release.set()
                self.assertEqual(saving.result().status_code,409)

    def test_cancel_waits_for_an_applying_save_then_refuses_its_committed_draft(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor, TimeoutError
        e=self.seed();drafts=Drafts(self.ledger);d=open_form(self.settings,drafts,e['id'])
        body={**self.body(e),'draft_id':d['id']};body['proposal']['entry']['description']='Committed held edit'
        publishing=threading.Event();release=threading.Event();original=Transaction.publish
        def held(tx,*args,**kwargs):
            publishing.set();self.assertTrue(release.wait(30));return original(tx,*args,**kwargs)
        with self.client(worker=False) as client:
            claim=client.post('/api/drafts/'+d['id']+'/claim',json={}).json()
            with patch.object(Transaction,'publish',held),ThreadPoolExecutor() as pool:
                saving=pool.submit(apply,self.settings,e['id'],secrets.token_hex(16),body,drafts=drafts)
                self.assertTrue(publishing.wait(10))
                cancelling=pool.submit(client.post,'/api/drafts/'+d['id']+'/cancel-edit',json={'claim_token':claim['claim_token']})
                try:
                    with self.assertRaises(TimeoutError):cancelling.result(timeout=.1)
                finally:release.set()
                self.assertEqual(saving.result(timeout=120)['entry_id'],e['id'])
                self.assertEqual(cancelling.result(timeout=120).status_code,409)
                self.assertEqual(drafts.get(d['id'])['state'],'added')

    def test_correction_after_cancel_reports_a_plain_conflict(self):
        e=self.seed()
        with self.client(worker=False) as client:
            d=client.post('/api/entries/'+e['id']+'/correction-form').json();url='/api/drafts/'+d['id']
            claim=client.post(url+'/claim',json={}).json()
            self.assertEqual(client.post(url+'/cancel-edit',json={'claim_token':claim['claim_token']}).status_code,200)
            body={**self.body(e),'draft_id':d['id']};body['proposal']['entry']['description']='Cancelled words'
            response=client.post('/api/entries/'+e['id']+'/corrections/'+secrets.token_hex(16),json=body)
            self.assertEqual(response.status_code,409,response.text)
            self.assertIn('cancelled',response.text)

    def test_fix_discard_restore_keeps_agent_proposal_separate_from_owner_edits(self):
        e=self.seed();drafts=Drafts(self.ledger);d=open_form(self.settings,drafts,e['id'])
        d['ledger_fix']=dict(state='ready',title='Fictional check',summary='Proposed correction')
        d['hand_form']['entry']['description']='Agent baseline';drafts.put(d)
        with self.client(worker=False) as client:
            url='/api/drafts/'+d['id'];claim=client.post(url+'/claim',json={}).json()
            changed=deepcopy(claim['form']);changed['entry']['description']='Owner discarded edits'
            client.post(url+'/hand-form',json={'claim_token':claim['claim_token'],'proposal':changed,'sources':[],'reason':'Owner reason'})
            client.post(url+'/release',json={'claim_token':claim['claim_token']})
            self.assertEqual(client.delete(url).status_code,204)
            self.assertEqual(client.post(url+'/restore').status_code,200)
            restored=Drafts(self.ledger).get(d['id'])
            self.assertEqual(restored['hand_form']['entry']['description'],'Owner discarded edits')
            self.assertEqual(restored['correction']['reason'],'Owner reason')
            again=client.post(url+'/claim',json={}).json()
            self.assertEqual(again['original_form']['entry']['description'],'Agent baseline')
            self.assertEqual(client.post(url+'/cancel-edit',json={'claim_token':again['claim_token']}).status_code,200)
            kept=Drafts(self.ledger).get(d['id'])
            self.assertEqual(kept['hand_form']['entry']['description'],'Agent baseline')
            self.assertEqual(kept['correction']['sources'],e['evidence'])

    def test_queue_poll_never_rebuilds_forms_and_an_old_malformed_edit_cannot_fail_the_list(self):
        e=self.seed();drafts=Drafts(self.ledger);d=open_form(self.settings,drafts,e['id'])
        old=open_form(self.settings,drafts,e['id']);old['correction']['before']={'description':'Older entry'};drafts.put(old)
        with self.client(worker=False) as client,patch('corrections.wire_entry',side_effect=AssertionError('List rebuilt a form')):
            for _ in range(2):
                response=client.get('/api/drafts')
                self.assertEqual(response.status_code,200,response.text)
                self.assertTrue({d['id'],old['id']} <= {r['id'] for r in response.json()})

    def test_cancel_reclaims_an_expired_edit_and_actually_discards_its_changes(self):
        e=self.seed();drafts=Drafts(self.ledger)
        with self.client(worker=False) as client:
            for fix in (True,False):
                with self.subTest(fix=fix):
                    d=open_form(self.settings,drafts,e['id'])
                    if fix:d['ledger_fix']=dict(state='ready',title='Fictional fix');drafts.put(d)
                    url='/api/drafts/'+d['id'];claim=client.post(url+'/claim',json={}).json()
                    typed=deepcopy(claim['form']);typed['entry']['description']='Owner edit to discard'
                    client.post(url+'/hand-form',json={'claim_token':claim['claim_token'],'proposal':typed})
                    old=drafts.get(d['id']);old['claim']['expires_at']='2000-01-01T00:00:00+00:00';drafts.put(old)
                    response=client.post(url+'/cancel-edit',json={'claim_token':claim['claim_token']})
                    self.assertEqual(response.status_code,200,response.text)
                    if fix:
                        kept=drafts.get(d['id']);self.assertEqual(kept['hand_form'],claim['form']);self.assertNotIn('claim',kept)
                    else:self.assertFalse(drafts.folder(d['id']).exists())

    def test_cancel_refuses_another_live_editor_without_touching_its_form(self):
        e=self.seed()
        with self.client(worker=False) as client:
            d=client.post('/api/entries/'+e['id']+'/correction-form').json();url='/api/drafts/'+d['id']
            first=client.post(url+'/claim',json={'claim_token':'a'*64}).json()
            client.post(url+'/claim',json={'claim_token':'b'*64,'previous_token':first['claim_token']})
            response=client.post(url+'/cancel-edit',json={'claim_token':first['claim_token']})
            self.assertEqual(response.status_code,409,response.text);self.assertIn('Another editor',response.text)
            self.assertEqual(Drafts(self.ledger).get(d['id'])['claim']['token'],'b'*64)

    def test_startup_clears_uncommitted_save_marker_and_completes_a_committed_one(self):
        e=self.seed();drafts=Drafts(self.ledger)
        pending=open_form(self.settings,drafts,e['id']);pending['correction_save']=secrets.token_hex(16);drafts.put(pending)
        saved=open_form(self.settings,drafts,e['id']);op=secrets.token_hex(16);saved['correction_save']=op;drafts.put(saved)
        body={**self.body(e),'draft_id':saved['id']};body['proposal']['entry']['description']='Committed before restart'
        with patch.object(drafts,'completed',side_effect=RuntimeError('Simulated crash after commit')):
            with self.assertRaisesRegex(RuntimeError,'Simulated crash'):apply(self.settings,e['id'],op,body,drafts=drafts)
        commit=self.jj('log','-r','@-','--no-graph','-T','commit_id');audit=records(self.ledger)
        with self.client(worker=False) as client:
            current=drafts.get(pending['id']);self.assertNotIn('correction_save',current)
            completed=drafts.get(saved['id']);self.assertEqual(completed['state'],'added');self.assertNotIn('correction_save',completed)
            self.assertEqual(completed['result']['entry_id'],e['id'])
            token=client.post('/api/drafts/'+pending['id']+'/claim',json={}).json()['claim_token']
            self.assertEqual(client.post('/api/drafts/'+pending['id']+'/cancel-edit',json={'claim_token':token}).status_code,200)
            self.assertEqual(records(self.ledger),audit);self.assertEqual(self.jj('log','-r','@-','--no-graph','-T','commit_id'),commit)

    def test_startup_keeps_an_unsettled_correction_marker_without_blocking_other_requests(self):
        class Crash(BaseException):pass
        e=self.seed();drafts=Drafts(self.ledger);blocked=open_form(self.settings,drafts,e['id']);op=secrets.token_hex(16)
        blocked['correction_save']=op;drafts.put(blocked)
        body={**self.body(e),'draft_id':blocked['id']};body['proposal']['entry']['description']='Interrupted fictional correction'
        with patch.object(Transaction,'verified_commit',side_effect=Crash('Stopped during Save')),patch.object(Transaction,'rollback',side_effect=Crash('Process stopped before rollback')):
            with self.assertRaises(Crash):apply(self.settings,e['id'],op,body,drafts=drafts)
        journal=self.ledger/'drafts/corrections'/op/'transaction.json';self.assertTrue(journal.exists())
        (self.ledger/'fictional-later-note.txt').write_text('An unrelated fictional change after the crash')
        self.jj('commit','-m','Fictional unrelated change after interrupted Save')
        healthy=open_form(self.settings,drafts,e['id']);healthy['correction_save']=secrets.token_hex(16);drafts.put(healthy)
        before=self.jj('log','-r','@-','--no-graph','-T','commit_id');audit=records(self.ledger);saved_journal=journal.read_bytes()
        with self.assertLogs('corrections',level='WARNING') as logs:
            with self.client() as client:
                self.assertEqual(client.get('/api/health').status_code,200)
                self.assertEqual(client.get('/api/drafts').status_code,200)
                self.assertEqual(drafts.get(blocked['id'])['correction_save'],op)
                self.assertNotIn('correction_save',drafts.get(healthy['id']))
                url='/api/drafts/'+blocked['id']
                for response in (client.post(url+'/cancel-edit',json={'claim_token':'a'*64}),client.delete(url)):
                    self.assertEqual(response.status_code,409,response.text);self.assertIn('Save',response.text)
                claim=client.post('/api/drafts/'+healthy['id']+'/claim',json={}).json()
                response=client.post('/api/drafts/'+healthy['id']+'/cancel-edit',json={'claim_token':claim['claim_token']})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(journal.read_bytes(),saved_journal);self.assertEqual(records(self.ledger),audit)
                self.assertEqual(self.jj('log','-r','@-','--no-graph','-T','commit_id'),before)
        self.assertTrue(any(op in message and 'later human changes' in message for message in logs.output),logs.output)

    def test_startup_leaves_an_unreadable_recovery_record_isolated(self):
        e=self.seed();drafts=Drafts(self.ledger);blocked=open_form(self.settings,drafts,e['id']);op=secrets.token_hex(16)
        blocked['correction_save']=op;drafts.put(blocked)
        folder=self.ledger/'drafts/corrections'/op;folder.mkdir(parents=True)
        (folder/'record.json').write_text('{');(folder/'transaction.json').write_text('{}')
        healthy=open_form(self.settings,drafts,e['id']);healthy['correction_save']=secrets.token_hex(16);drafts.put(healthy)
        with self.assertLogs('corrections',level='WARNING') as logs:
            with self.client() as client:
                self.assertEqual(client.get('/api/health').status_code,200)
                self.assertEqual(drafts.get(blocked['id'])['correction_save'],op)
                self.assertNotIn('correction_save',drafts.get(healthy['id']))
        self.assertTrue(any(op in message for message in logs.output),logs.output)

    def test_a_timed_out_fix_can_be_checked_saved_once_and_replayed(self):
        e=self.seed();drafts=Drafts(self.ledger);draft=open_form(self.settings,drafts,e['id'])
        draft.update(state='failed',ledger_fix=dict(state='failed',entry_id=e['id'],title='Fictional fix',message='The agent timed out'))
        drafts.put(draft)
        with self.client(worker=False) as client:
            url='/api/drafts/'+draft['id'];claim=client.post(url+'/claim',json={'claim_token':'a'*64})
            self.assertEqual(claim.status_code,200,claim.text)
            proposal=deepcopy(claim.json()['form']);proposal['entry']['description']='Owner finished the timed-out fix'
            checked=client.post(url+'/hand-form',json=dict(claim_token=claim.json()['claim_token'],proposal=proposal,sources=e['evidence'],reason='Checked by the owner'))
            self.assertEqual(checked.status_code,200,checked.text);self.assertTrue(checked.json()['ok'])
            before=len(records(self.ledger));op=secrets.token_hex(16)
            body=dict(version=draft['correction']['version'],proposal=proposal,sources=e['evidence'],approved_accounts=[],reason='Checked by the owner',draft_id=draft['id'])
            saved=client.post('/api/entries/'+e['id']+'/corrections/'+op,json=body)
            self.assertEqual(saved.status_code,200,saved.text)
            self.assertEqual(drafts.get(draft['id'])['state'],'added');self.assertNotIn('correction_save',drafts.get(draft['id']))
            self.assertEqual(next(row for row in entries(load(self.ledger)) if row['id']==e['id'])['description'],'Owner finished the timed-out fix')
            committed=self.jj('log','-r','@-','--no-graph','-T','commit_id')
            replay=client.post('/api/entries/'+e['id']+'/corrections/'+op,json=body)
            self.assertEqual(replay.status_code,200,replay.text);self.assertTrue(replay.json()['replayed'])
            self.assertEqual(len(records(self.ledger)),before+1);self.assertEqual(self.jj('log','-r','@-','--no-graph','-T','commit_id'),committed)
            current=next(row for row in entries(load(self.ledger)) if row['id']==e['id'])
            ordinary=open_form(self.settings,drafts,e['id']);ordinary['state']='failed';drafts.put(ordinary)
            forbidden={**self.body(current),'draft_id':ordinary['id']};forbidden['proposal']['entry']['description']='Ordinary failed correction'
            refused=client.post('/api/entries/'+e['id']+'/corrections/'+secrets.token_hex(16),json=forbidden)
            self.assertEqual(refused.status_code,409,refused.text);self.assertIn('This correction draft cannot be used',refused.text)

    def test_retry_requires_confirmation_before_replacing_kept_fix_edits(self):
        e=self.seed();drafts=Drafts(self.ledger);d=open_form(self.settings,drafts,e['id'])
        d.update(state='failed',ledger_fix=dict(state='failed',title='Fictional fix'));drafts.put(d)
        with self.client(worker=False) as client:
            url='/api/drafts/'+d['id'];claim=client.post(url+'/claim',json={}).json()
            typed=deepcopy(claim['form']);typed['entry']['description']='Kept owner edits'
            client.post(url+'/hand-form',json={'claim_token':claim['claim_token'],'proposal':typed,'sources':[],'reason':'Kept owner reason'})
            client.post(url+'/release',json={'claim_token':claim['claim_token']})
            refused=client.post(url+'/retry');self.assertEqual(refused.status_code,409,refused.text)
            kept=drafts.get(d['id']);self.assertEqual(kept['hand_form'],typed);self.assertEqual(kept['state'],'failed')
            check=client.get(url+'/retry-check');self.assertEqual(check.status_code,200,check.text);self.assertTrue(check.json()['replace_edits'])
            newer=drafts.get(d['id']);newer['correction']['reason']='Newer kept reason';drafts.put(newer)
            self.assertEqual(client.post(url+'/retry',json={'replace_edits':check.json()['version']}).status_code,409)
            self.assertEqual(drafts.get(d['id'])['correction']['reason'],'Newer kept reason')
            check=client.get(url+'/retry-check')
            accepted=client.post(url+'/retry',json={'replace_edits':check.json()['version']});self.assertEqual(accepted.status_code,200,accepted.text)
            queued=drafts.get(d['id']);self.assertEqual(queued['state'],'queued');self.assertEqual(queued['hand_form'],claim['form'])
            self.assertEqual(queued['correction']['sources'],e['evidence']);self.assertEqual(queued['correction']['reason'],'')

    def test_queue_discard_and_claim_refuse_a_save_in_progress(self):
        e=self.seed();drafts=Drafts(self.ledger)
        with self.client(worker=False) as client:
            d=open_form(self.settings,drafts,e['id']);d['correction_save']=secrets.token_hex(16);drafts.put(d)
            url='/api/drafts/'+d['id']
            with patch.object(client.app.state.worker,'call',side_effect=AssertionError('Discard entered FIFO during Save')):
                for method,path,body in [('DELETE',url,None),('POST','/api/retention/operations/'+secrets.token_hex(16),dict(action='queue-discard',item_id=d['id']))]:
                    with self.subTest(method=method):
                        refused=client.request(method,path,json=body);self.assertEqual(refused.status_code,409,refused.text);self.assertIn('Save',refused.text)
            claim=client.post(url+'/claim',json={});self.assertEqual(claim.status_code,409,claim.text);self.assertIn('Save',claim.text)

    def test_discard_keeps_queued_fix_claim_until_its_skip_marker_is_recorded(self):
        e=self.seed();drafts=Drafts(self.ledger);d=open_form(self.settings,drafts,e['id'])
        d.update(state='queued',queued_at=datetime.now(timezone.utc).isoformat(),ledger_fix=dict(state='queued',title='Fictional fix'));drafts.put(d)
        with self.client(worker=False) as client:
            token=client.post('/api/drafts/'+d['id']+'/claim',json={}).json()['claim_token']
            worker=client.app.state.worker;original=worker.call;observed=[]
            def check_flag(operation,**kwargs):
                current=drafts.get(d['id']);observed.append(current.get('discard_requested'))
                self.assertTrue(current.get('discard_requested'));self.assertNotIn('claim',current)
                self.assertGreater(worker.idle_delay(worker.clock()+10),0,'The worker skips the marked queued item')
                return original(operation,**kwargs)
            with patch.object(worker,'call',side_effect=check_flag):
                response=client.request('DELETE','/api/drafts/'+d['id'],json={'claim_token':token})
            self.assertEqual(response.status_code,204,response.text);self.assertEqual(observed,[True])
            self.assertTrue(drafts.get(d['id'],include_discarded=True)['trashed_at'])

    def test_cancel_removes_temporary_edit_without_commit_trash_or_audit(self):
        e=self.seed()
        with self.client(worker=False) as client:
            draft=client.post('/api/entries/'+e['id']+'/correction-form').json()
            claim=client.post('/api/drafts/'+draft['id']+'/claim',json={}).json()
            before=self.jj('log','-r','@-','--no-graph','-T','commit_id')
            audit=records(self.ledger)
            response=client.post('/api/drafts/'+draft['id']+'/cancel-edit',json={'claim_token':claim['claim_token']})
            self.assertEqual(response.status_code,200,response.text)
            self.assertFalse((self.ledger/'drafts'/draft['id']).exists())
            self.assertEqual(client.post('/api/drafts/'+draft['id']+'/cancel-edit',json={'claim_token':claim['claim_token']}).status_code,200)
            self.assertEqual(client.get('/api/trash').json()['items'],[])
            self.assertEqual(records(self.ledger),audit)
            self.assertEqual(self.jj('log','-r','@-','--no-graph','-T','commit_id'),before)

    def test_cancel_fix_preserves_proposal_and_releases_claim(self):
        e=self.seed();draft=open_form(self.settings,Drafts(self.ledger),e['id'])
        draft['ledger_fix']=dict(state='ready',title='Fictional check',summary='Proposed correction')
        draft['hand_form']['entry']['description']='Agent proposal'
        Drafts(self.ledger).put(draft)
        with self.client(worker=False) as client:
            claim=client.post('/api/drafts/'+draft['id']+'/claim',json={}).json()
            changed=deepcopy(claim['form']);changed['entry']['description']='Unsaved owner edit'
            client.post('/api/drafts/'+draft['id']+'/hand-form',json={'claim_token':claim['claim_token'],'proposal':changed})
            response=client.post('/api/drafts/'+draft['id']+'/cancel-edit',json={'claim_token':claim['claim_token']})
            self.assertEqual(response.status_code,200,response.text)
            kept=Drafts(self.ledger).get(draft['id'])
            self.assertNotIn('claim',kept)
            self.assertEqual(kept['hand_form'],draft['hand_form'])
            self.assertEqual(kept['ledger_fix'],draft['ledger_fix'])
            self.assertEqual(client.post('/api/drafts/'+draft['id']+'/cancel-edit',json={'claim_token':claim['claim_token']}).status_code,200)

    def test_identity_year_metadata_evidence_commit_and_audit_replay(self):
        before=self.seed(rule=True);body=self.body(before);body['proposal']['entry'].update(date='2027-01-02',description='Willow corrected')
        op=secrets.token_hex(16);result=self.save(before,body,op)
        current=entries(load(self.ledger))[0]
        self.assertEqual(current['id'],before['id']);self.assertEqual(current['references'],before['references']);self.assertEqual(current['classification'],before['classification'])
        self.assertEqual(current['postings'],before['postings']);self.assertEqual(current['evidence'],['Bills/2027-01-02/Willow.png'])
        self.assertFalse((self.ledger/before['evidence'][0]).exists());self.assertTrue((self.ledger/current['evidence'][0]).is_file())
        self.assertEqual(load(self.ledger)[self.ledger/'data/expenses/2026.json'],[])
        line=records(self.ledger)[-1];self.assertEqual(line['kind'],'correction');self.assertEqual(line['before'],before);self.assertEqual(line['after'],current)
        message=self.jj('log','-r','@-','--no-graph','-T','description')
        for field in ['Edit Willow corrected via dashboard','Entry: '+before['id'],'Changed: ','Reason: "','Rule: kept','Evidence: Bills/2027-01-02/Willow.png','Draft: '+op,'Added-by: user (correction)']: self.assertIn(field,message)
        shutil.rmtree(self.ledger/'drafts/corrections')
        self.assertTrue(self.save(before,body,op)['replayed']);self.assertTrue(clean(self.ledger))
        with self.assertRaisesRegex(ValueError,'Open the saved entry and compare'): self.save(before,{**body,'reason':'different'},op)

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
        for value in [dict(evidence=e['evidence']),dict(notes=e['evidence'])]:
            docs[self.ledger/'data/forecast.json']=[value];candidate=deepcopy(e);candidate['date']='2027-01-02'
            self.assertEqual(move_evidence(docs,self.ledger,e,candidate),[])
        docs[self.ledger/'data/forecast.json']=[];docs[self.ledger/'data/card-statements.json']=[dict(source=e['evidence'][0])]
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
        result=self.save(e,body);self.assertEqual(result['rule']['status'],'dropped');self.assertIsNone(entries(load(self.ledger))[0]['rule'])

    def test_new_account_consent_and_cli_same_service(self):
        e=self.seed();body=self.body(e);body['proposal']['entry']['postings'][0]['account']='Expenses:Learning';body['proposal']['new-accounts']=['Expenses:Learning']
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
            form['attachment-names']=['More']
            response=client.post(f'/api/drafts/{draft["id"]}/hand-form',json=dict(claim_token=token,proposal=form));self.assertTrue(response.json()['ok'],response.text)
            refused=client.post(f'/api/drafts/{draft["id"]}/hand-add',json=dict(claim_token=token,proposal_hash=response.json()['proposal_hash'],approved_accounts=[]));self.assertEqual(refused.status_code,409)
            with ledger_lock(self.ledger) as fd:
                with self.assertRaisesRegex(ValueError,'correction service'):Transaction(self.settings,drafts=client.app.state.drafts,draft=client.app.state.drafts.get(draft['id'])).apply(form,fd)
            body=dict(version=draft['correction']['version'],proposal=form,sources=e['evidence'],approved_accounts=[],draft_id=draft['id'])
            result=client.post(f'/api/entries/{e["id"]}/corrections/{secrets.token_hex(16)}',json=body);self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(len(entries(load(self.ledger))[0]['evidence']),2)

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
        d=drafts.create('Willow monthly membership');d['declined_rules']=[rule['id']];d['finished_by']=dict(actor='user',reason='hand');d['hand_form']=wire_entry(self.settings,e);d['hand_form']['entry']['date']='2026-12-29';d['hand_form']['attachment-names']=[];drafts.save_time(d)
        with ledger_lock(self.ledger) as fd: result=Transaction(self.settings,drafts,d).apply(d['hand_form'],fd)
        self.assertIsNone(result.get('rule'));self.assertEqual(result.get('resume_rules',[]),[]);self.assertIsNone(next(e for e in entries(load(self.ledger)) if e['id']==result['entry_id'])['rule']);self.assertEqual(len(load(self.ledger)[self.ledger/'data/forecast.json']),1)

    def test_statement_correction_preserves_membership_and_refuses_money_changes(self):
        from test_review import ReviewTests
        rows=ReviewTests.seed(self,statement=True);e=next(r for r in rows if (r['statement'] or {}).get('end'))
        body=self.body(e);body['proposal']['entry'].update(description='Corrected statement workshop',date='2027-01-02')
        result=self.save(e,body);after=next(r for r in entries(load(self.ledger)) if r['id']==e['id'])
        for key in ('statement','evidence','postings'): self.assertEqual(after[key],e[key])
        body=self.body(after);body['proposal']['entry']['postings'][0]['amount']='99.00';body['proposal']['entry']['postings'][1]['amount']='-99.00'
        with self.assertRaisesRegex(ValueError,'filed statement'):self.save(after,body)

    def test_unit_cost_hidden_fields_and_account_metadata_survive(self):
        e=self.seed();declarations=load(self.ledger)[self.ledger/'data/accounts.json'];foreign=next(c for c in declarations['commodities'] if c!=self.currency)
        e['postings'][0].update(amount='20.00',commodity=foreign,cost=dict(amount='5.00',commodity=self.currency,per='unit'))
        e['original-amount']=dict(amount='20.00',commodity=foreign);e['references']={'reference':'original-ref','order-id':['original-order']}
        e['statement']={'end':None,'serial':None,'page':None,'source-page':2,'source-row':7}
        dump(self.ledger/'data/expenses/2026.json',[e]);self.jj('commit','-m','Fictional unit conversion baseline')
        body=self.body(e);body['proposal']['entry']['notes'].append('A receipt note');self.save(e,body)
        after=entries(load(self.ledger))[0]
        for key in ('postings','original-amount','references','statement'):self.assertEqual(after[key],e[key])
        self.assertEqual(load(self.ledger)[self.ledger/'data/accounts.json'],declarations)
        after['extensions']={'fuel':{'litres':'5.00','trip-km':'50.00'}};dump(self.ledger/'data/expenses/2026.json',[after])
        manifest=json.loads((self.ledger/'ledger.json').read_text());manifest['extensions']['fuel']['ask']=False;dump(self.ledger/'ledger.json',manifest);self.jj('commit','-m','An extension the agent is no longer asked for')
        body=self.body(after);body['proposal']['entry']['description']='Corrected history label';self.save(after,body)
        updated=entries(load(self.ledger))[0]
        self.assertEqual(updated['extensions'],after['extensions'])

    def test_edit_entry_sets_and_clears_every_extension_type(self):
        work={'name':'work','description':'Fictional client work.','fields':{
            'rate':{'type':'decimal','filled-by':'agent','description':'Hourly rate.','journal-tag':'work-rate'},
            'client':{'type':'text','filled-by':'agent','description':'The client.','journal-tag':'work-client'},
            'hours':{'type':'whole-number','filled-by':'person','description':'Hours worked.','journal-tag':'work-hours'},
            'billed-on':{'type':'date','filled-by':'person','description':'When it was billed.','journal-tag':'work-billed-on'},
            'billable':{'type':'yes-no','filled-by':'person','description':'Whether it is billed.','journal-tag':'work-billable'},
            'stage':{'type':'choice','values':['draft','sent'],'filled-by':'person','description':'Billing stage.','journal-tag':'work-stage'}}}
        e=self.seed()
        (self.ledger/'extensions').mkdir(exist_ok=True);(self.ledger/'extensions/work.json').write_text(json.dumps(work)+'\n')
        manifest=json.loads((self.ledger/'ledger.json').read_text());manifest['extensions']['work']={'ask':False};dump(self.ledger/'ledger.json',manifest)
        e['extensions']={'fuel':None,'work':None};dump(self.ledger/'data/expenses/2026.json',[e]);self.jj('commit','-m','A fictional custom extension')
        body=self.body(e);self.assertEqual(body['proposal']['entry']['extensions'],{'fuel':None,'work':None})
        values={'rate':'12.50','client':'Fictional Studio','hours':3,'billed-on':'2026-11-02','billable':True,'stage':'sent'}
        body['proposal']['entry']['extensions']['work']=values;self.save(e,body)
        after=entries(load(self.ledger))[0];self.assertEqual(after['extensions'],{'fuel':None,'work':values})
        journal=(self.ledger/'expense_ledger.ledger').read_text()
        for line in ('; work-rate: 12.50','; work-client: Fictional Studio','; work-hours: 3','; work-billed-on: 2026-11-02','; work-billable: yes','; work-stage: sent'):self.assertIn(line,journal)
        body=self.body(after);self.assertEqual(body['proposal']['entry']['extensions']['work'],values)
        all_empty=self.body(after);all_empty['proposal']['entry']['extensions']['work']=dict.fromkeys(values)
        with self.assertRaisesRegex(ValueError,'must be null'):self.save(after,all_empty)
        body['proposal']['entry']['extensions']['work']=None;self.save(after,body)
        self.assertEqual(entries(load(self.ledger))[0]['extensions'],{'fuel':None,'work':None})
        for invalid in ({'hours':'3'},{'billable':'yes'},{'stage':'paid'},{'billed-on':'2026-02-30'},{'rate':'12.5.0'}):
            with self.subTest(invalid=invalid):
                body=self.body(after);body['proposal']['entry']['extensions']['work']={**values,**invalid}
                with self.assertRaises(ValueError):self.save(after,body)

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
        rule.update(lifecycle='removed',removal={'date':'2026-10-01','entry-ids':[]})
        entry=deepcopy(e);entry.update(id='b'*16,date='2026-11-29');entry['rule']=None;docs[self.ledger/'data/expenses/2026.json'].append(entry)
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
        draft.update(state='added',completed_at=__import__('agent').now(),result=dict(outcome='ready',entry_id=e['id'],sources=e['evidence']),receipt_reading=dict(state='queued',queued_at=__import__('agent').now(),entry_id=e['id'],sources=e['evidence']))
        drafts.put(draft);worker=Worker(self.settings,drafts)
        read(worker,draft)
        return drafts,drafts.get(draft['id']),worker

    def test_agreement_adds_details_through_correction_and_reads_filed_files(self):
        e=self.seed();drafts,d,worker=self.receipt(e)
        self.assertEqual(d['receipt_reading']['state'],'done')
        after=entries(load(self.ledger))[0]
        self.assertEqual(after['postings'],e['postings']);self.assertEqual(after['references']['reference'],'original-ref');self.assertIn('Receipt reference: receipt-reference',after['notes'])
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

    def test_a_receipt_with_a_whole_amount_is_used_as_a_decimal(self):
        # The reader returns the printed amount as typed; "Use the receipt" writes it as the store's decimal.
        e=self.seed();drafts,d,worker=self.receipt(e,amount='500')
        self.assertEqual(d['receipt_reading']['state'],'question')
        result=choose(self.settings,drafts,d,'use');self.assertEqual(result['receipt_reading']['state'],'done')
        after=entries(load(self.ledger))[0]
        self.assertEqual([p['amount'] for p in after['postings']],['500.00','-500.00'])

    def test_unreadable_and_unsafe_evidence_change_nothing(self):
        e=self.seed();drafts,d,worker=self.receipt(e,read_ok=False)
        self.assertEqual(d['receipt_reading']['state'],'unreadable');self.assertEqual(records(self.ledger),[])
        for source in ['../outside.png','Bills/../outside.png','/tmp/outside.png']:
            with self.assertRaises(ValueError):safe_evidence(self.ledger,source)
        (self.ledger/'Bills/link.png').symlink_to(self.ledger/e['evidence'][0])
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
            d.update(state='added',completed_at=__import__('agent').now(),result=dict(outcome='ready',entry_id=e['id']),receipt_reading=dict(state='reading',queued_at=__import__('agent').now(),entry_id=e['id'],sources=e['evidence']))
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
            d.update(state='added',completed_at=__import__('agent').now(),result=dict(outcome='ready',entry_id=e['id']),receipt_reading=dict(state='queued',queued_at=__import__('agent').now(),entry_id=e['id'],sources=e['evidence']))
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

