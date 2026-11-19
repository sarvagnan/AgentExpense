"""Check generation and the shipped time zone aliases."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo


TESTS = Path(__file__).resolve().parent
SERVER = TESTS.parent
GENERATOR = SERVER.parents[1] / 'scripts/generate_zone_aliases.py'


class ZoneAliasTests(unittest.TestCase):
    def setUp(self):
        self.aliases = json.loads((SERVER / 'zone_aliases.json').read_text())['aliases']

    def test_generator_flattens_links_and_skips_identity_links(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'zone_aliases.json'
            subprocess.run([sys.executable, GENERATOR, TESTS / 'fixtures/zone_aliases.zi', output], check=True)
            generated = json.loads(output.read_text())
        self.assertEqual(generated, {
            'version': '2026a',
            'aliases': {'Asia/Calcutta': 'Asia/Kolkata', 'UTC': 'Etc/UTC', 'Universal': 'Etc/UTC'},
        })
        for old, current in generated['aliases'].items():
            self.assertEqual(self.aliases[old], current)

    def test_every_canonical_zone_opens(self):
        for name in sorted(set(self.aliases.values())):
            with self.subTest(name=name):
                self.assertEqual(ZoneInfo(name).key, name)
