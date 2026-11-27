"""Weekly reports through the real audit and a fake reviewer."""
from secrets import token_hex
from unittest.mock import Mock, patch
import json

from support import Fixture, posting, rule
import audit
from adapters.base import Result
from store import dump, read_json
import test_review
import test_weekly_review_decisions as decisions


class WeeklyReviewReportTests(Fixture):
    Decisions = decisions.WeeklyReviewDecisionTests
    add, clock, reviewer, run_batch, review_suggestions = (
        Decisions.add, Decisions.clock, Decisions.reviewer, Decisions.run_batch, Decisions.review_suggestions)
    url = '/api/review/weekly'

    def drain(self, weekly):
        for _ in range(30):
            if not weekly.tick() and weekly.delay():
                return
        self.fail('Weekly review did not yield')

    def start(self, client):
        response = client.post(self.url, json=dict(action='start', request_id=token_hex(16)))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['run_id']

    def test_carry_over_answers_keep_asking_until_the_owner_answers(self):
        from weekly_review_report import carry_answers
        waiting, answered = 'a' * 32, 'b' * 32
        items = [dict(id=waiting, turns=[dict(actor='agent')]), dict(id=answered, turns=[dict(actor='user')])]
        with self.assertRaisesRegex(ValueError, f'{waiting}: the owner has not answered it'):
            carry_answers(dict(carry=[dict(issue=waiting, question=None), dict(issue=answered, question=None)]), items)
        with self.assertRaisesRegex(ValueError, f'{waiting}: the question needs concrete text'):
            carry_answers(dict(carry=[dict(issue=waiting, question=' '), dict(issue=answered, question=None)]), items)
        kept = [dict(issue=waiting, question='Which card paid?'), dict(issue=answered, question=None)]
        self.assertEqual(carry_answers(dict(carry=kept), items), kept)

    def test_carry_over_uses_same_issue_and_partial_answer(self):
        entry_id = test_review.ReviewTests.seed(self)[0]['id']
        manifest = self.ledger / 'ledger.json'
        baseline = self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
        dump(manifest, {**read_json(manifest), 'auditStart': {'after': baseline}})
        source = self.add(entry_id=entry_id)
        issue = self.review_suggestions((source, {'kind': 'question-only'}))[source]
        issue_url = '/api/review/issues/' + issue['id']
        with self.client(worker=False) as client:
            weekly = client.app.state.worker.weekly
            weekly.gate = Mock(**{'status.return_value': {}})
            staging = self.create(client, text='')
            owner = client.post(issue_url + '/answer/' + token_hex(16),
                                json={'text': 'The order included delivery.', 'draft_id': staging})
            self.assertEqual(owner.status_code, 200, owner.text)
            answer_id = owner.json()['audit_id']
            question = 'How much of the total was delivery?'
            with patch('adapters.get_adapter', return_value=Mock(run=Mock(
                    return_value=Result(dict(text=question, suggestion=None))))):
                self.drain(weekly)
            run_id = self.start(client)

            def invoke(task):
                if 'fine' in task.schema['properties']:
                    rows = audit.records(self.ledger)
                    carry, = [row for row in rows if row['kind'] == 'review-carry-over']
                    return Result(dict(fine=[], decisions=[carry['id']], patterns=[]))
                self.assertIn('The order included delivery.', task.prompt)
                self.assertIn('"owner_answered":true', task.prompt)
                self.assertIn(question, task.prompt)
                pending = task.lookups.call('review_pending', {})
                self.assertEqual(pending['items'], [])
                self.assertEqual(pending['carry'], [dict(id=issue['id'], question=question)])
                self.assertEqual(task.lookups.call('review_run_facts', {}), {'status': 'outside_grant'})
                file, = next(row['message_evidence'] for row in audit.records(self.ledger) if row['id'] == answer_id)
                from rules_service import digest
                evidence = task.lookups.call('review_evidence', {'evidence_id': digest(file['path'])[:32]})
                self.assertEqual(evidence['status'], 'available')
                return Result(dict(lines=[], rules=[], carry=[dict(issue=issue['id'], question=question)]))

            with patch('adapters.get_adapter', return_value=Mock(run=Mock(side_effect=invoke))):
                self.drain(weekly)
            rows = audit.records(self.ledger)
            carry, = [row for row in rows if row['kind'] == 'review-carry-over']
            self.assertEqual((carry['run_id'], carry['review_issue_id'], carry['decide']), (run_id, issue['id'], question))
            self.assertIn(answer_id, carry['prior_turns'])
            self.assertEqual([row['id'] for row in rows if row['kind'] == 'review'], [issue['id']])
            self.assertEqual(audit.unreviewed(rows), [])
            item, = [row for row in client.get('/api/drafts?include_review=true').json()['review']['items']
                     if row['kind'] == 'review-issue']
            self.assertEqual((item['id'], item['first_raised'], item['decide']), (issue['id'], issue['time'], question))
            run = client.get(self.url).json()['history'][0]
            self.assertEqual((run['status'], run['checked'], run['issues']), ('completed', 0, 0))
            decision, = run['report']['decisions']
            self.assertEqual((decision['number'], decision['issue_id'], decision['decide']), (1, issue['id'], question))

            self.assertEqual(decision['first_raised'], issue['time'])
            self.assertEqual(client.get(decision['source_href']).json()['id'], source)
            owner_items = client.get(f'/api/entries/{entry_id}/review-history').json()['items']
            answer = next(row for row in owner_items if row['id'] == answer_id)
            self.assertTrue(answer['message_evidence'][0]['href'].endswith(answer['message_evidence'][0]['path'][6:]))

            linked = self.add(entry_id=entry_id)
            self.checkpoint('Fictional new audit line for the same entry')
            run_id = self.start(client)
            matched = []

            def review_linked(task):
                if 'fine' in task.schema['properties']:
                    notes = json.loads(task.lookups.call('review_run_facts', {})['data'])
                    return Result(dict(fine=[dict(lines=[linked], reason='Checked the linked line')],
                                       decisions=[notes['decisions'][0]['id']], patterns=[]))
                self.assertIn(question, task.prompt)
                item, = task.lookups.call('review_pending', {})['items']
                self.assertEqual(item['id'], linked)
                task.lookups.call('review_audit_line', {'line_id': linked})
                task.lookups.call('review_entry', {'entry_id': entry_id})
                matched.append(item)
                if len(matched) == 1:
                    verdict = dict(line=linked, verdict='issue', note=issue['note'], decide=question, evidence=[],
                                   suggestion={'kind': 'question-only'})
                else:
                    verdict = dict(line=linked, verdict='ok', note='Checked the linked line')
                return Result(dict(lines=[verdict], rules=[], carry=[dict(issue=issue['id'], question=question)]))

            with patch('adapters.get_adapter', return_value=Mock(run=Mock(side_effect=review_linked))):
                self.drain(weekly)
            self.assertEqual(len(matched), 2)
            run = client.get(self.url).json()['history'][0]
            self.assertEqual((run['run_id'], run['checked'], run['issues']), (run_id, 1, 0))
            self.assertEqual(run['report']['decisions'][0]['issue_id'], issue['id'])
            carry_id = run['report']['decisions'][0]['id']
            reply = client.post(issue_url + '/answer/' + token_hex(16), json={'text': 'Delivery was five.'})
            self.assertEqual(reply.status_code, 200, reply.text)
            row = next(r for r in audit.records(self.ledger) if r['id'] == reply.json()['audit_id'])
            self.assertEqual(row['reply_to'], carry_id)

            def follow_up(task):
                self.assertIn(carry_id, task.prompt)
                self.assertIn('Delivery was five.', task.prompt)
                return Result(dict(text='The amount is now clear.', suggestion=None))

            with patch('adapters.get_adapter', return_value=Mock(run=Mock(side_effect=follow_up))) as adapter:
                self.drain(weekly)
                adapter.return_value.run.assert_called_once()

    def test_summary_checks_ids_counts_and_resumes_without_reviewing_again(self):
        recurring = rule('3' * 16, '2026-12-01', 'Fictional studio membership',
                         [posting('Expenses:Food', '12.50'), posting('Assets:Cash', '-12.50')])
        dump(self.ledger / 'data/forecast.json', [recurring])
        ok, finding, second_ok = self.add(), self.add(), self.add(rule_id=recurring['id'])
        self.checkpoint('Fictional batch for the report')
        calls = []

        def invoke(task):
            if 'fine' not in task.schema['properties']:
                calls.append('batch')
                for row in task.lookups.call('review_pending', {})['items']:
                    task.lookups.call('review_audit_line', {'line_id': row['id']})
                return Result(dict(lines=[
                    dict(line=ok, verdict='ok', note='Checked the first record'),
                    dict(line=finding, verdict='issue', note='Amount needs context', decide='Was this for two sessions?',
                         evidence=[], suggestion={'kind': 'question-only'}),
                    dict(line=second_ok, verdict='ok', note='Checked the second record')],
                    rules=[dict(rule=recurring['id'], verdict='propose-removal', reason='Review the renewal')]))
            calls.append('summary')
            page = task.lookups.call('review_run_facts', {})
            notes = json.loads(page['data'])
            self.assertEqual(notes['counts'], dict(checked=3, issues=1, rule_proposals=1, carried=0))
            issue, proposal = sorted(notes['decisions'], key=lambda row: row['kind'])
            report = dict(fine=[dict(reason='Receipts reconcile', lines=[ok, second_ok])],
                          decisions=[issue['id'], proposal['id']],
                          patterns=[dict(note='Review payment grouping', citations=[ok, issue['id']],
                                         suggested_change='Explain split payments in the policy.')])
            attempts = calls.count('summary')
            if attempts == 1:
                report['fine'].append(dict(reason='Duplicate grouping', lines=[ok]))
            elif attempts == 2:
                report['decisions'] = []
            elif attempts == 3:
                report['patterns'][0]['citations'].append('e' * 32)
            return Result(report)

        adapter = Mock(run=Mock(side_effect=invoke))
        with self.client(worker=False, timeout=15) as client, patch('adapters.get_adapter', return_value=adapter):
            weekly = client.app.state.worker.weekly
            weekly.gate = Mock(**{'status.return_value': {}})
            run_id = self.start(client)
            self.drain(weekly)
            self.assertEqual(calls, ['batch', 'summary', 'summary', 'summary'])
            status = client.get(self.url).json()
            self.assertEqual(status['history'], [])
            self.assertEqual((status['active']['checked'], status['active']['retry_action']), (3, 'retry-summary'))

        with self.client(worker=False, timeout=15) as client, patch('adapters.get_adapter', return_value=adapter):
            weekly = client.app.state.worker.weekly
            weekly.gate = Mock(**{'status.return_value': {}})
            retry = client.post(self.url, json=dict(action='retry-summary', run_id=run_id, request_id=token_hex(16)))
            self.assertEqual(retry.status_code, 200, retry.text)
            for _ in range(8):
                weekly.tick()
                if calls.count('summary') == 4:
                    break
            self.assertEqual(client.get(self.url).json()['active']['state'], 'report-pending')
            self.assertFalse(any(row['kind'] == 'weekly-review-summary' for row in audit.records(self.ledger)))

        with self.client(worker=False, timeout=15) as client, patch('adapters.get_adapter', return_value=adapter):
            weekly = client.app.state.worker.weekly
            self.drain(weekly)
            self.assertEqual(calls, ['batch', 'summary', 'summary', 'summary', 'summary'])
            run = client.get(self.url).json()['history'][0]
            report = run['report']
            group, = report['fine']
            decision, proposal = report['decisions']
            pattern, = report['patterns']
            self.assertEqual([group['number'], decision['number'], proposal['number'], pattern['number']], [1, 2, 3, 4])
            self.assertEqual(group['lines'], [ok, second_ok])
            self.assertEqual(decision['status'], 'open')
            self.assertEqual((proposal['rule_id'], proposal['status']), (recurring['id'], 'open'))
            self.assertEqual(pattern['citations'], [ok, decision['issue_id']])
            response = client.post('/api/review/issues/' + decision['issue_id'] + '/keep/' + token_hex(16), json={})
            self.assertEqual(response.status_code, 200, response.text)
            resolved, unchanged_proposal = client.get(self.url).json()['history'][0]['report']['decisions']
            self.assertEqual((resolved['number'], resolved['href'], resolved['status']),
                             (decision['number'], decision['href'], 'resolved'))
            self.assertEqual(resolved['resolution_id'], response.json()['audit_id'])
            change, = client.get('/api/review').json()['weekly_history']
            self.assertEqual(change['source_href'], resolved['resolution_href'])
            self.assertEqual(unchanged_proposal, proposal)
            self.assertFalse(audit.unreviewed(audit.records(self.ledger)))
