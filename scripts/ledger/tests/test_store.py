import os
"""Canonical JSON publication must remain readable during a concurrent write."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store import canonical_text, dump, read_json


class PublicationTests(unittest.TestCase):
    def test_readers_keep_the_previous_document_until_publication(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp')) as folder:
            target = Path(folder) / 'accounts.json'
            previous, updated = {'accounts': {'Old': {}}}, {'accounts': {'New': {}}}
            target.write_text(canonical_text(previous))
            target.chmod(0o640)
            observed = []

            def paused_write(path, text, encoding=None):
                # This is the real truncate-before-write boundary, not a timing
                # window: a concurrent reader runs while the writer is paused.
                with path.open('w', encoding=encoding) as handle:
                    observed.append(read_json(target))
                    return handle.write(text)

            with patch.object(Path, 'write_text', paused_write):
                dump(target, updated)
            self.assertEqual(observed, [previous])
            self.assertEqual(read_json(target), updated)
            # Not a store document (no data/ parent): the plain layout, no model.
            self.assertEqual(target.read_text(), canonical_text(updated, flat=False))
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            self.assertEqual(list(Path(folder).iterdir()), [target])


from test_ledger import Fixture, entry, write
from store import load
from generate_ledger import render


class DuplicateLinkTests(Fixture):
    def test_canonical_pair_validation_and_unchanged_journal(self):
        rows=[entry(),entry(identity='0000000000000002')]
        dump(self.year,rows)
        before=render(load(self.root),self.root)
        rows[0]['possible-duplicate-of']=[dict(id=rows[1]['id'],reason='amount-date-gap')]
        dump(self.year,rows)
        self.assertEqual(render(load(self.root),self.root),before)
        self.assertLess(canonical_text(rows).index('possible-duplicate-of'),canonical_text(rows).index('postings'))
        for links in ([dict(id=rows[0]['id'],reason='dissimilar-name')],[dict(id='f'*16,reason='amount-date-gap')],[dict(id=rows[1]['id'],reason='unknown')],rows[0]['possible-duplicate-of']*2):
            rows[0]['possible-duplicate-of']=links;write(self.year,rows)
            with self.assertRaises(ValueError):load(self.root)
        rows[0]['possible-duplicate-of']=[];rows[1]['possible-duplicate-of']=[dict(id=rows[0]['id'],reason='amount-date-gap')];write(self.year,rows)
        with self.assertRaisesRegex(ValueError,'greater than the entry ID'):load(self.root)


FIXTURES = Path(__file__).resolve().parents[3] / 'fixtures/ledgers'


@unittest.skipUnless(FIXTURES.is_dir(), 'the fixture ledgers are not in this application copy')
class RoundTripTests(unittest.TestCase):
    def test_every_fixture_document_round_trips_byte_for_byte(self):
        from store import load, serialize
        for root in sorted(path for path in FIXTURES.iterdir() if (path / 'ledger.json').is_file()):
            for path, value in load(root).items():
                with self.subTest(ledger=root.name, document=str(path.relative_to(root))):
                    self.assertEqual(serialize(path, value, root), path.read_text(encoding='utf-8'))
