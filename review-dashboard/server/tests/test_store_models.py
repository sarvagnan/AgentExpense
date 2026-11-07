"""Format 2 store models: generated schemas, setters, the derived answer and strictness.

Model-only tests (step 6h part 2 design). Nothing here reads a ledger.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
from typing import Literal
import unittest

from pydantic import BaseModel, ValidationError

APPLICATION = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
import store_models as m  # noqa: E402

STORE_MD = APPLICATION / 'docs/STORE.md'
AGENT_ENTRY = {'date', 'status', 'description', 'references', 'classification', 'original-amount', 'fuel', 'notes', 'postings'}
AGENT_POSTING = {'account', 'amount', 'commodity', 'cost'}
ENVELOPE = {'status', 'question', 'entry', 'new-accounts', 'doubts', 'attachment-names', 'explanation'}
# Keywords the Node loader's validator (review-dashboard/scripts/ledger-manifest.mjs) understands,
# plus annotations it ignores. A generated keyword outside this set would be silently skipped there.
NODE_KEYWORDS = {'$schema', '$id', '$defs', '$ref', 'title', 'description', 'anyOf', 'type', 'const', 'enum', 'minimum',
                 'maximum', 'pattern', 'minLength', 'format', 'minItems', 'uniqueItems', 'items', 'required', 'properties',
                 'patternProperties', 'additionalProperties'}


def posting(account, amount, commodity='INR', **extra):
    return {'account': account, 'amount': amount, 'commodity': commodity, 'cost': None, 'assert': None, 'notes': [], **extra}


def entry(**changes):
    value = {
        'id': '0123456789abcdef', 'date': '2026-03-04', 'status': 'confirmed', 'description': 'Orchid Market groceries',
        'evidence': [], 'statement': None, 'references': None, 'classification': None, 'original-amount': None, 'fuel': None,
        'reimbursement': None, 'policy-term-years': None, 'excluded-from-estimate': None, 'rule': None,
        'possible-duplicate-of': [], 'notes': [],
        'postings': [posting('Expenses:Groceries', '120.50'), posting('Assets:Cash', '-120.50')],
    }
    value.update(changes)
    return value


def examples():
    """(model name, JSON) for every example block in docs/STORE.md."""
    text = STORE_MD.read_text(encoding='utf-8')
    return re.findall(r'<!-- example: (\w+) -->\n```json\n(.*?)```', text, flags=re.S)


def schema_keywords(node, found=None):
    found = set() if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            if key in ('properties', 'patternProperties', '$defs', 'x-documents'):
                for item in value.values():
                    schema_keywords(item, found)
            elif not key.startswith('x-'):
                schema_keywords(value, found)
    elif isinstance(node, list):
        for item in node:
            schema_keywords(item, found)
    return found


class GeneratedSchemaTests(unittest.TestCase):
    def test_checked_in_schemas_match_the_models(self):
        for path, content in m.generated().items():
            with self.subTest(path=path.name):
                self.assertTrue(path.exists(), f'{path.name} is missing; run store_models.py --write')
                self.assertEqual(path.read_text(encoding='utf-8'), content,
                                 f'{path.name} is out of date; run: uv run --locked --directory review-dashboard/server '
                                 'python ../../scripts/ledger/store_models.py --write')

    def test_format_1_schema_is_untouched_by_format_2(self):
        live = json.loads((APPLICATION / 'schema/store.schema.json').read_text(encoding='utf-8'))
        self.assertIn('sources', live['$defs']['entry']['properties'])
        self.assertEqual(m.STORE_SCHEMA.name, 'store-2.schema.json')

    def test_store_schema_uses_only_keywords_the_node_loader_validates(self):
        unknown = {key for key in schema_keywords(json.loads(m.STORE_SCHEMA.read_text(encoding='utf-8')))
                   if not key.startswith('x-')} - NODE_KEYWORDS
        self.assertEqual(unknown, set())

    def test_every_document_has_a_root_definition(self):
        schema = m.store_schema()
        for path, ref in schema['x-documents'].items():
            with self.subTest(path=path):
                self.assertIn(ref.removeprefix('#/$defs/'), schema['$defs'])


class SetterTests(unittest.TestCase):
    def test_every_ledger_field_has_a_known_setter(self):
        known = {setter.value for setter in m.Setter}
        for model in m.LEDGER_DATA:
            for name, info in model.model_fields.items():
                with self.subTest(field=f'{model.__name__}.{info.alias}'):
                    self.assertTrue(m.setters(info), 'no setter')
                    self.assertLessEqual(set(m.setters(info)), known)

    def test_every_model_inside_an_entry_or_rule_is_checked_for_setters(self):
        reached, todo = set(), [m.Entry, m.Rule]
        while todo:
            model = todo.pop()
            if model in reached:
                continue
            reached.add(model)
            todo.extend(nested for info in model.model_fields.values() for nested in m._nested(info.annotation))
        self.assertEqual(reached, set(m.LEDGER_DATA))

    def test_a_field_added_without_a_setter_fails_the_check(self):
        class Grown(m.Entry):
            mood: str | None = m.field('mood', 'An unmarked field.')
        self.assertEqual(m.missing_setters(Grown), ['Grown.mood'])
        self.assertEqual(m.missing_setters(m.Entry), [])

    def test_store_schema_records_the_setters(self):
        entry_schema = m.store_schema()['$defs']['Entry']['properties']
        self.assertEqual(entry_schema['id']['x-set-by'], ['server'])
        self.assertEqual(entry_schema['statement']['x-set-by'], ['statement'])
        self.assertEqual(entry_schema['fuel']['x-module'], 'fuel')


class AnswerTests(unittest.TestCase):
    def agent_fields(self, model, modules):
        return {info.alias for info in model.model_fields.values()
                if 'agent' in m.setters(info) and m.module_of(info) in (None, *modules)}

    def test_answer_schema_holds_exactly_the_agents_fields(self):
        for fuel, path in ((True, m.ANSWER_SCHEMA), (False, m.ANSWER_SCHEMA_WITHOUT_FUEL)):
            with self.subTest(fuel=fuel):
                schema = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(set(schema['properties']), ENVELOPE)
                entry_ref = next(option['$ref'] for option in schema['properties']['entry']['anyOf'] if '$ref' in option)
                defs = schema['$defs']
                entry_schema = defs[entry_ref.removeprefix('#/$defs/')]
                expected = AGENT_ENTRY if fuel else AGENT_ENTRY - {'fuel'}
                self.assertEqual(set(entry_schema['properties']), expected)
                self.assertEqual(expected, self.agent_fields(m.Entry, {'fuel'} if fuel else set()))
                posting_schema = defs[entry_schema['properties']['postings']['items']['$ref'].removeprefix('#/$defs/')]
                self.assertEqual(set(posting_schema['properties']), AGENT_POSTING)
                self.assertEqual(AGENT_POSTING, self.agent_fields(m.Posting, set()))
                cost = next(o['$ref'] for o in posting_schema['properties']['cost']['anyOf'] if '$ref' in o)
                self.assertEqual(set(defs[cost.removeprefix('#/$defs/')]['properties']), {'amount', 'commodity'})

    def test_answer_schema_suits_strict_structured_output(self):
        for path in (m.ANSWER_SCHEMA, m.ANSWER_SCHEMA_WITHOUT_FUEL):
            schema = json.loads(path.read_text(encoding='utf-8'))
            objects = [schema, *schema['$defs'].values()]
            for node in objects:
                with self.subTest(path=path.name, node=node.get('description', '')[:40]):
                    self.assertIs(node['additionalProperties'], False)
                    self.assertEqual(set(node['required']), set(node['properties']))
            self.assertFalse([key for key in schema_keywords(schema) if key.startswith('x-')])

    def test_answer_rejects_fields_the_server_sets(self):
        model = m.answer_model(True)
        good = json.loads(dict(examples())['Answer'])
        model.model_validate(good)
        for key, value in (('statement', None), ('id', '0123456789abcdef'), ('evidence', []), ('rule', None)):
            bad = deepcopy(good)
            bad['entry'][key] = value
            with self.subTest(field=key), self.assertRaises(ValidationError):
                model.model_validate(bad)
        bad = deepcopy(good)
        bad['entry']['postings'][0]['assert'] = None
        with self.assertRaises(ValidationError):
            model.model_validate(bad)
        bad = deepcopy(good)
        bad['entry']['postings'][0]['cost']['per'] = 'total'
        with self.assertRaises(ValidationError):
            model.model_validate(bad)

    def test_fuel_is_refused_when_the_module_is_off(self):
        answer = json.loads(dict(examples())['Answer'])
        m.answer_model(False).model_validate({**answer, 'entry': {k: v for k, v in answer['entry'].items() if k != 'fuel'}})
        with self.assertRaises(ValidationError):
            m.answer_model(False).model_validate(answer)

    def test_derived_answer_follows_a_new_agent_field(self):
        class Grown(m.Entry):
            mood: str | None = m.field('mood', 'A new agent field.', (m.Setter.AGENT,))
        self.assertIn('mood', m.derive(Grown).model_fields)


class StrictnessTests(unittest.TestCase):
    def test_docs_examples_validate(self):
        found = examples()
        self.assertGreaterEqual({name for name, _ in found}, {'Entry', 'Rule', 'Answer'})
        models = {'Entry': m.Entry, 'Rule': m.Rule, 'Answer': m.answer_model(True)}
        for name, text in found:
            with self.subTest(example=name):
                value = json.loads(text)
                self.assertEqual(models[name].model_validate(value).model_dump(mode='json'), value)

    def test_an_entry_round_trips_in_field_order(self):
        value = entry()
        self.assertEqual(list(m.Entry.model_validate(value).model_dump(mode='json')), list(value))

    def test_a_missing_key_is_an_error(self):
        for key in ('notes', 'statement', 'evidence'):
            value = entry()
            del value[key]
            with self.subTest(key=key), self.assertRaises(ValidationError):
                m.Entry.model_validate(value)
        value = entry()
        del value['postings'][0]['cost']
        with self.assertRaises(ValidationError):
            m.Entry.model_validate(value)

    def test_an_extra_key_is_an_error(self):
        with self.assertRaises(ValidationError):
            m.Entry.model_validate(entry(sources=[]))
        value = entry()
        value['postings'][0]['per'] = 'total'
        with self.assertRaises(ValidationError):
            m.Entry.model_validate(value)
        with self.assertRaises(ValidationError):
            m.Entry.model_validate(entry(references={'reference': 'R-1', 'order-id': None, 'serial': None}))

    def test_amounts_are_exact_decimal_text(self):
        for amount in (120.5, 120, '120', '1.2e2', ' 120.50', '+120.50', '0120.50'):
            value = entry()
            value['postings'][0]['amount'] = amount
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                m.Entry.model_validate(value)
        value = entry()
        value['postings'][0]['cost'] = {'amount': 2.5, 'commodity': 'USD', 'per': 'total'}
        with self.assertRaises(ValidationError):
            m.Entry.model_validate(value)
        value['postings'][0]['cost']['amount'] = '100'
        m.Entry.model_validate(value)

    def test_strict_types_and_values(self):
        cases = {
            'date': '2026-02-30', 'status': 'Confirmed', 'id': '0123456789ABCDEF', 'description': 'two\nlines',
            'policy-term-years': '2', 'notes': ['one\nline'], 'postings': [],
        }
        for key, value in cases.items():
            with self.subTest(key=key), self.assertRaises(ValidationError):
                m.Entry.model_validate(entry(**{key: value}))
        with self.assertRaises(ValidationError):
            m.Entry.model_validate(entry(**{'policy-term-years': True}))

    def test_a_group_with_no_values_is_null(self):
        with self.assertRaises(ValidationError):
            m.Entry.model_validate(entry(references={'reference': None, 'order-id': None}))
        with self.assertRaises(ValidationError):
            m.Entry.model_validate(entry(statement={'end': None, 'serial': None, 'page': None, 'source-page': None, 'source-row': None}))
        m.Entry.model_validate(entry(fuel={'litres': '8.00', 'trip-km': None}))

    def test_other_documents(self):
        accounts = {'commodities': ['INR'], 'accounts': {
            'Assets:Cash': {'kind': 'cash', 'currency': 'INR', 'label': None, 'aliases': ['cash'], 'statement': None},
            'Expenses:Groceries': {'category': None}, 'Equity:Opening Balances': {}}}
        m.Accounts.model_validate(accounts)
        for name, metadata in (('Expenses:Groceries', {}), ('Income:Salary', {'category': None}),
                               ('Assets:Cash', {'category': None})):
            bad = deepcopy(accounts)
            bad['accounts'][name] = metadata
            with self.subTest(account=name), self.assertRaises(ValidationError):
                m.Accounts.model_validate(bad)
        bad = deepcopy(accounts)
        bad['accounts']['Assets:Cash']['aliases'] = ['Cash']
        with self.assertRaises(ValidationError):
            m.Accounts.model_validate(bad)
        rule = json.loads(dict(examples())['Rule'])
        m.Forecast.model_validate([rule])
        with self.assertRaises(ValidationError):
            m.Forecast.model_validate([{**rule, 'possible-duplicate-of': []}])
        m.RecurringSettings.model_validate({'never-automatic': ['Expenses:Housing']})
        with self.assertRaises(ValidationError):
            m.RecurringSettings.model_validate({'never-automatic': ['Expenses:Housing', 'Expenses:Housing']})


class DocsTests(unittest.TestCase):
    def test_store_md_names_every_ledger_field(self):
        text = STORE_MD.read_text(encoding='utf-8')
        for model in m.LEDGER_DATA:
            for info in model.model_fields.values():
                with self.subTest(field=f'{model.__name__}.{info.alias}'):
                    self.assertIn(f'`{info.alias}`', text)


if __name__ == '__main__':
    unittest.main()
