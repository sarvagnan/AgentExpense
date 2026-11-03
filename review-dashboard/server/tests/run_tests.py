"""Run every offline test with isolated workers and optional per-test timings."""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time
import unittest

SERVER = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SERVER / 'tests'), str(SERVER)]


class Output(io.StringIO):
    def writeln(self, value=''):
        self.write(value + '\n')


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = []

    def startTest(self, test):
        self.started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        self.timings.append({'test': test.id(), 'seconds': time.perf_counter() - self.started})
        super().stopTest(test)


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def batches(suite):
    """Keep whole classes together, and whole modules with module fixtures."""
    groups = {}
    for test in cases(suite):
        module = sys.modules[type(test).__module__]
        fixtures = any(hasattr(module, name) for name in ('setUpModule', 'tearDownModule'))
        key = type(test).__module__ if fixtures else test.id().rsplit('.', 1)[0]
        groups.setdefault(key, []).append(test.id())
    # Start larger batches first; no test, fixture or assertion is omitted.
    return sorted(groups.values(), key=len, reverse=True)


def run_batch(identities):
    output = Output()
    result = TimedResult(output, descriptions=True, verbosity=2)
    with redirect_stdout(output), redirect_stderr(output):
        suite = unittest.defaultTestLoader.loadTestsFromNames(identities)
        result.startTestRun()
        try:
            suite.run(result)
        finally:
            result.stopTestRun()
        result.printErrors()
    return dict(output=output.getvalue(), tests=result.testsRun,
                successful=result.wasSuccessful(), timings=result.timings,
                failures=len(result.failures), errors=len(result.errors),
                skipped=len(result.skipped), expectedFailures=len(result.expectedFailures),
                unexpectedSuccesses=len(result.unexpectedSuccesses))


def run_batches(groups, workers):
    expected = Counter(identity for group in groups for identity in group)
    totals = dict(tests=0, failures=0, errors=0, skipped=0, expectedFailures=0, unexpectedSuccesses=0)
    successful, timings = True, []
    if workers == 1:
        results = map(run_batch, groups)
        executor = None
    else:
        executor = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn'))
        futures = [executor.submit(run_batch, group) for group in groups]
        results = (future.result() for future in as_completed(futures))
    try:
        for result in results:
            print(result['output'], end='', flush=True)
            for key in totals:
                totals[key] += result[key]
            successful &= result['successful']
            timings.extend(result['timings'])
    finally:
        if executor is not None:
            executor.shutdown(wait=True)
    actual = Counter(item['test'] for item in timings)
    if actual != expected:
        successful = False
        totals['errors'] += 1
        print('ERROR: executed test inventory differs from complete discovery', file=sys.stderr)
    return dict(**totals, successful=bool(successful), timings=timings)


def main():
    destination = os.environ.get('EXPENSES_TEST_TIMINGS')
    ledger = Path(os.environ.get('LEDGER_DIR', 'ledger')).resolve()
    if destination:
        target = Path(destination.replace('{persona}', ledger.name)).resolve()
        if not target.is_relative_to(Path('/tmp').resolve()):
            raise ValueError('Test timing artifacts must stay under /tmp')
    workers = int(os.environ.get('EXPENSES_TEST_WORKERS', '2'))
    if workers < 1:
        raise ValueError('EXPENSES_TEST_WORKERS must be positive')
    started = time.perf_counter()
    suite = unittest.defaultTestLoader.discover(str(SERVER / 'tests'))
    groups = batches(suite)
    # Scheduler contracts create their own two-worker pools. Run them in the
    # parent after the ordinary pool closes, keeping total worker load bounded.
    contracts = [group for group in groups if group[0].startswith('test_runner.')]
    groups = [group for group in groups if group not in contracts]
    workers = min(workers, max(1, len(groups)))
    print(f'Running complete offline discovery in {workers} isolated worker processes', flush=True)
    result = run_batches(groups, workers)
    if contracts:
        contract_result = run_batches(contracts, 1)
        for key in ('tests', 'failures', 'errors', 'skipped', 'expectedFailures', 'unexpectedSuccesses'):
            result[key] += contract_result[key]
        result['successful'] &= contract_result['successful']
        result['timings'].extend(contract_result['timings'])
    result.update(persona=ledger.name, seconds=time.perf_counter() - started, workers=workers)
    print(f"\nRan {result['tests']} tests in {result['seconds']:.3f}s\n")
    details = ', '.join(f'{name}={result[name]}' for name in ('failures', 'errors', 'skipped', 'expectedFailures', 'unexpectedSuccesses') if result[name])
    print(('OK' if result['successful'] else 'FAILED') + (f' ({details})' if details else ''))
    if destination:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2) + '\n')
    return not result['successful']


if __name__ == '__main__':
    sys.exit(main())
