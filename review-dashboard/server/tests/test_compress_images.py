from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from support import fake_oxipng, png
from compress_images import compress, report_missing


def png_bytes(image, **options):
    output = io.BytesIO()
    image.save(output, format='PNG', **options)
    return output.getvalue()


class PngCompressionTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.work / 'fixture receipt.png'
        self.original = png()
        self.path.write_bytes(self.original)
        self.enterContext(patch.dict(os.environ, {'PATH': str(self.work)}))

    def test_smaller_replaces_and_preserves_permissions_and_mtime(self):
        with Image.open(self.path) as image:
            optimized = png_bytes(image)
        fake_oxipng(self.work, optimized)
        self.path.chmod(0o640)
        os.utime(self.path, ns=(1_700_000_000_000_000_000,) * 2)
        before = self.path.stat()

        _, saved = compress(self.path)

        self.assertEqual(self.path.read_bytes(), optimized)
        self.assertEqual(saved, len(self.original) - len(optimized))
        self.assertEqual(self.path.stat().st_mode, before.st_mode)
        self.assertEqual(self.path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_larger_output_is_not_used(self):
        with Image.open(self.path) as image:
            self.original = png_bytes(image)
        self.path.write_bytes(self.original)
        fake_oxipng(self.work, png())

        self.assertEqual(compress(self.path), ('already compact', 0))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_smaller_output_with_different_pixels_is_refused(self):
        original = Image.new('P', (64, 64))
        original.putpalette([255, 0, 0] + [0] * 765)
        self.original = png_bytes(original, compress_level=0)
        self.path.write_bytes(self.original)
        # The pixel indices are identical, but the first palette entry changes colour.
        original.putpalette([0, 0, 255] + [0] * 765)
        fake_oxipng(self.work, png_bytes(original))

        with self.assertRaisesRegex(ValueError, 'different pixels'):
            compress(self.path)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_missing_tool_skips_and_warns_once(self):
        report_missing.cache_clear()
        self.addCleanup(report_missing.cache_clear)
        messages = io.StringIO()

        with redirect_stderr(messages):
            self.assertEqual(compress(self.path), ('left as is', 0))
            self.assertEqual(compress(self.path), ('left as is', 0))

        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(messages.getvalue().count('oxipng is missing from PATH'), 1)
