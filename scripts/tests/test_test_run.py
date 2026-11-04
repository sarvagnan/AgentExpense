"""Owned-root leak check; it never inventories other sessions' /tmp files."""
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock
import shutil
import contextlib
import io
import json
import tempfile

APPLICATION=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(APPLICATION/'scripts'))
import test_run


class TestRunCleanup(unittest.TestCase):
    def probe(self, command, *, browser=None, keep=False, kept_markers=1):
        env={**os.environ}
        for key in ('AGENTEXPENSE_TEST_ROOT','EXPENSES_TEST_TMP','EXPENSES_TEST_ORACLE_CACHE','KEEP_ARTIFACTS'):
            env.pop(key,None)
        if browser:
            env.update(PERSONA='empty',ENGINES='chromium',THEMES='light',BROWSER_CASE='light-412',BROWSER_FOCUS='cleanup',BROWSER_CLEANUP_PROBE=browser,CAPTURE='0')
        if keep:env['KEEP_ARTIFACTS']='1'
        child=subprocess.Popen([str(APPLICATION/'scripts/test-run.sh'),*command],cwd=APPLICATION,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0,start_new_session=True)
        root=None;failure=[];output=[];deadline=time.monotonic()+180;terminated=False
        selector=selectors.DefaultSelector();selector.register(child.stdout,selectors.EVENT_READ)
        pending=b''
        try:
            while True:
                if time.monotonic()>deadline: self.fail('Cleanup probe did not finish: '+''.join(output)[-3000:])
                events=selector.select(.2)
                if not events:
                    if child.poll() is not None: break
                    continue
                chunk=os.read(child.stdout.fileno(),65536)
                if not chunk: break
                pending+=chunk
                while b'\n' in pending:
                    raw,pending=pending.split(b'\n',1);line=raw.decode(errors='replace')+'\n';output.append(line)
                    if line.startswith('Test run root: '):root=Path(line.split(': ',1)[1].strip())
                    if line.startswith('Failing traces: '):failure.append(Path(line.split(': ',1)[1].strip()))
                    if browser=='term' and 'CLEANUP_BROWSER_READY' in line:
                        os.kill(child.pid,signal.SIGTERM);terminated=True
            code=child.wait(timeout=10)
            self.assertIsNotNone(root,''.join(output))
            if keep:
                self.assertTrue(root.exists());self.assertEqual(len(list((root/'kept').rglob('keep-marker.txt'))),kept_markers)
            else:self.assertFalse(root.exists(),''.join(output)[-4000:])
            if browser=='fail':
                self.assertNotEqual(code,0);self.assertTrue(failure,''.join(output)[-4000:])
            elif browser=='term':
                self.assertTrue(terminated);self.assertNotEqual(code,0)
            else:self.assertEqual(code,0,''.join(output)[-4000:])
            for folder in failure:
                self.assertTrue(folder.name.startswith('agentexpense-failures-'))
                self.assertTrue(list(folder.iterdir()));self.assertTrue(all(p.suffix=='.zip' and p.is_file() for p in folder.iterdir()))
            return code
        finally:
            selector.close()
            if child.poll() is None:os.killpg(child.pid,signal.SIGKILL);child.wait()
            child.stdout.close()
            # These artificial failures belong to this test, not another run.
            for folder in failure:shutil.rmtree(folder)
            if keep and root and root.exists():shutil.rmtree(root)

    def test_keep_artifacts_retains_python_and_node_helper_outputs(self):
        python=APPLICATION/'review-dashboard/server/.venv/bin/python'
        self.probe([str(python),'scripts/tests/keep_probe.py'],keep=True)
        node=subprocess.check_output([sys.executable,str(APPLICATION/'scripts/node_toolchain.py')],text=True).strip()
        script="import './scripts/test-run.mjs';import{mkdtempSync,writeFileSync,rmSync}from'node:fs';import{rm}from'node:fs/promises';const make=()=>{const p=mkdtempSync(process.env.EXPENSES_TEST_TMP+'/keep-node-');writeFileSync(p+'/keep-marker.txt','fictional');return p};rmSync(make(),{recursive:true});await rm(make(),{recursive:true});"
        self.probe([str(Path(node)/'node'),'--input-type=module','-e',script],keep=True,kept_markers=2)

    def test_server_worker_suite_removes_owned_root(self):
        python=APPLICATION/'review-dashboard/server/.venv/bin/python'
        self.probe([str(python),'scripts/tests/server_cleanup_probe.py'])

    def test_node_and_browser_failure_and_signal_remove_owned_roots(self):
        node=subprocess.check_output([sys.executable,str(APPLICATION/'scripts/node_toolchain.py')],text=True).strip()
        self.probe([str(Path(node)/'node'),'--test','review-dashboard/tests/retention.test.mjs'])
        self.probe(['make','browser-tests'],browser='fail')
        self.probe(['make','browser-tests'],browser='term')
        # A late child was absent from the supervisor's first PID snapshot. It
        # ignores TERM and keeps writing cache-like files after its parent exits.
        # The isolated process-group fence must stop it before root removal.
        writer="const fs=require('node:fs'),p=process.env.TMPDIR+'/late-writer';fs.mkdirSync(p);process.on('SIGTERM',()=>{});setInterval(()=>fs.writeFileSync(p+'/'+Date.now(),'fictional'),10)"
        script="const {spawn}=require('node:child_process');process.on('SIGTERM',()=>{spawn(process.execPath,['-e',"+repr(writer)+"],{stdio:'ignore'});setTimeout(()=>process.exit(0),100)});console.log('CLEANUP_BROWSER_READY');setInterval(()=>{},1000)"
        self.probe([str(Path(node)/'node'),'-e',script],browser='term')


class SignalGroupTests(unittest.TestCase):
    def test_exited_unreaped_group_is_already_gone(self):
        # Every member has exited but is not yet reaped. macOS answers killpg
        # with EPERM here; cleanup must treat the group as gone.
        child=subprocess.Popen([sys.executable,'-c','pass'],start_new_session=True)
        try:
            os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOWAIT)
            self.assertFalse(test_run.group_has_live_members(child.pid))
            with tempfile.TemporaryDirectory() as root:
                Path(root,'process-group.json').write_text(json.dumps(dict(pgid=child.pid))+'\n')
                test_run.stop_group(root)
        finally:child.wait()
        self.assertFalse(test_run.signal_group(child.pid,0))

    def test_live_group_permission_failure_is_reported(self):
        with mock.patch.object(test_run.os,'killpg',side_effect=PermissionError(1,'Operation not permitted')):
            with mock.patch.object(test_run,'group_has_live_members',return_value=True):
                with self.assertRaises(PermissionError):test_run.signal_group(424242,signal.SIGTERM)
            with mock.patch.object(test_run,'group_has_live_members',return_value=False):
                self.assertFalse(test_run.signal_group(424242,signal.SIGTERM))
        with mock.patch.object(test_run.os,'killpg',side_effect=ProcessLookupError()):
            self.assertFalse(test_run.signal_group(424242,signal.SIGTERM))

    def test_live_member_is_detected(self):
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
        try:
            self.assertTrue(test_run.group_has_live_members(child.pid))
            self.assertTrue(test_run.signal_group(child.pid,0))
        finally:
            child.kill();child.wait()


class TestLedgerSelectionTests(unittest.TestCase):
    def select(self,application,environ):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):result=test_run.select_test_ledger(environ,application)
        return result,output.getvalue()

    def test_absent_default_ledger_selects_the_fictional_fixture_and_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            application=Path(folder)
            for environ in ({},{'LEDGER_DIR':str(application/'ledger')},{'LEDGER_DIR':''}):
                with self.subTest(environ=environ):
                    result,output=self.select(application,environ)
                    self.assertEqual(result,application/'fixtures/ledgers/asha')
                    self.assertEqual(environ['LEDGER_DIR'],str(application/'fixtures/ledgers/asha'))
                    self.assertEqual(output.splitlines(),['No ledger selected: tests use the fictional fixture fixtures/ledgers/asha (set LEDGER_DIR to choose another).'])
                    # A nested run inherits the selection without repeating the notice.
                    self.assertEqual(self.select(application,environ),(None,''))

    def test_selected_or_present_ledgers_are_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            application=Path(folder)
            other=str(application/'missing-elsewhere')
            environ={'LEDGER_DIR':other}
            self.assertEqual(self.select(application,environ),(None,''));self.assertEqual(environ,{'LEDGER_DIR':other})
            (application/'ledger').mkdir()
            for environ in ({},{'LEDGER_DIR':str(application/'ledger')}):
                before=dict(environ)
                self.assertEqual(self.select(application,environ),(None,''));self.assertEqual(environ,before)
