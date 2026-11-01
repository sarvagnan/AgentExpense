from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from support import Fixture, png


class RecoveryTests(Fixture):
    def test_discard_restore_words_answers_files_and_purge(self):
        with self.client(worker=False) as client:
            identity = self.create(client, 'Words to keep')
            records = client.app.state.drafts
            draft = records.get(identity)
            draft.update(state='question', questions=[dict(question='Which bank?', answer=None, asked_at=datetime.now(timezone.utc).isoformat())])
            records.put(draft)
            response = client.patch(f'/api/drafts/{identity}/unsent-answer', json={'question_index': 0, 'answer': 'Savings Account still typing'})
            self.assertEqual(response.status_code, 200)
            before = records.get(identity)
            file = before['files'][0]
            path = records.folder(identity) / 'files' / file['name']
            self.assertEqual(client.delete(f'/api/drafts/{identity}').status_code, 204)
            self.assertEqual(client.get(f'/api/drafts/{identity}').status_code, 404)
            self.assertEqual(client.get('/api/drafts?include_added=true').json(), [])
            self.assertTrue(path.exists())
            restored = client.post(f'/api/drafts/{identity}/restore').json()
            self.assertEqual(restored, before)
            self.assertEqual(restored['unsent_answer']['answer'], 'Savings Account still typing')
            self.assertEqual(client.get(f'/api/drafts/{identity}/files/{file["id"]}').content, png())
            client.delete(f'/api/drafts/{identity}')
            records.purge(datetime.now(timezone.utc) + timedelta(seconds=31))
            self.assertTrue(records.folder(identity).exists(), 'Keep the record until its audit is committed')
            self.assertTrue(records.get(identity, include_discarded=True)['audit_pending'])
            client.app.state.worker.flush_audits()
            from audit import records as audit_records
            line = audit_records(self.ledger)[0]
            self.assertEqual((line['draft_id'], line['outcome'], line['text']), (identity, 'discarded', 'Words to keep'))
            audit_file = 'data/additions/' + line['time'][:4] + '.jsonl'
            self.assertEqual(self.jj('diff', '-r', '@-', '--name-only').splitlines(), [audit_file])
            self.assertIn(line['id'], self.jj('file', 'show', '-r', '@-', audit_file))
            self.assertTrue(records.folder(identity).exists(), 'Audit must be committed before purge deletes the record')
            records.purge(datetime.now(timezone.utc) + timedelta(seconds=31))
            self.assertFalse(records.folder(identity).exists())
            self.assertEqual(client.post(f'/api/drafts/{identity}/restore').status_code, 404)

    def test_discard_working_rejected_added_never_changes_ledger_and_restart_hides(self):
        with self.client(worker=False) as client:
            identity = self.create(client)
            records = client.app.state.drafts
            draft = records.get(identity)
            draft['state'] = 'working'
            records.put(draft)
            self.assertEqual(client.delete(f'/api/drafts/{identity}').status_code, 409)
            draft.update(state='added', completed_at=datetime.now(timezone.utc).isoformat(), result={'entry_id': 'existing'})
            records.put(draft)
            before = self.bytes()
            self.assertEqual(client.delete(f'/api/drafts/{identity}').status_code, 204)
            self.assertEqual(records.all(), [])
            self.assertEqual(client.post(f'/api/drafts/{identity}/restore').status_code, 200)
            self.assertEqual(self.bytes(), before)
            draft = records.get(identity)
            draft['state'] = 'queued'
            records.put(draft)
            client.delete(f'/api/drafts/{identity}')
        with self.client(worker=False) as client:
            self.assertEqual(client.app.state.drafts.all(), [])
            self.assertEqual(client.post(f'/api/drafts/{identity}/restore').json()['state'], 'queued')

    def test_unsent_answer_identity_and_send_cleanup(self):
        with self.client(worker=False) as client:
            identity = self.create(client, upload=False)
            path = f'/api/drafts/{identity}'
            self.assertEqual(client.patch(path+'/unsent-answer', json={'question_index': 0, 'answer': 'Savings Account'}).status_code, 409)
            draft = client.app.state.drafts.get(identity)
            draft.update(state='question', questions=[dict(question='Which bank?', answer=None, asked_at=datetime.now(timezone.utc).isoformat())])
            client.app.state.drafts.put(draft)
            self.assertEqual(client.patch(path+'/unsent-answer', json={'question_index': 1, 'answer': 'wrong question'}).status_code, 409)
            self.assertEqual(client.patch(path+'/unsent-answer', json={'question_index': 0, 'answer': ''}).status_code, 200)
            client.patch(path+'/unsent-answer', json={'question_index': 0, 'answer': 'Savings Account'})
            self.assertEqual(client.get(path).json()['unsent_answer']['answer'], 'Savings Account')
            response = client.post(path+'/answer', json={'answer': 'Savings Account'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('unsent_answer', response.json())
            self.assertEqual(response.json()['questions'][0]['answer'], 'Savings Account')

    def test_cancel_upload_before_and_after_arrival_and_duplicate_request(self):
        with self.client(worker=False) as client:
            identity = self.create(client, upload=False)
            path = f'/api/drafts/{identity}'
            first, second = 'a'*32, 'b'*32
            upload = lambda token: client.post(path+'/files', files=[('files', ('receipt.png', png(), 'image/png'))], data={'upload_id': token})
            self.assertEqual(client.delete(path+'/uploads/'+first).status_code, 200)
            self.assertEqual(upload(first).json()['files'], [])
            response = upload(second)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()['files'][0]['id'], second)
            self.assertEqual(len(upload(second).json()['files']), 1)
            self.assertEqual(client.delete(path+'/uploads/'+second).json()['files'], [])
            self.assertEqual(upload(second).json()['files'], [])
            self.assertEqual(list((client.app.state.drafts.folder(identity)/'files').iterdir()), [])
            self.assertEqual(client.delete(path+'/uploads/not-a-token').status_code, 422)

    def test_text_only_autosave_retains_revision_for_storage_denial_recovery(self):
        with self.client(worker=False) as client:
            response = client.post('/api/drafts', json={'text': 'petrol 300', 'pieces': None, 'client_updated_at': 100})
            self.assertEqual(response.status_code, 201)
            identity = response.json()['id']
            self.assertEqual(response.json()['client_updated_at'], 100)
            self.assertEqual(response.json()['files'], [])
            response = client.patch(f'/api/drafts/{identity}', json={'text': 'petrol 350', 'client_updated_at': 200})
            self.assertEqual(response.status_code, 200)
            recovered = client.get('/api/drafts?include_added=true').json()[0]
            self.assertEqual((recovered['text'], recovered['client_updated_at']), ('petrol 350', 200))

    def test_record_only_commit_keeps_autosave_responsive(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from outcomes import OutcomeTransaction
        from support import ready
        answer=ready();answer.update(status='not_expense',entry=None,explanation='A note, not an expense')
        self.set_answers(answer)
        entered,release=threading.Event(),threading.Event()
        original=OutcomeTransaction.apply_record
        def held(transaction,fd):
            entered.set();release.wait()
            return original(transaction,fd)
        with patch.object(OutcomeTransaction,'apply_record',held),self.client() as client:
            with ThreadPoolExecutor(max_workers=1) as requests:
                try:
                    identity=self.create(client,'A note',upload=False)
                    self.save(client,identity)
                    self.assertTrue(entered.wait(180))
                    def autosave():
                        self.assertEqual(client.get('/api/drafts/'+identity).json()['state'],'added')
                        response=client.post('/api/drafts',json={'text':'New words during audit commit'})
                        self.assertEqual(response.status_code,201,response.text)
                        editing=response.json()['id']
                        response=client.patch('/api/drafts/'+editing,json={'text':'Newer words during audit commit'})
                        self.assertEqual(response.status_code,200,response.text)
                        self.assertEqual(client.get('/api/drafts/'+editing).json()['text'],'Newer words during audit commit')
                    # The timeout only guards a deadlock. Completion before release
                    # proves audit verification does not hold the draft mutex.
                    requests.submit(autosave).result(timeout=180)
                    self.assertFalse(release.is_set())
                finally:
                    release.set()
