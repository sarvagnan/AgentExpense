import os
"""Invented identifiers only; no private list or financial inputs."""
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_private import Matcher, read_list, scan


class PrivateScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp'), prefix='fictional-scan-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.list = self.root / 'identifiers.tsv'
        self.list.write_text('# Fictional inputs\n\nperson\tElm_User\nreference\tAB-XY\nphrase\tMoss Cafe\npath\t/demo/example\n')

    def run_scan(self, paths=None):
        out, err = io.StringIO(), io.StringIO()
        code = scan(self.list, paths or [self.root], root=self.root, output=out, errors=err)
        return code, out.getvalue(), err.getvalue()

    def test_case_insensitive_tokens_and_substrings(self):
        (self.root / 'demo.txt').write_text('ELM_USER; ab-xy\nThe MOSS CAFEway /DEMO/EXAMPLE/x\n')
        code, out, err = self.run_scan()
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines(), ['demo.txt:1: person', 'demo.txt:1: reference', 'demo.txt:2: path', 'demo.txt:2: phrase'])
        self.assertEqual(err, '')
        for value in ['elm_user', 'ab-xy', 'moss cafe', '/demo/example']:
            self.assertNotIn(value, out.casefold())

    def test_whole_token_includes_hyphens_and_underscores(self):
        (self.root / 'demo.txt').write_text('xElm_User Elm_User_x Elm_User-x xAB-XY AB-XY_z\n')
        self.assertEqual(self.run_scan()[0], 0)

    def test_duplicate_hits_only_once_per_label_per_line(self):
        (self.root / 'demo.txt').write_text('elm_user ELM_USER\nelm_user\n')
        self.assertEqual(self.run_scan()[1].splitlines(), ['demo.txt:1: person', 'demo.txt:2: person'])

    def test_overlapping_substrings_and_different_labels(self):
        matcher = Matcher([('one', 'a.b'), ('two', '.b'), ('three', 'A.B')])
        self.assertEqual(matcher.labels('a.B'), ['one', 'three', 'two'])

    def test_ignored_directories_env_and_gitignore(self):
        for name in ['ledger', '.git', '.jj', 'node_modules', 'dist', '__pycache__', 'tmp', 'custom', 'review-dashboard/app/data', 'review-dashboard/public/data']:
            folder = self.root / name; folder.mkdir(parents=True, exist_ok=True)
            (folder / 'input.txt').write_text('elm_user')
        (self.root / '.env.local').write_text('elm_user')
        (self.root / '.gitignore').write_text('/custom/\n*.secret\n')
        (self.root / 'ignore.secret').write_text('elm_user')
        self.assertEqual(self.run_scan(), (0, '', ''))

    def test_nested_ignore_and_negation(self):
        folder = self.root / 'docs'; folder.mkdir()
        (folder / '.gitignore').write_text('*.txt\n!keep.txt\n')
        (folder / 'omit.txt').write_text('elm_user')
        (folder / 'keep.txt').write_text('elm_user')
        self.assertEqual(self.run_scan()[1], 'docs/keep.txt:1: person\n')

    def test_binary_report_without_contents(self):
        (self.root / 'image.bin').write_bytes(b'\0elm_user')
        code, out, err = self.run_scan()
        self.assertEqual((code, out), (0, ''))
        self.assertIn('image.bin: binary', err)
        self.assertNotIn('elm_user', err)

    def test_symlinks_are_not_followed(self):
        (self.root / 'link').symlink_to(self.list)
        self.assertEqual(self.run_scan()[0], 0)

    def test_invalid_list_is_rejected_without_values(self):
        self.list.write_text('invalid secret content')
        with self.assertRaisesRegex(ValueError, 'line 1'):
            read_list(self.list)

    def test_unicode_casefold_and_token_boundaries(self):
        matcher = Matcher([('person', 'Élan')])
        self.assertEqual(matcher.labels('ÉLAN'), ['person'])
        self.assertEqual(matcher.labels('xélan élan_x élan-y'), [])

    def test_large_list(self):
        entries = [(f'token{i}', f'fiction-{i}') for i in range(4000)]
        entries += [(f'phrase{i}', f'Fiction phrase {i}.') for i in range(4000)]
        matcher = Matcher(entries)
        self.assertEqual(matcher.labels('FICTION-3999 and fiction phrase 3998.'), ['phrase3998', 'token3999'])

    def test_explicit_paths_deduplicate(self):
        target = self.root / 'demo.txt'; target.write_text('elm_user')
        self.assertEqual(self.run_scan([target, target])[1], 'demo.txt:1: person\n')

    def test_cli_status_and_missing_path(self):
        target = self.root / 'demo.txt'; target.write_text('elm_user')
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'check_private.py'), '--list', str(self.list)]
        result = subprocess.run(command + [str(target)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stdout.endswith('demo.txt:1: person\n'))
        self.assertNotIn('elm_user', result.stdout)
        result = subprocess.run(command + [str(self.root / 'missing')], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
