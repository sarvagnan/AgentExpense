"""Controller lifecycle tests use local subprocess doubles, never Incus/network."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_browser_box import box
import browser_box_lifecycle as lifecycle
if os.environ.get('BROWSER_BOX_SLOW_TESTS') != '1':
    # Starts real controller, guest and client processes; make test-browser-box sets this.
    raise unittest.SkipTest('slow browser-box process tests run with make test-browser-box')

FAKE_INCUS = Path(__file__).with_name('browser_box_fake.py').read_text()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        # The runner accepts run folders only under this root, so the shared test
        # temporary folder (EXPENSES_TEST_TMP under make test) cannot hold them.
        scratch=Path('/tmp/agentexpense-browser-box/tests')
        scratch.mkdir(parents=True,exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        box.CANCELLED.clear()
        box.STOP_DEADLINE=None
        self.addCleanup(box.CANCELLED.clear)
        self.controllers=[]
        self.addCleanup(self.stop_controllers)

    def stop_controllers(self):
        for pid in self.controllers:
            if lifecycle.process_identity(pid):
                try: os.kill(pid,signal.SIGTERM)
                except ProcessLookupError: pass
        deadline=time.monotonic()+5
        while any(lifecycle.process_identity(pid) for pid in self.controllers) and time.monotonic()<deadline:
            time.sleep(.02)

    def until(self, function, timeout=40):
        # This bounds fixture setup/result publication, including multiple
        # process-heavy attempts. Stop timing has its own unchanged assertion.
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            result=function()
            if result:return result
            time.sleep(.02)
        evidence=[]
        for path in self.root.glob('*-output/runner.json'):
            evidence.append(str(path)+'\n'+path.read_text())
            log=path.with_name('runner.log')
            if log.exists():evidence.append(log.read_text()[-4000:])
        self.fail('Timed out waiting for the owned subprocess evidence\n'+'\n'.join(evidence))

    def read(self,path):
        try:return json.loads(path.read_text())
        except FileNotFoundError:return None

    def start_fake(self, name, fake=None, extra_environment=None, config=None, timeout=30, cases=None,reconnect_timeout=4,infrastructure_retries=2):
        fake=fake or self.root/name
        fake.mkdir(exist_ok=True)
        binary=fake/'incus'
        binary.write_text('#!'+sys.executable+'\n'+FAKE_INCUS);binary.chmod(0o755)
        if config is not None:(fake/'faults.json').write_text(json.dumps(config))
        environment={**os.environ,'BOX_FAKE_HOME':str(fake),'BROWSER_BOX_REMOTE':'configured',
                     'BROWSER_BOX_PROJECT':'agentexpense-ci','BROWSER_BOX_CLIENT_REGISTRY':str(fake/'clients'),
                     'BROWSER_BOX_POOL':'fictional','PATH':str(fake)+os.pathsep+os.environ['PATH'],
                     'PYTHONDONTWRITEBYTECODE':'1'}
        environment.update(extra_environment or {})
        output=self.root/(name+'-output')
        launch=subprocess.run([sys.executable,'-B',str(box.TOOLS/'browser_box.py'),'--jobs','2' if cases else '1',
                               '--output',str(output),'--timeout',str(timeout),'--reconnect-timeout',str(reconnect_timeout),
                               '--infrastructure-retries',str(infrastructure_retries),
                               *(cases or ['asha:chromium:light:1440'])],env=environment,
                              text=True,capture_output=True,timeout=15)
        self.assertEqual(launch.returncode,0,launch.stderr)
        pid=self.read(output/'launcher.json')['pid'];self.controllers.append(pid)
        # The host's Git launcher can initialise its developer-tool cache under
        # a fresh test TMPDIR. Separate source/image preparation from the case
        # checks; the measured stop bound below remains unchanged.
        self.until(lambda:(s if (s:=self.read(output/'runner.json')) and
                           (s.get('image') or s['status'] in ('finished','cancelled')) else None),timeout=60)
        return output,fake,environment,pid

    def test_dropped_exec_resumes_the_same_case_without_using_test_retry(self):
        fake=self.root/'drop';fake.mkdir();(fake/'release').touch()
        output,fake,environment,pid=self.start_fake('drop',fake,{'BOX_DROP_EXEC':'1'})
        state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
        self.assertEqual(state['exit'],0,(output/'runner.log').read_text())
        attempts=self.read(output/'summary.json')['cases'][0]
        self.assertEqual(len(attempts),1)
        self.assertEqual(attempts[0]['outcome'],'passed')
        self.assertFalse(attempts[0]['rerun'])

    def test_transient_list_and_pull_errors_resume_and_preserve_results(self):
        fake=self.root/'transient';fake.mkdir();(fake/'release').touch()
        output,fake,_,_=self.start_fake('transient',fake,config={'failures':{'list':2,'file pull':2}})
        state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
        self.assertEqual(state['exit'],0,(output/'runner.log').read_text())
        records=self.read(output/'summary.json')['cases'][0]
        self.assertEqual([r['outcome'] for r in records],['passed'])
        self.assertEqual(self.read(fake/'counts.json')['guest_starts'],1)
        self.assertTrue((Path(records[0]['folder'])/'results/run.log').is_file())
        self.assertEqual(self.read(fake/'instances.json'),{})

    def test_progressing_pull_longer_than_the_old_request_limit_succeeds(self):
        fake=self.root/'slow-pull';fake.mkdir();(fake/'release').touch()
        output,fake,_,_=self.start_fake('slow-pull',fake,
                                      config={'slow_pull_seconds':32,'failures':{'file pull':1}},timeout=75)
        record_path=output/'asha-chromium-light-1440/attempt-1/result.json'
        record=self.until(lambda:(r if (r:=self.read(record_path)) and r.get('seconds') is not None else None),timeout=65)
        self.assertEqual(record['outcome'],'passed',record)
        self.assertTrue((record_path.parent/'results/run.log').is_file())
        state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
        self.assertEqual(state['exit'],0,(output/'runner.log').read_text())
        counters=self.read(fake/'counts.json')
        self.assertEqual(counters['guest_starts'],1)
        self.assertEqual(counters['file pull'],2)
        progress=[json.loads(line) for line in (fake/'pull-progress.jsonl').read_text().splitlines()]
        self.assertGreater(progress[-1]['bytes'],progress[0]['bytes'])
        self.assertGreater(progress[-1]['at']-progress[0]['at'],30)

    def test_finished_guest_collection_failure_preserves_outcome_and_never_relaunches(self):
        for code,outcome in [(0,'passed'),(1,'failed')]:
            with self.subTest(outcome=outcome):
                fake=self.root/outcome;fake.mkdir();(fake/'release').touch()
                output,fake,_,_=self.start_fake(outcome,fake,
                                              config={'collection_error':True,'test_exit':code})
                first=output/'asha-chromium-light-1440/attempt-1/result.json'
                record=self.until(lambda:(r if (r:=self.read(first)) and r.get('seconds') is not None else None))
                self.assertEqual((record['outcome'],record['exit']),(outcome,code),record)
                self.assertIn('collectionError',record)
                self.assertFalse(record['collected'])
                self.assertTrue(record['collection']['partial'])
                self.assertTrue(record['cleaned'])
                state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
                self.assertEqual(state['exit'],1,'Missing evidence must remain visible in the job status')
                records=self.read(output/'summary.json')['cases'][0]
                self.assertEqual(len(records),1)
                self.assertEqual(self.read(fake/'counts.json')['guest_starts'],1)
                self.assertEqual(self.read(fake/'instances.json'),{})

    def test_failed_pull_restart_preserves_the_largest_received_prefix(self):
        fake=self.root/'prefix';fake.mkdir()
        binary=fake/'incus';binary.write_text('#!'+sys.executable+'\n'+FAKE_INCUS);binary.chmod(0o755)
        (fake/'faults.json').write_text(json.dumps({'partial_pull_bytes':8}))
        partial=fake/'results.tar.gz.partial';partial.write_bytes(b'R'*64)
        incus=box.Incus('configured','agentexpense-ci');incus.reconnect_timeout=.1
        with patch.dict(os.environ,{'BOX_FAKE_HOME':str(fake),'PATH':str(fake)+os.pathsep+os.environ['PATH']}):
            incus.launch('configured:image','owned')
            with self.assertRaises(box.ConnectionLost),incus.budget(time.monotonic()+5):
                incus.call('file','pull','configured:owned/opt/browser-box/results.tar.gz',str(partial))
            self.assertEqual(partial.read_bytes(),b'R'*64)
            incus.cleanup('owned',ephemeral=True)

    def test_old_log_errors_do_not_make_a_missing_instance_transient(self):
        fake=self.root/'old-log';fake.mkdir()
        binary=fake/'incus';binary.write_text('#!'+sys.executable+'\n'+FAKE_INCUS);binary.chmod(0o755)
        log=fake/'incus.log';log.write_text('previous command: i/o timeout\n')
        incus=box.Incus('configured','agentexpense-ci');incus.reconnect_timeout=.1
        with patch.dict(os.environ,{'BOX_FAKE_HOME':str(fake),'PATH':str(fake)+os.pathsep+os.environ['PATH']}):
            with self.assertRaises(box.Infrastructure) as error:
                incus.exec('missing',box.GUEST_PYTHON,box.GUEST_SCRIPT,'status',log=log)
        self.assertIs(type(error.exception),box.Infrastructure)
        self.assertEqual(self.read(fake/'counts.json')['status'],1)

    def test_result_archive_preserves_logs_and_partial_trace_before_cleanup(self):
        fake=self.root/'evidence';fake.mkdir();(fake/'release').touch()
        output,fake,_,_=self.start_fake('evidence',fake)
        state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
        self.assertEqual(state['exit'],0,(output/'runner.log').read_text())
        record=self.read(output/'summary.json')['cases'][0][0]
        evidence=Path(record['folder'])/'results'
        self.assertIn('fixture server log',(evidence/'server.log').read_text())
        self.assertTrue((evidence/'artifacts/failure-partial.zip').is_file())
        self.assertEqual(self.read(fake/'instances.json'),{})

    def test_transient_cleanup_recovers_within_case_without_relaunch(self):
        fake=self.root/'cleanup';fake.mkdir();(fake/'release').touch()
        output,fake,_,_=self.start_fake('cleanup',fake,config={'failures':{'stop':3}})
        state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
        self.assertEqual(state['exit'],0,(output/'runner.log').read_text())
        rows=self.read(output/'summary.json')['cases'][0]
        self.assertEqual([r['outcome'] for r in rows],['passed'])
        self.assertEqual([r['testAttempt'] for r in rows],[1])
        self.assertNotIn('cleanupError',rows[0])
        self.assertEqual(self.read(fake/'counts.json')['guest_starts'],1)
        self.assertEqual(self.read(fake/'instances.json'),{})
        self.assertEqual(state['instances'],{})

    def test_guest_time_limit_is_infrastructure_with_evidence_and_no_test_rerun(self):
        fake=self.root/'budget';fake.mkdir();(fake/'release').touch()
        output,fake,_,_=self.start_fake('budget',fake,config={'guest_timeout':True})
        state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
        self.assertEqual(state['exit'],1,(output/'runner.log').read_text())
        records=self.read(output/'summary.json')['cases'][0]
        self.assertEqual(len(records),1)
        self.assertEqual((records[0]['outcome'],records[0]['exit']),('infrastructure',124))
        self.assertTrue((Path(records[0]['folder'])/'results/run.log').exists())
        self.assertEqual(self.read(fake/'instances.json'),{})

    def test_stop_cancels_two_running_cases_promptly_and_collects_both(self):
        cases=['empty:chromium:light:412','empty:webkit:light:1440']
        output,fake,environment,_=self.start_fake('two-stop',cases=cases)
        self.until(lambda:(self.read(fake/'counts.json') or {}).get('guest_starts')==2)
        started=time.monotonic()
        subprocess.run([sys.executable,'-B',str(box.TOOLS/'browser_box.py'),'--stop',str(output)],
                       env=environment,capture_output=True,check=True,timeout=5)
        state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='cancelled' else None))
        elapsed=time.monotonic()-started
        print('STOP_TIMING '+json.dumps({'cases':2,'seconds':round(elapsed,3)}),flush=True)
        self.assertLess(elapsed,12)
        records=self.read(output/'summary.json')['cases']
        self.assertEqual(len(records),2)
        for rows in records:
            self.assertEqual([r['outcome'] for r in rows],['stopped'])
            self.assertTrue((Path(rows[0]['folder'])/'results/run.log').exists())
        self.assertEqual(self.read(fake/'instances.json'),{})

    def test_missing_container_is_infrastructure_and_does_not_spend_test_rerun(self):
        fake=self.root/'lost';fake.mkdir();(fake/'release').touch()
        output,fake,_,_=self.start_fake('lost',fake,config={'lose_on_poll':True,'test_exit':1})
        state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
        self.assertEqual(state['exit'],1,(output/'runner.log').read_text())
        records=self.read(output/'summary.json')['cases'][0]
        self.assertEqual([r['outcome'] for r in records],['infrastructure','failed','failed'])
        self.assertEqual([r['testAttempt'] for r in records],[1,1,2])
        self.assertEqual([r['rerun'] for r in records],[False,False,True])
        self.assertEqual(self.read(fake/'instances.json'),{})

    def test_repeated_stop_during_launch_or_collection_is_prompt_and_not_retried(self):
        for phase,marker in [('launch','launch-entered'),('collect','collect-entered')]:
            with self.subTest(phase=phase):
                fake=self.root/phase;fake.mkdir();(fake/'release').touch()
                output,fake,environment,_=self.start_fake(phase,fake,config={'hold_'+phase:True})
                self.until(lambda:(fake/marker).exists())
                started=time.monotonic()
                for _ in range(2):
                    request=subprocess.run([sys.executable,'-B',str(box.TOOLS/'browser_box.py'),'--stop',str(output)],
                                           env=environment,text=True,capture_output=True,timeout=5)
                    self.assertEqual(request.returncode,0,request.stderr)
                state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='cancelled' else None))
                elapsed=time.monotonic()-started
                print('STOP_TIMING '+json.dumps({'phase':phase,'seconds':round(elapsed,3)}),flush=True)
                self.assertLess(elapsed,12)
                records=self.read(output/'summary.json')['cases'][0]
                self.assertEqual([r['outcome'] for r in records],['passed' if phase=='collect' else 'stopped'])
                self.assertEqual(self.read(fake/'instances.json'),{})
                if phase=='collect':self.assertTrue((Path(records[0]['folder'])/'results/run.log').exists())

    def test_killing_waiter_preserves_controller_and_completed_results(self):
        output,fake,environment,pid=self.start_fake('caller')
        self.until(lambda:self.read(fake/'entered.json'))
        waiter=subprocess.Popen([sys.executable,'-B',str(box.TOOLS/'browser_box.py'),'--wait',str(output)],
                                env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        waiter.kill();waiter.wait(timeout=5)
        os.kill(pid,0)
        (fake/'release').touch()
        state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='finished' else None))
        self.assertEqual(state['exit'],0,(output/'runner.log').read_text())
        summary=self.read(output/'summary.json')
        self.assertEqual(summary['cases'][0][0]['exit'],0)
        self.assertTrue(summary['cases'][0][0]['cleaned'])
        self.assertEqual(self.read(fake/'instances.json'),{})
        follow=subprocess.run([sys.executable,'-B',str(box.TOOLS/'browser_box.py'),'--wait',str(output)],
                              env={**environment,'BROWSER_BOX_REMOTE':''},text=True,capture_output=True,timeout=5)
        self.assertEqual(follow.returncode,0,follow.stderr)
        self.assertIn('CASE_RESULT',follow.stdout)
        commands=[json.loads(line) for line in (fake/'commands.jsonl').read_text().splitlines()]
        self.assertFalse(any(row[:2]==['image','delete'] for row in commands))

    def test_term_hup_int_clean_owned_instances_and_checkpoint_results(self):
        for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):
            with self.subTest(signal=sig):
                output,fake,environment,pid=self.start_fake('signal-'+str(sig))
                self.until(lambda:self.read(fake/'entered.json'))
                if sig==signal.SIGTERM:
                    stopped=subprocess.run([sys.executable,'-B',str(box.TOOLS/'browser_box.py'),'--stop',str(output)],
                                           env=environment,text=True,capture_output=True,timeout=5)
                    self.assertEqual(stopped.returncode,0,stopped.stderr)
                else:
                    os.kill(pid,sig)
                state=self.until(lambda:(s if (s:=self.read(output/'runner.json')) and s['status']=='cancelled' else None))
                self.assertEqual(state['exit'],130,(output/'runner.log').read_text())
                self.assertEqual(self.read(fake/'instances.json'),{})
                record=self.read(output/'summary.json')['cases'][0][0]
                self.assertTrue(record['cleaned'])
                self.assertEqual(record['outcome'],'stopped')
                self.assertEqual(len(self.read(output/'summary.json')['cases'][0]),1)

    def test_sigkilled_controller_is_reaped_by_the_next_controller(self):
        if not lifecycle.host_identity():self.skipTest('No stable host identity; stale ownership must remain unknown')
        output,fake,environment,pid=self.start_fake('killed')
        entered=self.until(lambda:self.read(fake/'entered.json'))
        os.kill(pid,signal.SIGKILL)
        self.until(lambda:lifecycle.process_identity(pid)=='')
        # Guest work is detached; no long-running local exec client remains.
        (fake/'release').touch()
        second,_,_,_=self.start_fake('replacement',fake)
        state=self.until(lambda:(s if (s:=self.read(second/'runner.json')) and s['status']=='finished' else None))
        self.assertEqual(state['exit'],0,(second/'runner.log').read_text())
        self.assertTrue(state['staleInstances'][0]['removed'])
        self.assertEqual(self.read(fake/'instances.json'),{})

    def test_detached_controller_survives_launchers_sigkill(self):
        # The launching shell is expendable; the session leader owns results.
        worker = self.root/'worker.py'
        worker.write_text('import os,signal,time\nfrom pathlib import Path\n'
                          'root=Path(__file__).parent\n'
                          'signal.signal(signal.SIGTERM,lambda *_:exit(0))\n'
                          'while not (root/"release").exists(): time.sleep(.01)\n'
                          '(root/"finished.next").write_text(str(os.getsid(0)))\n'
                          '(root/"finished.next").replace(root/"finished")\n')
        code = ('import sys,signal; from pathlib import Path; '
                f'sys.path.insert(0,{str(box.TOOLS)!r}); '
                'from browser_box_lifecycle import spawn_controller; '
                f'child=spawn_controller(Path({str(self.root)!r}),[sys.executable,{str(worker)!r}]); '
                'print(child.pid,flush=True); signal.pause()')
        parent = subprocess.Popen([sys.executable,'-B','-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        controller = None
        try:
            line = parent.stdout.readline()
            self.assertTrue(line, parent.stderr.read() if parent.poll() is not None else 'launcher failed')
            controller = int(line)
            parent.kill(); parent.wait(timeout=5)
            os.kill(controller,0)
            (self.root/'release').touch()
            deadline=time.monotonic()+5
            while not (self.root/'finished').exists() and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertEqual((self.root/'finished').read_text(),str(controller))
            self.assertEqual(json.loads((self.root/'launcher.json').read_text())['pid'],controller)
            self.assertTrue((self.root/'runner.log').exists())
        finally:
            if parent.poll() is None: parent.kill(); parent.wait(timeout=5)
            if controller:
                try: os.kill(controller,signal.SIGTERM)
                except ProcessLookupError: pass
            parent.stdout.close(); parent.stderr.close()

    def test_stale_cleanup_skips_alive_unknown_foreign_and_untagged_owners(self):
        incus=box.Incus('configured','agentexpense-ci')
        def instance(name, token, state):
            return {'name':name,'status':'Running','config':{'user.browser-box.owner':token,
                    'user.browser-box.controller':json.dumps({'token':token,'state':state})}}
        records=[instance('dead','a'*32,'dead'),instance('alive','b'*32,'alive'),
                 instance('unknown','c'*32,'unknown'),{'name':'untagged','config':{}},
                 {'name':'legacy','config':{'user.browser-box.owner':'d'*32}}]
        with patch.object(incus,'project_instances',return_value=records), \
             patch.object(box,'owner_status',side_effect=lambda record: record.get('state','unknown')), \
             patch.object(incus,'remove_stale') as remove:
            report=incus.reap_stale()
        remove.assert_called_once_with('dead','a'*32,records[0]['config']['user.browser-box.controller'])
        self.assertEqual(len(report),5)

    def test_owner_liveness_requires_same_host_and_matching_process_birth(self):
        record={'version':1,'token':'a'*32,'host':'this-host','pid':123,'birth':'original'}
        with patch.object(lifecycle,'host_identity',return_value='this-host'), patch.object(lifecycle,'process_identity',return_value='original'):
            self.assertEqual(box.owner_status(record),'alive')
        with patch.object(lifecycle,'host_identity',return_value='this-host'), patch.object(lifecycle,'process_identity',return_value='different'):
            self.assertEqual(box.owner_status(record),'dead')
        with patch.object(lifecycle,'host_identity',return_value='another-host'):
            self.assertEqual(box.owner_status(record),'unknown')
        with patch.object(lifecycle,'host_identity',return_value='this-host'), patch.object(lifecycle,'process_identity',return_value=None):
            self.assertEqual(box.owner_status(record),'unknown')
        self.assertEqual(box.owner_status({}),'unknown')
        with patch.object(lifecycle,'host_identity',return_value='this-host'),patch.object(lifecycle,'process_identity') as identity:
            self.assertEqual(box.owner_status({**record,'pid':2**100}),'unknown')
        identity.assert_not_called()

    def test_capacity_wait_and_quota_race_do_not_fail_the_case(self):
        incus=box.Incus('configured','agentexpense-ci')
        results=[SimpleNamespace(returncode=1,stderr='',stdout='The project is already at the maximum number of containers: 2'),
                 SimpleNamespace(returncode=0,stderr='')]
        with patch.object(incus,'project_instances',side_effect=[[{},{}],[],[]]), \
             patch.object(incus,'call',side_effect=results) as call, \
             patch.object(incus,'owned_instance',return_value={'expanded_devices':{'root':{'type':'disk'}}}), \
             patch.object(box.CANCELLED,'wait',return_value=False) as wait:
            incus.launch('configured:image','same-name')
        self.assertEqual(call.call_count,2)
        self.assertEqual([c.args[2] for c in call.call_args_list],['configured:same-name']*2)
        self.assertEqual([c.args for c in wait.call_args_list],[(2,),(2,)])

    def test_capacity_wait_cancels_and_non_capacity_errors_fail_without_retry(self):
        incus=box.Incus('configured','agentexpense-ci')
        with patch.object(incus,'project_instances',return_value=[{},{}]), \
             patch.object(box.CANCELLED,'wait',return_value=True),patch.object(incus,'call') as call:
            with self.assertRaisesRegex(box.BoxError,'Cancelled'):
                incus.launch('configured:image','owned')
        call.assert_not_called()
        with patch.object(incus,'project_instances',return_value=[]), \
             patch.object(incus,'call',return_value=SimpleNamespace(returncode=1,stderr='Project limits.memory configuration is invalid')) as call:
            with self.assertRaisesRegex(box.BoxError,'configuration is invalid'):
                incus.launch('configured:image','owned')
        call.assert_called_once()

    def test_stale_removal_rechecks_tag_and_liveness_before_stopping(self):
        incus=box.Incus('configured','agentexpense-ci')
        metadata=json.dumps({'token':'a'*32})
        row={'name':'owned','status':'Running','config':{'user.browser-box.owner':'a'*32,
                                                        'user.browser-box.controller':metadata}}
        with patch.object(incus,'instances',return_value=[row]),patch.object(box,'owner_status',return_value='alive'),patch.object(incus,'call') as command:
            with self.assertRaisesRegex(box.BoxError,'preserving'):
                incus.remove_stale('owned','a'*32,metadata)
        command.assert_not_called()
        with patch.object(incus,'instances',side_effect=[[row],[],[]]),patch.object(box,'owner_status',return_value='dead'),patch.object(incus,'call') as command:
            incus.remove_stale('owned','a'*32,metadata)
        self.assertEqual(command.call_args.args,('stop','configured:owned','--force'))

    def test_prune_refuses_a_project_with_instances(self):
        incus=box.Incus('configured','agentexpense-ci')
        with patch.object(incus,'project_instances',return_value=[{'name':'unknown-owner'}]),patch.object(incus,'call') as command:
            with self.assertRaisesRegex(box.BoxError,'idle project'):
                incus.prune_images('browser-box-'+'a'*24)
        command.assert_not_called()

    def test_prune_deletes_only_exact_provenance_checked_obsolete_alias(self):
        incus=box.Incus('configured','agentexpense-ci')
        current='browser-box-'+'a'*24
        old='browser-box-'+'b'*24
        def image(alias,proven=True,extra=False):
            return {'fingerprint':alias,'aliases':[{'name':alias}]+([{'name':'shared'}] if extra else []),
                    'properties':{'user.browser-box.hash':alias.removeprefix('browser-box-')} if proven else {}}
        images=[image(current),image(old),image('browser-box-'+'c'*24,False),image('browser-box-'+'d'*24,extra=True)]
        def call(*args,**kwargs):
            if args[:2]==('image','list'):
                return SimpleNamespace(stdout=json.dumps(images if args[3]=='browser-box-' else [images[1]]))
            return SimpleNamespace(stdout='')
        with patch.object(incus,'project_instances',return_value=[]),patch.object(incus,'call',side_effect=call) as command:
            incus.prune_images(current)
        deletions=[c.args for c in command.call_args_list if c.args[:2]==('image','delete')]
        self.assertEqual(deletions,[('image','delete','configured:'+old)])


if __name__=='__main__': unittest.main()
