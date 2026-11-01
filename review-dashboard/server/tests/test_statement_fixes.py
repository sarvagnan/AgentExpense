"""Fix-round lifecycle contracts; all writes go to disposable ledgers."""
from datetime import datetime, timedelta, timezone
import io
import threading
from unittest.mock import patch
from pypdf import PdfWriter
from support import Fixture
from statement_fixtures import TEXT
from statements import Statements


class StatementFixTests(Fixture):
    def session(self):
        writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
        output = io.BytesIO(); writer.write(output)
        sessions = Statements(self.settings)
        s = sessions.create(output.getvalue(), 'fixture.pdf')
        s.update(state='review', plan={'items': []}, fingerprint='stale')
        sessions.put(s)
        return sessions, s

    def test_discard_recovery_is_listed_durable_extendable_and_keeps_pdf(self):
        sessions, s = self.session()
        with self.client() as client:
            self.assertEqual(client.delete('/api/statements/'+s['id']).status_code, 204)
            data = client.get('/api/statements').json()
            recovered = data['recoverable'][0]
            self.assertEqual(recovered['id'], s['id'])
            self.assertGreater(datetime.fromisoformat(recovered['discarded_until']), datetime.now(timezone.utc)+timedelta(hours=23))
            self.assertTrue((sessions.folder(s['id'])/'read.pdf').exists())
            old = datetime.now(timezone.utc)+timedelta(minutes=1)
            recovered['discarded_until'] = old.isoformat(); sessions.put(recovered)
            response = client.post('/api/statements/'+s['id']+'/keep')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertGreater(datetime.fromisoformat(response.json()['discarded_until']), old+timedelta(hours=23))
            self.assertEqual(client.post('/api/statements/'+s['id']+'/restore').status_code, 200)
            self.assertFalse(sessions.get(s['id']).get('discarded_until'))

    def test_completed_sessions_leave_work_list_but_direct_result_remains(self):
        sessions, s = self.session()
        s['state'] = 'applied'; sessions.put(s)
        with self.client() as client:
            self.assertEqual(client.get('/api/statements').json()['sessions'], [])
            self.assertEqual(client.get('/api/statements/'+s['id']).json()['state'], 'applied')

    def test_read_cancel_cannot_resurrect_and_restore_restarts_reader(self):
        sessions, s = self.session()
        s['state'] = 'reading'; sessions.put(s)
        stale = dict(s)
        # Install the pause before startup can launch the reader, and retain it
        # until the client has stopped its worker.
        with patch('statements.Statements.work', return_value=False), self.client() as client:
            self.assertEqual(client.delete('/api/statements/'+s['id']).status_code, 204)
            stale['state'] = 'found'; sessions.put(stale)
            self.assertTrue(sessions.get(s['id'], include_discarded=True)['discarded_until'])
            self.assertEqual(client.post('/api/statements/'+s['id']+'/restore').status_code, 200)
            sessions.put(stale)
            self.assertEqual(sessions.get(s['id'])['state'], 'reading')

    def test_queued_apply_cancel_does_not_write_and_atomic_apply_refuses_cancel(self):
        sessions, s = self.session()
        with patch('statements.Statements.work', return_value=False), self.client() as client:
            self.assertEqual(client.post('/api/statements/'+s['id']+'/apply').status_code, 202)
            self.assertEqual(client.post('/api/statements/'+s['id']+'/cancel').status_code, 200)
            self.assertEqual(sessions.get(s['id'])['state'], 'review')
            s = sessions.get(s['id']); s.update(state='applying', apply_started=True); sessions.put(s)
            self.assertEqual(client.post('/api/statements/'+s['id']+'/cancel').status_code, 409)
            self.assertEqual(client.delete('/api/statements/'+s['id']).status_code, 409)

    def test_upload_cancel_token_covers_early_and_late_arrival(self):
        sessions, s = self.session()
        pdf = (sessions.folder(s['id'])/'read.pdf').read_bytes()
        with self.client() as client:
            token = 'a'*32
            self.assertEqual(client.delete('/api/statements/uploads/'+token).status_code, 204)
            self.assertEqual(client.post('/api/statements', files={'file': ('late.pdf', pdf)}, data={'upload_id': token}).status_code, 409)
            token = 'b'*32
            uploaded = client.post('/api/statements', files={'file': ('arrived.pdf', pdf)}, data={'upload_id': token}).json()
            self.assertEqual(client.delete('/api/statements/uploads/'+token).status_code, 204)
            self.assertEqual(client.get('/api/statements/'+uploaded['id']).status_code, 404)

    def test_stale_failure_is_structured_and_cannot_retry(self):
        sessions, s = self.session()
        sessions.queue_apply(s['id']); sessions.work(threading.Event())
        failed = sessions.get(s['id'])
        self.assertEqual(failed['failure_reason'], 'stale_checklist')
        with self.assertRaisesRegex(ValueError, 'Upload'):
            sessions.queue_apply(s['id'])

    def test_restart_keeps_interrupted_mutation_uncancellable(self):
        sessions, s = self.session()
        s.update(state='applying', apply_started=True); sessions.put(s)
        sessions.recover_work()
        with self.assertRaisesRegex(ValueError, 'started'):
            sessions.cancel_apply(s['id'])
        with self.assertRaisesRegex(ValueError, 'started'):
            sessions.discard(s['id'])

    def test_late_reader_preserves_handover_identity_and_extended_recovery(self):
        sessions, s = self.session()
        stale = dict(s)
        s.update(handover_draft='c'*32, handover_until='2099-01-01T00:00:00+00:00')
        sessions.put(s)
        stale['state'] = 'found'; sessions.put(stale)
        recovered = sessions.get(s['id'])
        self.assertEqual(recovered['handover_draft'], 'c'*32)
        self.assertEqual(recovered['handover_until'], '2099-01-01T00:00:00+00:00')

    def test_cancelled_reader_does_not_start_fallback_agent(self):
        sessions, s = self.session()
        s['state'] = 'reading'; sessions.put(s)
        def cancel_during_extraction(path):
            sessions.discard(s['id'])
            return TEXT.replace('Purchases: 120.00', 'Purchases: unreadable')
        with patch('statements.extract_text', side_effect=cancel_during_extraction), patch('agent.run') as run:
            sessions.read(s)
            run.assert_not_called()
        self.assertTrue(sessions.get(s['id'], include_discarded=True)['discarded_until'])

    def test_resume_suggestion_excludes_stale_checklists(self):
        from store import dump, read_json
        path = self.ledger/'data/accounts.json'
        declarations = read_json(path)
        declarations['accounts']['Liabilities:Plastic:Main'] = dict(kind='card',currency='INR',statement=dict(reader='example-card-pdf',identifier='5678'))
        dump(path,declarations)
        sessions, old = self.session()
        statement = dict(periodStart='2026-09-29', periodEnd='2026-10-28', previousBalance='0.00', rows=[])
        old.update(state='failed', statement=statement, fingerprint='old-store', failure_reason='stale_checklist')
        sessions.put(old)
        pdf = (sessions.folder(old['id'])/'read.pdf').read_bytes()
        with patch('statements.extract_text', return_value=TEXT), patch('statement_readers.Reader.read', return_value=statement), patch('statements.check_statement', return_value=[dict(ok=True, text='Fixture')]), patch('statements.plan', return_value=dict(items=[], counts={}, unmatched=[])), patch('statements.fingerprint', return_value='current-store'):
            fresh = sessions.create(pdf, 'new.pdf'); sessions.read(fresh)
            self.assertNotIn('duplicate_of', fresh)
            old.update(state='review', fingerprint='current-store', failure_reason=None); sessions.put(old)
            another = sessions.create(pdf, 'another.pdf'); sessions.read(another)
            self.assertEqual(another['duplicate_of'], old['id'])
