"""Review regressions through the CLI/files/process seams; no real Incus."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import test_browser_box_lifecycle as lifecycle_tests
from test_browser_box import box
import browser_box_lifecycle as lifecycle
if os.environ.get('BROWSER_BOX_SLOW_TESTS') != '1':
    # Starts real controller, guest and client processes; make test-browser-box sets this.
    raise unittest.SkipTest('slow browser-box process tests run with make test-browser-box')


class ReviewTests(unittest.TestCase):
    setUp=lifecycle_tests.LifecycleTests.setUp
    stop_controllers=lifecycle_tests.LifecycleTests.stop_controllers
    until=lifecycle_tests.LifecycleTests.until
    read=lifecycle_tests.LifecycleTests.read
    start_fake=lifecycle_tests.LifecycleTests.start_fake

    def test_outage_beyond_reconnect_window_resumes_a_running_guest(self):
        output,fake,_,_=self.start_fake('outage-running',config={'failures':{'status':3}},timeout=40)
        self.until(lambda:(self.read(fake/'counts.json') or {}).get('status',0)>=4)
        (fake/'release').touch()
        self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
        rows=self.read(output/'summary.json')['cases'][0]
        self.assertEqual([r['outcome'] for r in rows],['passed'],rows)
        self.assertEqual(self.read(fake/'counts.json')['guest_starts'],1)
        commands=[json.loads(line) for line in (fake/'commands.jsonl').read_text().splitlines()]
        self.assertFalse(any(row[0]=='exec' and row[-1]=='stop' for row in commands))

    def test_outage_recovery_preserves_passed_and_failed_terminal_status(self):
        for code in (0,1):
            with self.subTest(exit=code):
                fake=self.root/str(code);fake.mkdir();(fake/'release').touch()
                output,fake,_,_=self.start_fake('terminal-'+str(code),fake,
                        config={'failures':{'status':3},'test_exit':code},timeout=40)
                self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
                rows=self.read(output/'summary.json')['cases'][0]
                self.assertEqual([r['outcome'] for r in rows],['passed'] if code==0 else ['failed','failed'],rows)
                self.assertEqual(rows[0]['exit'],code)
                self.assertEqual(self.read(fake/'counts.json')['guest_starts'],1 if code==0 else 2)

    def test_poisoned_cleanup_does_not_fail_another_healthy_case(self):
        fake=self.root/'poison';fake.mkdir();(fake/'release').touch()
        output,fake,_,_=self.start_fake('poison',fake,config={'poison_cleanup':True,'finish_first_only':True},
                cases=['empty:chromium:light:412','empty:webkit:light:1440'])
        self.until(lambda:(fake/'blocked-cleanup').exists())
        time.sleep(.5)
        (fake/'release-second').touch()
        state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
        rows=self.read(output/'summary.json')['cases']
        self.assertEqual([[r['outcome'] for r in records] for records in rows],[['passed'],['passed']],rows)
        self.assertEqual(self.read(fake/'counts.json')['guest_starts'],2)
        self.assertTrue(state.get('cleanupPending'))

    def test_killed_controller_does_not_leave_a_blocked_local_client(self):
        output,fake,_,controller=self.start_fake('orphan-client',config={'hang_status':True},timeout=30)
        client=self.until(lambda:self.read(fake/'client-entered.json'))['pid']
        birth=lifecycle.process_identity(client)
        try:
            os.kill(controller,signal.SIGKILL)
            deadline=time.monotonic()+3
            while lifecycle.process_identity(client)==birth and time.monotonic()<deadline:time.sleep(.05)
            self.assertNotEqual(lifecycle.process_identity(client),birth,'local client survived its controller')
        finally:
            if lifecycle.process_identity(client)==birth:os.kill(client,signal.SIGKILL)

    def test_next_controller_reaps_exact_registered_orphan_clients(self):
        output,fake,_,controller=self.start_fake('orphan-reap',config={'hang_status':True},timeout=30)
        client=self.until(lambda:self.read(fake/'client-entered.json'))['pid']
        birth=lifecycle.process_identity(client)
        try:
            records=list((fake/'clients').glob('*.json'))
            self.assertTrue(records,'No exact-identity registry for an orphan client')
            record=next(json.loads(path.read_text()) for path in records if json.loads(path.read_text()).get('client',{}).get('pid')==client)
            os.kill(record['watcher']['pid'],signal.SIGKILL)
            os.kill(controller,signal.SIGKILL)
            (fake/'release').touch()
            second,_,_,_=self.start_fake('orphan-replacement',fake,config={})
            state=self.until(lambda:(s if (s:=self.read(second/'runner.json')) and s['status']=='finished' else None))
            self.assertNotEqual(lifecycle.process_identity(client),birth)
            self.assertTrue(state['localClientsReaped'])
        finally:
            if lifecycle.process_identity(client)==birth:os.kill(client,signal.SIGKILL)

    def test_early_stop_is_durable_before_controller_state_exists(self):
        output=self.root/'early';output.mkdir()
        (output/'launch.json').write_text(json.dumps({'remote':'configured','project':'agentexpense-ci',
                    'args':{'checkout':str(Path.cwd()),'cases':[]}}))
        self.assertEqual(box.inspect_run(output,stop=True),0)
        self.assertTrue((output/'stop.requested').is_file())
        with patch.dict(os.environ,{'BROWSER_BOX_CLIENT_REGISTRY':str(self.root/'clients')}), \
             patch.object(box.Incus,'_call_once',side_effect=AssertionError('Early stop reached a client')):
            self.assertEqual(box.controller_main(output),130)
        self.assertEqual(self.read(output/'runner.json')['status'],'cancelled')

    def test_linux_clones_do_not_share_owner_identity(self):
        def read(path):
            return 'shared-machine' if str(path)=='/etc/machine-id' else 'shared-boot'
        with patch.object(lifecycle.sys,'platform','linux'),patch.object(Path,'read_text',read), \
             patch.object(lifecycle.os,'readlink',side_effect=['pid:[100]','pid:[200]']):
            lifecycle.host_identity.cache_clear();first=lifecycle.host_identity()
            lifecycle.host_identity.cache_clear();second=lifecycle.host_identity()
        lifecycle.host_identity.cache_clear()
        self.assertNotEqual(first,second)

    def test_concurrent_stale_cleanup_tolerates_not_found(self):
        incus=box.Incus('configured','agentexpense-ci')
        raw=json.dumps({'token':'a'*32})
        row={'name':'old','status':'Running','config':{'user.browser-box.owner':'a'*32,'user.browser-box.controller':raw}}
        def command(*args,**kwargs):
            if kwargs.get('check',True):raise box.BoxError('Instance not found')
        with patch.object(incus,'instances',side_effect=[[row],[],[]]), \
             patch.object(box,'owner_status',return_value='dead'),patch.object(incus,'call',side_effect=command):
            incus.remove_stale('old','a'*32,raw)

    def test_stop_during_late_server_launch_waits_for_exact_cleanup(self):
        output,fake,environment,_=self.start_fake('late-launch',config={'late_launch':True})
        self.until(lambda:(fake/'launch-entered').exists())
        subprocess.run([sys.executable,'-B',str(box.TOOLS/'browser_box.py'),'--stop',str(output)],
                       env=environment,check=True,capture_output=True,timeout=5)
        state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='cancelled' else None))
        self.assertTrue((fake/'late-created').exists())
        self.assertEqual(self.read(fake/'instances.json'),{})
        self.assertEqual(state['instances'],{})

    def test_partial_collection_is_visible_in_record_summary_and_case_line(self):
        fake=self.root/'partial';fake.mkdir();(fake/'release').touch()
        output,fake,_,_=self.start_fake('partial',fake,config={'collection_partial':True})
        self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
        record=self.read(output/'summary.json')['cases'][0][0]
        self.assertEqual(record['outcome'],'passed')
        self.assertFalse(record['collected'])
        self.assertEqual(record['collection']['skipped'],['shots/late.png'])
        self.assertIn('collectionError',record)
        case_lines=[json.loads(line.split('CASE_RESULT ',1)[1]) for line in (output/'runner.log').read_text().splitlines() if line.startswith('CASE_RESULT ')]
        self.assertEqual(case_lines[0]['collection'],record['collection'])

    def test_local_reaper_preserves_unknown_and_reused_process_identities(self):
        registry=self.root/'registry';registry.mkdir()
        path=registry/'owned.json'
        record={'kind':'browser-box-client','parent':{},'watcher':{'pid':123,'birth':'original'},
                'client':{'pid':124,'birth':'original'}}
        path.write_text(json.dumps(record))
        with patch.object(box.client_watch,'owner_status',return_value='dead'), \
             patch.object(box.client_watch,'process_identity',return_value=None), \
             patch.object(box.client_watch.os,'kill') as kill:
            self.assertEqual(box.client_watch.reap(registry),[])
        kill.assert_not_called();self.assertTrue(path.exists())
        with patch.object(box.client_watch,'owner_status',return_value='dead'), \
             patch.object(box.client_watch,'process_identity',return_value='reused'), \
             patch.object(box.client_watch.os,'kill') as kill:
            box.client_watch.reap(registry)
        kill.assert_not_called()
