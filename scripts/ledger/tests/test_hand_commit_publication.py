"""Ordinary jj parents and publication bound to the verified commit."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit
import commit
import extension_update
from ledger_lock import ledger_lock
from project_paths import APPLICATION_ROOT
from store import dump, read_json
from test_ledger import entry
import test_hand_changes as hand_cases
import test_extensions as extension_cases


class PublicationTests(unittest.TestCase):
    def case(self):
        case = hand_cases.HandChangeTests(); case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_make_commit_after_plain_new(self):
        for count in (1, 2):
            with self.subTest(count=count):
                case = self.case()
                for _ in range(count): case.jj('new')
                dump(case.year, [entry(description='Saved after routine jj new')])
                run = subprocess.run(['make', 'commit', 'm=Fictional hand change', 'by=user',
                                      'LEDGER_PYTHON=' + sys.executable], cwd=APPLICATION_ROOT,
                                     env={**os.environ, 'LEDGER_DIR': str(case.root)}, capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn('Saved after routine jj new', case.jj('file', 'show', '-r', 'main', str(case.year.relative_to(case.root))))
                self.assertEqual(audit.records(case.root)[-1]['actor'], 'user')

    def test_described_or_nonempty_parent_does_not_stop_saving(self):
        for kind in ('described', 'nonempty'):
            with self.subTest(kind=kind):
                case = self.case()
                if kind == 'described': case.jj('describe', '-m', 'Unpublished description')
                else: (case.root / 'unpublished-note.txt').write_text('Keep unpublished work')
                case.jj('new'); case.save()
                if kind == 'nonempty': self.assertEqual((case.root / 'unpublished-note.txt').read_text(), 'Keep unpublished work')


    def test_extension_plan_refuses_a_concurrent_edit(self):
        case = self.case(); root = case.root
        (root / 'extensions').mkdir(); (root / 'extensions/work.json').write_text(json.dumps(extension_cases.WORK))
        manifest = read_json(root / 'ledger.json'); manifest['extensions'] = {'work': {'ask': True}}
        dump(root / 'ledger.json', manifest)
        original = extension_update.plan
        def plan(*args, **kwargs):
            result = original(*args, **kwargs)
            edited = read_json(case.year)
            edited[0]['description'] = 'Saved while the extension was planning'
            case.year.write_text(json.dumps(edited) + '\n')
            return result
        with patch.object(extension_update, 'plan', side_effect=plan), self.assertRaisesRegex(ValueError, 'changed while planning'):
            extension_update.update(root, 'work', actor='agent')
        self.assertEqual(read_json(case.year)[0]['description'], 'Saved while the extension was planning')
        self.assertEqual(audit.records(root), [])


    def test_publication_is_bound_to_the_verified_commit(self):
        case = self.case(); dump(case.year, [entry(description='Verified candidate')])
        original = commit.jj
        def moved(root, *args):
            if args[:2] == ('bookmark', 'set'): case.jj('new', 'main')
            return original(root, *args)
        with patch.object(commit, 'jj', side_effect=moved): result = case.save()
        self.assertEqual(case.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'), result)
