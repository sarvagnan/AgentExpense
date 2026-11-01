"""Optional modules never open disabled data; sources and schedules stay offline."""
from contextlib import redirect_stdout
from datetime import date
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from test_ledger import Fixture
from store import dump, load, read_json
from generate_ledger import render
from check_store import check, check_price_coverage
from ledger_manifest import read_manifest, module_settings, old_ledger_path, deprecated_old_ledger
from project_paths import APPLICATION_ROOT
from weekly_review import summary

spec = importlib.util.spec_from_file_location('price_dispatch', APPLICATION_ROOT / 'scripts/prices/fetch_fund_navs.py')
prices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prices)


class ModuleTests(Fixture):
    def modules(self, **modules):
        manifest = read_json(self.root / 'ledger.json')
        manifest['modules'] = modules
        dump(self.root / 'ledger.json', manifest)

    def test_defaults_and_explicit_off(self):
        self.assertEqual(module_settings(read_manifest(self.root)), dict(fuel=False, investments={'prices': None}, history={'oldLedger': None}, weeklyReview=False))
        self.modules(investments=False)
        self.assertIs(read_manifest(self.root)['modules']['investments'], False)
        for invalid in (True, 0, '', [], {'unexpected': True}):
            self.modules(investments=invalid)
            with self.assertRaises(ValueError):
                read_manifest(self.root)

    def test_off_store_and_journal_do_not_open_module_files(self):
        self.modules(fuel=False, investments=False, weeklyReview=False)
        for relative in ('data/fuel-log.json', prices.PRICE_PATH, 'data/prices/fund-sources.json'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Deliberately invalid disabled data')
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.name in ('fuel-log.json', 'mutual-funds.prices', 'fund-sources.json'):
                raise AssertionError('Disabled module file opened')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded), redirect_stdout(io.StringIO()) as output:
            documents = load(self.root)
            check(self.root)
            self.assertNotIn('include data/prices', render(documents, self.root))
            self.assertFalse(prices.update(self.root))
            self.assertIn('off', summary(self.root))
        self.assertIn('WARNING: fuel is off', output.getvalue())

    def test_manual_prices_preserve_journal_without_a_fetch_source(self):
        path = self.root / prices.PRICE_PATH
        path.parent.mkdir(parents=True)
        path.write_text('P 2026-10-01 DEMO 5 USD\n')
        documents = load(self.root)
        self.modules(investments={'prices': 'mutual-fund-nav'})
        configured = render(documents, self.root)
        self.assertIn('include data/prices/mutual-funds.prices', configured)
        self.modules(investments={'prices': None})
        self.assertEqual(render(documents, self.root), configured)
        with patch.object(prices, 'load_source', side_effect=AssertionError('null dispatch')):
            self.assertFalse(prices.update(self.root))
        path.unlink()
        self.assertNotIn('include data/prices', render(documents, self.root))

    def test_manual_price_directives_are_checked_without_initial_coverage(self):
        self.modules(investments={'prices': None})
        path = self.root / prices.PRICE_PATH
        path.parent.mkdir(parents=True)
        path.write_text('not a price directive\n')
        with self.assertRaisesRegex(ValueError, 'price directive'):
            check_price_coverage(load(self.root), self.root)

    def test_fuel_on_requires_and_checks_log(self):
        self.modules(fuel=True)
        with self.assertRaises(FileNotFoundError):
            check(self.root)
        path = self.root / 'data/fuel-log.json'
        path.write_text('{"entries":null}')
        with self.assertRaisesRegex(ValueError, 'entries array'):
            check(self.root)
        path.write_text('{"meta":{},"entries":[]}')
        with redirect_stdout(io.StringIO()):
            check(self.root)

    def test_weekly_off_reads_no_audit_and_on_counts_all_outcomes(self):
        with patch('weekly_review.records', side_effect=AssertionError('off read')):
            self.assertIn('off', summary(self.root))
        self.modules(weeklyReview=True)
        events = [{'kind': 'draft', 'id': 'a'}, {'kind': 'statement', 'id': 'b'}, {'kind': 'review', 'line_id': 'a'}]
        with patch('weekly_review.records', return_value=events):
            text = summary(self.root)
        self.assertIn('Unreviewed: 1', text)
        self.assertIn(str(APPLICATION_ROOT / 'review-dashboard/server/WEEKLY-REVIEW.md'), text)

    def test_history_resolution_and_deprecated_fallback(self):
        old = self.root / 'archive/old.ledger'
        old.parent.mkdir(); old.write_text('; fictional history\n')
        self.modules(history={'oldLedger': 'archive/old.ledger'})
        self.assertEqual(old_ledger_path(self.root), old.resolve())
        self.modules(history={'oldLedger': '~/old.ledger'})
        with patch.object(Path, 'home', return_value=old.parent), patch.dict('os.environ', {'HOME': str(old.parent)}):
            self.assertEqual(old_ledger_path(self.root), old.resolve())
        self.modules(history={'oldLedger': 'missing'})
        self.assertIsNone(old_ledger_path(self.root))
        self.modules(history={'oldLedger': None})
        deprecated_old_ledger.cache_clear()
        self.addCleanup(deprecated_old_ledger.cache_clear)
        with patch.dict('os.environ', {'EXPENSES_OLD_LEDGER': str(old)}), self.assertWarnsRegex(FutureWarning, 'deprecated'):
            self.assertEqual(old_ledger_path(self.root), old.resolve())
            self.assertEqual(old_ledger_path(self.root), old.resolve())

    def test_fake_price_source_interface_and_null_no_dispatch(self):
        self.modules(investments={'prices': None})
        with patch.object(prices, 'load_source', side_effect=AssertionError('null dispatch')):
            self.assertFalse(prices.update(self.root))
        self.modules(investments={'prices': 'test-source'})
        class Fake:
            commodities = ('DEMO',)
            def fetch(self, start, end):
                self.bounds = (start, end)
                return 'P 2026-10-01 DEMO 12.5000 USD\n'
        source = Fake()
        with patch.object(prices, 'load_source', return_value=source) as factory:
            self.assertTrue(prices.update(self.root, date(2026,10,1), date(2026,10,2)))
            factory.assert_called_once_with('test-source', self.root)
            self.assertFalse(prices.update(self.root, date(2026,10,1), date(2026,10,2)))
        self.assertIn('include data/prices/mutual-funds.prices', render(load(self.root), self.root))
        self.assertEqual(source.bounds, (date(2026,10,1), date(2026,10,2)))
        before = (self.root / prices.PRICE_PATH).read_bytes()
        for invalid in ('P 2026-10-01 UNKNOWN 3 USD', 'P 2026-10-01 DEMO -3 USD', 'P 2026-11-01 DEMO 3 USD', 'include private.ledger'):
            with patch.object(prices, 'load_source', return_value=source), patch.object(source, 'fetch', return_value=invalid), self.assertRaises(ValueError):
                prices.update(self.root, date(2026,10,1), date(2026,10,2))
            self.assertEqual((self.root / prices.PRICE_PATH).read_bytes(), before)
        for invalid in ('../other', 'unknown-source'):
            with self.assertRaises(ValueError):
                prices.load_source(invalid, self.root)

    def test_nav_source_uses_ledger_mapping_and_offline_response(self):
        dump(self.root / 'data/prices/fund-sources.json', {'DEMOFUND': ['999', '999001', 'FICTIONAL0001']})
        source = prices.load_source('mutual-fund-nav', self.root)
        self.assertEqual(source.commodities, ('DEMOFUND',))
        response = (APPLICATION_ROOT / 'scripts/prices/fixtures/nav-response.txt').read_text()
        with patch.object(source, 'download', return_value=response) as download:
            text = source.fetch(date(2026,10,1), date(2026,10,3))
        self.assertEqual(len(prices.parse_directives(text)), 2)
        self.assertIn('20.2500 INR', text)
        self.assertIn('mf=999', download.call_args[0][0])
        with patch.object(source, 'download', return_value=response.replace('FICTIONAL0001','OTHER')), self.assertRaises(ValueError):
            source.fetch(date(2026,10,1), date(2026,10,3))

    def test_configured_price_coverage_and_missing_optional_file(self):
        account = 'Holdings:Demo'
        docs = {self.root / 'data/accounts.json': {'accounts': {account: {'kind':'investment'}}},
                self.root / 'data/expenses/2026.json': [{'date':'2026-10-01','postings':[{'account':account,'amount':'2.000','commodity':'DEMO','cost':{'amount':'10.00','commodity':'USD'}}]}]}
        self.modules(investments={'prices':None})
        check_price_coverage(docs, self.root)
        self.modules(investments={'prices':'mutual-fund-nav'})
        with self.assertRaisesRegex(ValueError, 'no price'):
            check_price_coverage(docs, self.root)
        path = self.root / prices.PRICE_PATH;path.parent.mkdir();path.write_text('P 2026-10-01 DEMO 5 USD\n')
        check_price_coverage(docs, self.root)

    def test_price_source_interface_version_is_enforced(self):
        directory = self.root / 'sources';directory.mkdir()
        (directory / 'fake-source.py').write_text('INTERFACE_VERSION = 2\n')
        with patch.object(prices, '__file__', str(self.root / 'dispatcher.py')), self.assertRaisesRegex(ValueError, 'Unsupported price source interface'):
            prices.load_source('fake-source', self.root)
