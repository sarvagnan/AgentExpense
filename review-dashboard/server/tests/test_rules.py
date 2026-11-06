"""Real checked recurring writes in disposable ledgers; all agents are offline fakes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
from unittest.mock import patch

from persona_guard import persona_sensitive
from support import Fixture, ready
from store import dump, entries, load, read_json
from audit import append, event, records
from drafts import Drafts
from ledger_lock import ledger_lock
from rules_service import from_entry, match, operate, queue_usual, snapshot
from transactions import Transaction, clean


def payment_rule(day='2026-10-15', amount='100.00', life='active'):
    return dict(id='f000000000000001',date=day,status='confirmed',description='Willow club',lifecycle=life,
                schedule=dict(every='month',**{'from':day}),origin=dict(kind='owner'),
                postings=[dict(account='Expenses:Food',amount=amount,commodity='INR'),dict(account='Assets:Cash',amount='-'+amount,commodity='INR')])


class RuleTests(Fixture):
    def seed(self, rule=None, actual=None):
        if rule is not False: dump(self.ledger/'data/forecast.json',[rule or payment_rule()])
        if actual is not None: dump(self.ledger/'data/expenses/2026.json',actual)
        self.jj('commit','-m','Fictional rules baseline')

    def op(self, action, **kw):
        return operate(self.settings,secrets.token_hex(16),dict(action=action,**kw))

    def add_direct(self, text='Willow club', **body):
        drafts=Drafts(self.ledger);draft=drafts.create(text)
        day=body.pop('date','2026-11-15')
        queue_usual(self.settings,drafts,draft,dict(rule_id='f000000000000001',date=day,**body))
        with ledger_lock(self.ledger) as fd:
            result=Transaction(self.settings,drafts,draft).apply(draft['proposal'],fd)
        drafts.completed(draft,result)
        return drafts,draft,result

    def test_usual_verified_atomic_advance_audit_retry(self):
        self.seed();before=self.jj('log','-r','@-','--no-graph','-T','commit_id')
        drafts,draft,result=self.add_direct(date='2026-11-10')
        self.assertNotEqual(result['commit_id'],before);self.assertTrue(clean(self.ledger))
        documents=load(self.ledger);entry=entries(documents)[0];rule=documents[self.ledger/'data/forecast.json'][0]
        self.assertEqual(entry['rule'],dict(id=rule['id'],occurrence='2026-11-15'));self.assertEqual(rule['date'],'2026-12-15')
        self.assertEqual(entry['date'],'2026-11-10')
        line=records(self.ledger)[-1];self.assertEqual(len(line['rule_changes']),1);self.assertTrue(line['usual_payment'])
        self.assertEqual(line['entry_id'],entry['id']);self.assertIn('Added-by: user (usual payment, rule '+rule['id']+')',self.jj('log','-r','@-','--no-graph','-T','description'))
        queued_at=draft['queued_at'];queue_usual(self.settings,drafts,draft,dict(rule_id=rule['id'],date='2026-11-10'));self.assertEqual(draft['queued_at'],queued_at)
        with self.assertRaisesRegex(ValueError,'different usual'):queue_usual(self.settings,drafts,draft,dict(rule_id=rule['id'],date='2026-11-16'))

    def test_amount_this_payment_or_update_rule_and_duplicate_override(self):
        for update in (False,True):
            with self.subTest(update=update):
                self.seed()
                _,_,result=self.add_direct(amount='125',update_rule=update)
                self.assertEqual(entries(load(self.ledger))[-1]['postings'][0]['amount'],'125.00')
                rule=read_json(self.ledger/'data/forecast.json')[0]
                self.assertEqual(rule['postings'][0]['amount'],'125.00' if update else '100.00')
                drafts=Drafts(self.ledger);draft=drafts.create('Willow another')
                with self.assertRaisesRegex(ValueError,'already has'):queue_usual(self.settings,drafts,draft,dict(rule_id=rule['id'],date='2026-11-20'))
                queue_usual(self.settings,drafts,draft,dict(rule_id=rule['id'],date='2026-11-20',another_anyway=True))
                with ledger_lock(self.ledger) as fd:Transaction(self.settings,drafts,draft).apply(draft['proposal'],fd)
                self.assertTrue(records(self.ledger)[-1]['usual_payment']['another_anyway'])
                # Reset only this disposable fixture between the two variants.
                dump(self.ledger/'data/expenses/2026.json',[])
                self.jj('commit','-m','Reset disposable payment examples')

    def test_foreign_planned_cost_note_and_missing_payer_remember(self):
        r=payment_rule();r['postings'][0].update(amount='10.00',commodity='USD',cost=dict(amount='100.00',commodity='INR',per='total'))
        self.seed(r);_,_,result=self.add_direct()
        e=entries(load(self.ledger))[0];self.assertEqual(e['postings'][0]['cost'],r['postings'][0]['cost'])
        self.assertIn('statement reconciliation will correct',e['notes'][-1]);self.assertTrue(records(self.ledger)[-1]['usual_payment']['estimated'])
        self.assertTrue(result['usual_payment']['estimated'])
        declarations=read_json(self.ledger/'data/accounts.json');declarations['accounts']['Equity:Unassigned']={};dump(self.ledger/'data/accounts.json',declarations)
        r=payment_rule(day='2026-12-15');r['postings'][-1]['account']='Equity:Unassigned';dump(self.ledger/'data/forecast.json',[r]);self.jj('commit','-m','Unassigned payment fixture')
        drafts=Drafts(self.ledger);draft=drafts.create('Willow')
        with self.assertRaisesRegex(ValueError,'paying account'):queue_usual(self.settings,drafts,draft,dict(rule_id=r['id'],date='2026-12-15'))
        queue_usual(self.settings,drafts,draft,dict(rule_id=r['id'],date='2026-12-15',account='Assets:Cash',amount='120',update_rule=True,remember_account=False))
        with ledger_lock(self.ledger) as fd:Transaction(self.settings,drafts,draft).apply(draft['proposal'],fd)
        self.assertEqual(read_json(self.ledger/'data/forecast.json')[0]['postings'][-1]['account'],'Equity:Unassigned')
        draft=drafts.create('Willow next')
        queue_usual(self.settings,drafts,draft,dict(rule_id=r['id'],date='2027-01-15',account='Assets:Cash',remember_account=True))
        with ledger_lock(self.ledger) as fd:Transaction(self.settings,drafts,draft).apply(draft['proposal'],fd)
        self.assertEqual(read_json(self.ledger/'data/forecast.json')[0]['postings'][-1]['account'],'Assets:Cash')

    def test_automatic_words_hand_add_and_dont_make_rule_preserves_entry(self):
        self.seed(False)
        draft=Drafts(self.ledger).create('Willow annual membership');drafts=Drafts(self.ledger);drafts.save_time(draft)
        proposal=ready(new=False);proposal['attachment_names']=[];proposal['entry']['description']='Willow club'
        draft.update(proposal=proposal,finished_by=dict(actor='user',reason='agent unavailable'));drafts.put(draft)
        with ledger_lock(self.ledger) as fd:result=Transaction(self.settings,drafts,draft).apply(proposal,fd)
        rule=read_json(self.ledger/'data/forecast.json')[0]
        self.assertEqual(rule['schedule']['every'],'year');self.assertEqual(rule['origin']['kind'],'subscription-words')
        self.assertEqual(len(result['made_rules']),1);entry=deepcopy(entries(load(self.ledger))[0])
        self.op('removed',rule_id=rule['id'])
        self.assertEqual(entries(load(self.ledger))[0],entry);removed=read_json(self.ledger/'data/forecast.json')[0]
        self.assertEqual(removed['removal']['evidence'],[entry['id']]);self.assertEqual(removed['lifecycle'],'removed')
        self.assertEqual(self.op('backfill')['changed'],0)

    def test_backdated_addition_then_delete_does_not_block_later_saves(self):
        self.seed(False)
        drafts=Drafts(self.ledger)
        def add(day, text):
            draft=drafts.create(text);drafts.save_time(draft)
            proposal=ready(new=False);proposal['attachment_names']=[]
            proposal['entry'].update(date=day,description='Willow club')
            draft.update(proposal=proposal,finished_by=dict(actor='user',reason='agent unavailable'))
            drafts.put(draft)
            with ledger_lock(self.ledger) as fd:
                result=Transaction(self.settings,drafts,draft).apply(proposal,fd)
            drafts.completed(draft,result)
            return records(self.ledger)[-1]
        add('2026-09-15','Willow club monthly membership')
        latest=records(self.ledger)[-1]
        self.assertTrue(latest['rule_changes'])
        from retention_service import Retention
        from replacements import entry_hash
        service=Retention(self.settings,drafts)
        entry=entries(load(self.ledger))[0]
        service.operate(secrets.token_hex(16),dict(action='delete',entry_id=entry['id'],version=entry_hash(entry)))
        add('2026-10-15','Willow club later payment')
        self.op('kept',rule_id=read_json(self.ledger/'data/forecast.json')[0]['id'])
        self.assertTrue(clean(self.ledger))
        self.assertEqual(len(records(self.ledger)),4)

    def test_agent_and_hand_additions_link_and_advance_and_paused_offer(self):
        r=payment_rule(day='2026-10-01');r['description']='Cafe';self.seed(r)
        with self.client() as client:
            proposal=ready(new=False);proposal['attachment_names']=[];self.set_answers(proposal)
            identity=self.create(client,upload=False);self.save(client,identity);result=self.wait(client,identity,'added')['result']
            self.assertEqual(result['rule']['id'],r['id']);self.assertEqual(result['next_due'],'2026-11-01')
            self.assertEqual(len(self.calls()),1)
        self.op('paused',rule_id=r['id'])
        drafts=Drafts(self.ledger);draft=drafts.create('Cafe');drafts.save_time(draft)
        proposal=ready(new=False);proposal['attachment_names']=[];proposal['entry']['date']='2026-11-05';draft.update(proposal=proposal,finished_by=dict(actor='user',reason='paused agent'))
        with ledger_lock(self.ledger) as fd:result=Transaction(self.settings,drafts,draft).apply(proposal,fd)
        self.assertEqual(result['resume_rules'][0]['id'],r['id']);self.assertEqual(read_json(self.ledger/'data/forecast.json')[0]['lifecycle'],'paused')
        line=records(self.ledger)[-1]
        self.assertEqual(line['rule_changes'],[])
        self.assertEqual(read_json(self.ledger/'data/forecast.json')[0]['lifecycle'],'paused')

    def test_each_operation_idempotency_changed_payload_stale_and_review_answer(self):
        self.seed();identity='f000000000000001'
        for action in ('paused','resumed','ended','restored','removed','restored','edited','kept'):
            body=dict(action=action,rule_id=identity)
            if action=='edited':body['rule']={'description':'Willow club revised'}
            op_id=secrets.token_hex(16);result=operate(self.settings,op_id,body)
            self.assertTrue(operate(self.settings,op_id,body)['replayed']);self.assertEqual(records(self.ledger)[-1]['rule_action'],action)
            with self.assertRaisesRegex(ValueError,'different payload'):operate(self.settings,op_id,{**body,'entry_id':'1'*16})
        with self.assertRaisesRegex(ValueError,'changed'):self.op('paused',rule_id=identity,version='old')
        with ledger_lock(self.ledger):append(self.ledger,event('rule-review',rule_id=identity,rule_verdict='propose-removal',reason='No longer needed',reviewer='Offline reviewer'))
        self.jj('commit','-m','Fictional review proposal')
        self.assertIsNotNone(snapshot(self.settings)['rules'][0]['review']);self.op('kept',rule_id=identity)
        self.assertIsNone(snapshot(self.settings)['rules'][0]['review'])
        copy=payment_rule();copy['id']='f000000000000002';self.op('made',rule=copy)
        self.assertEqual(len(read_json(self.ledger/'data/forecast.json')),2)

    def test_backfill_catch_up_noop(self):
        a=payment_rule();a.pop('schedule');a.pop('origin');a.pop('lifecycle');a['id']='0000000000000001';a['date']='2026-09-15'
        b={**deepcopy(a),'id':'0000000000000002','date':'2026-10-15'};self.seed(False,[a,b])
        clock=lambda:datetime(2026,11,1,tzinfo=timezone.utc)
        made=operate(self.settings,secrets.token_hex(16),{'action':'backfill'},clock=clock)
        self.assertEqual(made['changed'],1);r=read_json(self.ledger/'data/forecast.json')[0];self.assertEqual(r['origin']['kind'],'history-pass')
        before=self.bytes();self.assertEqual(operate(self.settings,secrets.token_hex(16),{'action':'backfill'},clock=clock)['changed'],0);self.assertEqual(self.bytes(),before)
        no_op=secrets.token_hex(16)
        self.assertEqual(operate(self.settings,no_op,{'action':'backfill'},clock=clock)['changed'],0)
        self.assertTrue(operate(self.settings,no_op,{'action':'backfill'},clock=clock)['replayed'])
        with self.assertRaisesRegex(ValueError,'different payload'):operate(self.settings,no_op,{'action':'catch-up'},clock=clock)
        r['date']=r['schedule']['from']='2026-09-15';dump(self.ledger/'data/forecast.json',[r]);self.jj('commit','-m','Fictional stale rule')
        self.assertEqual(snapshot(self.settings,clock)['rules'][0]['rule_status'],'stale');caught=self.op('catch-up')
        self.assertEqual(caught['changed'],1);self.assertEqual(read_json(self.ledger/'data/forecast.json')[0]['date'],'2026-11-15')
        self.assertEqual(self.op('catch-up')['changed'],0)

    def test_atomic_rollback_rule_edits_and_stale_queued_rule(self):
        self.seed();before=self.bytes()
        with patch.object(Transaction,'verified_commit',side_effect=RuntimeError('injected verification failure')):
            with self.assertRaisesRegex(RuntimeError,'injected'):self.op('paused',rule_id='f000000000000001')
        self.assertEqual(self.bytes(),before);self.assertTrue(clean(self.ledger))
        drafts=Drafts(self.ledger);draft=drafts.create('Willow');queue_usual(self.settings,drafts,draft,dict(rule_id='f000000000000001',date='2026-11-15'))
        self.op('edited',rule_id='f000000000000001',rule=dict(description='Willow revised'))
        before=self.bytes()
        with ledger_lock(self.ledger) as fd:
            with self.assertRaisesRegex(ValueError,'rule changed'):Transaction(self.settings,drafts,draft).apply(draft['proposal'],fd)
        self.assertEqual(self.bytes(),before);self.assertTrue(clean(self.ledger))

    def test_api_reads_usual_fifo_place_and_access(self):
        self.seed()
        with self.client(worker=False) as client:
            self.assertEqual(len(client.get('/api/rules').json()['rules']),1)
            self.assertEqual(client.get('/api/rules/match',params={'text':'Willow 100 INR'}).json()['rule_id'],'f000000000000001')
            draft_id=self.create(client,upload=False);drafts=client.app.state.drafts;draft=drafts.get(draft_id)
            draft.update(state='queued',queued_at='2026-01-01T00:00:00+00:00');drafts.put(draft)
            response=client.post(f'/api/drafts/{draft_id}/usual',json=dict(rule_id='f000000000000001',date='2026-11-15'))
            self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['queued_at'],'2026-01-01T00:00:00+00:00')
            client.app.state.worker.process(drafts.get(draft_id));added=client.get('/api/drafts/'+draft_id).json()
            self.assertEqual(added['state'],'added');self.assertEqual(self.calls(),[])
            self.assertNotIn('can_undo',added['result'])
            replay=client.post(f'/api/drafts/{draft_id}/usual',json=dict(rule_id='f000000000000001',date='2026-11-15'));self.assertEqual(replay.status_code,200)
            entry_id=added['result']['entry_id'];self.assertEqual(client.get('/api/rules/from-entry/'+entry_id).status_code,200)
        with self.client(worker=False,dev=False) as client:
            response=client.post('/api/rules/operations/'+'1'*32,json=dict(action='paused',rule_id='f000000000000001'));self.assertEqual(response.status_code,403)

    def test_cli_shared_operations_match_backfill_and_catch_up(self):
        self.seed()
        server=self.application/'review-dashboard/server'
        shutil.copytree(Path(__file__).resolve().parents[1],server,ignore=shutil.ignore_patterns('.venv','tests','__pycache__'))
        script=self.application/'scripts/ledger/rules.py'
        def cli(*args):
            run=subprocess.run([__import__('sys').executable,str(script),'--root',str(self.ledger),*args],env={**os.environ,'LEDGER_DIR':str(self.ledger)},capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr);return json.loads(run.stdout)
        self.assertEqual(len(cli('list')['rules']),1);self.assertEqual(cli('match','Willow 100 INR')['rule_id'],'f000000000000001')
        # One shared word alone is only a suggestion, never a proposal (grade B-2 N2).
        weak=cli('match','Willow');self.assertEqual((weak['rule_id'],weak['matches']),(None,[]));self.assertEqual([m['id'] for m in weak['suggestions']],['f000000000000001'])
        payload=self.base/'operation.json';payload.write_text(json.dumps(dict(action='paused',rule_id='f000000000000001')))
        self.assertEqual(cli('operations',str(payload))['action'],'paused');self.assertEqual(cli('backfill')['changed'],0);self.assertEqual(cli('catch-up')['changed'],0)

    def test_receipt_read_waits_for_a_brief_competing_reader(self):
        self.seed();_,draft,_=self.add_direct()
        with self.client(worker=False) as client:
            held=threading.Event();release=threading.Event()
            def competing_read():
                with ledger_lock(self.ledger):
                    held.set();release.wait(2)
            thread=threading.Thread(target=competing_read);thread.start()
            self.assertTrue(held.wait(2));timer=threading.Timer(.2,release.set);timer.start()
            try:
                response=client.get('/api/drafts/'+draft['id'])
            finally:
                release.set();thread.join();timer.cancel()
            self.assertEqual(response.status_code,200,response.text)
            self.assertNotIn('can_undo',response.json()['result'])

    def test_usual_evidence_retry_preserves_fifo(self):
        self.seed()
        with self.client(worker=False) as client:
            identity=self.create(client);drafts=client.app.state.drafts
            response=client.post('/api/drafts/'+identity+'/usual',json=dict(rule_id='f000000000000001',date='2026-11-15'))
            self.assertEqual(response.status_code,200,response.text);position=response.json()['queued_at']
            draft=drafts.get(identity)
            with patch.object(Transaction,'verified_commit',side_effect=RuntimeError('injected commit failure')):
                with ledger_lock(self.ledger) as fd:
                    with self.assertRaisesRegex(RuntimeError,'injected'):Transaction(self.settings,drafts,draft).apply(draft['proposal'],fd)
            self.assertEqual(entries(load(self.ledger)),[]);self.assertEqual(read_json(self.ledger/'data/forecast.json')[0]['date'],'2026-10-15')
            draft.update(state='failed',error='injected');drafts.put(draft)
            from outcomes import draft_event
            self.assertEqual(draft_event(draft,'failed')['usual_payment'],draft['usual_payment'])
            retry=client.post('/api/drafts/'+identity+'/retry');self.assertEqual(retry.status_code,200);self.assertEqual(retry.json()['queued_at'],position)
            self.assertEqual(retry.json()['proposal']['status'],'ready')
            client.app.state.worker.process(drafts.get(identity));added=client.get('/api/drafts/'+identity).json()
            self.assertEqual(added['state'],'added');self.assertEqual(self.calls(),[])
            source=self.ledger/added['result']['sources'][0];self.assertTrue(source.is_file())

    def test_review_merge_preserves_rule_link_and_retargets_suppression_evidence(self):
        from review import apply as review_apply, snapshot as review_snapshot, link_pair
        a=payment_rule();a.pop('schedule');a.pop('origin');a.pop('lifecycle');a.update(id='0000000000000001',date='2026-10-15')
        b={**deepcopy(a),'id':'0000000000000002'}
        r=payment_rule(day='2026-11-15');r.update(lifecycle='removed',origin=dict(kind='monthly-history',evidence=[b['id']]),removal=dict(date='2026-10-20',evidence=[b['id']]))
        b['rule']=dict(id=r['id'],occurrence=b['date']);link_pair({a['id']:a,b['id']:b},a['id'],b['id'],'several-candidates')
        self.seed(r,[a,b]);state=review_snapshot(self.ledger)
        result=review_apply(self.settings,dict(request_id=secrets.token_hex(16),action='merge',entry_id=a['id'],other_id=b['id'],versions={i:state['entries'][i]['version'] for i in (a['id'],b['id'])}))
        actual=entries(load(self.ledger));self.assertEqual(len(actual),1);self.assertEqual(actual[0]['rule']['id'],r['id'])
        saved=read_json(self.ledger/'data/forecast.json')[0]
        self.assertEqual(saved['origin']['evidence'],[actual[0]['id']]);self.assertEqual(saved['removal']['evidence'],[actual[0]['id']]);self.assertTrue(records(self.ledger)[-1]['rule_changes'])

    @persona_sensitive
    def test_persona_backfill_and_catch_up_use_real_checked_services(self):
        from settings import APPLICATION
        for persona,count in [('asha',1),('empty',0),('jordan',1),('sam',1)]:
            with self.subTest(persona=persona):
                source=APPLICATION/'fixtures/ledgers'/persona
                shutil.rmtree(self.ledger/'data');shutil.copytree(source/'data',self.ledger/'data')
                shutil.copyfile(source/'ledger.json',self.ledger/'ledger.json');shutil.copyfile(source/'AGENTS.md',self.ledger/'AGENTS.md')
                if (source/'Bills').is_dir():shutil.copytree(source/'Bills',self.ledger/'Bills',dirs_exist_ok=True)
                self.jj('commit','-m','Fictional '+persona+' history baseline')
                clock=lambda:datetime(2026,11,2,tzinfo=timezone.utc)
                result=operate(self.settings,secrets.token_hex(16),dict(action='backfill'),clock=clock)
                self.assertEqual(result['changed'],count)
                if count:
                    created=[c['after'] for c in result['changes']]
                    self.assertEqual([r['description'] for r in created],['Cedar Studio membership'])
                    self.assertEqual(created[0]['date'],'2026-11-15')
                    self.assertTrue(clean(self.ledger));self.assertEqual(records(self.ledger)[-1]['rule_action'],'backfill')
                caught=operate(self.settings,secrets.token_hex(16),dict(action='catch-up'),clock=clock)
                self.assertEqual(caught['changed'],count)
                self.assertEqual(operate(self.settings,secrets.token_hex(16),dict(action='catch-up'),clock=clock)['changed'],0)

    def test_suggestion_dismissal_recomputes_postings_retains_suppression_and_replays(self):
        from copy import deepcopy
        rule=payment_rule()
        rows=[]
        for i,day in enumerate(('2026-09-03','2026-10-22'),1):
            entry=deepcopy(rule);entry.pop('schedule');entry.pop('origin');entry.pop('lifecycle');entry.update(id=f'{i:016x}',date=day);rows.append(entry)
        self.seed(False,rows)
        clock=lambda:datetime(2026,11,2,tzinfo=timezone.utc)
        suggestion=snapshot(self.settings,clock)['suggestions'][0]['rule']
        tampered=deepcopy(suggestion);tampered['description']='Caller cannot change this';tampered['postings'][0]['amount']='9999'
        operation=secrets.token_hex(16);body=dict(action='removed',rule=tampered)
        result=operate(self.settings,operation,body,clock=clock)
        self.assertTrue(operate(self.settings,operation,body,clock=clock)['replayed'])
        stored=read_json(self.ledger/'data/forecast.json')[0]
        self.assertEqual(stored['description'],rule['description']);self.assertEqual(stored['postings'],rule['postings']);self.assertEqual(stored['lifecycle'],'removed');self.assertEqual(stored['removal']['evidence'],[e['id'] for e in rows])
        self.assertEqual(snapshot(self.settings,clock)['suggestions'],[])
        line=records(self.ledger)[-1];self.assertEqual(line['rule_action'],'removed');self.assertIsNone(line['rule_changes'][0]['before']);self.assertTrue(clean(self.ledger))
        with self.assertRaisesRegex(ValueError,'different payload'):operate(self.settings,operation,dict(action='removed',rule=suggestion),clock=clock)
        with self.assertRaisesRegex(ValueError,'suggestion changed'):operate(self.settings,secrets.token_hex(16),dict(action='removed',rule=suggestion),clock=clock)
        for invalid in ([1],{'origin':[]},{'origin':{'evidence':'invalid'}}):
            with self.assertRaisesRegex(ValueError,'suggestion'):operate(self.settings,secrets.token_hex(16),dict(action='removed',rule=invalid),clock=clock)

    def test_declined_guess_is_only_an_editing_draft_choice(self):
        self.seed()
        with self.client() as client:
            draft=client.post('/api/drafts',json=dict(text='Willow club',declined_rules=['f000000000000001'])).json()
            self.assertEqual(draft['declined_rules'],['f000000000000001']);self.assertEqual(draft['state'],'editing')
            response=client.patch('/api/drafts/'+draft['id'],json=dict(declined_rules=[]))
            self.assertEqual(response.status_code,200);self.assertEqual(response.json()['declined_rules'],['f000000000000001'])
            for values in (['bad'],['f000000000000001']*2):
                self.assertEqual(client.patch('/api/drafts/'+draft['id'],json=dict(declined_rules=values)).status_code,422)
            self.assertEqual(client.get('/api/drafts/'+draft['id']).json()['state'],'editing')
        self.assertEqual(entries(load(self.ledger)),[]);self.assertEqual(records(self.ledger),[]);self.assertTrue(clean(self.ledger));self.assertEqual(self.calls(),[])

    def test_new_owner_rule_allocates_an_id_and_rejects_only_real_collisions(self):
        self.seed(False,[])
        proposed=payment_rule();proposed.pop('id')
        operation=secrets.token_hex(16);body=dict(action='made',rule=proposed)
        result=operate(self.settings,operation,body)
        saved=read_json(self.ledger/'data/forecast.json')[0]
        self.assertRegex(saved['id'],r'^[0-9a-f]{16}$');self.assertEqual(saved['description'],proposed['description']);self.assertEqual(len(result['changes']),1)
        self.assertTrue(operate(self.settings,operation,body)['replayed']);self.assertEqual(read_json(self.ledger/'data/forecast.json'),[saved]);self.assertTrue(clean(self.ledger))
        with self.assertRaisesRegex(ValueError,'already exists'):operate(self.settings,secrets.token_hex(16),dict(action='made',rule=saved))
        self.assertEqual(len(records(self.ledger)),1)
