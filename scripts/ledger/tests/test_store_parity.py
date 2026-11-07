"""Python (the pydantic models) and Node (the generated schema) refuse the same store documents.

Each case writes one bad document into a small ledger. Python reads it as the store does
(``read_json`` then the ledger's models); Node reads it as the dashboard build does
(``parseStoreJson`` then ``validateDocument`` against the ledger's composed schema, or
``readExtensions`` for a definition). Both must refuse it, naming the same document and
field, and with the same words where the models' own checks give the reason.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ledger import CARD, Fixture, entry, posting, rule, statement_row
from store import canonical_text, check_document, dump, ledger_models, read_json

APPLICATION = Path(__file__).resolve().parents[3]
VISIT = {'name': 'visit', 'description': 'Fictional visits.', 'fields': {
    'place': {'type': 'text', 'filled-by': 'agent', 'description': 'Where.', 'journal-tag': 'visit-place'},
    'count': {'type': 'whole-number', 'filled-by': 'person', 'description': 'How many.', 'journal-tag': 'visit-count'}}}
BANK = 'Assets:Bank:Savings Account'
BANK_STATEMENT = {'kind': 'bank', 'id': '4' * 16, 'account': BANK, 'start': '2026-01-01', 'end': '2026-01-28', 'source': 'Bills/bank.csv',
                  'source-balance-style': None, 'opening-balance': '100.00', 'closing-balance': '90.00', 'debits': '10.00', 'credits': '0.00',
                  'rows': 1, 'members': [{'date': '2026-01-02', 'serial': 'row-2', 'description': 'Fictional shop', 'amount': '10.00',
                                          'direction': 'Debit', 'citation': {'sheet': 'CSV', 'row': 2}, 'reference': None, 'balance': None,
                                          'entry-id': '2' * 16, 'disposition': 'recorded', 'review-candidates': []}],
                  'assertion-id': None, 'reader': 'generic-csv'}


DOCUMENT_ONLY = object()  # both refuse the document; the field is named differently


def visit_entry(**fields):
    fields.setdefault('extensions', {'visit': None})
    return entry(**fields)


def trash_item(**fields):
    item = {'id': 'a' * 32, 'entry': visit_entry(identity='e' * 16), 'removed-at': '2026-01-06T10:00:00+05:30', 'added-at': None,
            'reason': '', 'index': 0, 'files': [], 'kept': [], 'rule-changes': [], 'duplicate-links': {}}
    item.update(fields)
    return item


def foreign(amount='1.00', commodity='USD', cost=None):
    return posting('Expenses:Test', amount, commodity, cost=cost)


# (name, document, its value, and the reason both give when it comes from the models' own checks)
CASES = [
    ('all-null references', 'data/expenses/2026.json', [visit_entry(references={'reference': None, 'order-id': []})],
     'a group with no values must be null'),
    ('all-null statement row', 'data/expenses/2026.json', [visit_entry(statement=statement_row())], 'a group with no values must be null'),
    ('all-null extension group', 'data/expenses/2026.json', [visit_entry(extensions={'visit': {'place': None, 'count': None}})],
     'a group with no values must be null'),
    ('unbalanced entry', 'data/expenses/2026.json', [visit_entry(postings=[posting('Expenses:Test', '10.00'), posting(CARD, '-10.01')])],
     'the postings do not balance exactly in INR'),
    ('implicit conversion', 'data/expenses/2026.json', [visit_entry(postings=[foreign('1.00'), posting(CARD, '-90.00')])],
     'the postings do not balance exactly in INR, USD; amounts in different commodities need a cost (no implicit conversion)'),
    ('negative cost', 'data/expenses/2026.json', [visit_entry(postings=[
        foreign(cost={'amount': '-90.00', 'commodity': 'INR', 'per': 'total'}), posting(CARD, '90.00')])], None),
    ('zero cost', 'data/expenses/2026.json', [visit_entry(postings=[
        foreign(cost={'amount': '0.00', 'commodity': 'INR', 'per': 'unit'}), posting('Expenses:Test', '-1.00', 'USD')])], None),
    ('zero quantity at a total cost', 'data/expenses/2026.json', [visit_entry(postings=[
        foreign('0.00', cost={'amount': '90.00', 'commodity': 'INR', 'per': 'total'}), posting(CARD, '-90.00')])],
     'a total cost needs a non-zero amount'),
    ('inexact unit cost', 'data/expenses/2026.json', [visit_entry(postings=[
        foreign('3.00', cost={'amount': '33.33', 'commodity': 'INR', 'per': 'unit'}), posting(CARD, '-100.00')])],
     'the postings do not balance exactly in INR; amounts in different commodities need a cost (no implicit conversion)'),
    ('cost in its own commodity', 'data/expenses/2026.json', [visit_entry(postings=[
        posting('Expenses:Test', '10.00', cost={'amount': '10.00', 'commodity': 'INR', 'per': 'total'}), posting(CARD, '-10.00')])],
     'a cost must be in a different commodity from its posting'),
    ('statement end without serial or page', 'data/expenses/2026.json', [visit_entry(statement=statement_row(end='2026-01-28'))],
     'a card statement row needs its serial and page'),
    ('unsorted duplicate links', 'data/expenses/2026.json', [visit_entry(identity='1' * 16, **{'possible-duplicate-of': [
        {'id': '3' * 16, 'reason': 'dissimilar-name'}, {'id': '2' * 16, 'reason': 'dissimilar-name'}]})],
     'possible-duplicate-of needs unique, sorted IDs greater than the entry ID'),
    ('bank statement without assertion-id', 'data/card-statements.json', [BANK_STATEMENT], 'a balance-style statement needs assertion-id'),
    ('unbalanced rule', 'data/forecast.json', [rule(postings=[posting('Expenses:Test', '10.00'), posting(CARD, '-9.00')])],
     'the postings do not balance exactly in INR'),
    ('rule date off its schedule', 'data/forecast.json', [rule(date='2026-01-03')], 'a rule date must equal schedule.from'),
    ('removed rule without removal', 'data/forecast.json', [rule(lifecycle='removed')], 'a removed rule needs removal'),
    ('varying rule with three postings', 'data/forecast.json', [{**rule(postings=[
        posting('Expenses:Test', '6.00'), posting('Expenses:Test', '4.00'), posting(CARD, '-10.00')]), 'amount': 'varies'}],
     'only simple two-posting payments in one currency can vary'),
    ('bill on a fixed rule', 'data/forecast.json', [rule(bill={'amount': '10.00', 'received': '2026-01-01', 'pay-by': None,
                                                               'occurrence': '2026-01-02', 'evidence': []})],
     'a bill needs a varying rule, a positive amount and the current occurrence'),
    ('impossible date-time', 'data/trash.json', [trash_item(**{'removed-at': '2026-13-01T10:00:00Z'})], None),
    ('impossible clock time', 'data/trash.json', [trash_item(**{'removed-at': '2026-01-01T24:00:01Z'})], None),
    ('5.0 for an integer', 'data/expenses/2026.json', [visit_entry(statement=statement_row(page=5.0))], None),
    ('year 0000', 'data/expenses/2026.json', [visit_entry(day='0000-01-02')], None),
    ('U+FEFF in a currency code', 'data/expenses/2026.json', [visit_entry(**{'original-amount': {'amount': '5.00', 'commodity': 'US\ufeffD'}})], None),
    ('U+0085 in a currency code', 'data/expenses/2026.json', [visit_entry(**{'original-amount': {'amount': '5.00', 'commodity': 'US\u0085D'}})], None),
    ('U+001C in a currency code', 'data/expenses/2026.json', [visit_entry(**{'original-amount': {'amount': '5.00', 'commodity': 'US\u001cD'}})], None),
    # Account metadata is an untagged union by root: pydantic names its first branch, Node the field it checked.
    ('only U+0085 in an expense category', 'data/accounts.json', None, DOCUMENT_ONLY),
    ('5e0 for an integer', 'data/forecast.json', [rule(schedule={'every': 'month', 'from': '2026-01-02', 'interval': '5e0', 'anchor-day': None})], None),
]
EXPONENT = '"interval": "5e0"'


def python_failure(root, document):
    path = root / document
    try:
        ledger_models(root)
        check_document(path, read_json(path), root)
    except ValueError as error:
        return str(error)
    return None


def split_python(message):
    found = re.match(r'^(?P<doc>[^:]+\.json):(?P<loc>.*?): (?:Value error, )?(?P<reason>.*?)(?: \(and \d+ more\))?$', message, re.S)
    return (found['doc'], found['loc'], found['reason']) if found else (None, None, message)


def split_node(message):
    found = re.match(r'^Invalid (?P<doc>\S+?\.json)(?:\.(?P<loc>.*?))?(?:: (?P<reason>.*?))?\.$', message, re.S)
    return (found['doc'], found['loc'] or '', found['reason']) if found else (None, None, message)


NODE = """
import {parseStoreJson, readExtensions, readManifest, storeSchema, validateDocument} from %s;
import {readFile} from 'node:fs/promises';
import path from 'node:path';
const results = [];
for (const [root, document] of JSON.parse(process.argv[1])) {
  try {
    const {schema} = await storeSchema(root, await readManifest(root));
    const value = parseStoreJson(await readFile(path.join(root, document), 'utf8'), document);
    const key = document.startsWith('data/expenses/') ? 'data/expenses/<year>.json' : document;
    validateDocument(value, schema, key, document);
    results.push(null);
  } catch (error) { results.push(String(error.message)); }
}
console.log(JSON.stringify(results));
"""


class ParityTests(Fixture):
    def ledger(self, name, document, value):
        root = self.root.parent / f'{self.root.name}-{name.replace(" ", "-").replace(".", "")}'
        root.mkdir()
        self.addCleanup(__import__('shutil').rmtree, root, True)
        (root / 'extensions').mkdir()
        (root / 'extensions/visit.json').write_text(json.dumps(VISIT))
        manifest = read_json(self.root / 'ledger.json')
        manifest['extensions'] = {'visit': {'ask': True}}
        dump(root / 'ledger.json', manifest)
        accounts = read_json(self.root / 'data/accounts.json')
        (root / 'data/expenses').mkdir(parents=True)
        dump(root / 'data/accounts.json', accounts)
        for name_, empty in (('data/expenses/2026.json', [visit_entry()]), ('data/forecast.json', []), ('data/card-statements.json', []),
                             ('data/trash.json', [])):
            dump(root / name_, empty)
        if value is None:  # an expense category holding only whitespace JavaScript doesn't call whitespace
            value = deepcopy(accounts)
            value['accounts']['Expenses:Test'] = {'category': '\u0085'}
        text = canonical_text(value)
        if document == 'data/forecast.json' and EXPONENT in text:
            text = text.replace(EXPONENT, '"interval": 5e0')
        (root / document).write_text(text)
        return root

    def node(self, cases):
        module = json.dumps((APPLICATION / 'review-dashboard/scripts/ledger-manifest.mjs').as_uri())
        run = subprocess.run(['node', '--input-type=module', '-e', NODE % module, json.dumps([[str(r), d] for r, d in cases])],
                             capture_output=True, text=True, check=True)
        return json.loads(run.stdout)

    def test_both_languages_refuse_the_same_documents_at_the_same_field(self):
        roots = [(self.ledger(name, document, deepcopy(value)), document) for name, document, value, _ in CASES]
        # Valid documents pass both, the end of a day written as 24:00 included.
        valid = [(self.ledger('valid base', 'data/expenses/2026.json', [visit_entry()]), 'data/expenses/2026.json'),
                 (self.ledger('midnight', 'data/trash.json', [trash_item(**{'removed-at': '2026-01-01T24:00:00.000+05:30'})]), 'data/trash.json')]
        node = self.node([*roots, *valid])
        for (root, document), node_message in zip(valid, node[len(roots):]):
            self.assertIsNone(python_failure(root, document))
            self.assertIsNone(node_message)
        for (name, document, _, reason), (root, _), node_message in zip(CASES, roots, node):
            with self.subTest(case=name):
                python_message = python_failure(root, document)
                self.assertIsNotNone(python_message, 'Python accepted it')
                self.assertIsNotNone(node_message, 'Node accepted it')
                python_doc, python_loc, python_reason = split_python(python_message)
                node_doc, node_loc, node_reason = split_node(node_message)
                self.assertEqual(python_doc, document, python_message)
                if reason is DOCUMENT_ONLY:
                    self.assertEqual(node_doc, document, node_message)
                    continue
                self.assertEqual((node_doc, node_loc), (python_doc, python_loc), f'{python_message} | {node_message}')
                if reason:
                    self.assertEqual((python_reason, node_reason), (reason, reason))

    def test_both_refuse_a_repeated_json_key_and_name_the_document(self):
        root = self.ledger('repeated key', 'data/expenses/2026.json', [visit_entry()])
        path = root / 'data/expenses/2026.json'
        path.write_text(path.read_text().replace('"status": "confirmed",', '"status": "confirmed", "status": "pending",', 1))
        python_message = python_failure(root, 'data/expenses/2026.json')
        node_message = self.node([(root, 'data/expenses/2026.json')])[0]
        self.assertEqual(python_message, "data/expenses/2026.json: Duplicate JSON key: 'status'")
        self.assertEqual(node_message, 'Invalid data/expenses/2026.json: Duplicate JSON key: "status".')
        # A repeat in another object, or the same text inside a string, is not a repeated key.
        path.write_text(canonical_text([visit_entry(description='"status": "a", "status": "b"')]))
        self.assertIsNone(python_failure(root, 'data/expenses/2026.json'))
        self.assertIsNone(self.node([(root, 'data/expenses/2026.json')])[0])

    def test_both_refuse_an_extension_whose_journal_tag_clashes_with_a_core_label(self):
        for tag in ('amount', 'order-id', 'item'):
            with self.subTest(tag=tag):
                bad = deepcopy(VISIT)
                bad['fields']['place']['journal-tag'] = tag
                root = self.ledger('clash ' + tag, 'data/expenses/2026.json', [visit_entry()])
                (root / 'extensions/visit.json').write_text(json.dumps(bad))
                expected = f'journal tags clash with core labels: {tag}'
                with self.assertRaises(ValueError) as caught:
                    ledger_models(root)
                self.assertIn('extensions/visit.json is not a valid extension definition', str(caught.exception))
                self.assertIn(expected, str(caught.exception))
                node = self.node([(root, 'data/expenses/2026.json')])[0]
                self.assertIn('extensions/visit.json is not a valid extension definition', node)
                self.assertIn(expected, node)


if __name__ == '__main__':
    unittest.main()
