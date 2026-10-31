"""Run stdlib unittest with stable output for repeatable make verify logs."""
import io
from pathlib import Path
import re
import sys
import unittest

suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent))
output = io.StringIO()
result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
# Elapsed wall time is the only nondeterministic line in a successful unittest run.
sys.stdout.write(re.sub(r'(Ran \d+ tests) in [\d.]+s', r'\1', output.getvalue()))
sys.exit(0 if result.wasSuccessful() else 1)
