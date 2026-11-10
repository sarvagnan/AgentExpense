"""Browser save context, server fallback and legacy retries; fake agents only."""
from datetime import datetime, timedelta, timezone
import json
import os
import unittest
from unittest.mock import patch

from support import Fixture, ready
from agent import prompt_for
from draft_time import client_context, saved_local, server_zone
from transactions import message_for, to_store
from outcomes import draft_event
from test_agent import pdf_bytes


class ContextTests(unittest.TestCase):
    def test_offset_bounds_calendar_validation_future_limit_and_old_times(self):
        at = datetime(2026, 11, 1, tzinfo=timezone.utc)
        for offset in ('-14:00', '-05:00', '+00:00', '+01:00', '+05:30', '+14:00'):
            with self.subTest(offset=offset):
                self.assertEqual(client_context('2000-01-01T00:00:00' + offset, 'UTC', at=at)['client_time'],
                                 '2000-01-01T00:00:00' + offset)
        self.assertTrue(client_context((at + timedelta(hours=48)).isoformat(), 'UTC', at=at))
        for value in ('2026-11-01T00:00:00-14:01', '2026-11-01T00:00:00+15:00',
                      '2026-11-01T00:00:00+05:60', '2026-11-01T00:00:00+5:30',
                      '2026-11-01T00:00:00Z', '2026-11-01T00:00:00',
                      '2026-02-30T00:00:00+00:00', '2026-11-01T24:00:00+00:00',
                      (at + timedelta(hours=48, seconds=1)).isoformat()):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client_context(value, 'UTC', at=at)
        for zone in ('Nowhere/Invalid', '../UTC', '', 'UTC\nForged: metadata', None):
            with self.subTest(zone=zone), self.assertRaises(ValueError):
                client_context('2000-01-01T00:00:00+00:00', zone, at=at)
        with self.assertRaises(ValueError):
            client_context(None, 'UTC')

    def test_server_fallback_uses_local_zone_and_offset_at_the_save_instant(self):
        with patch.dict(os.environ, TZ='America/Chicago'):
            self.assertEqual(server_zone().key, 'America/Chicago')
            for instant, expected in [('2026-01-01T01:00:00+00:00', '2025-12-31T19:00:00-06:00'),
                                      ('2026-07-01T01:00:00+00:00', '2026-06-30T20:00:00-05:00')]:
                context = client_context(at=datetime.fromisoformat(instant))
                self.assertEqual(context, dict(client_time=expected, client_zone='America/Chicago'))


class DraftTimeTests(Fixture):
    def test_editing_draft_create_and_autosave_record_valid_browser_context(self):
        original = dict(client_time='2000-01-01T23:30:00+01:00', client_zone='Europe/Paris')
        updated = dict(client_time='2000-01-02T00:30:00-05:00', client_zone='America/Chicago')
        with self.client(worker=False) as client:
            response = client.post('/api/drafts', json=dict(text='lunch', **original))
            self.assertEqual(response.status_code, 201, response.text)
            identity = response.json()['id']
            self.assertEqual({k: response.json()[k] for k in original}, original)
            response = client.patch(f'/api/drafts/{identity}', json=dict(text='lunch yesterday', **updated))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual({k: response.json()[k] for k in updated}, updated)
            before = response.json()
            self.assertEqual(client.patch(f'/api/drafts/{identity}', json={'client_zone':'UTC'}).status_code, 422)
            self.assertEqual(client.get(f'/api/drafts/{identity}').json(), before)
            self.assertEqual(client.post('/api/drafts', json={**original, 'client_zone':'Invalid/Zone'}).status_code, 422)
            self.assertEqual(len(client.app.state.drafts.all()), 1)

    def test_saved_lines_prompt_dates_and_audit_preserve_all_three_offsets(self):
        cases = [('2000-01-01T23:45:09-05:00', 'America/Chicago'),
                 ('2000-01-02T00:15:09+05:30', 'Asia/Kolkata'),
                 ('2000-01-02T00:45:09+01:00', 'Europe/Paris')]
        with self.client(worker=False) as client:
            for local, zone in cases:
                with self.subTest(zone=zone):
                    identity = self.create(client, 'lunch yesterday', upload=False)
                    context = dict(client_time=local, client_zone=zone)
                    response = client.post(f'/api/drafts/{identity}/save', json=context)
                    self.assertEqual(response.status_code, 200, response.text)
                    draft = client.app.state.drafts.get(identity)
                    self.assertEqual({k: draft[k] for k in context}, context)
                    expected = f'{local[:10]} {local[11:16]} {local[-6:]} {zone}'
                    self.assertEqual(saved_local(draft), expected)
                    prompt, _ = prompt_for(self.settings, draft, client.app.state.drafts.folder(identity), self.base)
                    self.assertIn('Saved: ' + expected, prompt)
                    for instruction in ('undated entries', '"yesterday"', '"last Friday"', "draft's local Saved date"):
                        self.assertIn(instruction, prompt)
                    proposal = ready()
                    entry, _, _ = to_store(proposal, self.ledger, len(proposal['attachment-names'] or ()))
                    message = message_for(self.settings, draft, entry, [], proposal)
                    self.assertIn('\nSaved: ' + expected + '\n', message)
                    audit = draft_event(draft, 'added')
                    self.assertEqual({k: audit[k] for k in context}, context)
                    draft['state'] = 'failed'
                    client.app.state.drafts.put(draft)
                    retried = client.post(f'/api/drafts/{identity}/retry').json()
                    self.assertEqual({k: retried[k] for k in context}, context)

    def test_invalid_api_context_does_not_queue_or_change_draft(self):
        with self.client(worker=False) as client:
            identity = self.create(client, upload=False)
            before = client.get(f'/api/drafts/{identity}').json()
            for context in (dict(client_time='2000-01-01T00:00:00+14:01', client_zone='UTC'),
                            dict(client_time='2000-01-01T00:00:00+00:00', client_zone='Invalid/Zone'),
                            dict(client_time=(datetime.now(timezone.utc)+timedelta(days=3)).isoformat(), client_zone='UTC'),
                            dict(client_zone='UTC')):
                with self.subTest(context=context):
                    response = client.post(f'/api/drafts/{identity}/save', json=context)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(client.get(f'/api/drafts/{identity}').json(), before)

    def test_nonbrowser_save_records_server_local_context(self):
        with patch.dict(os.environ, TZ='Europe/Paris'), self.client(worker=False) as client:
            identity = self.create(client, upload=False)
            self.save(client, identity)
            draft = client.get(f'/api/drafts/{identity}').json()
            self.assertEqual(draft['client_zone'], 'Europe/Paris')
            self.assertLess(abs((datetime.fromisoformat(draft['client_time']) -
                                 datetime.fromisoformat(draft['saved_at'])).total_seconds()), 1)

    def test_legacy_failed_draft_loads_and_retries_using_original_instant(self):
        proposal = ready(); proposal['attachment-names'] = []
        self.set_answers(proposal)
        with self.client(worker=False) as client:
            identity = self.create(client, upload=False)
            folder = client.app.state.drafts.folder(identity)
            path = folder / 'draft.json'
            draft = json.loads(path.read_text())
            draft.update(state='failed', saved_at='2000-01-01T01:00:00+00:00')
            draft.pop('client_time'); draft.pop('client_zone')
            path.write_text(json.dumps(draft))
        with patch.dict(os.environ, TZ='America/Chicago'), self.client() as client:
            loaded = client.get(f'/api/drafts/{identity}').json()
            self.assertEqual(loaded['client_time'], '1999-12-31T19:00:00-06:00')
            self.assertEqual(loaded['client_zone'], 'America/Chicago')
            self.assertEqual(client.post(f'/api/drafts/{identity}/retry').status_code, 200)
            added = self.wait(client, identity, 'added')
            self.assertEqual(added['client_time'], loaded['client_time'])
            self.assertIn('Saved: 1999-12-31 19:00 -06:00 America/Chicago', self.calls()[0]['stdin'])
            self.assertIn('Saved: 1999-12-31 19:00 -06:00 America/Chicago',
                          self.jj('log', '-r', '@-', '--no-graph', '-T', 'description'))
            from audit import records
            self.assertEqual(records(self.ledger)[0]['client_time'], loaded['client_time'])

    def test_statement_upload_validation_fallback_and_handover_context(self):
        context = dict(client_time='2000-01-01T23:45:00-05:00', client_zone='America/Chicago')
        with self.client(worker=False) as client:
            upload = lambda data: client.post('/api/statements', data=data,
                                              files={'file': ('Statement.pdf', pdf_bytes(), 'application/pdf')})
            bad = upload({**context, 'client_zone':'Invalid/Zone'})
            self.assertEqual(bad.status_code, 422, bad.text)
            self.assertEqual(client.app.state.statements.all(), [])
            response = upload(context)
            self.assertEqual(response.status_code, 201, response.text)
            session = client.app.state.statements.get(response.json()['id'])
            self.assertEqual({k: session[k] for k in context}, context)
            with patch.dict(os.environ, TZ='Europe/Paris'):
                self.assertEqual(upload({}).json()['client_zone'], 'Europe/Paris')
            identity = self.create(client, upload=False)
            response = client.post(f'/api/drafts/{identity}/files', files={'files': ('Statement.pdf', pdf_bytes(), 'application/pdf')})
            draft = response.json()
            draft.update(context, statement_file=draft['files'][0]['id'])
            client.app.state.drafts.put(draft)
            handover = client.post(f'/api/drafts/{identity}/statements')
            self.assertEqual(handover.status_code, 201, handover.text)
            self.assertEqual({k: handover.json()[k] for k in context}, context)
            fallback = dict(session, statement_task=True, saved_at=session['created_at'], files=[])
            prompt, _ = prompt_for(self.settings, fallback, self.base, self.base)
            self.assertIn('Saved: 2000-01-01 23:45 -05:00 America/Chicago', prompt)
