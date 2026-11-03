"""Durable outage episodes, exact consent and checked human additions."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from dataclasses import replace
import json
import threading
import unittest
from unittest.mock import patch

import test_adapters as protocols
from adapters.base import AdapterError
from adapters.codex import Codex
from agent_pause import AgentPause
from drafts import Drafts
from support import Fixture, ready
from proposal_consent import proposal_hash
from audit import records


class FailureProtocols(unittest.TestCase):
    setUp=protocols.AdapterTests.setUp
    adapter=protocols.AdapterTests.adapter
    task=protocols.AdapterTests.task
    api_adapter=protocols.AdapterTests.api_adapter
    http_server=protocols.AdapterTests.http_server

    def test_every_cli_keeps_error_events_even_on_successful_exit(self):
        for name in ('codex','claude','pi','opencode'):
            for kind,message in [('usage_limit','Usage limit reached'),('auth','Authentication expired'),('transient','Network connection failed'),('bad_answer','Unsupported response')]:
                with self.subTest(adapter=name,kind=kind):
                    step={'kind':'provider_error','error':{'message':message,'reset_at':'2026-12-01T10:00:00+00:00'}}
                    if name=='codex':
                        self.config.write_text(json.dumps({'steps':[step]}));adapter=Codex(replace(self.settings,codex=str(self.fake)))
                    else: adapter=self.adapter(name,steps=[step])
                    with self.assertRaises(AdapterError) as raised:adapter.run(self.task(adapter))
                    error=raised.exception
                    self.assertEqual(error.kind,kind);self.assertIn(message,json.dumps(error.errors));self.assertIn('fixture stderr',error.stderr_tail)
                    self.assertEqual(error.reset_at,'2026-12-01T10:00:00+00:00')
                    self.config.with_suffix('.count').unlink(missing_ok=True)

    def test_pi_error_message_survives_an_aborted_settled_event(self):
        adapter=self.adapter('pi',steps=[dict(kind='provider_error',error={'message':'Usage limit reached'},pi_message_error=True)])
        with self.assertRaises(AdapterError) as raised:adapter.run(self.task(adapter))
        self.assertEqual(raised.exception.kind,'usage_limit')
        self.assertIn('Usage limit reached',str(raised.exception.errors))

    def test_direct_failure_kinds(self):
        for name in ('openai','anthropic'):
            for mode,kind in [('rate_always','usage_limit'),('auth','auth')]:
                with self.subTest(adapter=name,kind=kind),self.http_server(name,mode=mode) as (url,_,_,_):
                    adapter=self.api_adapter(name,url,retries=0)
                    with self.assertRaises(AdapterError) as raised:adapter.run(self.task(adapter))
                    self.assertEqual(raised.exception.kind,kind)
        from adapters.failures import http_reset
        self.assertEqual(http_reset({'x-ratelimit-reset':'2026-12-01T10:00:00Z'}),'2026-12-01T10:00:00+00:00')
        self.assertIsNotNone(http_reset({'retry-after':'15'}))

    def test_http_status_transport_deadline_and_reset_diagnostics(self):
        import asyncio
        import httpx
        from adapters.failures import classify
        for event,kind in [({'name':'RateLimitError'},'usage_limit'),({'message':'Too many requests'},'usage_limit'),({'name':'ProviderAuthError'},'auth'),({'name':'ServiceUnavailableError'},'transient'),({'message':'Invalid answer','reset_at':1790000429},'bad_answer')]:
            self.assertEqual(classify(event),kind)
        async def probe(name,status):
            adapter=self.api_adapter(name,'http://127.0.0.1/v1',retries=0)
            def respond(request):
                if status=='transport': raise httpx.ConnectError('offline',request=request)
                return httpx.Response(status,json={'error':{'message':'Fictional provider diagnostic'}},headers={'retry-after':'30'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                with self.assertRaises(AdapterError) as raised:
                    await adapter.request(client,self.task(adapter),{}, {'Authorization':'Bearer fictional-secret-token'})
            error=raised.exception
            self.assertEqual(error.kind,'auth' if status in (401,403) else 'usage_limit' if status==429 else 'transient')
            if status==429:self.assertIsNotNone(error.reset_at)
            if status!='transport':self.assertIn('Fictional provider diagnostic',str(error.errors))
        for name in ('openai','anthropic'):
            for status in (401,403,429,500,503,'transport'):
                with self.subTest(adapter=name,status=status):asyncio.run(probe(name,status))


class FinishTests(Fixture):
    def test_pause_backoffs_restart_reset_and_auth_recheck(self):
        clock=[datetime(2026,11,2,tzinfo=timezone.utc)]
        pause=AgentPause(self.ledger,lambda:clock[0])
        for kind,delays in [('usage_limit',[900,1800,3600,3600]),('transient',[5,15,45,900,5])]:
            pause.success()
            for delay in delays:
                pause.fail(kind);self.assertTrue(pause.blocked())
                self.assertEqual(datetime.fromisoformat(pause.status()['resumes_at'])-clock[0],timedelta(seconds=delay))
                pause=AgentPause(self.ledger,lambda:clock[0]);self.assertTrue(pause.blocked())
                clock[0]+=timedelta(seconds=delay);self.assertFalse(pause.blocked())
        pause.fail('usage_limit',(clock[0]+timedelta(hours=2)).isoformat());self.assertEqual(datetime.fromisoformat(pause.status()['resumes_at'])-clock[0],timedelta(hours=2))
        pause.fail('auth');clock[0]+=timedelta(seconds=30)
        gate=unittest.mock.Mock();gate.require_ready.side_effect=ValueError('not ready')
        self.assertTrue(pause.blocked(gate));clock[0]+=timedelta(seconds=30);gate.require_ready.side_effect=None
        self.assertFalse(pause.blocked(gate));self.assertIsNone(pause.status())

    def test_claim_conflicts_renewal_restart_release_expiry_and_contents(self):
        clock=[datetime(2026,11,2,tzinfo=timezone.utc)]
        with self.client(worker=False) as client:
            drafts=client.app.state.drafts;drafts.clock=lambda:clock[0]
            identity=self.create(client);self.save(client,identity)
            url='/api/drafts/'+identity
            claimed=client.post(url+'/claim',json={}).json();token=claimed['claim_token'];queued=claimed['draft']['queued_at']
            self.assertNotIn('token',client.get(url).json()['claim'])
            self.assertEqual(client.post(url+'/claim',json={}).status_code,409)
            self.assertEqual(client.post(url+'/hand-form',json={'claim_token':'wrong','proposal':ready()}).status_code,409)
            clock[0]+=timedelta(minutes=20)
            form=ready();response=client.post(url+'/hand-form',json={'claim_token':token,'proposal':form})
            self.assertTrue(response.json()['ok'],response.text)
            reboot=Drafts(self.ledger,clock=lambda:clock[0]);self.assertEqual(reboot.get(identity)['claim']['token'],token)
            draft=reboot.get(identity);draft['account_consent']={'old':True};reboot.put(draft)
            clock[0]+=timedelta(minutes=31);draft=reboot.get(identity)
            self.assertNotIn('claim',draft);self.assertNotIn('account_consent',draft);self.assertEqual(draft['hand_form'],form);self.assertEqual(draft['queued_at'],queued)
            token=client.post(url+'/claim',json={}).json()['claim_token']
            self.assertEqual(client.post(url+'/release',json={'claim_token':token}).status_code,200)
            self.assertEqual(drafts.get(identity)['hand_form'],form)
            draft=drafts.get(identity);draft['state']='working';drafts.put(draft)
            self.assertEqual(client.post(url+'/claim',json={}).status_code,409)

    def test_hand_finish_during_pause_commits_evidence_trailer_audit_and_consent(self):
        with self.client() as client:
            worker=client.app.state.worker;worker.pause.fail('usage_limit')
            identity=self.create(client);self.save(client,identity);url='/api/drafts/'+identity
            claimed=client.post(url+'/claim',json={}).json();token=claimed['claim_token']
            form=ready();checked=client.post(url+'/hand-form',json={'claim_token':token,'proposal':form}).json()
            self.assertTrue(checked['ok'],checked)
            body=dict(claim_token=token,proposal_hash=checked['proposal_hash'],approved_accounts=[])
            self.assertEqual(client.post(url+'/hand-add',json=body).status_code,409)
            body['approved_accounts']=form['new_accounts'];self.assertEqual(client.post(url+'/hand-add',json=body).status_code,200)
            draft=self.wait(client,identity,'added');self.assertEqual(self.calls(),[])
            self.assertEqual(draft['finished_by']['actor'],'user');self.assertEqual(len(draft['result']['sources']),1)
            self.assertIn('Added-by: user (finished by hand: usage limit)',self.jj('log','-r','@-','--no-graph','-T','description'))
            line=records(self.ledger)[0];self.assertEqual(line['account_consent']['proposal_hash'],proposal_hash(line['proposal']))
            self.assertEqual(line['finished_by']['actor'],'user');self.assertEqual(client.post(url+'/retry').status_code,409)
            self.assertIsNotNone(client.get('/api/health').json()['agent_pause'])
            from proposal_consent import validate_consent
            tampered=deepcopy(line);tampered['proposal']['entry']['description']='Changed'
            with self.assertRaises(ValueError):validate_consent(tampered)

    def test_new_root_question_exact_approval_no_second_agent_and_free_text_clears(self):
        form=ready();form['entry']['postings'][0]['account']='Income:Refunds';form['new_accounts']=['Income:Refunds']
        self.set_answers(form)
        with self.client() as client:
            identity=self.create(client);self.save(client,identity);url='/api/drafts/'+identity
            draft=self.wait(client,identity,'question');digest=draft['account_question']['proposal_hash']
            self.assertEqual(client.post(url+'/approve-accounts',json={'proposal_hash':'0'*64}).status_code,409)
            self.assertEqual(client.post(url+'/approve-accounts',json={'proposal_hash':digest}).status_code,200)
            self.wait(client,identity,'added');self.assertEqual(len(self.calls()),1)
        with self.client(worker=False) as client:
            identity=self.create(client);draft=client.app.state.drafts.get(identity)
            draft.update(state='question',account_question={'proposal_hash':'old'},account_proposal=form,account_consent={'old':True},questions=[dict(question='Create?',answer=None,asked_at='2026-11-02T00:00:00+00:00')])
            client.app.state.drafts.put(draft)
            self.assertEqual(client.post('/api/drafts/'+identity+'/answer',json={'answer':'Use the existing account'}).status_code,200)
            draft=client.app.state.drafts.get(identity);self.assertNotIn('account_consent',draft);self.assertNotIn('account_question',draft)

    def test_usage_outage_preserves_queue_order_and_repair_allowance(self):
        error={'kind':'provider_error','error':{'message':'Usage limit reached'}}
        self.set_answers(error)
        clock=[datetime(2026,11,2,tzinfo=timezone.utc)]
        with self.client() as client:
            worker=client.app.state.worker;worker.pause.clock=lambda:clock[0]
            ids=[self.create(client) for _ in range(3)]
            for identity in ids:self.save(client,identity)
            self.wait_condition(client,ids[0],lambda d: len(d['runs'])==1 and d['state']=='queued')
            self.assertEqual(len(self.calls()),1)
            queued=[client.app.state.drafts.get(i)['queued_at'] for i in ids]
            forms=[ready() for _ in ids]
            for i,form in enumerate(forms):form['entry']['description']=f'Fictional queued entry {i+1}'
            self.set_answers(*forms)
            clock[0]+=timedelta(minutes=15);worker.wake.set()
            for i,identity in enumerate(ids):
                draft=self.wait(client,identity,'added');self.assertEqual(draft['queued_at'],queued[i]);self.assertEqual(draft['check_errors'],[])
            self.assertIsNone(worker.pause.status())
            self.assertEqual(len(records(self.ledger)),3)

    def test_redundant_original_amount_normalises_and_account_errors_ask(self):
        form=ready();form['entry']['original_amount']={'amount':'100','commodity':'INR'};self.set_answers(form)
        with self.client() as client:
            identity=self.create(client);self.save(client,identity);draft=self.wait(client,identity,'added')
            self.assertTrue(draft['runs'][0]['normalisations'])
            self.assertNotIn('original-amount',json.loads((self.ledger/'data/expenses/2026.json').read_text())[0])

    def test_timeout_is_queued_once_without_repair_or_failure_audit(self):
        import subprocess
        self.set_answers({'kind':'timeout'})
        def timeout_after_start(process, timeout):
            while True:
                line=process.stdout.readline()
                if line.strip()=='FAKE_TIMEOUT_READY':raise subprocess.TimeoutExpired(process.args,timeout)
                if not line:self.fail('Fake exited before timeout boundary')
        with patch('agent.communicate',side_effect=timeout_after_start),self.client(timeout=20) as client:
            identity=self.create(client);self.save(client,identity)
            draft=self.wait_condition(client,identity,lambda d:d['state']=='queued' and len(d['runs'])==1)
            self.assertEqual(draft['runs'][0]['failure_kind'],'transient');self.assertEqual(draft['check_errors'],[])
            self.assertFalse(draft.get('audit_pending'));self.assertEqual(len(self.calls()),1)
            self.assertEqual(client.get('/api/health').json()['agent_pause']['attempt'],1)

    def test_account_failure_becomes_question_without_repair(self):
        form=ready();form['entry']['postings'][0]['account']='Unknown:Merchant';form['new_accounts']=['Unknown:Merchant'];self.set_answers(form)
        with self.client() as client:
            identity=self.create(client);self.save(client,identity);draft=self.wait(client,identity,'question')
            self.assertEqual(len(draft['runs']),1);self.assertEqual(draft['check_errors'],[])
            self.assertIn('kind',draft['questions'][-1]['question'])

    def test_statement_second_read_waits_and_resumes_while_parser_work_proceeds(self):
        from statements import Statements
        from statement_fixtures import statement_pdf,statement_answer
        from store import dump,load
        card='Liabilities:Plastic:Main'
        documents=load(self.ledger)
        documents[self.ledger/'data/accounts.json']['accounts'][card]=dict(kind='card',currency='INR',statement=dict(reader='example-card-pdf',identifier='5678'))
        dump(self.ledger/'data/accounts.json',documents[self.ledger/'data/accounts.json'])
        sessions=Statements(self.settings);sessions.agent_pause=AgentPause(self.ledger);sessions.agent_pause.fail('usage_limit')
        session=sessions.create(statement_pdf(),'Fictional.pdf')
        with patch('statement_readers.Reader.read',side_effect=ValueError('Second reading needed')),patch('agent.run') as run:
            sessions.work(threading.Event());self.assertEqual(sessions.get(session['id'])['state'],'agent_waiting');run.assert_not_called()
        # A different, readable file is parsed during the same pause.
        other=sessions.create(statement_pdf(),'Fictional-parser.pdf')
        sessions.work(threading.Event());self.assertEqual(sessions.get(other['id'])['state'],'found')
        sessions.agent_pause.success()
        record=dict(start='2026-11-02T00:00:00+00:00',end='2026-11-02T00:00:01+00:00',model='fake',effort='low',outcome='statement',usage={},lookups=[],error=None)
        with patch('statement_readers.Reader.read',side_effect=ValueError('Second reading needed')),patch('agent.run',return_value=(statement_answer(),record)) as run:
            sessions.work(threading.Event());self.assertEqual(sessions.get(session['id'])['state'],'found');run.assert_called_once()

    def test_invalid_hand_forms_persist_and_return_checks(self):
        with self.client(worker=False) as client:
            identity=self.create(client);self.save(client,identity);url='/api/drafts/'+identity
            token=client.post(url+'/claim',json={}).json()['claim_token']
            for form in ({},dict(entry=dict(postings=['invalid']))):
                response=client.post(url+'/hand-form',json=dict(claim_token=token,proposal=form))
                self.assertEqual(response.status_code,200,response.text);self.assertFalse(response.json()['ok'])
                self.assertEqual(client.app.state.drafts.get(identity)['hand_form'],form)

    def test_review_uses_the_same_fifo_during_pause(self):
        with self.client() as client:
            worker=client.app.state.worker;worker.pause.fail('usage_limit')
            def action(settings,body,**kwargs):
                return dict(thread=threading.current_thread().name,action=body['action'])
            with patch('review.apply',side_effect=action):
                result=client.post('/api/review/actions',json={'action':'merge'})
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json(),dict(thread='expense-drafts',action='merge'))

    def test_a_self_check_failure_can_pass_again_without_a_version_change(self):
        self.config.write_text(json.dumps({'extra_tool':'forbidden','steps':[{'answer':ready()}]}))
        with self.client(worker=False) as client:
            gate=client.app.state.agent_gate;gate.thread.join(10)
            self.assertEqual(gate.status()['state'],'paused')
            cached=json.loads((self.settings.state_dir/'agent-check.json').read_text())
            self.set_answers(ready())
            gate.require_ready(force=True)
            self.assertEqual(gate.status()['state'],'ready')
            self.assertEqual(cached['key'],json.loads((self.settings.state_dir/'agent-check.json').read_text())['key'])
