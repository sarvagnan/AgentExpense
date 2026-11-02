"""All adapter protocols and APIs tested offline with disposable /tmp state."""
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from jsonschema import ValidationError

from adapters import get_adapter,require_isolation,status_details
from adapters.base import AdapterError,LookupTools,Task
from adapters.cli import inspect_inventory,home_problems,child_environment
from agent import run,AgentFailure
from agent_check import fixture,OfflineFailure,PolicyFailure
from agent_gate import AgentGate,fingerprint
from settings import Settings,APPLICATION

ANSWER={'status':'not_expense','question':None,'entry':None,'new_accounts':[],
        'doubts':[],'attachment_names':[],'explanation':'Fictional offline document'}
SCHEMA={'type':'object','additionalProperties':False,'required':['value'],'properties':{'value':{'type':'string'}}}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/tmp',prefix='expense-adapter-test-');self.addCleanup(self.tmp.cleanup)
        self.work=Path(self.tmp.name).resolve();ledger,old,self.account=fixture(self.work)
        (ledger/'AGENTS.md').write_text('## Before adding\nFictional policy\n## Rebuild and verify\n')
        self.settings=Settings(ledger=ledger,old_ledger=old,state_dir=self.work/'state',model='anthropic/fixture',effort='high',timeout=5)
        self.config=self.work/'fake.json'
        self.fake=self.work/'fake-agent';self.fake.write_text('#!'+sys.executable+'\n'+Path(__file__).with_name('fake_agent.py').read_text().split('\n',1)[1]);self.fake.chmod(0o755)
        self.env=patch.dict(os.environ,{'EXPENSES_FAKE_CONFIG':str(self.config),'OFFLINE_API_KEY':'fictional-secret-token'})
        self.env.start();self.addCleanup(self.env.stop)

    def adapter(self,name,**values):
        self.config.write_text(json.dumps({'adapter':name,'protocol_module':str(Path(__file__).with_name('fake_protocols.py')),
                                          'steps':[{'answer':{'value':'ok'}}],**values}))
        return get_adapter(replace(self.settings,adapter=name,agent_binary=str(self.fake),allow_unisolated=True))

    def task(self,adapter,*,images=(),schema=SCHEMA,cancel=None,seconds=5):
        path=self.work/('task-'+adapter.describe().id);path.mkdir(exist_ok=True)
        return Task('Fictional task',tuple(images),LookupTools.for_settings(adapter.settings),schema,
                    time.monotonic()+seconds,cancel or threading.Event(),path)

    def test_all_cli_protocols_images_usage_lookup_validation_and_own_homes(self):
        image=self.work/'image.png';image.write_bytes(b'fictional-image-bytes')
        for name in ('claude','pi','opencode'):
            with self.subTest(adapter=name):
                adapter=self.adapter(name)
                adapter.offline_check(self.task(adapter).work)
                self.assertTrue(adapter.signed_in(self.work))
                result=adapter.run(self.task(adapter,images=[image]))
                self.assertEqual(result.answer,{'value':'ok'});self.assertEqual(sum(result.usage.values()),168)
                self.assertEqual(result.lookups[0]['tool'],'list_accounts');self.assertGreater(result.duration,0)
                call=json.loads(self.config.with_suffix('.calls').read_text().splitlines()[-1])
                self.assertEqual(call['home'],str(adapter.settings.agent_home));self.assertNotEqual(call['cwd'],str(adapter.settings.ledger))
                if name=='claude':self.assertEqual(call['input']['message']['content'][1]['source']['type'],'base64')
                if name=='pi':self.assertEqual(call['input']['images'][0]['mimeType'],'image/png')
                if name=='opencode':
                    self.assertIn(str(image),call['argv'])
                    with_progress=self.adapter(name,intermediate_text=True)
                    self.assertEqual(with_progress.run(self.task(with_progress)).answer,{'value':'ok'})
                for step in ({'kind':'exit'},{'kind':'noanswer'},{'kind':'invalid'},{'kind':'error'},{'answer':{'value':3}}):
                    adapter=self.adapter(name,steps=[step])
                    with self.assertRaises((ValueError,ValidationError)) as error:adapter.run(self.task(adapter))
                    self.assertNotIn('PRIVATE',str(error.exception))
                    if step.get('kind') in ('invalid','noanswer','exit'):self.assertIn('bad-answer stderr',getattr(error.exception,'stderr_tail',''))
                    if step.get('kind')=='error':self.assertTrue(getattr(error.exception,'lookups',[]),'Retain successful/failed lookups before the provider error')
                adapter=self.adapter(name,extra_tool=True)
                with self.assertRaisesRegex(ValueError,'extra tools|outside the lookup'):adapter.run(self.task(adapter))

    def test_cli_deadline_and_cancellation_cleanup_and_pi_abort(self):
        from adapters.process import kill
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],cwd=self.work,start_new_session=True)
        try:
            with patch('adapters.process.os.killpg',side_effect=PermissionError('group teardown race')):
                kill(child)
            self.assertIsNotNone(child.wait(timeout=3))
        finally:
            if child.poll() is None:child.kill()
            child.wait()
        for name in ('claude','pi','opencode'):
            for cancellation in (False,True):
                with self.subTest(adapter=name,cancel=cancellation):
                    self.config.with_suffix('.started').unlink(missing_ok=True)
                    adapter=self.adapter(name,steps=[{'kind':'timeout'}]);stop=threading.Event()
                    # Start the tested one-second deadline only after the fake
                    # protocol is ready. Process startup has its separate bound;
                    # host contention must not turn this into a pre-launch test.
                    task=SimpleNamespace(**vars(self.task(adapter,cancel=stop,seconds=self.settings.timeout)))
                    task.remaining=lambda:Task.remaining(task)
                    errors=[];spawned=[];original_popen=subprocess.Popen
                    def spawn(*args,**kwargs):
                        process=original_popen(*args,**kwargs);spawned.append(process);return process
                    def invoke():
                        with patch('adapters.cli.subprocess.Popen',side_effect=spawn):
                            try:adapter.run(task)
                            except Exception as exc:errors.append(exc)
                    thread=threading.Thread(target=invoke);thread.start()
                    # Wait on an explicit process-start marker, not a startup delay.
                    deadline=time.monotonic()+3
                    while not self.config.with_suffix('.started').exists() and time.monotonic()<deadline:threading.Event().wait(.005)
                    try:
                        self.assertTrue(self.config.with_suffix('.started').exists())
                        pid=int(self.config.with_suffix('.started').read_text())
                    except BaseException:
                        stop.set();thread.join(4)
                        raise
                    task.deadline=time.monotonic()+1
                    if cancellation:stop.set()
                    thread.join(4);self.assertFalse(thread.is_alive());self.assertTrue(errors)
                    self.assertEqual(len(spawned),1);self.assertEqual(spawned[0].pid,pid)
                    # A numeric PID can be reused by concurrent test commands.
                    # The exact child handle proves termination and reaping.
                    self.assertIsNotNone(spawned[0].returncode)
                    self.assertEqual(spawned[0].wait(timeout=0),spawned[0].returncode)
                    if cancellation:self.assertIn('cancel',str(errors[0]).lower())
                    if name=='pi':self.assertEqual(json.loads(self.config.with_suffix('.abort').read_text().splitlines()[-1])['type'],'abort')

    def test_allowlists_reject_every_added_source_for_each_adapter(self):
        for name in ('claude','pi','opencode','openai','anthropic'):
            adapter=self.adapter(name) if name in ('claude','pi','opencode') else self.api_adapter(name,'https://example.invalid/v1')
            lookups=LookupTools.for_settings(self.settings)
            expected=adapter.expected_inventory(lookups) if hasattr(adapter,'expected_inventory') else adapter.inventory(lookups)
            inspect_inventory(expected,expected)
            for category in ('tools','mcp','instructions','skills','plugins','hooks','settings','extensions','prompts','themes','commands'):
                bad={**expected,category:[*expected[category],'forbidden']}
                with self.subTest(adapter=name,category=category),self.assertRaisesRegex(OfflineFailure,category):inspect_inventory(bad,expected)
        for name in ('claude','pi','opencode'):
            adapter=self.adapter(name);adapter.environment()
            for filename in ('AGENTS.md','CLAUDE.md','skills','plugins','hooks','settings.json','mcp.json'):
                path=adapter.settings.agent_home/'config'/filename;path.touch()
                with self.assertRaises(OfflineFailure):adapter.offline_check(self.work)
                path.unlink()

    def test_generated_cli_config_cannot_widen_command_or_permission_allowlists(self):
        for name in ('claude','pi','opencode'):
            adapter=self.adapter(name);original=adapter.config
            def widened(task):
                config=original(task)
                if name=='claude':config['mcpServers']['ledger']['command']='forbidden-command'
                elif name=='pi':config['command']=['forbidden-command']
                else:config['permissions'].append({'action':'shell','resource':'*','effect':'allow'})
                return config
            with self.subTest(adapter=name),patch.object(adapter,'config',side_effect=widened),self.assertRaises(OfflineFailure):
                adapter.offline_check(self.task(adapter).work)

    def test_selection_defaults_environment_local_and_custom_contract(self):
        with patch.dict(os.environ,{},clear=True),patch('settings.APPLICATION',self.work):
            selected=Settings.from_env();self.assertEqual(selected.adapter,'codex')
            (self.work/'local.json').write_text(json.dumps({'agent':{'adapter':'claude','model':'fixture','effort':'low'}}))
            self.assertEqual(Settings.from_env().model,'fixture');self.assertEqual(Settings.from_env().adapter,'claude')
            with patch.dict(os.environ,{'EXPENSES_AGENT':'pi','ADAPTER':'opencode','EXPENSES_AGENT_MODEL':'override'}):
                self.assertEqual(Settings.from_env().adapter,'pi');self.assertEqual(Settings.from_env().model,'override')
            (self.work/'local.json').write_text(json.dumps({'agent':{'adapter':'anthropic'}}))
            self.assertEqual(Settings.from_env().model,'claude-opus-5-5')
        custom=self.work/'custom.py';custom.write_text('from adapters.codex import Codex\nfrom dataclasses import replace\nclass Custom(Codex):\n def describe(self): return replace(super().describe(),id="custom",isolation=False)\ndef create_adapter(settings): return Custom(settings)\n')
        settings=replace(self.settings,adapter='custom',custom_adapter=str(custom))
        with self.assertRaises(PolicyFailure):require_isolation(get_adapter(settings),settings)
        require_isolation(get_adapter(settings),replace(settings,allow_unisolated=True))
        ledger_module=self.settings.ledger/'adapters/local.py';ledger_module.parent.mkdir()
        ledger_module.write_text(custom.read_text())
        registered=replace(settings,custom_adapter='ledger:adapters/local.py')
        self.assertEqual(registered.custom_adapter,str(ledger_module.resolve()))
        probe_settings=replace(registered,ledger=self.work/'different-probe-ledger')
        self.assertEqual(get_adapter(probe_settings).describe().id,'custom')
        minimal=self.work/'minimal.py'
        minimal.write_text('from adapters.base import Description, Result\nclass Minimal:\n def describe(self): return Description("minimal","Offline minimal","1",True,"Offline double")\n def run(self,task): return Result({"value":"ok"})\n def offline_check(self,work,*,stop=None): pass\n def live_probe(self,work,account,*,stop=None): pass\n def signed_in(self,work,*,stop=None): return True\n def login_command(self): return []\ndef create_adapter(settings): return Minimal()\n')
        minimal_settings=replace(self.settings,adapter='minimal',custom_adapter=str(minimal),state_dir=self.work/'minimal-state')
        minimal_gate=AgentGate(minimal_settings);self.addCleanup(minimal_gate.close)
        minimal_gate.require_ready();self.assertEqual(minimal_gate.status()['state'],'ready')
        custom.write_text(custom.read_text().replace('id="custom"','id="custom",interface_version=2'))
        with self.assertRaisesRegex(ValueError,'interface version'):get_adapter(settings)

    def test_gate_refuses_unisolated_and_caches_per_adapter_model_effort_flags(self):
        adapter=self.adapter('opencode');settings=replace(adapter.settings,allow_unisolated=False)
        gate=AgentGate(settings);self.addCleanup(gate.close)
        with self.assertRaisesRegex(ValueError,'allowUnisolated'):gate.require_ready()
        self.assertEqual(gate.status()['state'],'paused')
        self.assertFalse(self.config.with_suffix('.checks').exists())
        for name in ('claude','pi','opencode'):
            adapter=self.adapter(name);gate=AgentGate(adapter.settings);self.addCleanup(gate.close)
            gate.require_ready();self.assertEqual(gate.status()['state'],'ready')
            before=self.config.with_suffix('.checks').read_text().count('Isolation test')
            gate.require_ready();self.assertEqual(self.config.with_suffix('.checks').read_text().count('Isolation test'),before)
            first=fingerprint(adapter.settings,'version')
            self.assertNotEqual(first,fingerprint(replace(adapter.settings,effort='low'),'version'))
            self.assertNotEqual(first,fingerprint(replace(adapter.settings,model='another'),'version'))
            details=status_details(adapter.settings);self.assertEqual(details['adapter']['id'],name)
            self.assertIn('ADAPTER='+name,details['login_command'])
            if name=='opencode':self.assertTrue(details['isolation_warning'])
            data=json.loads(self.config.read_text());data['signed_out']=True;self.config.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'ADAPTER='+name):gate.require_ready()
            self.assertEqual(gate.status()['state'],'signed out')

    def test_all_cli_adapters_use_shared_production_runner(self):
        draft=dict(files=[],text='Fictional document',questions=[],saved_at='2026-11-01T10:00:00+00:00')
        for name in ('claude','pi','opencode'):
            adapter=self.adapter(name,steps=[{'answer':ANSWER}])
            with patch('agent_gate.get_gate') as gate:
                answer,record=run(adapter.settings,draft,self.work)
                gate.return_value.require_ready.assert_called_once()
            self.assertEqual(answer,ANSWER);self.assertEqual(record['adapter'],name);self.assertEqual(record['outcome'],'not_expense')

    def test_status_endpoint_reports_each_adapter_without_starting_a_read_check(self):
        from app import create_app
        from fastapi.testclient import TestClient
        for name in ('claude','pi','opencode'):
            with self.subTest(adapter=name):
                adapter=self.adapter(name)
                app=create_app(adapter.settings,start_worker=False)
                with TestClient(app) as client:
                    app.state.agent_gate.thread.join(5)
                    self.assertFalse(app.state.agent_gate.thread.is_alive())
                    with patch.object(app.state.agent_gate,'require_ready',side_effect=AssertionError('Read started a check')):
                        status=client.get('/api/agent-status').json()
                    self.assertEqual(status['state'],'ready');self.assertEqual(status['adapter']['id'],name)
                    self.assertEqual(status['model'],self.settings.model);self.assertIn('ADAPTER='+name,status['login_command'])
                    self.assertEqual(status['adapter']['isolation'],name!='opencode')
        settings=replace(self.settings,adapter='openai',model='fixture')
        # An incomplete machine configuration must remain a readable unavailable
        # state, rather than making the read-only status endpoint return 500.
        app=create_app(settings,start_worker=False)
        with TestClient(app) as client:
            app.state.agent_gate.thread.join(5)
            status=client.get('/api/agent-status').json()
            self.assertEqual(status['state'],'unavailable');self.assertEqual(status['adapter']['id'],'openai')

    def test_worker_shutdown_cancels_and_preserves_fifo_draft_for_restart(self):
        from app import create_app
        from fastapi.testclient import TestClient
        adapter=self.adapter('pi',steps=[{'kind':'timeout'}])
        app=create_app(replace(adapter.settings,dev=True))
        with TestClient(app) as client:
            app.state.agent_gate.thread.join(5)
            self.assertEqual(app.state.agent_gate.status()['state'],'ready')
            self.config.with_suffix('.started').unlink(missing_ok=True)
            response=client.post('/api/drafts',json={'text':'Fictional offline input'})
            self.assertEqual(response.status_code,201,response.text)
            identity=response.json()['id']
            self.assertEqual(client.post('/api/drafts/'+identity+'/save').status_code,200)
            deadline=time.monotonic()+5
            while not self.config.with_suffix('.started').exists() and time.monotonic()<deadline:
                threading.Event().wait(.005)
            self.assertTrue(self.config.with_suffix('.started').exists())
        draft=app.state.drafts.get(identity)
        self.assertEqual(draft['state'],'queued');self.assertIsNone(draft['error'])
        self.assertEqual(len(draft['runs']),1);self.assertEqual(draft['runs'][0]['outcome'],'interrupted');self.assertEqual(draft.get('answer_failures',0),0)
        self.assertFalse(draft.get('audit_pending'))
        self.assertFalse(app.state.worker.thread.is_alive())

    def api_adapter(self,name,url,**options):
        return get_adapter(replace(self.settings,adapter=name,api_options=json.dumps({'baseUrl':url,'keyEnv':'OFFLINE_API_KEY',**options})))

    @contextmanager
    def http_server(self,shape,*,mode='normal'):
        received=[];started=threading.Event();release=threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));received.append((self.path,body,dict(self.headers)))
                started.set()
                if mode=='slow':release.wait(3)
                if mode=='transport' and len(received)==1:
                    self.connection.close();return
                if mode=='rate_always' or mode=='rate' and len(received)==1:
                    self.send_response(429);self.end_headers();return
                if mode=='auth':
                    self.send_response(401);self.end_headers();self.wfile.write(b'fictional-secret-token');return
                is_tool_result=any(m.get('role')=='tool' or any(isinstance(c,dict) and c.get('type')=='tool_result' for c in (m.get('content') if isinstance(m.get('content'),list) else [])) for m in body['messages'])
                if mode=='loop':is_tool_result=False
                tool='shell' if mode=='unknown' else 'list_accounts'
                if shape=='openai':
                    message={'role':'assistant','content':json.dumps({'value':'ok'})} if is_tool_result else {'role':'assistant','content':None,'tool_calls':[{'id':'lookup1','type':'function','function':{'name':tool,'arguments':'{}'}}]}
                    answer={'choices':[{'message':message,'finish_reason':'stop' if is_tool_result else 'tool_calls'}],'usage':{'prompt_tokens':10,'completion_tokens':5}}
                else:
                    content=[{'type':'text','text':json.dumps({'value':'ok'})}] if is_tool_result else [{'type':'tool_use','id':'lookup1','name':tool,'input':{}}]
                    answer={'content':content,'stop_reason':'end_turn' if is_tool_result else 'tool_use','usage':{'input_tokens':10,'output_tokens':5}}
                if mode=='tokens':answer['usage']={'input_tokens':1000001}
                if mode=='refusal':
                    if shape=='openai':answer['choices'][0]['message']['refusal']='fictional-secret-token'
                    else:answer['stop_reason']='refusal'
                if mode=='malformed':
                    if shape=='openai':answer['choices'][0]['message']={'role':'assistant','content':'fictional-secret-token'}
                    else:answer['content']=[{'type':'text','text':'fictional-secret-token'}]
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                try:self.wfile.write(json.dumps(answer).encode())
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:yield 'http://127.0.0.1:'+str(server.server_port)+'/v1',received,started,release
        finally:release.set();server.shutdown();server.server_close();thread.join()

    def test_direct_api_tool_loops_schemas_images_usage_retry_and_secret_redaction(self):
        for name in ('openai','anthropic'):
            for mode in ('normal',):
                with self.subTest(adapter=name,mode=mode),self.http_server(name,mode=mode) as (url,received,_,_):
                    adapter=self.api_adapter(name,url);image=self.work/'test.png';image.write_bytes(b'fictional')
                    adapter.offline_check(self.work);self.assertEqual(received,[])
                    result=adapter.run(self.task(adapter,images=[image]))
                    self.assertEqual(result.answer,{'value':'ok'});self.assertEqual(len(result.lookups),1);self.assertEqual(sum(result.usage.values()),30)
                    self.assertEqual(len(received),3 if mode=='rate' else 2)
                    first=received[0][1];self.assertEqual(len(first['tools']),4)
                    self.assertIn('output_config' if name=='anthropic' else 'response_format',first)
                    self.assertIn('image',json.dumps(first));self.assertNotIn('fictional-secret-token',json.dumps(result.metadata()))
            for mode,reason in (('unknown','allow-list'),('auth','authentication'),('rate','usage limit')):
                with self.http_server(name,mode=mode) as (url,_,_,_):
                    adapter=self.api_adapter(name,url)
                    with self.assertRaisesRegex(AdapterError,reason) as raised:adapter.run(self.task(adapter))
                    self.assertNotIn('fictional-secret-token',str(raised.exception))
            for limits,reason in (({'maxTools':1,'maxTurns':1},'turn limit'),({'maxTokens':1},'context size|token limit')):
                with self.http_server(name) as (url,_,_,_):
                    adapter=self.api_adapter(name,url,**limits)
                    with self.assertRaisesRegex(AdapterError,reason):adapter.run(self.task(adapter))

    def test_direct_api_transport_exhaustion_tool_token_refusal_and_malformed_bounds(self):
        for name in ('openai','anthropic'):
            for mode,options,reason in [('rate_always',{'retries':1},'usage limit'),
                    ('loop',{'maxTools':1},'tool-call limit'),('tokens',{},'token limit'),
                    ('refusal',{},'refused'),('malformed',{},'contents withheld')]:
                with self.subTest(adapter=name,mode=mode),self.http_server(name,mode=mode) as (url,received,_,_):
                    adapter=self.api_adapter(name,url,**options)
                    with self.assertRaises(AdapterError) as raised:adapter.run(self.task(adapter))
                    self.assertIn(reason,str(raised.exception));self.assertNotIn('fictional-secret-token',str(raised.exception))
                    if mode=='rate_always':self.assertEqual(len(received),1);self.assertEqual(raised.exception.kind,'usage_limit')
            with self.http_server(name,mode='transport') as (url,received,_,_):
                with self.assertRaises(AdapterError) as raised:self.api_adapter(name,url).run(self.task(self.api_adapter(name,url)))
                self.assertEqual(raised.exception.kind,'transient');self.assertEqual(len(received),1)
        key=self.work/'api-key.txt';key.write_text('fictional-file-key')
        adapter=self.api_adapter('openai','https://example.invalid/v1',keyEnv='MISSING_OFFLINE_KEY',keyFile=str(key))
        self.assertEqual(adapter.key(),'fictional-file-key');self.assertTrue(adapter.signed_in(self.work))
        self.assertNotIn('fictional-file-key',json.dumps(adapter.fingerprint()))

    def test_direct_api_deadline_and_cancellation_interrupt_inflight_http(self):
        for name in ('openai','anthropic'):
            for cancellation in (True,False):
                with self.http_server(name,mode='slow') as (url,_,started,release):
                    adapter=self.api_adapter(name,url);stop=threading.Event();errors=[];task=self.task(adapter,cancel=stop,seconds=1)
                    def invoke():
                        try:adapter.run(task)
                        except Exception as exc:errors.append(exc)
                    thread=threading.Thread(target=invoke);thread.start();self.assertTrue(started.wait(2))
                    if cancellation:stop.set()
                    thread.join(2);self.assertFalse(thread.is_alive());self.assertTrue(errors)
                    self.assertIn('cancel' if cancellation else 'deadline',str(errors[0]).lower());release.set()


if __name__=='__main__':unittest.main()
