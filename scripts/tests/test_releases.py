"""Release versions, changelog sections, local.json update settings and dependency checks."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

APPLICATION = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APPLICATION / 'scripts'))
import dependencies  # noqa: E402
import local_settings  # noqa: E402
import releases  # noqa: E402

CHANGELOG = '''# Changelog

## [Unreleased]

- Not yet.

## [0.3.0-rc.1] - 2026-12-01

Pre-release notes.

## [0.2.0] - 2026-11-20

### Upgrade notes

1. Run make update.

## [0.1.1] - 2026-11-10

Fixes.

## [0.1.0] - 2026-11-05

First.

[0.2.0]: https://example.invalid/compare/v0.1.0...v0.2.0
'''


class VersionTests(unittest.TestCase):
    def test_semantic_order(self):
        ordered = ['0.1.0', '0.1.1', '0.2.0-alpha', '0.2.0-alpha.1', '0.2.0-alpha.beta', '0.2.0-beta',
                   '0.2.0-beta.2', '0.2.0-beta.11', '0.2.0-rc.1', '0.2.0', '0.10.0', '1.0.0']
        versions = [releases.Version(text) for text in ordered]
        self.assertEqual(sorted(reversed(versions)), versions)
        self.assertEqual(releases.Version('v1.2.3'), releases.Version('1.2.3'))
        self.assertEqual(releases.Version('v1.2.3-rc.1').tag, 'v1.2.3-rc.1')

    def test_rejects_non_releases(self):
        for text in ('1.2', '01.2.3', '1.2.3-', '1.2.3-01', 'v1.2.3+build', 'latest', ''):
            with self.subTest(text=text):
                self.assertIsNone(releases.parse(text))

    def test_releases_ignore_pre_releases_unless_asked(self):
        tags = ['v0.1.0', 'v0.2.0', 'v0.3.0-rc.1', 'v0.3.0-rc.2', 'release-9', '0.9.0', 'v0.2.0']
        self.assertEqual([str(v) for v in releases.releases(tags)], ['0.2.0', '0.1.0'])
        self.assertEqual([str(v) for v in releases.releases(tags, pre=True)], ['0.3.0-rc.2', '0.3.0-rc.1', '0.2.0', '0.1.0'])
        self.assertEqual([str(v) for v in releases.newer(tags, '0.1.0')], ['0.2.0'])
        self.assertEqual(releases.newer(tags, '0.2.0'), [])
        self.assertEqual([str(v) for v in releases.newer(tags, '0.2.0', pre=True)], ['0.3.0-rc.2', '0.3.0-rc.1'])

    def test_ls_remote_output(self):
        output = 'a1\trefs/tags/v0.1.0\nb2\trefs/tags/v0.2.0\nc3\trefs/heads/main\nbad line\n'
        self.assertEqual(releases.ls_remote_tags(output), ['v0.1.0', 'v0.2.0'])

    def test_changelog_sections_between_versions(self):
        sections = releases.changelog_sections(CHANGELOG, '0.1.0', '0.2.0')
        self.assertTrue(sections.startswith('## [0.2.0] - 2026-11-20'))
        self.assertIn('1. Run make update.', sections)
        self.assertIn('## [0.1.1] - 2026-11-10', sections)
        for absent in ('Unreleased', '0.3.0-rc.1', '## [0.1.0]', 'https://example.invalid'):
            self.assertNotIn(absent, sections)
        self.assertIn('0.3.0-rc.1', releases.changelog_sections(CHANGELOG, '0.2.0', '0.3.0-rc.1'))
        self.assertEqual(releases.changelog_sections(CHANGELOG, '0.2.0', '0.2.0'), '')

    def test_release_notes_for_one_version(self):
        self.assertEqual(releases.release_notes(CHANGELOG, '0.2.0'), '### Upgrade notes\n\n1. Run make update.')
        self.assertEqual(releases.release_notes(CHANGELOG, 'v0.1.0'), 'First.', 'link references are left out')
        self.assertIsNone(releases.release_notes(CHANGELOG, '0.9.0'))

    def test_this_changelog_has_a_section_for_the_current_version(self):
        version = (APPLICATION / 'VERSION').read_text().strip()
        text = (APPLICATION / 'CHANGELOG.md').read_text()
        self.assertIn(f'## [{version}]', text)
        previous = releases.Version(version)
        self.assertEqual(releases.changelog_sections(text, '0.0.0', previous).count(f'## [{version}]'), 1)


class LocalSettingsTests(unittest.TestCase):
    def test_defaults_and_valid_values(self):
        self.assertEqual(local_settings.update_settings({}), {'remote': 'origin', 'notice': False})
        self.assertIsNone(local_settings.service_settings({}))
        self.assertEqual(local_settings.update_settings({'updates': {'remote': 'upstream', 'notice': True}}),
                         {'remote': 'upstream', 'notice': True})
        self.assertEqual(local_settings.service_settings({'service': {'systemd': 'expense-dashboard.service'}}),
                         ('systemd', 'expense-dashboard.service'))
        self.assertEqual(local_settings.service_settings({'service': {'launchd': 'org.example.agentexpense'}}),
                         ('launchd', 'org.example.agentexpense'))

    def test_invalid_values(self):
        for local in ({'updates': []}, {'updates': {'remote': '--upload-pack=x'}}, {'updates': {'notice': 'yes'}},
                      {'updates': {'other': 1}}):
            with self.subTest(local=local), self.assertRaises(ValueError):
                local_settings.update_settings(local)
        for local in ({'service': 'x'}, {'service': {}}, {'service': {'systemd': 'a', 'launchd': 'b'}},
                      {'service': {'cron': 'x'}}, {'service': {'systemd': '-x'}}, {'service': {'launchd': 'a b'}},
                      {'service': {'systemd': 1}}):
            with self.subTest(local=local), self.assertRaises(ValueError):
                local_settings.service_settings(local)

    def test_reads_the_application_file(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP')) as folder:
            self.assertEqual(local_settings.read_local(folder), {})
            Path(folder, 'local.json').write_text('[]')
            with self.assertRaises(ValueError):
                local_settings.read_local(folder)


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP'))
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'review-dashboard/node_modules').mkdir(parents=True)
        self.lock({'': {'version': '0.1.0'}, 'node_modules/a': {'version': '1.0.0'},
                   'node_modules/b-other-platform': {'version': '2.0.0', 'optional': True}})

    def lock(self, packages, version='0.1.0'):
        (self.root / 'review-dashboard/package-lock.json').write_text(json.dumps({'version': version, 'packages': packages}))

    def installed(self, packages):
        (self.root / 'review-dashboard/node_modules/.package-lock.json').write_text(json.dumps({'packages': packages}))

    def test_npm_comparison(self):
        self.assertIsNone(dependencies.npm_differences(self.root))
        self.installed({'node_modules/a': {'version': '1.0.0'}})
        self.assertEqual(dependencies.npm_differences(self.root), [])
        # A release that changes only the application's own version needs no reinstall.
        self.lock({'': {'version': '0.2.0'}, 'node_modules/a': {'version': '1.0.0'}}, version='0.2.0')
        self.assertEqual(dependencies.npm_differences(self.root), [])
        self.lock({'': {'version': '0.2.0'}, 'node_modules/a': {'version': '1.1.0'}, 'node_modules/c': {'version': '1.0.0'}})
        self.installed({'node_modules/a': {'version': '1.0.0'}, 'node_modules/gone': {'version': '1.0.0'}})
        self.assertEqual(sorted(dependencies.npm_differences(self.root)), ['node_modules/a', 'node_modules/c', 'node_modules/gone'])

    def test_installs_only_what_differs(self):
        calls = []
        self.installed({'node_modules/a': {'version': '1.0.0'}})
        self.assertFalse(dependencies.install_npm(self.root, run=lambda command, check: calls.append(command)))
        self.assertFalse(dependencies.install_python(self.root, run=lambda command, check: calls.append(command), matches=lambda root: True))
        self.assertEqual(calls, [])
        self.installed({'node_modules/a': {'version': '0.9.0'}})
        with redirect_stdout(io.StringIO()) as said:
            self.assertTrue(dependencies.install_npm(self.root, run=lambda command, check: calls.append(command)))
            self.assertTrue(dependencies.install_python(self.root, run=lambda command, check: calls.append(command), matches=lambda root: False))
        self.assertEqual(said.getvalue().splitlines(), ['1 npm packages differ from package-lock.json; running npm ci.',
                                                        'The server environment differs from uv.lock; running uv sync --locked.'])
        self.assertEqual(calls, [['npm', '--prefix', str(self.root / 'review-dashboard'), 'ci'],
                                 ['uv', 'sync', '--locked', '--directory', str(self.root / 'review-dashboard/server')]])


if __name__ == '__main__':
    unittest.main()
