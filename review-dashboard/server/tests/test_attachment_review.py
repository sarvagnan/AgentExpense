"""Review regressions at task, process, recovery and HTTP boundaries; no models."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import subprocess
import select
import sys
import threading
import time
import unittest

import agent
import agent_timeouts
from adapters.base import AdapterError
from support import Fixture, png
import test_adapters as protocols
from test_agent import pdf_bytes


class ProcessReview(unittest.TestCase):
    setUp=protocols.AdapterTests.setUp
    adapter=protocols.AdapterTests.adapter
    task=protocols.AdapterTests.task
    # Inherit only the fixture helpers, not the adapter suite a second time.
    def test_pi_blocked_stdin_obeys_deadline_and_server_stop(self):
        real_popen=subprocess.Popen
        for stop in (False, True):
            with self.subTest(stop=stop):
                adapter=self.adapter('pi')
                cancel=threading.Event();task=replace(self.task(adapter,seconds=10 if stop else .5,cancel=cancel),prompt='Fictional page '*200000)
                children=[];errors=[]
                def launch(*args,**kwargs):
                    child=real_popen([sys.executable,'-c','import time; time.sleep(60)'],**kwargs)
                    children.append(child);return child
                def run():
                    try: adapter.run(task)
                    except Exception as exc:errors.append(exc)
                with patch('adapters.cli.subprocess.Popen',side_effect=launch):
                    thread=threading.Thread(target=run,daemon=True);thread.start()
                    if stop:
                        until=time.monotonic()+2
                        while time.monotonic()<until:
                            if children and not select.select([], [children[0].stdin], [], 0)[1]:break
                            threading.Event().wait(.005)
                        self.assertTrue(children and not select.select([], [children[0].stdin], [], 0)[1], 'Cancellation must happen with the pipe full')
                        cancel.set()
                    thread.join(2)
                    blocked=thread.is_alive()
                    if blocked:
                        for child in children:child.kill()
                        thread.join(2)
                self.assertFalse(blocked,'Pi remained blocked writing stdin after its deadline/cancellation')
                self.assertEqual(errors[0].kind,'bad_answer' if stop else 'timeout')
                self.assertTrue(all(child.poll() is not None for child in children))


class PreparationReview(Fixture):
    def test_preparation_consumes_the_task_deadline(self):
        clock=[100.0]
        draft=dict(files=[],text='Fictional note',questions=[])
        def prepare(*args,**kwargs):clock[0]+=self.settings.timeout+1;return 'Fictional prompt',[]
        adapter=SimpleNamespace(run=lambda task:task.remaining())
        with patch('agent_gate.get_gate',return_value=SimpleNamespace(require_ready=lambda:None)),patch('agent.prompt_for',side_effect=prepare),patch('agent.time.monotonic',side_effect=lambda:clock[0]),patch('adapters.get_adapter',return_value=adapter):
            with self.assertRaises(agent.AgentFailure) as raised:agent.run(self.settings,draft,self.base)
        self.assertEqual(raised.exception.kind,'timeout')

    def test_pdf_checks_stop_between_pages_including_statements(self):
        from statement_reading import extract
        source=self.base/'fictional.pdf';source.write_bytes(pdf_bytes(100))
        for statement in (False,True):
            work=self.base/str(statement);work.mkdir();checks=[]
            def stopped():
                checks.append(1)
                if len(list(work.glob('*.png')))>=1:raise AdapterError('Agent cancelled')
            with self.assertRaisesRegex(AdapterError,'cancelled'):
                if statement:extract(source,work,check=stopped)
                else:agent.prepare(source,work,1,check=stopped)
            self.assertEqual(len(list(work.glob('*.png'))),1)

    def test_server_stop_during_pdf_preparation_is_interrupted(self):
        with self.client(worker=False, ledger_check=False, timeout=20) as client:
            worker=client.app.state.worker;drafts=client.app.state.drafts
            identity=self.create(client,upload=False)
            response=client.post(f'/api/drafts/{identity}/files',files={'files':('Fictional-long.pdf',pdf_bytes(100),'application/pdf')})
            self.assertEqual(response.status_code,201,response.text)
            self.save(client,identity)
            draft=drafts.get(identity);draft['consecutive_timeouts']=2;drafts.put(draft)
            render=agent.pdfium.PdfPage.render;pages=[]
            def stop_after_first_page(page,*args,**kwargs):
                result=render(page,*args,**kwargs);pages.append(1);worker.stop_event.set();return result
            with patch('agent_gate.get_gate',return_value=SimpleNamespace(require_ready=lambda:None)),patch.object(agent.pdfium.PdfPage,'render',stop_after_first_page),patch('adapters.get_adapter') as adapter:
                worker.start();worker.thread.join(10)
                if worker.thread.is_alive():worker.close();self.fail('Preparation did not respond to server stop')
            result=drafts.get(identity)
            self.assertEqual(pages,[1]);adapter.return_value.run.assert_not_called()
            self.assertEqual(result['state'],'queued')
            self.assertEqual(result['consecutive_timeouts'],0);self.assertEqual(result['runs'][-1]['outcome'],'interrupted')
            self.assertNotEqual(result['runs'][-1].get('failure_kind'),'timeout')
            worker.close()

    def test_timeout_words_fit_units_and_task(self):
        self.assertIn('1 minute',agent_timeouts.reason(60));self.assertNotIn('1 minutes',agent_timeouts.reason(60))
        self.assertIn('1 second)',agent_timeouts.reason(1))
        self.assertNotIn('pages or files',agent_timeouts.reason(60,attachments=False))
        self.assertIn('smaller statement file',agent_timeouts.reason(60,statement=True))
        self.assertNotIn('files',agent_timeouts.reason(60,statement=True))
        receipt=agent_timeouts.reason(60,receipt=True)
        self.assertNotIn('pages or files',receipt);self.assertIn('Retry, or Dismiss and use Edit entry to add the details.',receipt)


class LifecycleReview(Fixture):
    def test_receipt_dismiss_retry_audits_and_history_bound(self):
        from receipts import read
        from test_attachment_timeouts import timeout
        with self.client(worker=False) as client:
            drafts=client.app.state.drafts;worker=client.app.state.worker
            draft=drafts.create('Fictional saved payment')
            path=self.ledger/'Bills/fictional.png';path.write_bytes(png())
            draft.update(state='added',saved_at=agent.now(),completed_at=agent.now(),result=dict(entry_id='a'*16,sources=['Bills/fictional.png']),receipt_reading=dict(state='queued',queued_at='2000-01-01T00:00:00+00:00',sources=['Bills/fictional.png'],entry_id='a'*16))
            drafts.put(draft);before={p:p.read_bytes() for p in (self.ledger/'data/expenses').rglob('*') if p.is_file()}
            with patch('agent.run',side_effect=timeout()):
                for attempt in range(2):
                    for _ in range(3):read(worker,draft)
                    draft=drafts.get(draft['id'])
                    event=draft['audit_pending'][-1]
                    self.assertEqual(len(event['runs']),3,'A failure audit repeats earlier retry attempts')
                    self.assertIsNone(event.get('entry_id'),'Reading failure must not label a saved addition failed')
                    if attempt==0:
                        response=client.post('/api/drafts/'+draft['id']+'/retry');self.assertEqual(response.status_code,200)
                        self.assertNotEqual(response.json()['receipt_reading']['queued_at'],'2000-01-01T00:00:00+00:00')
                response=client.post('/api/drafts/'+draft['id']+'/receipt-dismiss');self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['receipt_reading']['state'],'dismissed')
                self.assertEqual(response.json()['state'],'added');self.assertTrue(path.exists())
                self.assertEqual(before,{p:p.read_bytes() for p in (self.ledger/'data/expenses').rglob('*') if p.is_file()})
                count=len(response.json()['audit_pending'])
                self.assertEqual(client.post('/api/drafts/'+draft['id']+'/receipt-dismiss').status_code,200)
                self.assertEqual(len(drafts.get(draft['id'])['audit_pending']),count)
            draft=drafts.get(draft['id']);draft['runs']=[dict(index=i) for i in range(150)]
            draft['receipt_reading']['runs']=[dict(index=i) for i in range(150)];drafts.put(draft)
            stored=drafts.get(draft['id']);self.assertLessEqual(len(stored['runs']),50);self.assertLessEqual(len(stored['receipt_reading']['runs']),50)

    def test_restart_interrupts_receipt_and_resolved_fix(self):
        with self.client(worker=False) as client:
            drafts=client.app.state.drafts;worker=client.app.state.worker
            draft=drafts.create('Fictional interrupted receipt');draft.update(state='added',saved_at=agent.now(),completed_at=agent.now(),receipt_reading=dict(state='reading',consecutive_timeouts=2,run=dict(start=agent.now(),end=None)))
            drafts.put(draft)
            completed=drafts.create('Fictional completed fix');completed.update(state='added',completed_at=agent.now(),ledger_fix=dict(state='resolved'));drafts.put(completed)
            fix=drafts.create('Fictional resolved fix');fix.update(state='working',ledger_fix=dict(state='resolved'));drafts.put(fix)
            with patch.object(worker,'loop',return_value=None):worker.start();worker.thread.join(2)
            current=drafts.get(draft['id'])['receipt_reading'];self.assertEqual(current['consecutive_timeouts'],0);self.assertEqual(current['run']['outcome'],'interrupted')
            self.assertEqual(drafts.get(fix['id'])['state'],'question')
            self.assertEqual(drafts.get(completed['id'])['state'],'added')
            response=client.post('/api/drafts/'+fix['id']+'/retry');self.assertEqual(response.status_code,409);self.assertIn('now passes',response.text)
            worker.close()
