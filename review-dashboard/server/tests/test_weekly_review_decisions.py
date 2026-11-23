"""Weekly review decisions through the fake batch adapter and normal save routes."""
from copy import deepcopy
from secrets import token_hex

from support import Fixture, posting, rule

import audit
from corrections import wire_entry
from store import dump
import test_review
import test_weekly_review_batch as batch_tests


class WeeklyReviewDecisionTests(Fixture):
    add = batch_tests.WeeklyReviewBatchTests.add
    clock = batch_tests.WeeklyReviewBatchTests.clock
    reviewer = batch_tests.WeeklyReviewBatchTests.reviewer
    run_batch = batch_tests.WeeklyReviewBatchTests.run_batch

    def review_suggestions(self, *items, rules=()):
        from weekly_review_batch import freeze_run
        answer = dict(lines=[dict(line=line, verdict='issue', note='Check the saved details',
                                  decide='Should the saved details change?', evidence=[],
                                  suggestion=suggestion) for line, suggestion in items], rules=list(rules))
        self.checkpoint('Fictional sources for checked suggestions')
        result = self.run_batch(freeze_run(self.settings), self.reviewer(answer))
        self.assertEqual(result['status'], 'committed', result)
        return {row['line_id']: row for row in audit.records(self.ledger) if row['kind'] == 'review'}

    def test_review_suggestion_reuses_entry_and_rule_checks(self):
        rows = test_review.ReviewTests.seed(self, statement=True)
        locked, ordinary = rows[2:4]
        recurring = rule('a' * 16, '2026-12-01', 'Fictional renewal',
                         [posting('Expenses:Food', '10.00'), posting('Assets:Cash', '-10.00')])
        dump(self.ledger / 'data/forecast.json', [recurring])
        locked_form = wire_entry(self.settings, locked)
        locked_form['entry']['postings'][0]['amount'] = '13.50'
        locked_form['entry']['postings'][1]['amount'] = '-13.50'
        entry_form = wire_entry(self.settings, ordinary)
        entry_form['entry']['description'] = 'Willow Workshop admission'
        rule_form = {key: deepcopy(recurring[key]) for key in
                     ('description', 'date', 'status', 'postings', 'schedule', 'amount', 'notes')}
        rule_form['postings'][0]['amount'] = '20.00'
        locked_id = self.add(entry_id=locked['id'])
        rule_id = self.add(rule_id=recurring['id'])
        entry_id = self.add(entry_id=ordinary['id'])
        rows = self.review_suggestions(
            (locked_id, dict(kind='entry', target=locked['id'], answer=locked_form)),
            (rule_id, dict(kind='rule', target=recurring['id'], form=rule_form)),
            (entry_id, dict(kind='entry', target=ordinary['id'], answer=entry_form)),
            rules=[dict(rule=recurring['id'], verdict='propose-removal', reason='Check the schedule')])
        findings = {identity: row['suggestion'] for identity, row in rows.items()}
        self.assertEqual(findings[locked_id]['kind'], 'question-only')
        self.assertIn('statement', findings[locked_id]['reason'])
        self.assertEqual(findings[rule_id]['kind'], 'question-only')
        self.assertIn('forecast', findings[rule_id]['reason'])
        self.assertEqual(findings[entry_id]['form'], entry_form)
        self.assertRegex(findings[entry_id]['id'], '^[0-9a-f]{32}$')

    def test_review_suggestion_stale_at_open_or_save(self):
        entry = test_review.ReviewTests.seed(self)[0]
        original = wire_entry(self.settings, entry)
        suggested = deepcopy(original)
        suggested['entry']['description'] = 'Willow Workshop admission'
        source = self.add(entry_id=entry['id'])
        issue = self.review_suggestions((source, dict(kind='entry', target=entry['id'], answer=suggested)))[source]
        link = dict(review_issue_id=issue['id'], review_suggestion_id=issue['suggestion']['id'])
        url = f'/api/review/issues/{issue["id"]}/suggestion/{issue["suggestion"]["id"]}'
        editor = f'/api/entries/{entry["id"]}'
        with self.client(worker=False) as client:
            opened = client.get(url)
            self.assertEqual(opened.status_code, 200, opened.text)
            self.assertEqual(opened.json()['form'], suggested)
            self.assertEqual(opened.json()['baseline'], original)
            draft = client.post(editor + '/correction-form', json=link).json()
            draft_url = f'/api/drafts/{draft["id"]}'
            claim = client.post(draft_url + '/claim', json={}).json()
            edited = deepcopy(suggested)
            edited['entry']['notes'].append('Retain my draft edit')
            staged = client.post(draft_url + '/hand-form',
                                 json=dict(proposal=edited, claim_token=claim['claim_token']))
            self.assertEqual(staged.status_code, 200, staged.text)
            version = opened.json()['version']
            concurrent = deepcopy(original)
            concurrent['entry']['description'] = 'Saved by the owner elsewhere'
            updated = client.post(editor + '/corrections/' + token_hex(16),
                                  json=dict(version=version, proposal=concurrent))
            self.assertEqual(updated.status_code, 200, updated.text)
            for route in (url, url.rsplit('/', 1)[0] + '/' + 'b' * 32):
                result = client.get(route)
                self.assertEqual((result.status_code, result.json()['detail']), (409, 'Suggestion is out of date'))
            save = client.post(editor + '/corrections/' + token_hex(16),
                               json=dict(link, version=version, proposal=edited, draft_id=draft['id']))
            self.assertEqual((save.status_code, save.json()['detail']), (409, 'Suggestion is out of date'))
            self.assertEqual(client.get(draft_url).json()['hand_form'], edited)
            self.assertFalse(any(row.get('review_issue_id') == issue['id'] for row in audit.records(self.ledger)))

    def test_review_owner_actions_close_only_on_verified_commit(self):
        from unittest.mock import patch
        from transactions import Transaction

        entry = test_review.ReviewTests.seed(self)[0]
        original = wire_entry(self.settings, entry)
        suggested = deepcopy(original)
        suggested['entry']['description'] = 'Willow Workshop admission'
        recurring = rule('a' * 16, '2026-12-01', 'Fictional membership', entry['postings'])
        dump(self.ledger / 'data/forecast.json', [recurring])
        rule_form = {key: deepcopy(recurring[key]) for key in
                     ('description', 'date', 'status', 'postings', 'schedule', 'amount', 'notes')}
        rule_form['description'] = 'Willow membership'
        entry_line, rule_line, keep_line = self.add(entry_id=entry['id']), self.add(rule_id=recurring['id']), self.add()
        findings = self.review_suggestions(
            (entry_line, dict(kind='entry', target=entry['id'], answer=suggested)),
            (rule_line, dict(kind='rule', target=recurring['id'], form=rule_form)),
            (keep_line, dict(kind='question-only')),
            rules=[dict(rule=recurring['id'], verdict='propose-removal', reason='Check the renewal')])
        missing_target = self.add(entry_id='f' * 16)
        legacy = audit.event('review', line_id=missing_target, verdict='issue',
                             note='Check the archived source', reviewer='Offline reviewer')
        audit.append(self.ledger, legacy)
        self.checkpoint('Fictional legacy issue')
        with self.client(worker=False) as client:
            def review_queue():
                response = client.get('/api/drafts?include_review=true')
                self.assertEqual(response.status_code, 200, response.text)
                queue = response.json()['review']
                self.assertEqual(queue['needs_you'], len(queue['items']))
                return {row['id']: row for row in queue['items']}

            def get_rule():
                return client.get('/api/rules').json()['rules'][0]

            def prefill(line):
                issue = findings[line]
                suggestion = issue['suggestion']
                url = f'/api/review/issues/{issue["id"]}/suggestion/{suggestion["id"]}'
                response = client.get(url)
                self.assertEqual(response.status_code, 200, response.text)
                link = dict(review_issue_id=issue['id'], review_suggestion_id=suggestion['id'])
                return dict(link, version=response.json()['version']), response.json()['form']

            items = review_queue()
            self.assertEqual(client.get('/api/review/weekly').status_code, 200)
            self.assertEqual(items[legacy['id']]['decide'], 'Review the cited record.')
            self.assertEqual(client.get(items[legacy['id']]['source_href']).json()['id'], missing_target)
            entry_link, proposal = prefill(entry_line)
            correct = f'/api/entries/{entry["id"]}/corrections/' + token_hex(16)
            self.assertEqual(client.post(correct, json=dict(entry_link, proposal=original)).status_code, 409)
            entry_body = dict(entry_link, proposal=proposal)
            keep_issue = findings[keep_line]['id']
            keep = f'/api/review/issues/{keep_issue}/keep/' + token_hex(16)
            note = dict(note='Confirmed the original details')
            with patch.object(Transaction, 'verified_commit', side_effect=RuntimeError('Offline verifier refusal')):
                self.assertEqual(client.post(correct, json=entry_body).status_code, 409)
                self.assertEqual(client.post(keep, json=note).status_code, 409)
            self.assertIn(entry_link['review_issue_id'], review_queue())
            self.assertIn(keep_issue, review_queue())
            saved = client.post(correct, json=entry_body)
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertTrue(client.post(correct, json=entry_body).json()['replayed'])
            kept = client.post(keep, json=note)
            self.assertEqual(kept.status_code, 200, kept.text)
            self.assertEqual(client.post(keep, json=note).json()['audit_id'], kept.json()['audit_id'])
            self.assertEqual(client.post(keep, json={'note': 'Different note'}).status_code, 409)

            proposal_id = get_rule()['review']['id']
            audit.append(self.ledger, audit.event('rule-review', rule_id=recurring['id'], rule_verdict='confirm',
                       reason='The later payment matches', reviewer='Offline reviewer', run_id=token_hex(16),
                       batch=1, rule_sources=[rule_line]))
            self.checkpoint('Fictional later automatic confirmation')
            self.assertEqual(get_rule()['review']['id'], proposal_id)
            self.assertIn(proposal_id, review_queue())

            rule_link, form = prefill(rule_line)
            form['description'] = 'Willow annual membership'
            edit_rule = '/api/rules/operations/' + token_hex(16)
            edited = client.post(edit_rule, json=dict(rule_link, rule=form, action='edited', rule_id=recurring['id']))
            self.assertEqual(edited.status_code, 200, edited.text)
            dispose = '/api/rules/operations/' + token_hex(16)
            disposition = dict(action='kept', rule_id=recurring['id'], version=get_rule()['version'])
            self.assertEqual(client.post(dispose, json=dict(disposition, review_proposal_id='b' * 32)).status_code, 409)
            accepted = client.post(dispose, json=dict(disposition, review_proposal_id=proposal_id))
            self.assertEqual(accepted.status_code, 200, accepted.text)
            items = review_queue()
            self.assertTrue({entry_link['review_issue_id'], rule_link['review_issue_id'], keep_issue, proposal_id}.isdisjoint(items))
            self.assertIsNone(get_rule()['review'])
        audit_by_id = {row['id']: row for row in audit.records(self.ledger)}
        self.assertFalse(audit_by_id[saved.json()['audit_id']]['review_suggestion_edited'])
        self.assertTrue(audit_by_id[edited.json()['audit_id']]['review_suggestion_edited'])
        self.assertEqual(audit_by_id[accepted.json()['audit_id']]['review_proposal_id'], proposal_id)
        self.assertEqual(audit_by_id[kept.json()['audit_id']]['review_issue_id'], keep_issue)
        self.assertNotIn(kept.json()['audit_id'], {r['id'] for r in audit.unreviewed(audit.records(self.ledger))})

    def test_review_attributes_a_missing_actor_once(self):
        from audit_changes import identity
        from store import read_json

        manifest_path = self.ledger / 'ledger.json'
        manifest = read_json(manifest_path)
        manifest['auditStart'] = {'after': identity(self.ledger, 'main')['commit']}
        dump(manifest_path, manifest)
        self.checkpoint('Begin checking fictional history')
        test_review.ReviewTests.seed(self)
        commit = identity(self.ledger, 'main')['commit']

        with self.client(worker=False) as client:
            def items():
                return client.get('/api/drafts?include_review=true').json()['review']['items']

            found = next(row for row in items() if row['kind'] == 'missing-actor')
            self.assertEqual(found['id'], commit)
            url = f'/api/review/unattributed/{commit}'
            self.assertEqual(client.post(url, json={'actor': 'reviewer'}).status_code, 422)
            self.assertEqual(client.post('/api/review/unattributed/' + 'b' * 40, json={'actor': 'user'}).status_code, 409)
            saved = client.post(url, json={'actor': 'agent'})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()['actor'], 'agent')
            replay = client.post(url, json={'actor': 'user'}).json()
            self.assertEqual(replay['actor'], 'agent')
            self.assertEqual(replay['audit_id'], saved.json()['audit_id'])
            self.assertEqual(items(), [])
            changes = [row for row in audit.unreviewed(audit.records(self.ledger)) if row['kind'] == 'change']
            self.assertEqual([(row['commit'], row['actor']) for row in changes], [(commit, 'agent')])

            manifest['auditStart']['after'] = 'b' * 40
            dump(manifest_path, manifest)
            self.checkpoint('Fictional moved audit boundary')
            diagnostic, = items()
            self.assertEqual(diagnostic['kind'], 'history-diagnostic')
            self.assertNotIn('actors', diagnostic)
