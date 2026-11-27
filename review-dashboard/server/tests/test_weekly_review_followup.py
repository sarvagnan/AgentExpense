"""Offline owner messages and read-only follow-up."""
from secrets import token_hex
import json
from datetime import date, timedelta
from unittest.mock import Mock, patch

from support import Fixture, entry, png, posting
import audit
from adapters.base import Result
from corrections import wire_entry
import test_review
import test_weekly_review_decisions as decisions


class WeeklyReviewFollowupTests(Fixture):
    Decisions = decisions.WeeklyReviewDecisionTests
    add, clock, reviewer, run_batch, review_suggestions = (
        Decisions.add, Decisions.clock, Decisions.reviewer, Decisions.run_batch, Decisions.review_suggestions)

    def test_precedent_returns_owner_answers_and_readonly_period_checks(self):
        from audit_changes import identity
        from lookups import LookupStore
        from review_lookup import ReviewLookup, issue_grant
        from store import dump

        first, second, earlier = ('1' * 16, '2' * 16, '3' * 16)
        postings = [posting('Expenses:Food', '10.00'), posting('Assets:Cash', '-10.00')]
        sessions = [entry(earlier, '2026-01-01', 'Aster Hall', postings), entry(first, '2026-02-01', 'Willow Workshop', postings),
                    entry(second, '2026-02-02', 'Willow Workshop — pottery', postings)]
        sessions[1]['possible-duplicate-of'] = [dict(id=second, reason='several-candidates')]
        path = self.ledger / 'data/expenses/2026.json'
        dump(path, sessions)
        line = self.add(entry_id=first)
        issue = self.review_suggestions((line, {'kind': 'question-only'}))[line]['id']
        with self.client(worker=False, timeout=15) as client:
            body = dict(text='The earlier booking covered one session.')
            url = f'/api/review/issues/{issue}'
            self.assertEqual(client.post(f'{url}/answer/{token_hex(16)}', json=body).status_code, 200)
            self.assertEqual(client.post(f'{url}/keep/{token_hex(16)}', json={'note': 'Leave it'}).status_code, 200)
            history = client.get(f'/api/entries/{first}/review-history').json()['items']
            self.assertEqual([r['kind'] for r in history], ['review-message', 'review-resolution'])

        selected = self.add(entry_id=second)
        sessions[0]['postings'] = [postings[0], {**postings[1], 'assert': {
            'amount': '0.00', 'commodity': 'INR'}}]
        dump(path, sessions)
        self.checkpoint('Fictional outside-period imbalance')
        revision = identity(self.ledger, 'main')['commit']
        grant = issue_grant(self.ledger, self.base, revision, [selected])
        lookups = LookupStore(self.ledger, review=ReviewLookup(self.ledger, grant))
        before = self.bytes()
        def lookup(name, **arguments):
            return json.loads(lookups.call(name, arguments)['data'])
        payee = lookup('review_precedent', entry_id=second, payee='Willow workshop')
        self.assertEqual([r['entry']['id'] for r in payee['matches']], [first])
        self.assertEqual(payee['matches'][0]['history'][0]['text'], body['text'])
        account = lookup('review_precedent', entry_id=second, account='Assets:Cash')
        self.assertEqual({r['entry']['id'] for r in account['matches']}, {first, earlier})
        report = lookup('review_period_checks')
        self.assertEqual(report['suspected_duplicates'][0]['entries'], [first, second])
        self.assertIn('assertions', [r['check'] for r in report['mismatches'] if r['scope'] == 'outside_period'])
        self.assertEqual(self.bytes(), before)

    def test_review_followup_files_and_reply_are_scoped_and_durable(self):
        entry = test_review.ReviewTests.seed(self)[0]
        financial = self.ledger / 'data/expenses/2026.json'
        saved = financial.read_bytes()
        line = self.add(entry_id=entry['id'])
        issue = self.review_suggestions((line, {'kind': 'question-only'}))[line]['id']
        operation = token_hex(16)
        url = f'/api/review/issues/{issue}/answer/{operation}'
        with self.client(worker=False, timeout=15) as client:
            staging = self.create(client, text='')
            body = dict(text='Go ahead', draft_id=staging)
            result = client.post(url, json=body)
            self.assertEqual(result.status_code, 200, result.text)
            message = result.json()['audit_id']
            replay = client.post(url, json=body)
            self.assertEqual(replay.json()['audit_id'], message)
            self.assertEqual(client.post(url, json={**body, 'text': 'Different answer'}).status_code, 409)
            row = next(r for r in audit.records(self.ledger) if r['id'] == message)
            self.assertEqual((row['kind'], row['actor'], row['reply_to']), ('review-message', 'user', issue))
            file, = row['message_evidence']
            self.assertTrue(file['path'].startswith(f'Bills/Reviews/{issue}/{message}/'))
            self.assertEqual((self.ledger / file['path']).read_bytes(), png())
            self.assertEqual(client.get(f'/api/drafts/{staging}').json()['state'], 'added')
            self.assertEqual(client.get('/api/drafts?include_review=true').json()['review']['items'][0]['id'], issue)
            self.assertNotIn(row, audit.unreviewed(audit.records(self.ledger)))

            weekly = client.app.state.worker.weekly
            weekly.gate = Mock()
            proposal = wire_entry(self.settings, entry)
            proposal['entry']['description'] = 'Willow Workshop pottery'
            def reply(task):
                self.assertIn('Go ahead', task.prompt)
                source, = task.lookups.call('review_pending', {})['items']
                self.assertEqual(source['id'], line)
                evidence = [task.lookups.call('review_evidence', {'evidence_id': handle})
                            for handle in source['evidence_ids']]
                self.assertEqual([result['status'] for result in evidence], ['available', 'available'])
                proofs = [json.loads(result['data']) for result in evidence]
                self.assertEqual([p['text'] for p in proofs if 'text' in p], ['Fictional evidence'])
                self.assertEqual([v for p in proofs for v in p['visuals']], [dict(page=1, image=1)])
                return Result(dict(text='Please confirm the payment date.',
                                   suggestion=dict(kind='entry', target=entry['id'], answer=proposal)))
            adapter = Mock(run=Mock(side_effect=reply))
            with patch('adapters.get_adapter', return_value=adapter):
                weekly.tick()  # Reviewer gate.
                weekly.tick()  # Checkpoint the reply in a distinct idle turn.
            adapter.run.assert_called_once()
            self.assertEqual(len([r for r in audit.records(self.ledger) if r['kind'] == 'review-message']), 1)

        with self.client(worker=False, timeout=15) as client, patch('adapters.get_adapter', return_value=adapter):
            client.app.state.worker.weekly.tick()
            history = client.get(f'/api/drafts?include_review=true&review_issue_id={issue}').json()['review']['history']
            self.assertEqual([(r['actor'], r['reply_to']) for r in history['items']],
                             [('user', issue), ('reviewer', message)])
            self.assertEqual(history['items'][1]['text'], 'Please confirm the payment date.')
            replacement = history['items'][1]['suggestion']['id']
            ref = client.get(f'/api/review/issues/{issue}/suggestion/{replacement}')
            self.assertEqual(ref.json()['form'], proposal)
            self.assertEqual(financial.read_bytes(), saved)
            adapter.run.assert_called_once()

            weekly = client.app.state.worker.weekly
            weekly.gate = Mock()
            def send(text):
                response = client.post(f'/api/review/issues/{issue}/answer/{token_hex(16)}', json={'text': text})
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()['audit_id']
            def finding():
                items = client.get('/api/drafts?include_review=true').json()['review']['items']
                return next(item for item in items if item['id'] == issue)
            def finish():
                for _ in range(5):
                    weekly.tick()
                    item = finding()
                    if item.get('followup') != 'waiting':
                        return item
                self.fail('The reviewer did not finish its turn')
            replies = Mock(run=Mock(return_value=Result(dict(text='Here is the explanation.', suggestion=None))))
            with patch('adapters.get_adapter', return_value=replies):
                discarded = send('An older answer')
                weekly.tick()
                weekly.tick()
                newest = send('A newer answer')
                finish()
                rows = audit.records(self.ledger)
                self.assertFalse(any(r['actor'] == 'reviewer' and r['reply_to'] == discarded
                                     for r in rows if r['kind'] == 'review-message'))
                self.assertTrue(any(r.get('reply_to') == newest for r in rows))
                for _ in range(4):
                    send('A further detail')
                    finish()
                send('Please explain one more detail')
                calls = replies.run.call_count
                weekly.tick()
                self.assertEqual(replies.run.call_count, calls)
                self.assertEqual(finding()['reason'], 'Six reviewer replies this week')
                next_week = date.fromisoformat(weekly.today()) + timedelta(days=7)
                weekly.today = next_week.isoformat
                weekly.next_check = 0
                weekly.tick()
                self.assertEqual(replies.run.call_count, calls + 1)
                self.assertEqual(client.post(f'/api/review/issues/{issue}/keep/{token_hex(16)}', json={}).status_code, 200)
                weekly.tick()
                self.assertEqual(sum(r['kind'] == 'review-message' and r['actor'] == 'reviewer'
                                     for r in audit.records(self.ledger)), 6)

    def test_review_attachment_rollback_retains_temporary_upload(self):
        from transactions import Transaction
        line = self.add()
        issue = self.review_suggestions((line, {'kind': 'question-only'}))[line]['id']
        with self.client(worker=False) as client:
            draft = self.create(client, text='')
            url = f'/api/review/issues/{issue}/answer/{token_hex(16)}'
            with patch.object(Transaction, 'verified_commit', side_effect=ValueError('Fictional verification refusal')):
                self.assertEqual(client.post(url, json={'draft_id': draft}).status_code, 409)
            self.assertFalse((self.ledger / 'Bills/Reviews').exists())
            self.assertFalse(any(r['kind'] == 'review-message' for r in audit.records(self.ledger)))
            self.assertEqual(client.get(f'/api/drafts/{draft}').json()['state'], 'editing')
            self.assertEqual(client.post(url, json={'draft_id': draft}).status_code, 200)

    def test_review_answer_replay_completes_draft_left_open_by_a_hard_stop(self):
        import drafts as drafts_module
        line = self.add()
        issue = self.review_suggestions((line, {'kind': 'question-only'}))[line]['id']
        with self.client(worker=False) as client:
            draft = self.create(client, text='')
            url = f'/api/review/issues/{issue}/answer/{token_hex(16)}'
            with patch.object(drafts_module.Drafts, 'completed', side_effect=RuntimeError('Fictional hard stop')):
                try:
                    stopped = client.post(url, json={'draft_id': draft}).status_code
                except RuntimeError:
                    stopped = None
                self.assertNotEqual(stopped, 200)
            self.assertTrue(any(r['kind'] == 'review-message' for r in audit.records(self.ledger)))
            self.assertEqual(client.get(f'/api/drafts/{draft}').json()['state'], 'editing')
            replay = client.post(url, json={'draft_id': draft})
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertTrue(replay.json()['replayed'])
            self.assertEqual(client.get(f'/api/drafts/{draft}').json()['state'], 'added')

    def test_review_followup_retry_keeps_reply_identity(self):
        line = self.add()
        issue = self.review_suggestions((line, {'kind': 'question-only'}))[line]['id']
        with self.client(worker=False, timeout=15) as client:
            weekly = client.app.state.worker.weekly
            weekly.gate = Mock()
            response = client.post(f'/api/review/issues/{issue}/explain/{token_hex(16)}', json={})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('precedent', audit.records(self.ledger)[-1]['text'])
            weekly.folder.rename(weekly.folder.with_name('old-review-drafts'))
            adapter = Mock(run=Mock(return_value=Result(dict(text='\u200b'))))
            def finding():
                view = client.get('/api/drafts?include_review=true').json()['review']
                return next(item for item in view['items'] if item['id'] == issue)
            reply_id = finding()['reply_id']
            with patch('adapters.get_adapter', return_value=adapter):
                for _ in range(4):
                    weekly.tick()
                self.assertEqual(finding()['followup'], 'failed')
                self.assertEqual(adapter.run.call_count, 3)
                retry = f'/api/review/issues/{issue}/retry/{reply_id}'
                for _ in range(2):
                    self.assertEqual(client.post(retry, json={}).json()['reply_id'], reply_id)
                adapter.run.return_value = Result(dict(text='Please check the filed reference.'))
                weekly.tick()
                weekly.tick()
                self.assertNotIn('followup', finding())
                self.assertEqual(audit.records(self.ledger)[-1]['id'], reply_id)
