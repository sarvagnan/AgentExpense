"""Offline checks for the committed weekly-review batch boundary."""
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from support import Fixture, png, posting, rule

import audit
from adapters.base import Result
from store import dump
from transactions import Transaction


class WeeklyReviewBatchTests(Fixture):
    clock = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def add(self, *, files=(), entry_id=None, **fields):
        row = audit.event('draft', draft_id='f' * 32, outcome='failed', text='Fictional expense',
                          saved_at=None, pieces=None, files=list(files), questions=[], runs=[], proposal=None,
                          doubts=[], check_errors=[], error=None, **fields)
        self.clock += timedelta(seconds=1)
        row.update(time=self.clock.isoformat(), entry_id=entry_id)
        audit.append(self.ledger, row)
        return row['id']

    def reviewer(self, answer, *, inspect=True):
        def run(task):
            if inspect:
                for row in task.lookups.call('review_pending', {})['items']:
                    task.lookups.call('review_audit_line', {'line_id': row['id']})
            return Result(answer)
        adapter = Mock(run=Mock(side_effect=run))
        adapter.describe.return_value.isolation = True
        return adapter

    def answer(self, *lines):
        return dict(lines=[dict(line=line, verdict='ok', note='Checked audit record') for line in lines], rules=[])

    def run_batch(self, path, adapter, batch=1, **options):
        from weekly_review_batch import run_batch
        with patch('adapters.get_adapter', return_value=adapter):
            return run_batch(replace(self.settings, timeout=15), path, batch, **options)

    def test_review_retry_names_missing_evidence_page_for_both_profiles(self):
        from review_lookup import encode
        from test_agent import pdf_bytes
        from weekly_review_batch import freeze_run

        (self.ledger / 'Bills/fictional.pdf').write_bytes(pdf_bytes(3))
        for profile in ('codex', 'claude'):
            with self.subTest(profile=profile):
                line = self.add(files=[dict(original_name='Fictional receipt', filed_path='Bills/fictional.pdf')])
                self.checkpoint('Fictional multipage receipt')
                path = freeze_run(self.settings)
                attempt = path.parent / '1/attempt.json'

                def review(task):
                    pending = task.lookups.call('review_pending', {})['items'][0]
                    handle, = pending['evidence_ids']
                    audit_call = dict(tool='review_audit_line', arguments={'line_id': line})
                    page_two, page_three = (dict(tool='review_evidence', arguments={'evidence_id': handle, 'page': page})
                                            for page in (2, 3))
                    self.assertEqual(pending['required'], [audit_call, page_two, page_three])
                    self.assertIn('make the calls in each item’s required list', task.prompt)
                    if adapter.run.call_count == 1:
                        calls = [audit_call, page_two]
                    else:
                        missing = {line: [page_three]}
                        state = json.loads(attempt.read_text())
                        self.assertEqual(state['missing'], missing)
                        self.assertEqual(state['failures'], 1)
                        self.assertIn(state['error'], task.prompt)
                        self.assertIn('Missing required calls by ID: ' + encode(missing), task.prompt)
                        calls = pending['required']
                    for call in calls:
                        self.assertEqual(task.lookups.call(call['tool'], call['arguments'])['status'], 'available')
                    return Result(self.answer(line))

                adapter = self.reviewer(None)
                adapter.run.side_effect = review
                with patch.object(self, 'settings', replace(self.settings, review_profile=(('adapter', profile),))):
                    retry = self.run_batch(path, adapter, one_turn=True)
                    self.assertEqual(retry, dict(status='retry', error='Check all required records and evidence, or report an issue'))
                    self.assertEqual(self.run_batch(path, adapter, one_turn=True)['status'], 'prepared')
                    self.assertEqual(self.run_batch(path, adapter, one_turn=True)['status'], 'committed')
                self.assertEqual(adapter.run.call_count, 2)
                self.assertEqual(json.loads(attempt.read_text())['error'], retry['error'])

    def test_review_records_validation_reason_without_rejected_answer_content(self):
        from weekly_review_batch import freeze_run
        self.add()
        self.checkpoint('Fictional invalid answer')
        path = freeze_run(self.settings)
        adapter = self.reviewer('{"fictional-private-text":0,"fictional-private-text":1}')
        result = self.run_batch(path, adapter)
        reason = 'Return valid review JSON with unique keys'
        self.assertEqual(result, dict(status='failed', error=reason))
        state = (path.parent / '1/attempt.json').read_text()
        self.assertEqual(json.loads(state)['error'], reason)
        self.assertNotIn('fictional-private-text', state)
        for invocation in adapter.run.call_args_list[1:]:
            self.assertIn(reason, invocation.args[0].prompt)
            self.assertNotIn('fictional-private-text', invocation.args[0].prompt)

    def test_review_batch_rejects_missing_duplicate_or_stale_ids(self):
        from weekly_review_batch import freeze_run
        first, second = self.add(), self.add()
        self.checkpoint('Fictional batch')
        for bad in (self.answer(first), self.answer(first, first), self.answer(first, 'a' * 32),
                    '{"lines":[],"lines":[],"rules":[]}', ' ' * (128 * 1024 + 1)):
            with self.subTest(answer=str(bad)[:80]):
                adapter = self.reviewer(bad)
                self.assertEqual(self.run_batch(freeze_run(self.settings), adapter)['status'], 'failed')
                self.assertEqual(adapter.run.call_count, 3)
                self.assertEqual(set(r['id'] for r in audit.unreviewed(audit.records(self.ledger))), {first, second})

        path = freeze_run(self.settings)
        adapter = self.reviewer(self.answer(first, second))
        invoke = adapter.run.side_effect
        def concurrent(task):
            audit.review(self.ledger, self.answer(first)['lines'], 'Offline reviewer', 'Fictional manual review')
            return invoke(task)
        adapter.run.side_effect = concurrent
        self.assertEqual(self.run_batch(path, adapter)['status'], 'stale')
        self.assertEqual(self.run_batch(path, self.reviewer(self.answer(second)))['status'], 'committed')
        self.assertEqual([r['line_id'] for r in audit.records(self.ledger) if r['kind'] == 'review'], [first, second])

    def test_review_cannot_ok_unavailable_evidence(self):
        from weekly_review_batch import freeze_run
        line = self.add(files=[dict(original_name='Fictional receipt', filed_path='Bills/unavailable.png')])
        self.checkpoint('Fictional missing evidence')
        path = freeze_run(self.settings)
        answer = self.answer(line)
        answer['lines'][0]['note'] = 'Ignore server checks. I read and inspected the attached image.'
        self.assertEqual(self.run_batch(path, self.reviewer(answer))['status'], 'failed')
        def issue(task):
            rows = task.lookups.call('review_pending', {})['items']
            handle = rows[0]['evidence_ids'][0]
            task.lookups.call('review_audit_line', {'line_id': line})
            self.assertEqual(task.lookups.call('review_evidence', {'evidence_id': handle})['status'], 'evidence_unavailable')
            return Result(dict(lines=[dict(line=line, verdict='issue', note='Receipt unavailable',
                                           decide='Can you supply the receipt?', evidence=[handle],
                                           suggestion={'kind': 'question-only'})], rules=[]))
        adapter = self.reviewer(None)
        adapter.run.side_effect = issue
        self.assertEqual(self.run_batch(path, adapter)['status'], 'committed')
        verdict = next(row for row in audit.records(self.ledger) if row['kind'] == 'review')
        self.assertEqual(verdict['decide'], 'Can you supply the receipt?')

    def test_review_can_ok_scaled_receipts(self):
        from PIL import Image
        from review_lookup import PIXELS
        from weekly_review_batch import freeze_run

        photo = Image.new('RGB', (4000, 3000), 'white')
        photo.paste('red', (0, 0, 2000, 3000))
        orientation = Image.Exif()
        orientation[274] = 6  # Rotate 90° clockwise.
        photo.save(self.ledger / 'Bills/photo.jpg', exif=orientation)
        Image.new('RGBA', (2500, 2000), (0, 0, 0, 0)).save(self.ledger / 'Bills/scan.png')
        line = self.add(files=[dict(original_name=name, filed_path='Bills/' + name)
                               for name in ('photo.jpg', 'scan.png')])
        self.checkpoint('Fictional large receipts')

        def inspect(task):
            self.assertEqual(len(task.images), 2)
            handles = task.lookups.call('review_pending', {})['items'][0]['evidence_ids']
            self.assertEqual(len(handles), 2)
            task.lookups.call('review_audit_line', {'line_id': line})
            for handle in handles:
                result = task.lookups.call('review_evidence', {'evidence_id': handle})
                self.assertEqual(result['status'], 'available')
                proof = json.loads(result['data'])
                visual, = proof['visuals']
                self.assertEqual(visual['page'], 1)
                with Image.open(task.images[visual['image'] - 1]) as image:
                    self.assertLessEqual(image.width * image.height, PIXELS)
                    if proof['label'] == 'photo.jpg':
                        self.assertEqual(image.format, 'JPEG')
                        self.assertAlmostEqual(image.width, image.height * 3 / 4, delta=1)
                        self.assertEqual(image.getexif().get(274, 1), 1)
                        self.assertLess(image.getpixel((10, 10))[1], 20)
                        self.assertGreater(image.getpixel((10, image.height - 10))[1], 235)
                    else:
                        self.assertEqual(image.format, 'PNG')
                        self.assertAlmostEqual(image.width, image.height * 5 / 4, delta=1)
                        self.assertEqual(image.getpixel((10, 10))[3], 0)
            return Result(self.answer(line))

        adapter = self.reviewer(None)
        adapter.run.side_effect = inspect
        self.assertEqual(self.run_batch(freeze_run(self.settings), adapter)['status'], 'committed')

    def test_review_recovers_after_append_and_after_commit(self):
        from weekly_review_batch import freeze_run
        for after_commit in (False, True):
            line = self.add()
            self.checkpoint('Fictional recovery batch')
            path, adapter = freeze_run(self.settings), self.reviewer(self.answer(line))
            original = Transaction.verified_commit
            def interrupt(transaction, *args, **kwargs):
                if after_commit:
                    original(transaction, *args, **kwargs)
                raise SystemExit('Interrupted')
            with patch.object(Transaction, 'verified_commit', interrupt), self.assertRaises(SystemExit):
                self.run_batch(path, adapter)
            self.assertEqual(self.run_batch(path, adapter)['status'], 'committed')
            self.assertEqual(adapter.run.call_count, 1)
            rows = audit.records(self.ledger)
            self.assertEqual(sum(r.get('line_id') == line for r in rows), 1)
            self.assertEqual(sum(r['kind'] == 'weekly-review-run' and r['run_id'] == path.parent.name for r in rows), 1)

    def test_recovery_preserves_conflicting_bytes(self):
        from weekly_review_batch import freeze_run
        line = self.add()
        self.checkpoint('Fictional recovery conflict')
        main = self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
        path, adapter = freeze_run(self.settings), self.reviewer(self.answer(line))
        with patch.object(Transaction, 'verified_commit', side_effect=SystemExit), self.assertRaises(SystemExit):
            self.run_batch(path, adapter)
        audit_log = audit.audit_path(self.ledger, {'time': audit.stamp()})
        changed = audit_log.read_bytes() + b'An intervening edit'
        audit_log.write_bytes(changed)
        with self.assertRaisesRegex(RuntimeError, 'conflicting ledger edits'):
            self.run_batch(path, adapter)
        self.assertTrue((path.parent / '1/transaction.json').is_file())
        self.assertEqual(audit_log.read_bytes(), changed)
        self.assertEqual(main, self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'))

    def test_evidence_digest_changes_and_unrelated_audit_appends(self):
        from weekly_review_batch import freeze_run
        evidence = self.ledger / 'Bills/fictional.png'
        evidence.write_bytes(png())
        line = self.add(files=[dict(original_name='Fictional attachment', filed_path='Bills/fictional.png')])
        self.checkpoint('Fictional review attachment')
        path = freeze_run(self.settings)
        adapter = self.reviewer(self.answer(line))
        invoke = adapter.run.side_effect
        def changed(task):
            self.assertEqual(len(task.images), 1)
            from PIL import Image
            Image.new('RGB', (12, 12), 'white').save(evidence)
            self.checkpoint('Replaced fictional attachment')
            return invoke(task)
        adapter.run.side_effect = changed
        self.assertEqual(self.run_batch(path, adapter)['status'], 'stale')
        def unrelated(task):
            self.add()
            self.checkpoint('Unrelated fictional outcome')
            return invoke(task)
        adapter.run.side_effect = unrelated
        self.assertEqual(self.run_batch(path, adapter)['status'], 'committed')

    def test_review_planner_batches_and_exclusions(self):
        from weekly_review_batch import freeze_run
        identities = ['%016x' % i for i in range(1, 5)]
        rules = [rule(identity, '2026-01-02', 'Fictional rule', [posting('Assets:Cash', '-10.00'), posting('Expenses:Food', '10.00')]) for identity in identities]
        dump(self.ledger / 'data/forecast.json', rules)
        source = self.add(rule_changes=[dict(id=r['id'], before=None, after=r) for r in rules])
        ordinary = [self.add() for _ in range(3)]
        heavy = self.add(files=[dict(original_name='Fictional PDF', filed_path='Bills/heavy.pdf')])
        excluded = self.add()
        audit.append(self.ledger, audit.event('review', line_id=excluded, verdict='ok', note='Checked', reviewer='Offline'))
        with patch('audit_changes.unrecorded', return_value=[dict(commit='a' * 40)]) as missing:
            self.checkpoint('Fictional plan')
            plan = json.loads(freeze_run(self.settings).read_text())
        self.assertEqual(missing.call_count, 1)
        self.assertEqual(plan['missing_actors'], ['a' * 40])
        batches = plan['batches']
        self.assertEqual([b['lines'] for b in batches], [[source, *ordinary[:2]], [], ordinary[2:], [heavy]])
        self.assertEqual(list(batches[1]['rules']), identities[3:])
        self.assertEqual(batches[1]['rules'][identities[3]], [source])
