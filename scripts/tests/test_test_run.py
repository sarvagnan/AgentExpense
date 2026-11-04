"""Owned-root leak check; it never inventories other sessions' /tmp files."""
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
import unittest
import shutil

APPLICATION=Path(__file__).resolve().parents[2]


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

    def test_server_node_and_browser_failure_and_signal_remove_owned_roots(self):
        python=APPLICATION/'review-dashboard/server/.venv/bin/python'
        self.probe([str(python),'scripts/tests/server_cleanup_probe.py'])
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
