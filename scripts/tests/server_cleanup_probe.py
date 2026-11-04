"""Small real server suite in a spawned worker, including its oracle cache."""
from pathlib import Path
import sys
import unittest
SERVER=Path(__file__).resolve().parents[2]/'review-dashboard/server'
sys.path[:0]=[str(SERVER/'tests'),str(SERVER)]
from run_tests import batches,run_batches

if __name__=='__main__':
    suite=unittest.defaultTestLoader.discover(str(SERVER/'tests'),pattern='test_modules.py')
    result=run_batches(batches(suite),2)
    print(f"Small server worker suite: {result['tests']} tests; successful={result['successful']}")
    raise SystemExit(not result['successful'])
