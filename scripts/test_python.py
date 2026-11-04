"""Initialize test ownership before stdlib unittest imports its helpers."""
import runpy
import sys
from test_run import ensure_root

ensure_root()
if sys.argv[1:2]!=['-m'] or len(sys.argv)<3:raise SystemExit('Usage: test_python.py -m MODULE [ARGS]')
module=sys.argv[2];sys.argv=[module,*sys.argv[3:]]
runpy.run_module(module,run_name='__main__',alter_sys=True)
