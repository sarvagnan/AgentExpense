"""The large fictional ledger generator: deterministic, complete and strictly valid."""
from collections import Counter
from decimal import Decimal
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

APPLICATION = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(APPLICATION / 'scripts/ledger'), str(APPLICATION / 'scripts/fixtures')]
import generate_large
from ledger_manifest import read_manifest
from store import entries, load


def files(root):
    # The journal is generated output, written by one of the tests below.
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file() and p.name != 'expense_ledger.ledger'}


def generate(*args):
    from contextlib import redirect_stdout
    import io
    with redirect_stdout(io.StringIO()):
        return generate_large.main([*args, '--no-repository'])


class GenerateLargeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='expense-large-')
        cls.base = Path(cls.temp.name)
        cls.root = generate('--dir', str(cls.base / 'one'))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_same_seed_writes_the_same_bytes_and_another_seed_differs(self):
        again = generate('--dir', str(self.base / 'two'))
        self.assertEqual(files(self.root), files(again))
        other = generate('--dir', str(self.base / 'three'), '--seed', '7', '--entries', '800')
        self.assertNotEqual(files(self.root)['data/forecast.json'], files(other)['data/forecast.json'])
        shutil.rmtree(again)
        shutil.rmtree(other)

    def test_default_covers_every_feature_and_passes_the_strict_checks(self):
        from check_store import check, check_card_statements
        from generate_ledger import render
        documents = load(self.root)
        actual = entries(documents)
        self.assertGreater(len(actual), 4000)
        self.assertEqual({e['date'][:4] for e in actual}, {str(y) for y in range(2021, 2027)})
        accounts = documents[self.root / 'data/accounts.json']['accounts']
        kinds = Counter(meta.get('kind') for meta in accounts.values() if meta.get('kind'))
        self.assertLessEqual({'bank', 'card', 'cash', 'wallet', 'investment', 'person'}, set(kinds))
        self.assertEqual({p['commodity'] for e in actual for p in e['postings']}, {'INR', 'USD', 'BENCHFUND'})
        rules = documents[self.root / 'data/forecast.json']
        self.assertEqual(len(rules), 32)
        self.assertTrue(any(r.get('amount') == 'varies' for r in rules))
        self.assertEqual({e['rule']['id'] for e in actual if e['rule']}, {r['id'] for r in rules})
        # Fuel readings go through the fuel extension that ledger.json lists.
        self.assertEqual(read_manifest(self.root)['extensions'], {'fuel': {'ask': True}})
        self.assertGreater(sum(1 for e in actual if (e['extensions']['fuel'] or {}).get('litres')), 100)
        statements = documents[self.root / 'data/card-statements.json']
        self.assertGreater(len(statements), 40)
        self.assertEqual(sum(s['rows'] for s in statements), sum(1 for e in actual if (e['statement'] or {}).get('end')))
        unfiled = [e for e in actual if e['date'] > statements[-1]['end'] and any(p['account'] == generate_large.CARD for p in e['postings'])]
        self.assertGreater(len(unfiled), 100)
        assertions = [p for e in actual for p in e['postings'] if p.get('assert')]
        self.assertGreater(len(assertions), 60)
        self.assertTrue(any(p.get('cost') and p['commodity'] == 'BENCHFUND' for e in actual for p in e['postings']))
        self.assertTrue(any(p.get('cost') and p['commodity'] == 'USD' for e in actual for p in e['postings']))
        self.assertTrue(any(e['status'] == 'pending' for e in actual))
        self.assertTrue((self.root / 'data/prices/mutual-funds.prices').read_text().count('\nP ') > 50)
        self.assertTrue(list((self.root / 'data/additions').glob('*.jsonl')))
        sources = {s for e in actual for s in e['evidence']}
        self.assertTrue(all((self.root / s).is_file() for s in sources))
        for entry in actual:
            totals = Counter()
            for p in entry['postings']:
                cost = p.get('cost')
                key = cost['commodity'] if cost else p['commodity']
                totals[key] += Decimal(cost['amount']) * (1 if Decimal(p['amount']) >= 0 else -1) if cost else Decimal(p['amount'])
            self.assertTrue(all(v == 0 for v in totals.values()), entry['id'])
        self.assertTrue(all(s['result'] == 'PASS' for s in check_card_statements(documents, self.root)['statements']))
        journal = render(documents, self.root)
        self.assertTrue(1_000_000 <= len(journal.encode()) <= 1_500_000, len(journal.encode()))
        (self.root / 'expense_ledger.ledger').write_text(journal)
        hledger = os.environ.get('HLEDGER_BIN') or shutil.which('hledger')
        if hledger:
            result = subprocess.run([hledger, '-f', str(self.root / 'expense_ledger.ledger'), 'check', '-s', 'ordereddates'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(os.devnull, 'w') as quiet:
                from contextlib import redirect_stdout
                with redirect_stdout(quiet):
                    check(self.root)

    def test_refuses_the_application_tree_and_a_used_folder(self):
        from contextlib import redirect_stderr
        import io
        for args in (['--dir', str(APPLICATION / 'tmp' / 'never-large')], ['--dir', str(self.root)],
                     ['--dir', str(self.base / 'tiny'), '--entries', '10']):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                generate(*args)
        self.assertFalse((APPLICATION / 'tmp' / 'never-large').exists())
        self.assertFalse((self.base / 'tiny').exists())


if __name__ == '__main__':
    unittest.main()
