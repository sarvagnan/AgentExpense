"""Retention uses disposable repositories, real checks, and offline doubles only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import shutil
import subprocess
from unittest.mock import patch

from support import Fixture, ready, entry as store_entry, posting
from audit import records
from drafts import Drafts
from retention_service import Retention, AdditionTimes
from replacements import entry_hash
from store import load, dump, entries
from transactions import Transaction, clean
from rules_service import queue_usual


class RetentionTests(Fixture):
    def setUp(self):
        super().setUp()
        self.now=datetime.now(timezone.utc)
        self.drafts=Drafts(self.ledger,clock=lambda:self.now)
        self.service=Retention(self.settings,self.drafts,lambda:self.now)

    def add(self, description='Willow lunch', source=None):
        d=self.drafts.create(description);d.update(saved_at=self.now.isoformat(),state='working')
        a=ready(new=False);a['attachment-names']=[];a['entry']['description']=description
        from ledger_lock import ledger_lock
        with ledger_lock(self.ledger) as fd: result=Transaction(self.settings,self.drafts,d).apply(a,fd)
        self.drafts.completed(d,result)
        entry=next(e for e in entries(load(self.ledger)) if e['id']==result['entry_id'])
        if source:
            entry['evidence']=[source];(self.ledger/source).parent.mkdir(parents=True,exist_ok=True);(self.ledger/source).write_bytes(b'Fictional receipt\n')
            docs=load(self.ledger);docs[self.ledger/f"data/expenses/{entry['date'][:4]}.json"]=[entry];dump(self.ledger/f"data/expenses/{entry['date'][:4]}.json",[entry]);self.jj('commit','-m','File fictional evidence')
        return entry

    def op(self, action, **body):
        return self.service.operate(secrets.token_hex(16),dict(action=action,**body))

    def delete(self, entry):
        return self.op('delete',entry_id=entry['id'],version=entry_hash(entry))

    def test_delete_restore_exact_files_idempotent_and_commit_format(self):
        e=self.add(source='Bills/2026-10-29/Willow.txt');before=deepcopy(e);file=(self.ledger/e['evidence'][0]).read_bytes();expense_path=self.ledger/f"data/expenses/{e['date'][:4]}.json";expense_bytes=expense_path.read_bytes()
        op=secrets.token_hex(16);body=dict(action='delete',entry_id=e['id'],version=entry_hash(e),reason='Duplicate thought\nkept trace')
        out=self.service.operate(op,body);self.assertEqual(entries(load(self.ledger)),[]);self.assertFalse((self.ledger/e['evidence'][0]).exists());self.assertTrue(self.service.operate(op,body)['replayed'])
        self.assertEqual(self.service.listing()['items'][0]['id'],out['item_id'])
        result=self.op('restore',item_id=out['item_id']);self.assertEqual(entries(load(self.ledger)),[before]);self.assertEqual((self.ledger/e['evidence'][0]).read_bytes(),file);self.assertEqual(expense_path.read_bytes(),expense_bytes)
        self.assertIn('Files restored: '+e['evidence'][0],self.jj('log','-r','@-','--no-graph','-T','description'));self.assertTrue(clean(self.ledger));self.assertEqual([r['retention_action'] for r in records(self.ledger) if r['kind']=='retention'],['deleted','restored'])

    def test_delete_coverage_window_and_unknown(self):
        e=self.add();self.assertTrue(self.service.eligibility(e['id'])['offered'])
        self.now+=timedelta(days=8);self.assertIn('window',self.service.eligibility(e['id'])['reason']);self.now-=timedelta(days=8)
        docs=load(self.ledger);docs[self.ledger/'data/accounts.json']['accounts'][e['postings'][1]['account']]['kind']='bank';dump(self.ledger/'data/accounts.json',docs[self.ledger/'data/accounts.json']);rows=docs[self.ledger/'data/expenses/2026.json'];assertion=store_entry('e'*16,'2026-10-31','Confirmed monthly balance',[posting(e['postings'][1]['account'],'0.00',e['postings'][1]['commodity'],**{'assert':dict(amount=e['postings'][1]['amount'],commodity=e['postings'][1]['commodity'])})]);rows.append(assertion);dump(self.ledger/'data/expenses/2026.json',rows);self.jj('commit','-m','Confirm fictional bank balance')
        self.assertIn('confirmed',self.service.eligibility(e['id'])['reason'])
        with self.assertRaisesRegex(ValueError,'confirmed'):self.delete(e)

    def test_history_first_commit_unknown_later_commit_fallback_and_audit(self):
        e=self.add();root=self.base/'history-ledger';shutil.copytree(self.ledger,root,ignore=shutil.ignore_patterns('.jj','.git','drafts','additions'))
        def jj(*args): return subprocess.check_output(['jj','--config','user.name="Offline Fixture"','--config','user.email="fixture@example.invalid"','-R',str(root),*args],text=True,stderr=subprocess.PIPE).strip()
        subprocess.check_call(['jj','git','init','--colocate',str(root)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        jj('commit','-m','Fictional setup containing imported entry');history=AdditionTimes(root);self.assertIsNone(history.get(e['id'],[]))
        later=deepcopy(e);later['id']='d'*16;audited=deepcopy(e);audited['id']='c'*16
        dump(root/'data/expenses/2026.json',[e,later,audited]);jj('commit','-m','Later entries with and without audit');history.refresh();self.assertIsNotNone(history.get(later['id'],[]))
        op_before=jj('--ignore-working-copy','op','log','--limit','1','--no-graph','-T','id')
        with patch('retention_service.subprocess',create=True):
            for _ in range(5): self.assertIsNotNone(history.get(later['id'],[]))
        self.assertEqual(jj('--ignore-working-copy','op','log','--limit','1','--no-graph','-T','id'),op_before)
        self.assertIsNone(history.get(e['id'],[dict(kind='draft',outcome='added',entry_id=e['id'],time=self.now.isoformat())]))
        stamp=(self.now-timedelta(days=2)).isoformat()
        self.assertEqual(history.get(audited['id'],[dict(kind='draft',outcome='added',entry_id=audited['id'],time=stamp)]),stamp)

    def test_queue_discard_get_race_restore_saved_and_expiry(self):
        with self.client(worker=False) as client:
            identity=self.create(client,upload=False)
            response=client.delete('/api/drafts/'+identity);self.assertEqual(response.status_code,204,response.text)
            # An already-scheduled result read gets a tombstone, never a 404.
            late=client.get('/api/drafts/'+identity);self.assertEqual(late.status_code,200);self.assertTrue(late.json()['trashed_at'])
            self.assertEqual(client.get('/api/drafts').json(),[])
            self.assertEqual(client.patch('/api/drafts/'+identity,json=dict(text='late autosave')).status_code,404)
            restored=client.post('/api/drafts/'+identity+'/restore');self.assertEqual(restored.status_code,200,restored.text);self.assertEqual(restored.json()['state'],'editing')
        d=self.drafts.get(identity);d['updated_at']=(self.now-timedelta(days=2)).isoformat();self.drafts.put(d);self.service.sweep(force=True)
        self.assertTrue(self.service.listing()['items']);self.now+=timedelta(days=8);self.service.sweep(force=True);self.assertEqual(self.service.listing()['items'],[])
        self.assertTrue(clean(self.ledger))

    def test_http_delete_retry_and_queued_discard_never_runs_agent(self):
        e=self.add();identity=secrets.token_hex(16)
        body=dict(action='delete',entry_id=e['id'],version=entry_hash(e))
        with self.client(worker=False) as client:
            path='/api/retention/operations/'+identity
            first=client.post(path,json=body);self.assertEqual(first.status_code,200,first.text)
            retry=client.post(path,json=body);self.assertEqual(retry.status_code,200,retry.text);self.assertTrue(retry.json()['replayed'])
            queued=self.create(client,upload=False);self.save(client,queued)
            worker=client.app.state.worker
            with patch.object(worker.pause,'blocked',return_value=True),patch('agent.run',side_effect=AssertionError('Discard ran the agent')):
                worker.start()
                try:
                    response=client.delete('/api/drafts/'+queued);self.assertEqual(response.status_code,204,response.text)
                    self.assertTrue(client.get('/api/drafts/'+queued).json()['trashed_at'])
                finally:worker.close()

    def test_shared_file_survives_two_deletions_and_one_permanent_removal(self):
        e=self.add(source='Bills/2026-10-29/shared.txt');other=deepcopy(e);other['id']='c'*16
        dump(self.ledger/'data/expenses/2026.json',[e,other]);self.jj('commit','-m','Share fictional evidence');self.service.history.refresh()
        first=self.delete(e);second=self.delete(other)
        self.assertTrue((self.ledger/e['evidence'][0]).is_file())
        self.op('remove',items=[first['item_id']]);self.assertTrue((self.ledger/e['evidence'][0]).is_file())
        self.op('restore',item_id=second['item_id']);self.assertEqual(entries(load(self.ledger)),[other]);self.assertTrue(clean(self.ledger))

    def test_settings_preview_immediate_effects_and_permanent_removal(self):
        e=self.add();out=self.delete(e);self.now+=timedelta(days=2)
        values=dict(delete_days=14,trash_days=1,saved_hours=12);preview=self.service.preview(values);self.assertEqual(preview['remove_now'][0]['id'],out['item_id'])
        self.op('settings',limits=values);self.assertEqual(self.service.listing()['items'],[]);self.assertEqual(read_manifest(self.ledger)['retention'],values);self.assertTrue(clean(self.ledger))

    def test_sweep_thirty_second_clock_and_saved_then_trash_deadlines(self):
        d=self.drafts.create('Willow saved words')
        self.now+=timedelta(hours=24)
        with patch('retention_service.time.monotonic',return_value=100), patch.object(self.service.history,'refresh',side_effect=AssertionError('Expiry must not traverse addition history')):
            self.service.sweep()
        self.assertTrue(self.drafts.get(d['id'],include_discarded=True)['trashed_at'])
        self.now+=timedelta(days=7)
        with patch('retention_service.time.monotonic',return_value=129.999):
            self.service.sweep()
        self.assertEqual(len(self.service.listing()['items']),1)
        with patch('retention_service.time.monotonic',return_value=130):
            self.service.sweep()
        self.assertEqual(self.service.listing()['items'],[])
        self.assertTrue(clean(self.ledger))

    def test_eligibility_refreshes_history_before_using_its_cached_times(self):
        entry=self.add(); outside=deepcopy(entry);outside['id']='b'*16
        docs=load(self.ledger);path=self.ledger/f"data/expenses/{entry['date'][:4]}.json"
        dump(path,[*docs[path],outside]);self.jj('commit','-m','Outside addition for eligibility cache')
        self.assertIsNone(self.service.history.get(outside['id'],[]))
        self.assertIsNotNone(self.service.eligibility(outside['id'])['added_at'])

    def test_a_ledger_jj_cannot_read_defers_the_sweep_in_one_line(self):
        # The worker's sweep against a folder that is not a jj repository (as in an
        # agent-check fixture): one warning line naming the command, no traceback.
        from dataclasses import replace
        from worker import Worker
        plain=self.base/'not-a-repository';plain.mkdir()
        worker=Worker(self.settings,self.drafts)
        worker.retention=Retention(replace(self.settings,ledger=plain),self.drafts,lambda:self.now)
        with self.assertLogs(level='WARNING') as log: worker.sweep_retention()
        self.assertEqual(len(log.records),1);self.assertIsNone(log.records[0].exc_info)
        message=log.records[0].getMessage()
        self.assertTrue(message.startswith(f'Retention sweep deferred: jj -R {plain} diff --summary exited 1: '),message)
        self.assertIn('no jj repo',message)
        # Other failures are bugs and keep their traceback.
        worker.retention=type('Broken',(),{'sweep':lambda self:1/0})()
        with self.assertLogs(level='ERROR') as log: worker.sweep_retention()
        self.assertIsNotNone(log.records[0].exc_info)

    def test_saved_statement_restore_uses_shared_preview_and_expiry_limits(self):
        import io
        from pypdf import PdfWriter
        from statements import Statements
        sessions=Statements(self.settings);self.service.statements=sessions
        data=io.BytesIO();writer=PdfWriter();writer.add_blank_page(width=200,height=200);writer.write(data)
        with patch('statements.now',side_effect=lambda:self.now.isoformat()):
            s=sessions.create(data.getvalue(),'Willow saved statement.pdf');s.update(state='saved',cancelled=True);sessions.put(s)
            self.op('queue-discard',item_id=s['id']);self.op('queue-restore',item_id=s['id'])
            self.assertEqual(sessions.get(s['id'])['state'],'saved')
            self.now+=timedelta(hours=12)
            values=dict(delete_days=7,trash_days=1,saved_hours=12)
            self.assertEqual(self.service.preview(values)['move_to_trash'],[dict(id=s['id'],description=s['original_name'])])
            self.op('settings',limits=values);self.assertTrue(sessions.get(s['id'],include_discarded=True)['trashed_at'])
            self.now+=timedelta(days=1);self.service.sweep(force=True)
            self.assertEqual(self.service.listing()['items'],[]);self.assertFalse(sessions.folder(s['id']).exists())
        self.assertTrue(clean(self.ledger))

    def test_restored_queue_words_can_be_saved_without_old_payment_authority(self):
        d=self.drafts.create('Willow restored words')
        d.update(state='failed',finished_by=dict(actor='user',reason='correction'),account_consent={},usual_request={'old':True})
        self.drafts.put(d);self.op('queue-discard',item_id=d['id']);self.op('queue-restore',item_id=d['id'])
        restored=self.drafts.get(d['id']);self.assertEqual(restored['state'],'editing')
        for key in ('finished_by','account_consent','usual_payment','usual_request','refusal','correction'):self.assertNotIn(key,restored)
        restored.update(state='working',saved_at=self.now.isoformat());self.drafts.put(restored)
        from ledger_lock import ledger_lock
        answer=ready(new=False);answer['attachment-names']=[]
        with ledger_lock(self.ledger) as fd: result=Transaction(self.settings,self.drafts,restored).apply(answer,fd)
        self.assertTrue(result['entry_id']);self.assertTrue(clean(self.ledger))

    def test_delete_and_restore_queue_behind_an_inflight_checked_save(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from ledger_lock import ledger_lock
        entry=self.add()
        with self.client() as client, ThreadPoolExecutor(max_workers=2) as pool:
            worker=client.app.state.worker
            for action in ('delete','restore'):
                entered=threading.Event();release=threading.Event();queued=threading.Event()
                class Calls(list):
                    def append(self,job):super().append(job);queued.set()
                worker.calls=Calls()
                def save():
                    entered.set();self.assertTrue(release.wait(30))
                    d=self.drafts.create('Willow concurrent payment');d.update(state='working',saved_at=self.now.isoformat());self.drafts.put(d)
                    answer=ready(new=False);answer['attachment-names']=[]
                    with ledger_lock(self.ledger) as fd: result=Transaction(self.settings,self.drafts,d).apply(answer,fd)
                    self.drafts.completed(d,result);return result
                saving=pool.submit(worker.call,save)
                try:
                    self.assertTrue(entered.wait(30));queued.clear()
                    body=dict(action=action,entry_id=entry['id'],version=entry_hash(entry)) if action=='delete' else dict(action=action,item_id=removed)
                    acting=pool.submit(client.post,'/api/retention/operations/'+secrets.token_hex(16),json=body)
                    self.assertTrue(queued.wait(30));self.assertFalse(acting.done(),'The action must wait for the earlier checked save')
                    self.assertEqual(any(e['id']==entry['id'] for e in entries(load(self.ledger))),action=='delete')
                finally:release.set()
                saved=saving.result(30);response=acting.result(30);self.assertEqual(response.status_code,200,response.text)
                if action=='delete':removed=response.json()['item_id']
                self.assertTrue(any(e['id']==saved['entry_id'] for e in entries(load(self.ledger))))
                self.assertEqual(any(e['id']==entry['id'] for e in entries(load(self.ledger))),action=='restore')
        self.assertTrue(clean(self.ledger))

    def test_shared_files_stay_and_restore_refused_has_audit_commit(self):
        e=self.add(source='Bills/2026-10-29/shared.txt');other=deepcopy(e);other['id']='c'*16;other['description']='Another fictional meal';dump(self.ledger/'data/expenses/2026.json',[e,other]);self.jj('commit','-m','Share fictional evidence')
        out=self.delete(e);self.assertTrue((self.ledger/e['evidence'][0]).exists());self.assertEqual(self.service.listing()['items'][0]['files'],e['evidence'])
        metadata=load(self.ledger)[self.ledger/'data/accounts.json'];metadata['accounts'][e['postings'][1]['account']]['kind']='bank';dump(self.ledger/'data/accounts.json',metadata)
        assertion=store_entry('b'*16,'2026-10-31','Monthly confirmed balance',[posting(e['postings'][1]['account'],'0.00',e['postings'][1]['commodity'],**{'assert':dict(amount=other['postings'][1]['amount'],commodity=e['postings'][1]['commodity'])})])
        dump(self.ledger/'data/expenses/2026.json',[other,assertion]);self.jj('commit','-m','Confirm remaining balance')
        result=self.op('restore',item_id=out['item_id']);self.assertTrue(result['refused']);self.assertEqual(records(self.ledger)[-1]['retention_action'],'restore-refused');self.assertTrue(clean(self.ledger))

    def test_rollback_and_restart_after_commit_before_receipt(self):
        e=self.add();before=(self.ledger/'data/expenses/2026.json').read_bytes()
        with patch.object(Transaction,'verified_commit',side_effect=RuntimeError('injected retention failure')):
            with self.assertRaisesRegex(RuntimeError,'injected'):self.delete(e)
        self.assertEqual((self.ledger/'data/expenses/2026.json').read_bytes(),before);self.assertTrue(clean(self.ledger))
        original=Transaction.publish
        # Real publication/commit, followed by process loss before runtime work.
        with patch.object(self.service,'runtime',side_effect=RuntimeError('restart boundary')):
            with self.assertRaisesRegex(RuntimeError,'restart boundary'):self.delete(e)
        replacement=Retention(self.settings,self.drafts,lambda:self.now);replacement.sweep(force=True);self.assertEqual(len(replacement.listing()['items']),1);self.assertTrue(clean(self.ledger))


from ledger_manifest import read_manifest

class RetentionPaymentTests(Fixture):
    from test_varying import VaryingTests as _varying
    seed=_varying.seed
    usual=_varying.usual
    rule=_varying.rule

    def test_varying_recompute_delete_restore_and_paid_bill(self):
        for bill in (False,True):
            with self.subTest(bill=bill):
                r=self.seed(bill=bill)
                if bill:
                    source='Bills/2026-11-01/Willow-bill.txt';(self.ledger/source).parent.mkdir(parents=True,exist_ok=True);(self.ledger/source).write_text('Fictional unpaid bill')
                    r['bill']['evidence']=[source];dump(self.ledger/'data/forecast.json',[r]);self.jj('commit','-m','File fictional bill evidence')
                drafts,d,result=self.usual(**({} if bill else dict(amount='120.00')))
                after=deepcopy(self.rule());service=Retention(self.settings,drafts)
                entry=next(e for e in entries(load(self.ledger)) if e['id']==result['entry_id'])
                if bill:
                    status=service.eligibility(entry['id']);self.assertNotIn(source,status['files']);self.assertIn(source,[f['path'] for f in status['kept']])
                removed=service.operate(secrets.token_hex(16),dict(action='delete',entry_id=entry['id'],version=entry_hash(entry)))
                self.assertEqual(self.rule()['postings'][0]['amount'],'110.00');self.assertEqual(self.rule()['date'],entry['rule']['occurrence'])
                if bill:self.assertEqual(self.rule()['bill'],r['bill']);self.assertTrue((self.ledger/source).is_file())
                service.operate(secrets.token_hex(16),dict(action='restore',item_id=removed['item_id']))
                self.assertEqual(self.rule(),after);self.assertEqual(next(e for e in entries(load(self.ledger)) if e['id']==entry['id']),entry)

    def test_old_payment_undo_operation_is_gone(self):
        self.seed()
        from rules_service import operate
        with self.assertRaises(ValueError):operate(self.settings,secrets.token_hex(16),dict(action='undone'))
        with self.client(worker=False) as client:
            self.assertIn(client.post('/api/rules/undo',json={}).status_code,(404,405))


class ReceiptFactTests(__import__('unittest').TestCase):
    def test_unparseable_facts_become_unknown_without_losing_reading(self):
        from receipts import validate_reading
        for raw,expected in [('2026-10-29','2026-10-29'),('29/10/2026','2026-10-29'),('10-29-2026','2026-10-29'),('04/05/2026',None),('29 October 2026',None),('garbage',None),('2026-02-30',None)]:
            with self.subTest(raw=raw):
                answer=dict(date=raw,amount='not money',payment_datetime='no time',notes=['Keep this detail'])
                validate_reading(answer);self.assertEqual(answer['date'],expected);self.assertIsNone(answer['amount']);self.assertIsNone(answer['payment_datetime']);self.assertIn('Keep this detail',answer['notes'])
                if expected is None:self.assertIn('Receipt date not understood: '+raw,answer['notes'])
        for locale,expected in [('en-GB','2026-05-04'),('en-US','2026-04-05')]:
            answer=dict(date='04/05/2026',amount='12.34',payment_datetime=None,notes=[]);validate_reading(answer,locale);self.assertEqual(answer['date'],expected);self.assertEqual(answer['amount'],'12.34')

class ReceiptCancellationTests(Fixture):
    def setUp(self):
        super().setUp();self.now=datetime.now(timezone.utc);self.drafts=Drafts(self.ledger);self.service=Retention(self.settings,self.drafts,lambda:self.now)
    add=RetentionTests.add
    op=RetentionTests.op
    delete=RetentionTests.delete
    def test_reading_cannot_apply_after_entry_removed(self):
        import threading
        from worker import Worker
        from receipts import read
        e=self.add(source='Bills/2026-10-29/reading.txt')
        d=next(d for d in self.drafts.all() if d.get('result',{}).get('entry_id')==e['id'])
        d['receipt_reading']=dict(state='queued',queued_at=self.now.isoformat(),sources=e['evidence'],entry_id=e['id']);self.drafts.put(d)
        worker=Worker(self.settings,self.drafts);entered=threading.Event();release=threading.Event();failures=[]
        def fake(*args,**kwargs):
            entered.set();self.assertTrue(release.wait(30))
            return dict(read_ok=True,date=None,amount=None,currency=None,paying_account=None,payment_datetime=None,payee=None,references=[],notes=['Late receipt note must not apply']),{}
        def run():
            try:read(worker,d)
            except Exception as exc:failures.append(exc)
        with patch('agent.run',side_effect=fake):
            thread=threading.Thread(target=run);thread.start()
            try:
                self.assertTrue(entered.wait(30));self.delete(e)
            finally:release.set();thread.join(30)
        self.assertFalse(thread.is_alive());self.assertEqual(failures,[]);self.assertEqual(entries(load(self.ledger)),[]);self.assertEqual(self.drafts.get(d['id'])['receipt_reading']['state'],'cancelled')
        self.assertFalse(any(r['kind']=='correction' for r in records(self.ledger)))
