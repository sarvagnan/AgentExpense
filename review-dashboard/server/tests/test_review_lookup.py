"""Offline review grant and profile checks using fictional ledgers."""
from dataclasses import replace
import io
import json
import os
import subprocess
import time
from unittest.mock import patch

from agent_gate import fingerprint, get_gate
from agent_pause import AgentPause
from support import Fixture, entry, posting
from settings import Settings


class ReviewLookupTests(Fixture):
    def setUp(self):
        super().setUp()
        self.work = self.base
        from audit import append, event
        from store import dump
        identity = '0000000000000001'
        dump(self.ledger / 'data/expenses/2026.json', [
            entry(identity, '2026-01-02', 'Fictional workshop',
                  [posting('Assets:Cash', '-10.00'), posting('Expenses:Food', '10.00')]),
        ])
        append(self.ledger, event('draft', draft_id='f' * 32, outcome='added', text='Fictional workshop',
                                  saved_at=None, pieces=None, files=[], questions=[], runs=[], proposal=None,
                                  doubts=[], check_errors=[], error=None, entry_id=identity))

    def revision(self, line):
        from review_lookup import issue_grant
        with patch('review_lookup.committed_snapshot', return_value=self.ledger):
            return issue_grant(self.ledger, self.work, 'a' * 40, [line], {})

    def test_review_lookup_pages_and_budget(self):
        from audit import records
        from review_lookup import PAGE, ReviewLookup, encode
        line = records(self.ledger)[0]['id']
        grant = self.revision(line)
        store = ReviewLookup(self.ledger, grant)
        selected = store.pending()['items'][0]
        self.assertEqual(selected['id'], line)
        snapshot = json.loads(store.entry(selected['entry_id'])['data'])
        self.assertEqual(snapshot['entry']['id'], selected['entry_id'])
        self.assertEqual(set(snapshot['accounts']), {p['account'] for p in snapshot['entry']['postings']})
        value = '🧾' * 18000
        first = store.page('audit', [line], value)
        parts = [first['data']]
        while first['next']:
            self.assertLessEqual(len(json.dumps(first, ensure_ascii=False).encode()), 24 * 1024)
            first = store.page('audit', [line], value, first['next'])
            parts.append(first['data'])
        self.assertLessEqual(len(json.dumps(first).encode()), 24 * 1024)
        self.assertEqual(json.loads(''.join(parts)), value)

        rule = 'c' * 16
        required = [('audit_line', [line]), *(('evidence', ['e' * 32, page]) for page in range(1, 401))]
        store = ReviewLookup(self.ledger, replace(grant, rules=(rule,), required={line: required}, rule_sources={rule: [line]}))
        pending = store.pending()
        self.assertEqual(pending['status'], 'page_limit')
        self.assertEqual(pending['items'], [{'id': line}])
        self.assertEqual(pending['rule_requirements'], {rule: dict(
            required=[dict(tool='review_rule', arguments={'rule_id': rule})], sources=[line])})
        self.assertLessEqual(len(encode(pending).encode()), PAGE)
        result = store.audit_line(line)
        parts = []
        while True:
            self.assertLessEqual(len(encode(result).encode()), PAGE)
            parts.append(result['data'])
            if not result['next']:
                break
            result = store.audit_line(line, cursor=result['next'])
        calls = json.loads(''.join(parts))['required']
        self.assertEqual(calls[0], dict(tool='review_audit_line', arguments={'line_id': line}))
        self.assertEqual(calls[1:], [dict(tool='review_evidence', arguments={'evidence_id': 'e' * 32, 'page': page})
                                     for page in range(1, 401)])

    def test_review_lookup_refuses_forged_grants_and_paths(self):
        from audit import records
        from review_lookup import ReviewLookup
        line = records(self.ledger)[0]['id']
        grant = self.revision(line)
        store = ReviewLookup(self.ledger, grant)
        self.assertEqual(store.audit_line('ungranted'), {'status': 'outside_grant'})
        result = store.page('audit', [line], 'x' * 100000)
        self.assertEqual(store.page('entry', [line], {}, result['next']), {'status': 'invalid_cursor'})
        self.assertEqual(store.page('audit', ['different'], {}, result['next']), {'status': 'invalid_cursor'})
        self.assertEqual(ReviewLookup(self.ledger, replace(grant, expires=time.time()-1)).pending(),
                         {'status': 'expired_grant'})
        (self.ledger / 'Bills/link').symlink_to(self.work)
        for path in ('../ledger.json', 'Bills/../ledger.json', 'Bills/link/secret', 'Bills/../../secret'):
            with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                store.source(path, None)
        with self.assertRaises(ValueError):
            from review_lookup import issue_grant
            issue_grant(self.ledger, self.work, 'main | @', [line], {})

    def test_review_profile_defaults_and_gate_caches(self):
        (self.work / 'local.json').write_text('{}')
        with patch('settings.APPLICATION', self.work), patch.dict(os.environ, {
            'LEDGER_DIR': str(self.ledger), 'EXPENSES_STATE_DIR': str(self.work/'state'),
            'EXPENSES_AGENT': 'pi', 'EXPENSES_AGENT_MODEL': 'fictional/add',
        }):
            inherited = Settings.from_env()
            self.assertEqual(inherited.for_role('review').model, 'fictional/add')
            (self.work / 'local.json').write_text('{"reviewAgent": {}}')
            self.assertEqual(Settings.from_env().for_role('review').adapter, 'codex')
            (self.work / 'local.json').write_text(json.dumps({'reviewAgent': {'adapter': 'pi', 'model': 'fictional/reviewer'}}))
            distinct = Settings.from_env()
            review = distinct.for_role('review')
            self.assertEqual(review.model, 'fictional/reviewer')
            self.assertEqual(review.agent_home, distinct.agent_home)
            with patch.dict(os.environ, {'ROLE': 'review', 'ADAPTER': 'pi'}):
                from settings import command_settings
                with self.assertRaisesRegex(ValueError, 'ADAPTER'):
                    command_settings()
        self.assertEqual(fingerprint(inherited, 'fixture'), fingerprint(distinct, 'fixture'))
        review_fingerprint = fingerprint(review, 'fixture')
        self.assertEqual(review_fingerprint['role'], 'review')
        self.assertIn('review_evidence', {tool['name'] for tool in review_fingerprint['review_inventory']})
        add_gate, review_gate = get_gate(distinct), get_gate(review)
        add_gate.save_cache({'state': 'ready'})
        self.assertEqual(review_gate.read_cache(), {})
        review_gate.save_cache({'state': 'paused'})
        self.assertEqual(add_gate.read_cache(), {'state': 'ready'})
        add_pause, review_pause = AgentPause(self.ledger, settings=distinct), AgentPause(self.ledger, settings=review)
        review_pause.fail('usage_limit')
        self.assertIsNone(add_pause.status())
        self.assertIsNotNone(AgentPause(self.ledger, settings=review).status())
        self.assertIsNone(AgentPause(self.ledger, settings=replace(review, model='fictional/other')).status())
        from app import create_app
        from fastapi.testclient import TestClient
        status = TestClient(create_app(distinct, start_worker=False)).get('/api/agent-status').json()
        self.assertEqual(status['model'], 'fictional/add')
        self.assertEqual(status['reviewer']['model'], 'fictional/reviewer')
        self.assertEqual(status['reviewer']['agent_pause']['kind'], 'usage_limit')
        self.assertIsNone(status['agent_pause'])


class CommittedReviewTests(Fixture):
    def test_review_transports_use_the_same_committed_batch(self):
        from audit import audit_path, event
        from audit_changes import change_line
        from PIL import Image
        from review_lookup import committed_snapshot, review_task
        from store import dump
        from weekly_review_batch import scope

        proof = self.ledger / 'Bills/folder/receipt.png'
        proof.parent.mkdir()
        Image.new('RGB', (10, 10), 'white').save(proof)
        identity = '0000000000000001'
        original = entry(identity, '2026-01-02', 'Fictional workshop',
                         [posting('Assets:Cash', '-10.00'), posting('Expenses:Food', '10.00')],
                         evidence=['Bills/folder/receipt.png'])
        expenses = self.ledger / 'data/expenses/2026.json'
        dump(expenses, [original])
        (self.ledger / '.gitattributes').write_text('data/expenses/* export-ignore\n')
        self.checkpoint('Fictional evidence revision')
        original_revision = self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
        hand = change_line(self.ledger, original_revision, 'user', recorded=True)
        record = event('draft', draft_id='f' * 32, outcome='added', text='Fictional workshop', saved_at=None,
                       pieces={'source': 'Bills/ungranted.png'}, files=[{'original_name': 'receipt.png', 'filed_path': 'Bills/folder/receipt.png'}],
                       questions=[], runs=[], proposal=None, doubts=[], check_errors=[], error=None, entry_id=identity)
        path = audit_path(self.ledger, record)
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(record) + '\n' + json.dumps(hand) + '\n')
        self.checkpoint('Fictional committed review fixture')
        revision = self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
        dump(expenses, [{**original, 'description': 'Uncommitted edit'}])
        proof.unlink()
        proof.symlink_to(expenses)
        snapshot = committed_snapshot(self.ledger, self.base, revision)
        review, required, _ = scope(self.ledger, self.base, revision, snapshot, dict(lines=[record['id'], hand['id']], rules={}))
        grant = replace(review.grant, required=required)
        self.assertEqual(len(grant.evidence), 2)
        evidence = next(iter(grant.evidence))
        task = review_task(self.settings, self.base, grant, 'Review the committed batch.', {}, time.monotonic() + 30)
        tools = task.lookups
        self.assertEqual(task.images, tools.images)
        self.assertIn('Weekly review policy', task.prompt)
        calls = [('review_pending', {}), ('review_entry', {'entry_id': identity}),
                 ('review_evidence', {'evidence_id': evidence})]
        self.assertEqual(len(tools.images), 2)
        history = json.loads(tools.call('review_revision', {'line_id': hand['id']})['data'])
        self.assertEqual(history['commit'], original_revision)
        self.assertIn('Bills/folder/receipt.png', history['diff'])
        from review_lookup import ReviewLookup
        with self.assertRaisesRegex(ValueError, 'evidence_unavailable'):
            ReviewLookup(self.ledger, grant).source('Bills/folder', original_revision)
        pending = tools.call('review_pending', {'limit': 1})
        self.assertEqual(pending['items'][0]['required'], [
            dict(tool='review_audit_line', arguments={'line_id': record['id']}),
            dict(tool='review_entry', arguments={'entry_id': identity})])
        second = tools.call('review_pending', {'limit': 1, 'cursor': pending['next']})['items'][0]
        self.assertEqual(second['id'], hand['id'])
        self.assertIn(dict(tool='review_revision', arguments={'line_id': hand['id'], 'parent_index': 0}), second['required'])
        self.assertEqual(json.loads(tools.call(*calls[1])['data'])['entry']['description'], 'Fictional workshop')
        self.assertEqual(json.loads(tools.call(*calls[2])['data'])['visuals'], [{'page': 1, 'image': 1}])
        oversized = io.BytesIO()
        Image.new('1', (2001, 2000)).save(oversized, 'PNG')
        oversized_store = ReviewLookup(self.ledger, grant)
        with patch.object(oversized_store, 'source', return_value=oversized.getvalue()):
            self.assertEqual(oversized_store.evidence(evidence)['status'], 'oversized')
        scan = io.BytesIO()
        Image.new('RGB', (1100, 600), 'white').save(scan, 'PDF')
        with patch.object(oversized_store, 'source', return_value=scan.getvalue()):
            render_work = self.base / 'rendered-proof'
            render_work.mkdir()
            images = oversized_store.images(render_work)
            with Image.open(images[0]) as rendered:
                self.assertLessEqual(rendered.width * rendered.height, 4_000_000)
            pages = json.loads(oversized_store.evidence(evidence)['data'])
            self.assertEqual((pages['pages'], pages['visuals']), (1, [{'page': 1, 'image': 1}]))
        payload = ''.join(json.dumps(dict(jsonrpc='2.0', method='tools/call', id=i,
                                           params=dict(name=name, arguments=args))) + '\n'
                          for i, (name, args) in enumerate(calls))
        result = subprocess.run(tools.command, cwd=self.base, input=payload, text=True, capture_output=True, check=True)
        stdio = [json.loads(json.loads(row)['result']['content'][0]['text']) for row in result.stdout.splitlines()]
        self.assertEqual(stdio, [tools.call(*call) for call in calls])
        for forgery in ({'path': '../ledger.json'}, {'grant': {'lines': []}}):
            with self.assertRaisesRegex(ValueError, 'Invalid lookup arguments'):
                tools.call('review_evidence', {'evidence_id': evidence, **forgery})
