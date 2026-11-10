"""Held offline task results exercise per-item deadline counts without wall-clock races."""
from copy import deepcopy
from unittest.mock import patch
import threading

from support import Fixture, ready, png
import agent
import agent_timeouts
from audit import records
from drafts import Drafts
from worker import Worker


def timeout():
    return agent.AgentFailure('Offline deadline', dict(failure_kind='timeout', start=agent.now(), end=agent.now(),
        model='fictional', effort='high', outcome='failed', error='Offline deadline', usage={}, lookups=[], doubts=[]))


class AttachmentTimeoutTests(Fixture):
    def test_two_timeouts_then_success_and_non_timeout_reset(self):
        original=agent.run
        attempts=[]
        def held(*args,**kwargs):
            attempts.append(1)
            if len(attempts)<3:raise timeout()
            return original(*args,**kwargs)
        with patch('agent.run',side_effect=held),self.client() as client:
            identity=self.create(client);self.save(client,identity)
            draft=self.wait(client,identity,'added')
            self.assertEqual(len(draft['runs']),3);self.assertEqual(draft['consecutive_timeouts'],0)
            self.assertFalse(client.app.state.worker.pause.blocked())
        item={}
        self.assertFalse(agent_timeouts.failed(item,'timeout'))
        self.assertFalse(agent_timeouts.failed(item,'timeout'))
        self.assertFalse(agent_timeouts.failed(item,'transient'));self.assertEqual(item['consecutive_timeouts'],0)
        self.assertFalse(agent_timeouts.failed(item,'timeout'))
        self.assertEqual(agent_timeouts.reason(600),'This took too long to read (three tries of 10 minutes). Nothing was saved. Try fewer pages or files.')

    def test_receipt_three_timeouts_retains_saved_payment_and_retry_resets(self):
        from receipts import read
        drafts=Drafts(self.ledger);draft=drafts.create('Fictional saved payment')
        path=self.ledger/'Bills/receipt.png';path.write_bytes(png())
        draft.update(state='added',saved_at=agent.now(),completed_at=agent.now(),result={'entry_id':'a'*16},
                     receipt_reading=dict(state='queued',queued_at=agent.now(),sources=['Bills/receipt.png'],entry_id='a'*16))
        drafts.put(draft);worker=Worker(self.settings,drafts)
        before=(self.ledger/'data/expenses/2026.json').read_bytes()
        with patch('agent.run',side_effect=timeout()):
            for i in range(3):
                read(worker,draft);draft=drafts.get(draft['id'])
                self.assertEqual(draft['receipt_reading']['state'],'queued' if i<2 else 'failed')
                self.assertFalse(worker.pause.blocked())
        self.assertEqual((self.ledger/'data/expenses/2026.json').read_bytes(),before)
        self.assertTrue(path.exists());self.assertEqual(len(draft['audit_pending']),1)
        self.assertIn('Your payment is saved',draft['receipt_reading']['message'])
        with self.client(worker=False) as client:
            response=client.post('/api/drafts/'+draft['id']+'/retry')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['receipt_reading']['consecutive_timeouts'],0)
            self.assertEqual(response.json()['state'],'added')

    def test_shutdown_interrupts_without_incrementing_timeout_count(self):
        with self.client(worker=False) as client:
            worker=client.app.state.worker;drafts=client.app.state.drafts
            draft=drafts.create('Interrupted fictional task');draft.update(state='queued',queued_at=agent.now(),saved_at=agent.now(),consecutive_timeouts=2)
            drafts.put(draft)
            def interrupted(*args,**kwargs):
                worker.stop_event.set();raise timeout()
            with patch('agent.run',side_effect=interrupted):
                worker.start();worker.thread.join(5)
            result=drafts.get(draft['id'])
            self.assertEqual(result['state'],'queued');self.assertEqual(result.get('consecutive_timeouts',0),0)
            self.assertEqual(result['runs'][-1]['outcome'],'interrupted')
            worker.close()
