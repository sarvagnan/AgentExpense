"""Invented statement text and PDFs only; no history or live models."""
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
APP = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(APP / 'scripts/statements'))
sys.path.insert(0, str(APP / 'scripts/ledger'))
from statement_pdf import parse_text, parse_pdf, checks, recognised, ROW_HEADER
from extract_statements import extract
from agent import statement_result
from settings import Settings
from statement_fixtures import TEXT, statement_pdf, statement_answer


class FictionalParser(unittest.TestCase):
    def test_rows_and_printed_totals(self):
        result = parse_text(TEXT)
        self.assertEqual(len(result['rows']), 2)
        self.assertEqual(result['card_last4'], '5678')
        self.assertEqual(result['minimumDue'], '9.00')
        self.assertEqual(result['dueDate'], '2026-02-15')
        self.assertEqual(result['rows'][0]['foreignAmount'], '2.00')
        self.assertEqual(result['rows'][0]['foreignCurrency'], 'USD')
        self.assertIsNone(result['rows'][1]['foreignAmount'])
        self.assertTrue(all(c['ok'] for c in checks(result)))

    def test_mismatched_printed_totals(self):
        for old, new in [('Purchases: 120.00', 'Purchases: 121.00'), ('Credits: 30.00', 'Credits: 31.00'), ('Total due: 90.00', 'Total due: 91.00')]:
            with self.subTest(old=old):
                self.assertFalse(all(c['ok'] for c in checks(parse_text(TEXT.replace(old, new)))))
        self.assertFalse(checks(parse_text(TEXT), previous='1.00')[-1]['ok'])

    def test_optional_expected_identity(self):
        self.assertTrue(recognised(TEXT))
        self.assertTrue(recognised(TEXT, '5678'))
        self.assertFalse(recognised(TEXT, '9999'))
        with self.assertRaises(ValueError): parse_text(TEXT, expected_last4='9999')

    def test_wrong_format_and_missing_period(self):
        for text in [TEXT.replace('Example Card Statement', 'Unrelated document'), TEXT.replace('Statement period:', 'Period:')]:
            with self.assertRaises(ValueError): parse_text(text)

    def test_required_headers_and_optional_identity_appear_once(self):
        for line in TEXT.splitlines()[:11]:
            if ':' not in line: continue
            if line.startswith('Card last four digits:'):
                self.assertIsNone(parse_text(TEXT.replace(line + '\n', ''))['card_last4'])
                with self.assertRaises(ValueError): parse_text(TEXT.replace(line, line+'\n'+line))
                continue
            with self.subTest(line=line):
                with self.assertRaises(ValueError): parse_text(TEXT.replace(line + '\n', ''))
                with self.assertRaises(ValueError): parse_text(TEXT.replace(line, line + '\n' + line))

    def test_duplicate_serials_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            parse_text(TEXT.replace('EX-002', 'EX-001'))

    def test_malformed_rows_are_never_silently_skipped(self):
        for old, new in [('120.00 | Debit', 'bad | Debit'), ('| Debit |', '| Other |'), ('EX-001', ''), ('Fable Shop', ''), ('2026-01-02', '2026-02-30'), ('| 2.00 | USD', '| 2.00 |'), ('| 2.00 | USD', '| | USD'), ('| 2.00 | USD', '| 2.00 | US'), ('| Debit | 2.00', '| Debit 2.00')]:
            with self.subTest(new=new):
                with self.assertRaises(ValueError): parse_text(TEXT.replace(old, new))

    def test_period_and_due_dates_are_valid(self):
        for old, new in [('2026-01-01 to 2026-01-28', '2026-01-29 to 2026-01-28'), ('2026-02-15', '2026-02-30')]:
            with self.assertRaises(ValueError): parse_text(TEXT.replace(old, new))

    def test_no_rows_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'No statement rows'):
            parse_text(TEXT.split(ROW_HEADER)[0])

    def test_page_numbers_repeated_table_headings_and_decimal_grouping(self):
        text = TEXT.replace('2026-01-03 |', '\f' + ROW_HEADER + '\n2026-01-03 |')
        result = parse_text(text.replace('120.00', '1,120.00').replace('90.00', '1,090.00'))
        self.assertEqual([row['page'] for row in result['rows']], [1, 2])
        self.assertEqual(result['rows'][0]['amount'], '1120.00')
        self.assertTrue(all(c['ok'] for c in checks(result)))

    def test_pdfium_reads_invented_pdf_across_pages(self):
        with tempfile.TemporaryDirectory(prefix='example-card-pdf-') as temporary:
            path = Path(temporary) / 'CardStatement-2026-01-28.pdf'
            text = TEXT.replace('2026-01-03 |', '\f' + ROW_HEADER + '\n2026-01-03 |')
            path.write_bytes(statement_pdf(text))
            self.assertEqual(parse_pdf(path), parse_text(text))

    def test_batch_reader_uses_same_layout_and_rejects_bad_totals(self):
        with tempfile.TemporaryDirectory(prefix='example-card-text-') as temporary:
            path = Path(temporary) / 'CardStatement-2026-01-28.txt'
            path.write_text(TEXT)
            exported = extract(temporary)
            self.assertEqual(exported['statements'][0]['totalDue'], '90.00')
            self.assertEqual(exported['transactions'][0]['internationalCurrency'], 'USD')
            self.assertEqual(exported['transactions'][0]['serialNumber'], 'EX-001')
            path.write_text(TEXT.replace('Purchases: 120.00', 'Purchases: 121.00'))
            with self.assertRaises(ValueError): extract(temporary)

    def test_fallback_checks_only_configured_suffix(self):
        answer = statement_answer()
        self.assertEqual(statement_result(answer), parse_text(TEXT))
        statement_result(answer, '5678')
        with self.assertRaises(ValueError): statement_result(answer, '9999')

    def test_settings_read_optional_history(self):
        with patch.dict('os.environ', {'EXPENSES_OLD_LEDGER':''}):
            self.assertIsNone(Settings.from_env().old_ledger)
