"""Worker scheduling preserves discovery, fixtures and unittest failure outcomes."""
from collections import Counter
from contextlib import redirect_stdout
import importlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

from run_tests import batches, run_batches


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='expense-runner-test-', dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp'))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        sys.path.insert(0, str(self.root))
        self.addCleanup(sys.path.remove, str(self.root))

    def module(self, source):
        name = 'fictional_runner_' + uuid.uuid4().hex
        (self.root / (name + '.py')).write_text(source)
        module = importlib.import_module(name)
        self.addCleanup(sys.modules.pop, name, None)
        return module

    def test_workers_keep_complete_classes_module_fixtures_and_every_test(self):
        log = self.root / 'fixtures.jsonl'
        common = f'''import json, os, unittest
from unittest.mock import patch

def record(event):
    with open({str(log)!r}, 'a') as out:
        out.write(json.dumps(dict(event=event, pid=os.getpid())) + '\\n')
'''
        first = self.module(common + '''
def setUpModule(): record('module start')
def tearDownModule(): record('module end')
class First(unittest.TestCase):
    @classmethod
    def setUpClass(cls): record('class start')
    @classmethod
    def tearDownClass(cls): record('class end')
    def test_one(self):
        record('one')
        with patch.dict(os.environ, {'EXPENSES_RUNNER_FIXTURE': 'first'}):
            self.assertEqual(os.environ['EXPENSES_RUNNER_FIXTURE'], 'first')
    def test_two(self):
        record('two')
        for value in range(3):
            with self.subTest(value=value): self.assertLess(value, 3)
class Second(unittest.TestCase):
    def test_three(self): record('three')
''')
        second = self.module(common + '''
class Third(unittest.TestCase):
    def test_four(self):
        record('four')
        with patch.dict(os.environ, {'EXPENSES_RUNNER_FIXTURE': 'second'}):
            self.assertEqual(os.environ['EXPENSES_RUNNER_FIXTURE'], 'second')
    def test_five(self): record('five')
''')
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(module) for module in (first, second))
        groups = batches(suite)
        self.assertEqual(sorted(map(len, groups)), [2, 3], 'Module fixtures keep both classes in one worker batch')
        with redirect_stdout(io.StringIO()): result = run_batches(groups, 2)
        self.assertTrue(result['successful'], result)
        self.assertEqual(result['tests'], 5)
        self.assertEqual(Counter(item['test'] for item in result['timings']), Counter(identity for group in groups for identity in group))
        import json
        events = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(Counter(event['event'] for event in events), Counter(['module start', 'module end', 'class start', 'class end', 'one', 'two', 'three', 'four', 'five']))
        self.assertTrue(all(event['pid'] != os.getpid() for event in events))

    def test_failures_errors_subtests_skips_and_expected_failures_are_preserved(self):
        module = self.module('''import unittest
class Outcomes(unittest.TestCase):
    def test_failure(self): self.fail('Fictional failure')
    def test_error(self): raise ValueError('Fictional error')
    def test_subtest(self):
        with self.subTest(value=1): self.assertEqual(1, 2)
    @unittest.skip('Fictional skip')
    def test_skip(self): pass
    @unittest.expectedFailure
    def test_expected_failure(self): self.fail('Fictional expected failure')
    @unittest.expectedFailure
    def test_unexpected_success(self): self.assertTrue(True)
''')
        groups = batches(unittest.defaultTestLoader.loadTestsFromModule(module))
        output = io.StringIO()
        with redirect_stdout(output): result = run_batches(groups, 2)
        self.assertFalse(result['successful'])
        self.assertEqual(result['tests'], 6)
        self.assertEqual((result['failures'], result['errors'], result['skipped'], result['expectedFailures']), (2, 1, 1, 1))
        self.assertEqual(result['unexpectedSuccesses'], 1)
        self.assertEqual(len(result['timings']), 6)
        for message in ('Fictional failure', 'Fictional error', 'value=1', 'Fictional skip', 'expected failure'):
            self.assertIn(message, output.getvalue())
