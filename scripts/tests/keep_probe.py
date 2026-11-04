"""Exercise artifact retention through the ordinary Python temporary helper."""
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_run import ensure_root
ensure_root()
with tempfile.TemporaryDirectory(prefix='keep-python-',dir=os.environ['EXPENSES_TEST_TMP']) as folder:
    (Path(folder)/'keep-marker.txt').write_text('fictional')
