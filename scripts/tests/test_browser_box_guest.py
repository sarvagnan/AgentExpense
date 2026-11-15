"""Exercise the real detached supervisor with shell doubles, never a browser."""
import json
from itertools import chain, repeat
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from test_browser_box import box
from browser_box_lifecycle import process_identity
import guest_case
if os.environ.get('BROWSER_BOX_SLOW_TESTS') != '1':
    # Starts real controller, guest and client processes; make test-browser-box sets this.
    raise unittest.SkipTest('slow browser-box process tests run with make test-browser-box')


class GuestSupervisorTests(unittest.TestCase):
    def setUp(self):
        scratch=Path(os.environ.get('EXPENSES_TEST_TMP','/tmp/agentexpense-browser-box'))
        scratch.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.script=self.root/'case.sh'
        self.collector=self.root/'collect.py';self.collector.write_text('pass\n')
        self.addCleanup(self.stop_guest)

    def command(self,action,timeout=30):
        return [sys.executable,'-B',str(box.TOOLS/'guest_case.py'),action,'--root',str(self.runtime),
                '--script',str(self.script),'--collector',str(self.collector),'--timeout',str(timeout)]

    def read_status(self):
        result=subprocess.run(self.command('status'),text=True,capture_output=True,check=True,timeout=5)
        return json.loads(result.stdout)

    def finished(self):
        deadline=time.monotonic()+9
        while time.monotonic()<deadline:
            state=self.read_status()
            if state['state']=='finished':return state
            time.sleep(.05)
        self.fail('Guest did not finish within its offline watchdog bound')

    def stop_guest(self):
        subprocess.run(self.command('stop'),capture_output=True,timeout=5)
        try:pid=json.loads((self.runtime/'started.json').read_text())['supervisor_pid']
        except FileNotFoundError:return
        deadline=time.monotonic()+6
        while process_identity(pid) and time.monotonic()<deadline:time.sleep(.05)
        if process_identity(pid):os.kill(pid,signal.SIGKILL)

    def test_launch_is_detached_idempotent_and_output_never_uses_exec_pipe(self):
        self.script.write_text('touch '+shlex.quote(str(self.runtime/'test.started'))+'\n'
                               'echo start >> '+shlex.quote(str(self.runtime/'starts'))+'\n'
                               +shlex.quote(sys.executable)+" -c 'print(\"x\"*2000000)'\nsleep .2\n")
        first=subprocess.run(self.command('launch'),capture_output=True,text=True,check=True,timeout=2)
        second=subprocess.run(self.command('launch'),capture_output=True,text=True,check=True,timeout=2)
        self.assertEqual(json.loads(first.stdout)['supervisor_pid'],json.loads(second.stdout)['supervisor_pid'])
        self.assertEqual(self.finished()['outcome'],'passed')
        self.assertEqual((self.runtime/'starts').read_text(),'start\n')
        self.assertGreater((self.runtime/'run.log').stat().st_size,2000000)

    def test_guest_timeout_ends_a_case_without_any_controller(self):
        self.script.write_text('touch '+shlex.quote(str(self.runtime/'test.started'))+"\ntrap '' TERM\nsleep 30\n")
        started=time.monotonic()
        subprocess.run(self.command('launch',timeout=.3),capture_output=True,check=True,timeout=2)
        result=self.finished()
        self.assertEqual((result['outcome'],result['exit']),('infrastructure',124))
        self.assertLess(time.monotonic()-started,7)

    def test_guest_stop_preserves_log_and_never_waits_for_test_completion(self):
        self.script.write_text('touch '+shlex.quote(str(self.runtime/'test.started'))+'\necho partial\nsleep 30\n')
        subprocess.run(self.command('launch'),capture_output=True,check=True,timeout=2)
        deadline=time.monotonic()+3
        while not (self.runtime/'test.started').exists() and time.monotonic()<deadline:time.sleep(.02)
        started=time.monotonic()
        for _ in range(2):subprocess.run(self.command('stop'),capture_output=True,check=True,timeout=2)
        self.assertEqual(self.finished()['outcome'],'stopped')
        self.assertLess(time.monotonic()-started,7)
        self.assertIn('partial',(self.runtime/'run.log').read_text())

    def test_stop_during_guest_collection_keeps_completed_outcome_and_exit(self):
        for code in (0,1):
            with self.subTest(exit=code):
                root=self.runtime/str(code);root.mkdir()
                script=root/'case.sh';script.write_text('touch '+shlex.quote(str(root/'test.started'))+f'\nexit {code}\n')
                collector=root/'collector.py'
                collector.write_text('from pathlib import Path\nimport time\nr=Path(__file__).parent\n'
                    'while not (r/"release").exists():time.sleep(.02)\n'
                    '(r/"archived-exit").write_text((r/"exit").read_text())\n')
                child=subprocess.Popen([sys.executable,'-B',str(box.TOOLS/'guest_case.py'),'run','--root',str(root),
                        '--script',str(script),'--collector',str(collector),'--timeout','20'])
                try:
                    deadline=time.monotonic()+4
                    while time.monotonic()<deadline:
                        if (root/'status.json').exists() and json.loads((root/'status.json').read_text())['state']=='collecting':break
                        time.sleep(.02)
                    (root/'stop.requested').touch();(root/'release').touch();child.wait(timeout=4)
                    status=json.loads((root/'status.json').read_text())
                    self.assertEqual((status['outcome'],status['exit']),('passed' if code==0 else 'failed',code))
                    self.assertEqual((root/'archived-exit').read_text().strip(),str(code))
                finally:
                    if child.poll() is None:child.kill();child.wait()

    def test_normal_guest_collection_can_outlast_the_old_fifteen_second_cap(self):
        self.script.write_text('touch '+shlex.quote(str(self.runtime/'test.started'))+'\nexit 0\n')
        self.collector.write_text('import time\nfrom pathlib import Path\ntime.sleep(16)\n'
                                  'Path(__file__).with_name("collector-complete").touch()\n')
        child=subprocess.Popen(self.command('run',timeout=35))
        try:
            child.wait(timeout=25)
            result=self.read_status()
            self.assertEqual((result['outcome'],result['exit']),('passed',0))
            self.assertNotIn('collection_error',result)
            self.assertTrue((self.root/'collector-complete').exists())
        finally:
            if child.poll() is None:child.terminate();child.wait(timeout=5)

    def test_supervisor_death_during_collection_preserves_frozen_test_result(self):
        child=subprocess.Popen([sys.executable,'-B','-c','pass']);child.wait()
        (self.runtime/'status.json').write_text(json.dumps({'state':'collecting','outcome':'failed',
                                         'exit':1,'supervisor_pid':child.pid}))
        status=guest_case.status(self.runtime)
        self.assertEqual((status['outcome'],status['exit']),('failed',1))
        self.assertIn('collection_error',status)

    def test_watchdog_signals_reparented_descendants_but_preserves_reused_pids(self):
        # /proc can change a descendant's parent to PID 1 after TERM. Its
        # birth stays stable; a reused PID's birth does not.
        original={100:(1,'root'),200:(100,'child'),201:(100,'old')}
        current={200:(1,'child'),201:(1,'new')}
        child=unittest.mock.Mock(pid=100)
        child.poll.return_value=0
        with patch.object(guest_case,'process_table',side_effect=chain([original],repeat(current))), \
             patch.object(guest_case.os,'killpg'),patch.object(guest_case.os,'kill') as kill:
            guest_case.terminate_case(child)
        kill.assert_any_call(200,signal.SIGTERM)
        kill.assert_any_call(200,signal.SIGKILL)
        self.assertFalse(any(call.args[0]==201 for call in kill.call_args_list))
