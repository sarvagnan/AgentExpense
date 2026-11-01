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
        with tempfile.TemporaryDirectory(dir='/tmp') as folder:
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
            self.assertEqual(target.read_text(), canonical_text(updated))
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            self.assertEqual(list(Path(folder).iterdir()), [target])
