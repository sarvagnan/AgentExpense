"""Held-work tests for the shared commit worker, indexes and background publication."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from support import Fixture
from ledger_lock import ledger_lock
from store import load, dump, read_json
import strict_check


class LightSaveTests(Fixture):
    def test_snapshot_failure_keeps_commit_visible_and_can_retry(self):
        with self.client(worker=False) as client:
            job = self.idle(client)
            with patch.object(job, 'prepare', side_effect=OSError('injected snapshot failure')):
                result = client.post('/api/settings/name', json={'name':'Saved despite build failure'})
            self.assertEqual(result.status_code, 200)
            self.assertTrue(result.json()['data_stale'])
            self.assertIn('snapshot failure', result.json()['refresh_error'])
            self.assertEqual(read_json(self.ledger/'ledger.json')['name'], 'Saved despite build failure')
            self.assertEqual(client.post('/api/data-refresh').status_code, 200)
            self.idle(client)

    def test_receipt_failure_after_commit_never_publishes_the_rolled_back_data(self):
        from currency_settings import SettingsRecords
        original = SettingsRecords.put
        def fail(records, draft):
            if draft.get('committed_result'):
                # GETs of previous results must not start this unacknowledged
                # candidate while its durable receipt can still fail.
                job.result({'commit_id': job.ready})
                self.assertFalse(job.running)
                raise OSError('injected receipt failure')
            return original(records, draft)
        with self.client(worker=False) as client:
            job = self.idle(client); generation = job.generation
            saves = client.app.state.saves; index = saves.index
            before = self.bytes()
            with patch.object(SettingsRecords, 'put', fail):
                with self.assertRaisesRegex(OSError, 'receipt failure'):
                    client.post('/api/settings/name', json={'name':'Rolled back fixture'})
            self.assertEqual(self.bytes(), before)
            self.assertEqual(job.generation, generation)
            self.assertFalse(job.status()['data_stale'])
            self.assertIs(saves.index, index)

    def test_running_builder_and_older_refresh_cannot_authorize_a_failed_receipt(self):
        from currency_settings import SettingsRecords
        from transactions import refresh
        original_put = SettingsRecords.put
        entered, release = threading.Event(), threading.Event()
        seen = []
        with self.client(worker=False) as client:
            job = self.idle(client)
            original_runner = job.runner
            def held(candidate):
                seen.append(candidate['id'])
                if len(seen) == 1:
                    entered.set()
                    if not release.wait(20): raise RuntimeError('builder was not released')
                original_runner(candidate)
            job.runner = held
            try:
                first = client.post('/api/settings/name', json={'name':'Receipted candidate A'})
                self.assertEqual(first.status_code, 200)
                self.assertTrue(entered.wait(10))
                before = self.bytes()
                def fail_receipt(records, draft):
                    if draft.get('committed_result', {}).get('name') == 'Unreceipted candidate B':
                        candidate = job.pending['id']
                        release.set()
                        with job.condition:
                            self.assertTrue(job.condition.wait_for(lambda: not job.running, 20))
                        self.assertNotIn(candidate, seen, 'A running builder consumed an unreceipted candidate')
                        # The older request finishes after B committed, but its
                        # refresh may authorize only its own receipted candidate.
                        refresh(self.settings, first.json())
                        with job.condition:
                            self.assertTrue(job.condition.wait_for(lambda: not job.running, 20))
                        self.assertNotIn(candidate, seen, 'An older refresh authorized the newer candidate')
                        self.assertNotEqual(job.generation, candidate)
                        self.assertEqual(client.get(f'/api/data/{candidate}/dashboard.json.gz').status_code, 404)
                        raise OSError('injected candidate B receipt failure')
                    return original_put(records, draft)
                with patch.object(SettingsRecords, 'put', fail_receipt):
                    with self.assertRaisesRegex(OSError, 'candidate B receipt failure'):
                        client.post('/api/settings/name', json={'name':'Unreceipted candidate B'})
                self.assertEqual(self.bytes(), before)
                self.idle(client)
            finally:
                release.set()

    def idle(self, client):
        job = client.app.state.data_refresh
        self.assertTrue(job.wait_idle(30), 'data builder did not settle')
        self.assertFalse(job.status()['data_stale'], job.status())
        return job

    def audit_only_save(self, client):
        from outcomes import OutcomeTransaction, draft_event
        from transactions import clean
        drafts = client.app.state.drafts
        draft = drafts.create('Fictional audit-only outcome')
        draft['saved_at'] = draft['created_at']
        with ledger_lock(self.ledger) as fd:
            self.assertTrue(clean(self.ledger))
            OutcomeTransaction(self.settings, drafts, draft, draft_event(draft, 'not_expense')).apply_record(fd)

    def test_day_invalidation_survives_an_audit_only_save_superseding_its_build(self):
        import gzip
        with patch.dict(os.environ, {'DASHBOARD_TODAY':'2026-11-01'}), self.client(worker=False) as client:
            job = self.idle(client)
            entered, release = threading.Event(), threading.Event()
            original = job.runner
            def held(candidate):
                if not entered.is_set():
                    entered.set()
                    if not release.wait(20): raise RuntimeError('daily build was not released')
                original(candidate)
            job.runner = held
            with patch.dict(os.environ, {'DASHBOARD_TODAY':'2026-11-02'}):
                try:
                    with ledger_lock(self.ledger):
                        job.prepare(force=True)
                        job.activate(job.committed(job.ready))
                    self.assertTrue(entered.wait(10))
                    self.audit_only_save(client)
                finally:
                    release.set()
                self.idle(client)
                value = client.get(f'/api/data/{job.generation}/dashboard.json.gz')
                self.assertEqual(json.loads(gzip.decompress(value.content))['meta']['today'], '2026-11-02')
                provenance = json.loads((job.folder/job.generation/'provenance.json').read_text())
                self.assertEqual(provenance['day'], '2026-11-02')
                self.assertEqual(provenance['builder'], job.builder)

    def test_startup_builder_invalidation_survives_a_superseding_audit_only_save(self):
        import gzip
        from app import create_app
        from fastapi.testclient import TestClient
        with self.client(worker=False) as client:
            self.idle(client)
        builder = self.application/'review-dashboard/scripts/build-dashboard-data.mjs'
        with builder.open('a') as handle:
            handle.write("\nconst reviewFs = await import('node:fs/promises');\nconst reviewFile = process.env.DASHBOARD_DATA_DIR + '/app/data/dashboard.json';\nconst reviewValue = JSON.parse(await reviewFs.readFile(reviewFile, 'utf8'));\nreviewValue.meta.reviewBuilderFixture = 'updated';\nawait reviewFs.writeFile(reviewFile, JSON.stringify(reviewValue));\n")
        app = create_app(self.settings, start_worker=False)
        job = app.state.data_refresh
        original = job.runner
        entered, release = threading.Event(), threading.Event()
        def held(candidate):
            if not entered.is_set():
                entered.set()
                if not release.wait(20): raise RuntimeError('startup build was not released')
            original(candidate)
        job.runner = held
        with TestClient(app) as client:
            try:
                self.assertTrue(entered.wait(10))
                self.audit_only_save(client)
            finally:
                release.set()
            self.idle(client)
            value = client.get(f'/api/data/{job.generation}/dashboard.json.gz')
            self.assertEqual(json.loads(gzip.decompress(value.content))['meta'].get('reviewBuilderFixture'), 'updated')
            provenance = json.loads((job.folder/job.generation/'provenance.json').read_text())
            self.assertEqual(provenance['builder'], job.builder)

    def extension_baseline(self):
        from support import entry, posting
        from save_service import SaveService
        definition = {'name':'work', 'description':'Fictional work fields.', 'fields':{
            'client':{'type':'text','filled-by':'agent','description':'Client.','journal-tag':'work-client'},
            'billable':{'type':'yes-no','filled-by':'person','description':'Billable.','journal-tag':'work-billable'}}}
        path = self.ledger/'extensions/work.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(definition))
        manifest = read_json(self.ledger/'ledger.json')
        manifest['extensions']['work'] = {'ask':True}
        dump(self.ledger/'ledger.json', manifest)
        year = self.ledger/'data/expenses/2026.json'
        rows = [entry('a'*16,'2026-10-29','Fictional work payment',
                      [posting('Expenses:Food','1.00'),posting('Assets:Cash','-1.00')],
                      extensions={'fuel':None,'work':{'client':'Fixture client','billable':True}})]
        dump(year, rows)
        service = SaveService(self.settings)
        service.start();self.addCleanup(service.close)
        self.assertIsNotNone(service.index)
        definition['fields'] = dict(reversed(list(definition['fields'].items())))
        path.write_text(json.dumps(definition))
        return service, year, rows

    def test_extension_order_change_cannot_reuse_a_canonical_verdict_on_reload(self):
        from check_store import _check
        service, year, rows = self.extension_baseline()
        with self.assertRaisesRegex(ValueError, 'is not canonical'):
            _check(self.ledger, emit=lambda *args:None)
        with ledger_lock(self.ledger): service.before()
        self.assertIsNone(service.index, 'A reload accepted what the full verifier refused')

    def test_extension_order_change_cannot_skip_an_unchanged_document_write(self):
        from check_store import _check
        service, year, rows = self.extension_baseline()
        old = year.read_bytes()
        dump(year, rows)
        self.assertNotEqual(year.read_bytes(), old)
        _check(self.ledger, emit=lambda *args:None)

    def test_old_current_generation_is_retained_for_ten_minutes_after_supersession(self):
        with self.client(worker=False) as client:
            job = self.idle(client)
            generation = job.generation
            at = time.time()
            os.utime(job.folder/generation, (at-3600, at-3600))
            for seconds, expected in [(0,200),(599,200),(601,404)]:
                with self.subTest(seconds_after_supersession=seconds), patch('data_refresh.time.time',return_value=at+seconds):
                    response = client.post('/api/settings/name',json={'name':f'Retention fixture {seconds}'})
                    self.assertEqual(response.status_code,200,response.text)
                    self.idle(client)
                    self.assertEqual(client.get(f'/api/data/{generation}/dashboard.json.gz').status_code,expected)

    def test_async_daily_failure_backs_off_and_cannot_supersede_a_persons_retry(self):
        from daily_refresh import DailyRefresh
        clock = [0]
        with patch.dict(os.environ, {'DASHBOARD_TODAY':'2026-11-01'}), self.client(worker=False) as client:
            job = self.idle(client)
            original, attempts = job.runner, []
            def fail(candidate):
                attempts.append(candidate['id'])
                raise RuntimeError('injected daily failure')
            job.runner = fail
            daily = DailyRefresh(self.settings,clock=lambda:clock[0],today=lambda:'2026-11-02')
            with patch.dict(os.environ, {'DASHBOARD_TODAY':'2026-11-02'}):
                daily.tick();self.assertTrue(job.wait_idle(10))
                failure = job.status()['refresh_error']
                for value in (30,60,599):
                    clock[0] = value;daily.tick();self.assertTrue(job.wait_idle(10))
                    self.assertEqual(len(attempts),1,'Maintenance retried a failed async builder before its backoff')
                    self.assertEqual(job.status()['refresh_error'],failure)
                entered, release = threading.Event(), threading.Event()
                def held(candidate):
                    attempts.append(candidate['id']);entered.set()
                    if not release.wait(20):raise RuntimeError('Retry was not released')
                    original(candidate)
                job.runner = held
                try:
                    self.assertEqual(client.post('/api/data-refresh').status_code,200)
                    self.assertTrue(entered.wait(10))
                    candidate = job.latest_candidate
                    clock[0] = 630;daily.tick()
                    self.assertEqual(job.latest_candidate,candidate,'Maintenance overtook the person’s Retry')
                finally:release.set()
                self.idle(client)
                clock[0] = 660;daily.tick();self.assertTrue(job.wait_idle(10))
                self.assertEqual(len(attempts),2)

    def test_status_write_and_cleanup_errors_cannot_leave_a_dead_builder_running(self):
        import shutil
        for boundary in ('status-write','input-cleanup'):
            with self.subTest(boundary=boundary), self.client(worker=False) as client:
                job = self.idle(client)
                original_runner = job.runner
                replace, remove = Path.replace, shutil.rmtree
                def failed_replace(path, target):
                    if threading.current_thread().name == 'dashboard-data' and path == job.record.with_suffix('.tmp'):
                        raise OSError('injected status write failure')
                    return replace(path,target)
                def failed_remove(path, *args, **kwargs):
                    if threading.current_thread().name == 'dashboard-data' and Path(path).parent == job.snapshot_folder:
                        raise OSError('injected input cleanup failure')
                    return remove(path,*args,**kwargs)
                if boundary == 'status-write':
                    job.runner = lambda candidate:(_ for _ in ()).throw(RuntimeError('injected build failure'))
                target, replacement = ('pathlib.Path.replace',failed_replace) if boundary == 'status-write' else ('shutil.rmtree',failed_remove)
                with patch(target,replacement):
                    response = client.post('/api/settings/name',json={'name':'Committed despite refresh I/O failure'})
                    self.assertEqual(response.status_code,200,response.text)
                    job.thread.join(10)
                    self.assertFalse(job.thread.is_alive())
                    self.assertFalse(job.running,'A dead builder retained the running flag')
                self.assertEqual(read_json(self.ledger/'ledger.json')['name'],'Committed despite refresh I/O failure')
                job.runner = original_runner
                self.assertEqual(client.post('/api/data-refresh').status_code,200)
                self.idle(client)

    def test_builder_thread_start_failure_keeps_the_receipted_commit_and_can_retry(self):
        with self.client(worker=False) as client:
            job = self.idle(client)
            start = threading.Thread.start
            def fail(thread):
                if thread.name == 'dashboard-data':raise OSError('injected thread start failure')
                return start(thread)
            with patch('threading.Thread.start',fail):
                response = client.post('/api/settings/name',json={'name':'Receipted before thread failure'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(read_json(self.ledger/'ledger.json')['name'],'Receipted before thread failure')
            self.assertFalse(job.running)
            self.assertIn('thread start failure',job.status()['refresh_error'])
            self.assertEqual(client.post('/api/data-refresh').status_code,200)
            self.idle(client)

    def test_source_stamp_readers_never_observe_a_partial_replacement(self):
        from dashboard_data import check_source
        with self.client(worker=False) as client:
            job = self.idle(client)
            stamp = self.settings.dashboard_data/'app/data/.source-ledger.json'
            write, observed = Path.write_text, []
            def held_write(path, text, *args, **kwargs):
                if path.parent == stamp.parent and path.name.startswith('.source-ledger'):
                    write(path,'{',*args,**kwargs)
                    try: observed.append(check_source(self.settings.dashboard_data,self.ledger))
                    except ValueError: observed.append(False)
                return write(path,text,*args,**kwargs)
            with patch('pathlib.Path.write_text',held_write):
                self.assertEqual(client.post('/api/settings/name',json={'name':'Atomic provenance fixture'}).status_code,200)
                self.idle(client)
            self.assertEqual(observed,[True],'A reader mistook a partial source stamp for another ledger')

    def test_unexpected_refresh_startup_errors_keep_the_server_available_and_retryable(self):
        import shutil
        output, copy, read = subprocess.check_output, shutil.copyfile, Path.read_bytes
        for boundary in ('jj','snapshot','builder-identity'):
            with self.subTest(boundary=boundary):
                record = self.ledger/'drafts/data-refresh.json'
                if record.exists():
                    value = json.loads(record.read_text());value['stale'] = True
                    record.write_text(json.dumps(value))
                active = [True]
                def failed_output(command, *args, **kwargs):
                    if active[0] and boundary == 'jj' and 'diff' in command and '--summary' in command:
                        raise OSError('injected startup jj failure')
                    return output(command,*args,**kwargs)
                def failed_copy(source, target, *args, **kwargs):
                    if active[0] and boundary == 'snapshot' and Path(source) == self.ledger/'ledger.json' and 'dashboard-inputs' in Path(target).parts:
                        raise OSError('injected startup snapshot failure')
                    return copy(source,target,*args,**kwargs)
                def failed_read(path, *args, **kwargs):
                    if active[0] and boundary == 'builder-identity' and path == self.application/'review-dashboard/scripts/build-dashboard-data.mjs':
                        raise OSError('injected startup builder-identity failure')
                    return read(path,*args,**kwargs)
                with patch('subprocess.check_output',failed_output), patch('shutil.copyfile',failed_copy), patch('pathlib.Path.read_bytes',failed_read):
                    with self.client(worker=False) as client:
                        self.assertEqual(client.get('/api/health').status_code,200)
                        status = client.get('/api/data-status').json()
                        self.assertTrue(status['data_stale'])
                        self.assertIn('injected startup '+boundary+' failure',status['refresh_error'])
                        active[0] = False
                        self.assertEqual(client.post('/api/data-refresh').status_code,200)
                        self.idle(client)

    def test_outside_commit_is_noticed_and_failed_verification_retains_the_index(self):
        with self.client(worker=False) as client:
            self.idle(client)
            saves = client.app.state.saves
            baseline = saves.full_checks
            path = self.ledger / 'data/expenses/2026.json'
            # A terminal commit goes through exactly the shared verifier.
            import commit as ledger_commit
            with ledger_lock(self.ledger) as fd:
                manifest = read_json(self.ledger / 'ledger.json');manifest['name'] = 'External fixture'
                dump(self.ledger / 'ledger.json', manifest)
                ledger_commit.verify(self.ledger, emit=lambda *args: None)
                intended = self.bytes()
                original = ledger_commit.jj
                def fail_bookmark(root, *args):
                    if args[:2] == ('bookmark', 'set'): raise ValueError('injected bookmark failure')
                    return original(root, *args)
                with patch.object(ledger_commit, 'jj', fail_bookmark):
                    with self.assertRaisesRegex(ValueError, 'bookmark failure'):
                        ledger_commit.commit(self.ledger, 'Interrupted external settings', lock_fd=fd, emit=lambda *args: None)
                self.assertEqual(self.bytes(), intended, 'CLI rollback must retain the caller’s intended edits')
                try:
                    ledger_commit.commit(self.ledger, 'External settings', lock_fd=fd, emit=lambda *args: None)
                except subprocess.CalledProcessError as exc:
                    self.fail(exc.stderr)
            self.assertEqual(client.post('/api/settings/name', json={'name':'Server fixture'}).status_code, 200)
            self.assertGreater(saves.full_checks, baseline)
            index = saves.index
            before = self.bytes()
            with patch('commit.verify', side_effect=ValueError('injected strict refusal')):
                with self.assertRaisesRegex(RuntimeError, 'failed verification'):
                    client.post('/api/settings/name', json={'name':'Refused fixture'})
            self.assertIs(saves.index, index)
            self.assertEqual(self.bytes(), before)
            self.assertIsNone(saves.stamp)

    def test_many_commits_make_one_running_build_and_one_latest_queued_build(self):
        with self.client(worker=False) as client:
            job = self.idle(client)
            entered, release = threading.Event(), threading.Event()
            original = job.runner
            seen = []
            def held(candidate):
                self.assertTrue(candidate['source'].is_relative_to(self.ledger / 'drafts'))
                seen.append(candidate['commit'])
                if len(seen) == 1:
                    entered.set()
                    if not release.wait(30): raise RuntimeError('test did not release builder')
                original(candidate)
            job.runner = held
            try:
                first = client.post('/api/settings/name', json={'name':'First fixture'})
                self.assertEqual(first.status_code, 200, first.text)
                self.assertTrue(entered.wait(10))
                self.assertTrue(first.json()['data_stale'])
                for number in range(4):
                    result = client.post('/api/settings/name', json={'name':f'Latest fixture {number}'})
                    self.assertEqual(result.status_code, 200, result.text)
                latest = result.json()['commit_id']
                self.assertEqual(len(seen), 1)
                self.assertEqual(job.pending['commit'], latest)
                self.assertTrue(json.loads(job.record.read_text())['stale'])
                old = job.generation
                self.assertEqual(client.get('/api/data-status').json()['generation'], old)
            finally:
                release.set()
            self.idle(client)
            self.assertEqual(len(seen), 2)
            self.assertEqual(seen[-1], latest)
            self.assertEqual(job.ready, latest)
            self.assertFalse(json.loads(job.record.read_text())['stale'])
            value = client.get(f'/api/data/{job.generation}/dashboard.json.gz')
            self.assertEqual(value.status_code, 200)
            import gzip
            self.assertEqual(json.loads(gzip.decompress(value.content))['meta']['ledger']['name'], 'Latest fixture 3')

    def test_failed_build_keeps_previous_generation_and_retry_repairs_it(self):
        with self.client(worker=False) as client:
            job = self.idle(client); previous = job.generation
            original = job.runner
            job.runner = lambda candidate: (_ for _ in ()).throw(RuntimeError('injected builder failure'))
            result = client.post('/api/settings/name', json={'name':'Saved fixture'})
            self.assertEqual(result.status_code, 200)
            self.assertTrue(job.wait_idle(10))
            self.assertEqual(job.generation, previous)
            self.assertTrue(job.status()['data_stale'])
            self.assertIn('injected builder failure', job.status()['refresh_error'])
            job.runner = original
            self.assertEqual(client.post('/api/data-refresh').status_code, 200)
            self.idle(client)
            self.assertNotEqual(job.generation, previous)

    def test_refresh_refuses_large_untracked_store_input_before_snapshotting(self):
        with self.client(worker=False) as client:
            job=self.idle(client);generation=job.generation
            (self.ledger/'data/expenses/2099.json').write_text('['+' '*(2*1024*1024)+']')
            response=client.post('/api/data-refresh')
            self.assertEqual(response.status_code,409)
            self.assertIn('Finish the ledger changes',response.json()['detail'])
            self.assertEqual(job.generation,generation)
            self.assertIsNone(job.pending)

    def test_queue_event_picks_up_add_within_two_hundred_ms_without_a_poll(self):
        from support import ready
        answer = ready(); answer['attachment-names'] = []
        self.set_answers(answer)
        from app import create_app
        from fastapi.testclient import TestClient
        app = create_app(self.settings)
        idle, picked = threading.Event(), threading.Event()
        event = threading.Event()
        waits = threading.Condition()
        class ObservedEvent:
            def set(self): event.set()
            def clear(self): event.clear()
            def wait(self, timeout=None):
                idle.set()
                with waits:
                    self_timeout.append(timeout)
                    waits.notify_all()
                return event.wait(timeout)
        self_timeout = []
        worker = app.state.worker
        controlled = [42.0]
        worker.clock = lambda: controlled[0]
        sweeps = []
        sweep = worker.sweep_retention
        def maintenance():
            sweeps.append(controlled[0]);sweep()
        worker.sweep_retention = maintenance
        worker.wake = ObservedEvent()
        original = worker.process
        times = []
        def process(draft):
            times.append(time.perf_counter())
            picked.set()
            return original(draft)
        worker.process = process
        with TestClient(app) as client:
            self.assertTrue(idle.wait(20))
            self.assertEqual(self_timeout[0],30)
            controlled[0]=71.9;worker.wake.set()
            with waits:self.assertTrue(waits.wait_for(lambda:len(self_timeout)>=2,10))
            self.assertAlmostEqual(self_timeout[-1],.1)
            self.assertEqual(len(sweeps),1)
            controlled[0]=72;worker.wake.set()
            with waits:self.assertTrue(waits.wait_for(lambda:len(self_timeout)>=3,10))
            self.assertEqual(len(sweeps),2)
            identity = self.create(client, upload=False)
            started = time.perf_counter()
            self.save(client, identity)
            self.assertTrue(picked.wait(2))
            self.assertLess(times[0]-started, .2)
            self.assertGreater(self_timeout[0], 20, 'idle wait is a maintenance deadline, not a work poll')
            self.wait(client, identity, 'added')

    def test_process_exit_between_commit_and_build_is_repaired_on_startup(self):
        # A real abrupt exit, rather than normal lifespan cleanup, leaves the
        # pre-commit durable marker and old complete generation on disk.
        script = self.base / 'crash.py'
        script.write_text('''import sys,os,threading\nfrom pathlib import Path\nsys.path[:0]=sys.argv[1:3]\nfrom settings import Settings\nfrom app import create_app\nfrom fastapi.testclient import TestClient\ns=Settings(application=Path(sys.argv[3]),ledger=Path(sys.argv[4]),dashboard_data=Path(sys.argv[5]),state_dir=Path(sys.argv[6]),dev=True,codex=sys.argv[7],ledger_check=False)\na=create_app(s,start_worker=False)\nwith TestClient(a) as c:\n j=a.state.data_refresh\n assert j.wait_idle(30)\n j.runner=lambda candidate:threading.Event().wait(60)\n assert c.post('/api/settings/name',json={'name':'Committed before exit'}).status_code==200\n assert j.status()['data_stale']\n os._exit(0)\n''')
        from support import SERVER, APPLICATION
        result = subprocess.run([os.sys.executable, str(script), str(SERVER), str(APPLICATION/'scripts/ledger'),
            str(self.application), str(self.ledger), str(self.settings.dashboard_data), str(self.base/'crash-state'), str(self.fake)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads((self.ledger/'drafts/data-refresh.json').read_text())['stale'])
        with self.client(worker=False) as client:
            job = self.idle(client)
            self.assertEqual(job.wanted, job.ready)
            self.assertFalse(json.loads(job.record.read_text())['stale'])


class MixedSaveAgreementTests(unittest.TestCase):
    def test_mixed_server_saves_match_full_journal_reports_and_dashboard(self):
        import tempfile
        from bench_save import Bench
        from incremental_check import LedgerIndex
        from generate_ledger import render
        from compare_outputs import snapshot, normalize
        from store import _model_memos
        checked, stores = [], []
        original = LedgerIndex.check
        case = self
        def verdict(function):
            try: function(); return None
            except ValueError as exc: return type(exc).__name__, str(exc)
        def agreement(index, documents, root):
            incremental = verdict(lambda: original(index, documents, root))
            full = verdict(lambda: strict_check.check(documents, root))
            case.assertEqual(incremental, full)
            from check_store import _check, check_card_statements
            from store import validate
            loaded = documents, validate(documents,root)
            inc_store = verdict(lambda:_check(root,loaded=loaded,strict_checker=lambda d,r:original(index,d,r),
                membership_checker=index.check_membership,emit=lambda *args:None))
            full_store = verdict(lambda:_check(root,loaded=loaded,strict_checker=strict_check.check,
                membership_checker=check_card_statements,emit=lambda *args:None))
            case.assertEqual(inc_store,full_store)
            stores.append(1)
            checked.append(1)
            if full is not None: strict_check.check(documents, root)
        class CheckedBench(Bench):
            def record(self, *args, **kwargs):
                result = super().record(*args, **kwargs)
                documents = load(self.ledger)
                case.assertEqual((self.ledger/'expense_ledger.ledger').read_text(), render(documents, self.ledger))
                # Full store verification also runs without the model memo.
                memo = _model_memos.pop(self.ledger.resolve(), None)
                try:
                    from check_store import _check
                    _check(self.ledger, emit=lambda *args: None)
                finally:
                    if memo is not None: _model_memos[self.ledger.resolve()] = memo
                return result
            def rename(self, rep):
                result = super().rename(rep)
                if rep == self.reps:
                    full = snapshot(self.ledger)
                    journal = (self.ledger/'expense_ledger.ledger').read_text()
                    from compare_outputs import strip_header
                    case.assertEqual(full['journal'], strip_header(journal))
                    from second_check import find_hledger
                    hledger = find_hledger()
                    if hledger:
                        for name, args in [('print',['print']),('bal',['bal','--flat']),('bal-cost',['bal','--flat','-B']),('reg',['reg'])]:
                            actual = subprocess.check_output([hledger,'-f',str(self.ledger/'expense_ledger.ledger'),*args],text=True)
                            case.assertEqual(actual, full[name])
                    for key, expected in normalize(full, ()).items():
                        if not key.startswith('dashboard/'): continue
                        actual = json.loads((self.settings.dashboard_data/'app/data'/key.split('/',1)[1]).read_text())
                        if 'meta' in actual: actual['meta'].pop('generatedAt',None)
                        case.assertEqual(actual, expected, key)
                return result
        with tempfile.TemporaryDirectory(prefix='mixed-save-',dir=os.environ['EXPENSES_TEST_TMP']) as temp, patch.dict(os.environ):
            with patch.object(LedgerIndex, 'check', agreement):
                bench = CheckedBench(Path(temp),None,1,7)
                bench.run()
                self.assertEqual(len(bench.samples),9)
                self.assertGreaterEqual(len(checked),9)
                self.assertGreaterEqual(len(stores),9)
