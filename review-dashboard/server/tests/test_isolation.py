from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from isolation import assert_unchanged, snapshot


class IsolationTests(unittest.TestCase):
    expense_name = 'ledger/data/expenses/2026.json'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='expense-guard-', dir='/tmp')
        self.addCleanup(temporary.cleanup)
        self.application = Path(temporary.name)
        self.ledger = self.application / 'ledger'
        self.file = self.application / self.expense_name
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b'original\n')
        self.before = snapshot(self.application)

    def git(self, *args, at=None):
        return subprocess.check_output(
            ['git', '-c', 'user.name=Offline Test', '-c', 'user.email=test@example.invalid',
             '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null',
             '-C', str(at or self.ledger), *args], stderr=subprocess.PIPE).decode().strip()

    def repository(self):
        self.git('init', '--initial-branch=main')
        self.commit('Fixture baseline')

    def commit(self, message):
        self.git('add', 'data/expenses')
        self.git('commit', '-m', message)

    def assert_changed(self, before=None):
        with self.assertRaisesRegex(AssertionError, r'ledger/data/expenses/2026\.json'):
            assert_unchanged(self.before if before is None else before, self.application)

    def test_committed_main_sync_passes_with_file_and_commit_notes(self):
        self.repository()
        self.file.write_bytes(b'synced main\n')
        (self.file.parent / '2027.json').write_bytes(b'new committed year\n')
        self.commit('Synced addition')
        commit = self.git('rev-parse', 'refs/heads/main')[:7]
        notes = io.StringIO()
        with redirect_stdout(notes):
            assert_unchanged(self.before, self.application)
        self.assertEqual(notes.getvalue(), ''.join(
            f'ledger/data/expenses/{year}.json changed to ledger main {commit} during the run; not a test write\n'
            for year in ('2026', '2027')))

    def test_uncommitted_content_fails_and_names_file(self):
        self.repository()
        self.file.write_bytes(b'synced main\n')
        self.commit('Synced addition')
        self.file.write_bytes(b'uncommitted test write\n')
        self.assert_changed()

    def test_missing_repository_stays_strict_with_matching_enclosing_repository(self):
        self.file.write_bytes(b'uncommitted test write\n')
        self.assert_changed()
        enclosing_file = self.application / 'data/expenses/2026.json'
        enclosing_file.parent.mkdir(parents=True)
        enclosing_file.write_bytes(b'uncommitted test write\n')
        self.git('init', '--initial-branch=main', at=self.application)
        self.git('add', 'data/expenses', at=self.application)
        self.git('commit', '-m', 'Enclosing repository', at=self.application)
        self.assert_changed()

    def test_unavailable_git_or_main_stays_strict(self):
        self.repository()
        self.file.write_bytes(b'synced main\n')
        self.commit('Synced addition')
        with patch.dict(os.environ, {'PATH': str(self.application / 'missing-executables')}):
            self.assert_changed()
        self.git('branch', '-m', 'other')
        self.assert_changed()

    def test_dashboard_outputs_deletions_and_symlinks_stay_strict(self):
        self.repository()
        strict_names = ['review-dashboard/app/data/dashboard.json',
                        'review-dashboard/public/data/dashboard.json.gz',
                        'review-dashboard/dist/index.html']
        for name in strict_names:
            path = self.application / name
            path.parent.mkdir(parents=True)
            path.write_bytes(b'original\n')
        before = snapshot(self.application)
        self.file.write_bytes(b'synced main\n')
        self.commit('Synced addition')
        for name in strict_names:
            (self.application / name).write_bytes(b'changed\n')
        notes = io.StringIO()
        with redirect_stdout(notes), self.assertRaises(AssertionError) as error:
            assert_unchanged(before, self.application)
        for name in strict_names:
            self.assertIn(name, str(error.exception))
        self.assertNotIn(self.expense_name, str(error.exception))
        self.assertEqual(len(notes.getvalue().splitlines()), 1)
        for name in strict_names:
            (self.application / name).write_bytes(b'original\n')
        self.file.unlink()
        self.commit('Committed removal')
        self.assert_changed(before)
        (self.file.parent / 'target.json').write_bytes(b'original\n')
        self.file.symlink_to('target.json')
        self.commit('Committed symlink')
        with redirect_stdout(io.StringIO()):
            self.assert_changed(before)
