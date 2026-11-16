"""Terminal verification never consumes changes made by another editor."""
import json
import os
from pathlib import Path
import sys
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit
import commit
import test_hand_changes as hand_cases
from test_ledger import entry
from store import dump, read_json
import test_hand_commit_interrupts as interrupt_cases
from ledger_lock import ledger_lock


class EditorPreservationTests(unittest.TestCase):
    def check_writer(self, writer, same_path):
        import test_audit as audit_cases
        import test_extensions as extension_cases
        from extension_update import update
        case = hand_cases.HandChangeTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        if writer == 'record':
            dump(case.year, [entry(description='Earlier hand change')])
            target = case.raw_commit('Earlier hand change')
        elif writer == 'review':
            line = audit_cases.draft()
            line['entry_id'] = entry()['id']
            audit.append(case.root, line)
            case.raw_commit('Earlier outcome')
            target = line['id']
        elif writer == 'extension':
            (case.root / 'extensions').mkdir()
            (case.root / 'extensions/work.json').write_text(json.dumps(extension_cases.WORK))
            manifest = read_json(case.root / 'ledger.json')
            manifest['extensions'] = {'work': {'ask': True}}
            dump(case.root / 'ledger.json', manifest)
        else:
            dump(case.year, [entry(description='Intended hand change')])
        prior = case.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
        before = {p.name: p.read_bytes() for p in (case.root / 'data/additions').glob('*.jsonl')}
        original = commit.verify
        changed = case.year if same_path else case.root / 'Bills/new-receipt.pdf'
        saved = []
        def verify(root, **kwargs):
            result = original(root, **kwargs)
            if same_path:
                items = read_json(case.year)
                items[0]['description'] = 'Saved by the person during verification'
                dump(case.year, items)
            else:
                changed.parent.mkdir(exist_ok=True)
                changed.write_bytes(b'%PDF fictional receipt saved during verification')
            saved.append(changed.read_bytes())
            return result
        with patch.object(commit, 'verify', side_effect=verify), patch.object(commit, 'jj', wraps=commit.jj) as calls:
            with self.assertRaises(ValueError) as error:
                if writer == 'record':
                    audit.record(case.root, [dict(commit=target, actor='user')])
                elif writer == 'review':
                    audit.review(case.root, [dict(line=target, verdict='ok', note='Checked')], 'Offline reviewer', 'Review')
                elif writer == 'extension':
                    update(case.root, 'work', actor='agent')
                else:
                    case.save()
        self.assertTrue(changed.exists(), 'The person’s saved file disappeared')
        self.assertEqual(changed.read_bytes(), saved[0])
        self.assertEqual(case.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'), prior)
        self.assertEqual({p.name: p.read_bytes() for p in (case.root / 'data/additions').glob('*.jsonl')}, before)
        self.assertIn(str(changed.relative_to(case.root)), str(error.exception))
        self.assertIn('nothing was committed', str(error.exception).lower())
        self.assertFalse(any(call.args[1:2] == ('commit',) for call in calls.call_args_list))

    def test_new_receipt_saved_during_verification_survives(self):
        for writer in ('hand', 'record', 'review', 'extension'):
            with self.subTest(writer=writer):
                self.check_writer(writer, False)

    def test_existing_covered_file_saved_during_verification_survives(self):
        for writer in ('hand', 'record', 'review', 'extension'):
            with self.subTest(writer=writer):
                self.check_writer(writer, True)

    def test_edit_during_jj_commit_stays_on_disk_and_off_main(self):
        case = hand_cases.HandChangeTests(); case.setUp(); self.addCleanup(case.doCleanups)
        dump(case.year, [entry(description='Intended')])
        prior = case.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
        original = commit.jj
        late = case.root / 'Bills/last-moment.pdf'
        def jj(root, *args):
            if args[0] == 'commit':
                late.parent.mkdir(exist_ok=True); late.write_bytes(b'%PDF fictional last moment receipt')
            return original(root, *args)
        with patch.object(commit, 'jj', side_effect=jj), self.assertRaisesRegex(ValueError, 'candidate changed'):
            case.save()
        self.assertEqual(late.read_bytes(), b'%PDF fictional last moment receipt')
        self.assertEqual(case.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'), prior)
        self.assertEqual(audit.records(case.root), [])

    def test_line_uses_its_original_snapshot_even_if_another_editor_snapshots(self):
        import audit_changes
        case = hand_cases.HandChangeTests(); case.setUp(); self.addCleanup(case.doCleanups)
        dump(case.year, [entry(description='Intended')])
        original = audit_changes.change_line
        def line(*args, **kwargs):
            result = original(*args, **kwargs)
            dump(case.year, [entry(description='Another editor saved and snapshotted')])
            case.jj('status')
            return result
        with patch.object(audit_changes, 'change_line', side_effect=line), self.assertRaisesRegex(ValueError, 'paths changed'):
            case.save()
        self.assertEqual(read_json(case.year)[0]['description'], 'Another editor saved and snapshotted')
        self.assertEqual(audit.records(case.root), [])


class InterruptedWorkTests(unittest.TestCase):
    def setUp(self):
        self.case = hand_cases.HandChangeTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)

    def test_ignored_and_untracked_junk_is_preserved(self):
        case = self.case
        with (case.root / '.gitignore').open('a') as out: out.write('/ignored-junk/\n')
        case.raw_commit('Ignore fictional scratch output'); interrupt_cases.crash(case)
        files = [case.root / 'Bills/.DS_Store', case.root / 'data/.DS_Store', case.root / 'scratch-note.tmp', case.root / 'ignored-junk/late.bin']
        for path in files:
            path.parent.mkdir(exist_ok=True); path.write_bytes(b'Harmless local junk')
        interrupt_cases.restore_audit(case); case.save()
        for path in files: self.assertEqual(path.read_bytes(), b'Harmless local junk')

    def test_regenerated_journal_does_not_block_the_retry(self):
        interrupt_cases.crash(self.case)
        commit.verify(self.case.root, emit=lambda *args: None)
        journal = self.case.root / 'expense_ledger.ledger'; rendered = journal.read_bytes()
        interrupt_cases.restore_audit(self.case); self.case.save()
        self.assertEqual(journal.read_bytes(), rendered)

    def test_completed_publication_keeps_later_edits(self):
        interrupt_cases.crash(self.case, 'bookmark')
        dump(self.case.year, [entry(description='Later edit after completed publication')]); saved = self.case.year.read_bytes()
        with ledger_lock(self.case.root): pass
        self.assertEqual(self.case.year.read_bytes(), saved)
        self.assertEqual(len(audit.records(self.case.root)), 1)


    def test_unpublished_parent_can_be_published_and_missing_audit_is_reported(self):
        from audit_changes import unrecorded
        self.case.set_start()
        dump(self.case.year, [entry(description='Unpublished earlier work')]); self.case.jj('commit', '-m', 'Unpublished work')
        earlier = self.case.jj('log', '-r', '@-', '--no-graph', '-T', 'commit_id')
        dump(self.case.year, [entry(description='Later hand edit')]); saved = self.case.year.read_bytes()
        self.case.save()
        self.assertEqual(self.case.year.read_bytes(), saved)
        self.assertIn(earlier, [row['commit'] for row in unrecorded(self.case.root, audit.records(self.case.root))])
