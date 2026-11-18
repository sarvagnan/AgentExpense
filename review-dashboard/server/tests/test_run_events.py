from datetime import datetime, timedelta, timezone
import json
import time

from run_events import MAX_ARGUMENTS, MAX_EVENTS, MAX_SNIPPET, RunEvents, read_events
from support import Fixture


class RunEventTests(Fixture):
    def test_cursor_reads_in_order_before_the_fake_run_finishes(self):
        answer = dict(status='question', question='Which category fits?', entry=None,
                      doubts=[], explanation=None, **{'new-accounts': [], 'attachment-names': []})
        self.set_answers(dict(kind='answer', answer=answer, progress=True, hold_progress=True))
        control = self.config.with_suffix('.progress')
        control.write_text('hold')
        with self.client(timeout=30) as client:
            identity = self.create(client, 'Fictional book', upload=False)
            url = f'/api/drafts/{identity}/events'
            self.assertEqual(client.get(url).json(), {'after': 0, 'events': []})
            self.save(client, identity)

            def poll(after, kind):
                until = time.monotonic() + 10
                while time.monotonic() < until:
                    response = client.get(url, params={'after': after})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.headers['cache-control'], 'no-store')
                    batch = response.json()
                    if batch['events'] and batch['events'][-1]['type'] == kind:
                        return batch
                    time.sleep(.02)
                self.fail(f'Event {kind} did not arrive')

            try:
                first = poll(0, 'progress')
                self.assertEqual([event['type'] for event in first['events']], ['start', 'prompt', 'lookup', 'progress'])
                self.assertEqual(first['events'][0]['adapter'], 'codex')
                self.assertEqual(first['events'][0]['model'], self.settings.model)
                self.assertEqual(first['events'][2]['tool'], 'entries_near')
                self.assertEqual(json.loads(first['events'][2]['arguments']), {'day': '2026-10-29'})
                self.assertGreater(first['events'][2]['result_bytes'], 0)
                self.assertNotIn('Fictional lookup payload', json.dumps(first))
                self.assertNotIn('Fictional book', json.dumps(first))
                self.assertEqual(client.get(f'/api/drafts/{identity}').json()['state'], 'working')
                control.write_text('reconnect')
                second = poll(first['after'], 'reconnecting')
                self.assertEqual(len(second['events']), 1)
                self.assertEqual(client.get(url, params={'after': second['after']}).json()['events'], [])
            finally:
                control.write_text('finish')
            self.wait(client, identity, 'question')
            last = poll(second['after'], 'exit')
            self.assertEqual([event['type'] for event in last['events']], ['exit'])
            self.assertEqual(last['events'][0]['outcome'], 'question')

    def test_caps_and_torn_or_malformed_lines_do_not_break_the_cursor(self):
        events = RunEvents(self.base, 'pi', 'fixture')
        events.allowed = {'entries_near'}
        events.lookup('entries_near', {'query': 'λ' * 900}, {'result': 'private fictional result'})
        for _ in range(MAX_EVENTS):
            events.progress('x' * 900)
        events.write('error', terminal=True, text='timeout')
        events.write('exit', terminal=True, outcome='error')
        full = read_events(self.base, 0)
        self.assertEqual(len(full['events']), MAX_EVENTS)
        self.assertEqual(len(full['events'][1]['arguments']), MAX_ARGUMENTS)
        self.assertEqual(len(full['events'][2]['text']), MAX_SNIPPET)
        self.assertFalse(any('private fictional result' in str(event) for event in full['events']))
        self.assertEqual([e['type'] for e in full['events'][-2:]], ['error', 'exit'])
        with events.path.open('ab') as handle:
            handle.write(b'bad JSON\n{"type":')
        tail = read_events(self.base, full['after'])
        self.assertEqual(tail, {'after': full['after'] + len(b'bad JSON\n'), 'events': []})
        RunEvents(self.base, 'pi', 'fixture')
        self.assertEqual([e['type'] for e in read_events(self.base, tail['after'])['events']], ['start'])

    def test_provider_errors_and_exits_keep_diagnostics_out_of_events(self):
        self.set_answers(dict(kind='provider_error', error={'message': 'Fictional provider diagnostic'}))
        with self.client(timeout=10) as client:
            identity = self.create(client, upload=False)
            self.save(client, identity)
            failed = self.wait(client, identity, 'failed')
            events = client.get(f'/api/drafts/{identity}/events').json()['events']
            kinds = ['start', 'prompt', 'lookup', 'error', 'error', 'exit']
            self.assertEqual([event['type'] for event in events], kinds * len(failed['runs']))
            self.assertEqual(events[-1]['outcome'], 'error')
            self.assertNotIn('Fictional provider diagnostic', json.dumps(events))
            self.assertIn('Fictional provider diagnostic', json.dumps(failed['runs']))

    def test_events_expire_with_folders_and_their_api_does_not_modify_claims(self):
        with self.client(worker=False) as client:
            draft = self.create(client, 'Fictional book', upload=False)
            drafts, statements = client.app.state.drafts, client.app.state.statements
            statement = statements.create(b'date,amount\n2026-10-29,1\n', 'Fictional.csv')['id']
            for collection, identity, state_file in [(drafts, draft, 'draft.json'),
                                                     (statements, statement, 'session.json')]:
                folder = collection.folder(identity)
                path = folder / state_file
                value = json.loads(path.read_text())
                old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
                if collection is drafts:
                    value.update(state='added', completed_at=old, claim={'expires_at': old})
                    endpoint = f'/api/drafts/{identity}/events'
                else:
                    value.update(state='already_filed', updated_at=old)
                    endpoint = f'/api/statements/{identity}/events'
                path.write_text(json.dumps(value))
                contents = path.read_bytes()
                RunEvents(folder, 'codex', 'fixture')
                response = client.get(endpoint)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['events'][0]['model'], 'fixture')
                self.assertEqual(path.read_bytes(), contents)
                self.assertEqual(client.get(endpoint, params={'after': -1}).status_code, 422)
                collection.purge()
                self.assertFalse(folder.exists())
                self.assertEqual(client.get(endpoint).status_code, 404)
