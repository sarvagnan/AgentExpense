"""Offline fixtures for Python discovery and toolchain pin updates."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from python_upgrade import BOX, SERVER, box_pins, latest_python, lock_changes, write_pins


UV_DOWNLOADS = '''[
  {"version": "3.16.0a1", "implementation": "cpython", "variant": "default"},
  {"version": "3.15.11", "implementation": "cpython", "variant": "freethreaded"},
  {"version": "3.15.2", "implementation": "cpython", "variant": "default"},
  {"version": "3.15.10", "implementation": "cpython", "variant": "default"},
  {"version": "3.15.11rc1", "implementation": "cpython", "variant": "default"},
  {"version": "3.14.19", "implementation": "cpython", "variant": "default"},
  {"version": "3.17.0", "implementation": "pypy", "variant": "default"}
]'''
SETUP = Path('.github/actions/setup/action.yml')
DIGEST = 'a' * 64
OLD_DIGEST = 'b' * 64
FILES = {
    SERVER / '.python-version': '3.14\n',
    SERVER / 'pyproject.toml': '[project]\nversion = "0.1.0"\nrequires-python = ">=3.14"\n',
    SETUP: 'inputs:\n  uv-version:\n    description: Exact uv version\n    default: "0.12.13"\n'
           '  jj-version:\n    default: "0.47.1"\n',
    BOX: f'PINS = {{"uv": "0.12.13", "python": "3.14.7",\n'
         f'        "uv_sha256":"{OLD_DIGEST}",  # archive checksum\n'
         '        "jj": "0.47.1"}\n'
         'OTHER = {"uv": "unchanged", "python": "unchanged"}\n',
}


class PythonDiscoveryTests(unittest.TestCase):
    def test_selects_newest_stable_default_cpython_numerically(self):
        self.assertEqual(latest_python(json.loads(UV_DOWNLOADS)), '3.15.10')

    def test_requires_a_stable_cpython_download(self):
        with self.assertRaisesRegex(ValueError, 'no stable CPython downloads'):
            latest_python(json.loads(UV_DOWNLOADS)[:2])


class PinTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for path, contents in FILES.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(contents)

    def test_updates_all_pins_and_preserves_surrounding_text(self):
        write_pins(self.root, '0.13.0', DIGEST, '3.15.10')
        expected = dict(FILES)
        expected[SERVER / '.python-version'] = '3.15\n'
        expected[SERVER / 'pyproject.toml'] = FILES[SERVER / 'pyproject.toml'].replace('>=3.14', '>=3.15')
        expected[SETUP] = FILES[SETUP].replace('0.12.13', '0.13.0')
        expected[BOX] = FILES[BOX].replace('0.12.13', '0.13.0').replace(OLD_DIGEST, DIGEST).replace('3.14.7', '3.15.10')
        self.assertEqual({path: (self.root / path).read_text() for path in FILES}, expected)
        self.assertEqual(box_pins(self.root),
                         {'uv': '0.13.0', 'uv_sha256': DIGEST, 'python': '3.15.10', 'jj': '0.47.1'})

    def test_python_patch_update_changes_only_the_box(self):
        write_pins(self.root, '0.12.13', OLD_DIGEST, '3.14.10')
        changed = [path for path in FILES if (self.root / path).read_text() != FILES[path]]
        self.assertEqual(changed, [BOX])
        self.assertEqual(box_pins(self.root)['python'], '3.14.10')

    def test_unrecognised_pin_or_digest_leaves_all_files_untouched(self):
        (self.root / SETUP).write_text('# pin moved\n')
        expected = {**FILES, SETUP: '# pin moved\n'}
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            write_pins(self.root, '0.13.0', 'missing', '3.15.10')
        with self.assertRaisesRegex(ValueError, 'action.yml'):
            write_pins(self.root, '0.13.0', DIGEST, '3.15.10')
        self.assertEqual({path: (self.root / path).read_text() for path in FILES}, expected)

    def test_lock_summary_tracks_all_package_versions_including_marker_forks(self):
        before, after = (self.root / name for name in ('uv-before.lock', 'uv.lock'))
        unchanged = '[[package]]\nname = "fixture"\nversion = "1.0"\n'
        before.write_text(unchanged + '[[package]]\nname = "fixture"\nversion = "1.1"\n')
        after.write_text(unchanged + '[[package]]\nname = "fixture"\nversion = "1.2"\n')
        self.assertEqual(lock_changes(before, after), ['- Removed `fixture==1.1`', '- Added `fixture==1.2`'])
        self.assertEqual(lock_changes(after, after), [])
