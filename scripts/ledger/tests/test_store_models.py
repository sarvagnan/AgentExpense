"""Format 2 store models: generated schemas, setters, extensions, the derived answer and strictness.

Model-only tests: nothing here reads a ledger, except the extension listing tests,
which use their own temporary folders.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from pydantic import ValidationError

APPLICATION = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
import store_models as m  # noqa: E402

STORE_MD = APPLICATION / 'docs/STORE.md'
EXTENSIONS_MD = APPLICATION / 'docs/EXTENSIONS.md'
JORDAN_EXTENSION = APPLICATION / 'fixtures/ledgers/jordan/extensions/learning.json'
# The server suite runs these ledger tests inside application copies without docs/ or fixtures/;
# the checkout's own run (make test-ledger) always has them.
needs_sources = unittest.skipUnless(STORE_MD.exists() and JORDAN_EXTENSION.exists(), 'docs and fixtures are not in this application copy')
AGENT_ENTRY = {'date', 'status', 'description', 'references', 'classification', 'original-amount', 'notes', 'postings'}
AGENT_POSTING = {'account', 'amount', 'commodity', 'cost'}
ENVELOPE = {'status', 'question', 'entry', 'new-accounts', 'doubts', 'attachment-names', 'explanation'}
# Keywords the Node loader's validator (review-dashboard/scripts/ledger-manifest.mjs) understands,
# plus annotations it ignores. A generated keyword outside this set would be silently skipped there.
NODE_KEYWORDS = {'$schema', '$id', '$defs', '$ref', 'title', 'description', 'anyOf', 'type', 'const', 'enum', 'minimum',
                 'maximum', 'pattern', 'minLength', 'format', 'minItems', 'uniqueItems', 'items', 'required', 'properties',
                 'patternProperties', 'additionalProperties', 'propertyNames', 'minProperties'}
FUEL = m.read_definition(APPLICATION / 'extensions/fuel.json')
EVERY_TYPE = {
    'name': 'work', 'description': 'Expenses for client work.',
    'fields': {
        'client': {'type': 'text', 'filled-by': 'agent', 'description': 'The client.', 'journal-tag': 'work-client'},
        'hours': {'type': 'decimal', 'filled-by': 'agent', 'description': 'Hours billed.', 'journal-tag': 'work-hours'},
        'visits': {'type': 'whole-number', 'filled-by': 'person', 'description': 'Visits.', 'journal-tag': 'work-visits'},
        'invoiced': {'type': 'date', 'filled-by': 'person', 'description': 'Invoice date.', 'journal-tag': 'work-invoiced'},
        'billable': {'type': 'yes-no', 'filled-by': 'person', 'description': 'Billed to the client.', 'journal-tag': 'work-billable'},
        'band': {'type': 'choice', 'values': ['standard', 'reduced'], 'filled-by': 'agent', 'description': 'Rate band.', 'journal-tag': 'work-band'},
    }}


def listed(*items):
    """LedgerExtension tuples: (name, ask, definition)."""
    return tuple(m.LedgerExtension.of(name, ask, definition) for name, ask, definition in items)


def posting(account, amount, commodity='INR', **extra):
    return {'account': account, 'amount': amount, 'commodity': commodity, 'cost': None, 'assert': None, 'notes': [], **extra}


def entry(**changes):
    value = {
        'id': '0123456789abcdef', 'date': '2026-03-04', 'status': 'confirmed', 'description': 'Orchid Market groceries',
        'evidence': [], 'statement': None, 'references': None, 'classification': None, 'original-amount': None,
        'reimbursement': None, 'excluded-from-estimate': None, 'extensions': {}, 'rule': None,
        'possible-duplicate-of': [], 'notes': [],
        'postings': [posting('Expenses:Groceries', '120.50'), posting('Assets:Cash', '-120.50')],
    }
    value.update(changes)
    return value


def answer(**entry_changes):
    value = {'status': 'ready', 'question': None, 'entry': {
        'date': '2026-03-04', 'status': 'confirmed', 'description': 'Orchid Market groceries', 'references': None,
        'classification': None, 'original-amount': None, 'notes': [],
        'postings': [{'account': 'Expenses:Groceries', 'amount': '120.50', 'commodity': 'INR', 'cost': None},
                     {'account': 'Assets:Cash', 'amount': '-120.50', 'commodity': 'INR', 'cost': None}]},
        'new-accounts': [], 'doubts': [], 'attachment-names': [], 'explanation': None}
    value['entry'].update(entry_changes)
    return value


def examples(path=STORE_MD):
    """(model name, context, JSON) for every example block in a document."""
    text = path.read_text(encoding='utf-8')
    return re.findall(r'<!-- example: (\w+)(?: (\w+))? -->\n```json\n(.*?)```', text, flags=re.S)


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

    def test_the_format_1_schemas_are_gone(self):
        for name in ('store-2.schema.json', 'answer-2.schema.json', 'answer-2-without-fuel.schema.json'):
            self.assertFalse((APPLICATION / 'schema' / name).exists(), name)
        self.assertEqual(json.loads(m.STORE_SCHEMA.read_text())['x-format'], 2)

    def test_schemas_use_only_keywords_the_node_loader_validates(self):
        composed = m.models_for(listed(('fuel', True, FUEL), ('work', True, EVERY_TYPE))).store_schema()
        for name, schema in (('store', json.loads(m.STORE_SCHEMA.read_text())), ('extension', json.loads(m.EXTENSION_SCHEMA.read_text())),
                             ('composed', composed)):
            with self.subTest(schema=name):
                self.assertEqual({key for key in schema_keywords(schema) if not key.startswith('x-')} - NODE_KEYWORDS, set())

    def test_every_document_has_a_root_definition(self):
        schema = m.store_schema()
        for path, ref in schema['x-documents'].items():
            with self.subTest(path=path):
                self.assertIn(ref.removeprefix('#/$defs/'), schema['$defs'])

    def compositions(self):
        return {'none': (), 'fuel': listed(('fuel', True, FUEL)),
                'fuel and every type': listed(('fuel', True, FUEL), ('work', False, EVERY_TYPE)),
                'every type first': listed(('work', True, EVERY_TYPE), ('fuel', False, FUEL)),
                'learning': listed(('learning', True, json.loads(JORDAN_EXTENSION.read_text())))}

    @needs_sources
    def test_a_composed_schema_equals_what_the_models_produce(self):
        core, types = m.store_schema(), m.extension_types()
        for name, extensions in self.compositions().items():
            with self.subTest(extensions=name):
                composed = m.compose_store_schema(core, [(item.name, json.loads(item.text)) for item in extensions], types)
                self.assertEqual(composed, m.models_for(extensions).store_schema())

    @needs_sources
    def test_node_composes_the_same_schema(self):
        module = (APPLICATION / 'review-dashboard/scripts/ledger-manifest.mjs').as_uri()
        core, types = m.store_schema(), m.extension_types()
        for name, extensions in self.compositions().items():
            with self.subTest(extensions=name):
                given = [{'name': item.name, 'ask': item.ask, 'definition': json.loads(item.text)} for item in extensions]
                code = (f'import {{composeStoreSchema}} from {json.dumps(module)};'
                        'const [core, extensions, types] = JSON.parse(process.argv[1]);'
                        'console.log(JSON.stringify(composeStoreSchema(core, extensions, types)));')
                run = subprocess.run(['node', '--input-type=module', '-e', code, json.dumps([core, given, types])],
                                     capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(run.stdout), m.models_for(extensions).store_schema())

    def test_type_map_is_generated_from_the_python_types(self):
        types = json.loads(m.EXTENSION_TYPES.read_text())
        self.assertEqual(set(types), {'decimal', 'text', 'whole-number', 'date', 'yes-no', 'choice'})
        self.assertEqual(types['yes-no'], {'type': 'boolean'})
        self.assertEqual(types['whole-number'], {'minimum': 0, 'type': 'integer'})


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
        self.assertNotIn('fuel', entry_schema)
        self.assertNotIn('policy-term-years', entry_schema)
        self.assertNotIn('policy-term-years', m.store_schema()['$defs']['Rule']['properties'])

    def test_extension_fields_record_who_fills_them(self):
        group = m.extension_group(m.ExtensionDefinition.model_validate(EVERY_TYPE))
        self.assertEqual(m.setters(group.model_fields['x_client']), ('agent', 'person'))
        self.assertEqual(m.setters(group.model_fields['x_visits']), ('person',))


class ExtensionDefinitionTests(unittest.TestCase):
    @needs_sources
    def test_built_in_fuel_and_the_docs_example_validate(self):
        self.assertEqual(list(FUEL.fields), ['litres', 'trip-km'])
        self.assertEqual([f.journal_tag for f in FUEL.fields.values()], ['fuel-filled-litres', 'trip-km'])
        for name, _, text in examples(EXTENSIONS_MD):
            self.assertEqual(name, 'ExtensionDefinition')
            m.ExtensionDefinition.model_validate_json(text)
        m.ExtensionDefinition.model_validate(EVERY_TYPE)

    def test_invalid_definitions_fail(self):
        field = EVERY_TYPE['fields']['client']
        cases = {
            'name': {**EVERY_TYPE, 'name': 'Work'},
            'field name': {**EVERY_TYPE, 'fields': {'Client': field}},
            'no fields': {**EVERY_TYPE, 'fields': {}},
            'type': {**EVERY_TYPE, 'fields': {'client': {**field, 'type': 'money'}}},
            'filled-by': {**EVERY_TYPE, 'fields': {'client': {**field, 'filled-by': 'statement'}}},
            'choice without values': {**EVERY_TYPE, 'fields': {'band': {**EVERY_TYPE['fields']['band'], 'values': []}}},
            'values on text': {**EVERY_TYPE, 'fields': {'client': {**field, 'values': ['a']}}},
            'duplicate tags': {**EVERY_TYPE, 'fields': {'client': field, 'other': {**field}}},
            'core label': {**EVERY_TYPE, 'fields': {'client': {**field, 'journal-tag': 'order-id'}}},
            # hledger reads "original amount:" as the tag "amount" and "forecast item:" as "item".
            'hledger tag of a core label': {**EVERY_TYPE, 'fields': {'client': {**field, 'journal-tag': 'amount'}}},
            'hledger tag of forecast item': {**EVERY_TYPE, 'fields': {'client': {**field, 'journal-tag': 'item'}}},
            'missing key': {**EVERY_TYPE, 'fields': {'client': {k: v for k, v in field.items() if k != 'journal-tag'}}},
            'two-line description': {**EVERY_TYPE, 'description': 'one\ntwo'},
        }
        for name, value in cases.items():
            with self.subTest(case=name), self.assertRaises(ValidationError):
                m.ExtensionDefinition.model_validate(value)

    def test_listing_checks_files_names_and_clashes_between_extensions(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp')) as folder:
            root = Path(folder)
            (root / 'extensions').mkdir()
            write = lambda name, value: (root / 'extensions' / f'{name}.json').write_text(json.dumps(value))
            write('work', EVERY_TYPE)
            self.assertEqual([item.name for item in m.ledger_extensions(root, {'fuel': {'ask': True}, 'work': {'ask': False}})], ['fuel', 'work'])
            with self.assertRaisesRegex(ValueError, 'extensions/other.json is missing'):
                m.ledger_extensions(root, {'other': {'ask': True}})
            write('other', {**EVERY_TYPE, 'name': 'work'})
            with self.assertRaisesRegex(ValueError, 'match the file name'):
                m.ledger_extensions(root, {'other': {'ask': True}})
            write('other', {**EVERY_TYPE, 'name': 'other', 'fields': {'trip': {**EVERY_TYPE['fields']['client'], 'journal-tag': 'trip-km'}}})
            with self.assertRaisesRegex(ValueError, 'clashes with extension "fuel"'):
                m.ledger_extensions(root, {'fuel': {'ask': True}, 'other': {'ask': True}})
            write('fuel', {**json.loads((APPLICATION / 'extensions/fuel.json').read_text())})
            with self.assertRaisesRegex(ValueError, 'built-in extension'):
                m.ledger_extensions(root, {'fuel': {'ask': True}})


class AnswerTests(unittest.TestCase):
    def test_core_answer_schema_holds_exactly_the_agents_fields(self):
        schema = json.loads(m.ANSWER_SCHEMA.read_text(encoding='utf-8'))
        self.assertEqual(set(schema['properties']), ENVELOPE)
        defs = schema['$defs']
        entry_schema = defs['AnswerEntry']
        self.assertEqual(set(entry_schema['properties']), AGENT_ENTRY)
        self.assertEqual(AGENT_ENTRY | {'extensions'}, {info.alias for info in m.Entry.model_fields.values() if 'agent' in m.setters(info)})
        posting_schema = defs[entry_schema['properties']['postings']['items']['$ref'].removeprefix('#/$defs/')]
        self.assertEqual(set(posting_schema['properties']), AGENT_POSTING)
        cost = next(o['$ref'] for o in posting_schema['properties']['cost']['anyOf'] if '$ref' in o)
        self.assertEqual(set(defs[cost.removeprefix('#/$defs/')]['properties']), {'amount', 'commodity'})

    def test_asked_extensions_give_only_their_agent_fields(self):
        extensions = listed(('fuel', True, FUEL), ('work', True, EVERY_TYPE), ('policy', False, {
            'name': 'policy', 'description': 'Policies.', 'fields': {'term-years': {'type': 'whole-number', 'filled-by': 'person',
                                                                                    'description': 'Years.', 'journal-tag': 'policy-term'}}}))
        schema = m.models_for(extensions).answer_schema()
        self.assertEqual(set(schema['$defs']['AnswerExtensions']['properties']), {'fuel', 'work'})
        self.assertEqual(set(schema['$defs']['AnswerWorkExtension']['properties']), {'client', 'hours', 'band'})
        self.assertEqual(schema['$defs']['AnswerWorkExtension']['properties']['band']['anyOf'][0], {'enum': ['standard', 'reduced'], 'type': 'string'})
        form = m.models_for(extensions).form.model_json_schema()
        self.assertEqual(set(form['$defs']['FormExtensions']['properties']), {'fuel', 'work', 'policy'})

    def test_answer_schemas_suit_strict_structured_output(self):
        for name, schema in (('core', json.loads(m.ANSWER_SCHEMA.read_text())),
                             ('extensions', m.models_for(listed(('fuel', True, FUEL), ('work', True, EVERY_TYPE))).answer_schema())):
            for node in [schema, *schema['$defs'].values()]:
                with self.subTest(schema=name, node=node.get('description', '')[:40]):
                    self.assertIs(node['additionalProperties'], False)
                    self.assertEqual(set(node['required']), set(node['properties']))
            self.assertFalse([key for key in schema_keywords(schema) if key.startswith('x-')])

    def test_the_answer_rejects_server_fields_snake_case_whole_numbers_and_empty_groups(self):
        model = m.answer_model(())
        model.model_validate(answer())
        bad_entries = {'statement': None, 'id': '0123456789abcdef', 'evidence': [], 'rule': None, 'extensions': {},
                       'references': {'reference': None, 'order-id': []}, 'order_id': None, 'original_amount': None}
        for key, value in bad_entries.items():
            with self.subTest(field=key), self.assertRaises(ValidationError):
                model.model_validate(answer(**{key: value}))
        for change in ({'new_accounts': []}, {'attachment_names': []}):
            bad = answer()
            bad.update(change)
            with self.subTest(change=change), self.assertRaises(ValidationError):
                model.model_validate(bad)
        for amount in ('120', '120.5.0', 120.5):
            bad = answer()
            bad['entry']['postings'][0]['amount'] = amount
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                model.model_validate(bad)
        bad = answer()
        bad['entry']['postings'][0]['assert'] = None
        with self.assertRaises(ValidationError):
            model.model_validate(bad)
        bad = answer()
        bad['entry']['postings'][0]['cost'] = {'amount': '10.00', 'commodity': 'USD', 'per': 'total'}
        with self.assertRaises(ValidationError):
            model.model_validate(bad)

    def test_unlisted_unasked_and_person_extension_fields_fail(self):
        models = m.models_for(listed(('fuel', True, FUEL), ('work', False, EVERY_TYPE)))
        models.answer.model_validate(answer(extensions={'fuel': {'litres': '8.00', 'trip-km': None}}))
        for extensions in ({'fuel': None, 'other': None}, {'fuel': None, 'work': None}, {},
                           {'fuel': {'litres': '8.00', 'trip-km': None, 'note': None}}):
            with self.subTest(extensions=extensions), self.assertRaises(ValidationError):
                models.answer.model_validate(answer(extensions=extensions))
        asked = m.models_for(listed(('work', True, EVERY_TYPE)))
        with self.assertRaises(ValidationError):
            asked.answer.model_validate(answer(extensions={'work': {'client': 'Fable', 'hours': None, 'band': None, 'visits': 2}}))
        asked.answer.model_validate(answer(extensions={'work': {'client': 'Fable', 'hours': None, 'band': None}}))
        form = dict(answer(extensions={'fuel': None, 'work': {k: None for k in EVERY_TYPE['fields']} | {'visits': 2}}))
        models.form.model_validate(form)
        models.form.model_validate({**form, 'account-metadata': {'Assets:Bank:New': {'kind': 'bank', 'currency': 'INR'}}})

    def test_derived_answer_follows_a_new_agent_field(self):
        class Grown(m.Entry):
            mood: str | None = m.field('mood', 'A new agent field.', (m.Setter.AGENT,))
        self.assertIn('mood', m.derive(Grown).model_fields)


class StrictnessTests(unittest.TestCase):
    def models(self, context):
        return m.models_for(listed(('fuel', True, FUEL))) if context == 'fuel' else m.models_for(())

    @needs_sources
    def test_docs_examples_validate_and_round_trip(self):
        found = examples()
        self.assertGreaterEqual({name for name, _, _ in found}, {'Entry', 'Rule', 'Answer'})
        for name, context, text in found:
            models = self.models(context)
            model = {'Entry': models.entry, 'Rule': m.Rule, 'Answer': models.answer}[name]
            with self.subTest(example=name, context=context):
                value = json.loads(text)
                self.assertEqual(model.model_validate(value).model_dump(mode='json', by_alias=True), value)

    def test_an_entry_round_trips_in_field_order(self):
        value = entry()
        self.assertEqual(list(m.Entry.model_validate(value).model_dump(mode='json', by_alias=True)), list(value))
        composed = m.models_for(listed(('work', True, EVERY_TYPE), ('fuel', True, FUEL))).entry
        value = entry(extensions={'work': None, 'fuel': {'litres': '8.00', 'trip-km': None}})
        dumped = composed.model_validate(value).model_dump(mode='json', by_alias=True)
        self.assertEqual(list(dumped), list(value))
        self.assertEqual(list(dumped['extensions']), ['work', 'fuel'])

    def test_extension_values_by_type(self):
        entry_model = m.models_for(listed(('work', True, EVERY_TYPE))).entry
        good = {'client': 'Fable Studio', 'hours': '2.5', 'visits': 3, 'invoiced': '2026-03-31', 'billable': False, 'band': 'reduced'}
        entry_model.model_validate(entry(extensions={'work': good}))
        for key, bad in (('client', 'two\nlines'), ('hours', 2.5), ('hours', '1e3'), ('visits', -1), ('visits', '3'), ('visits', 3.0),
                         ('invoiced', '2026-02-30'), ('billable', 'yes'), ('billable', 0), ('band', 'premium')):
            with self.subTest(field=key, value=bad), self.assertRaises(ValidationError):
                entry_model.model_validate(entry(extensions={'work': {**good, key: bad}}))
        with self.assertRaises(ValidationError):
            entry_model.model_validate(entry(extensions={'work': {k: None for k in good}}))
        with self.assertRaises(ValidationError):
            entry_model.model_validate(entry(extensions={}))
        with self.assertRaises(ValidationError):
            m.Entry.model_validate(entry(extensions={'work': None}))

    def test_a_missing_key_is_an_error(self):
        for key in ('notes', 'statement', 'evidence', 'extensions'):
            value = entry()
            del value[key]
            with self.subTest(key=key), self.assertRaises(ValidationError):
                m.Entry.model_validate(value)
        value = entry()
        del value['postings'][0]['cost']
        with self.assertRaises(ValidationError):
            m.Entry.model_validate(value)

    def test_an_extra_key_is_an_error(self):
        for extra in ({'sources': []}, {'fuel': None}, {'policy-term-years': None}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                m.Entry.model_validate(entry(**extra))
        value = entry()
        value['postings'][0]['per'] = 'total'
        with self.assertRaises(ValidationError):
            m.Entry.model_validate(value)
        with self.assertRaises(ValidationError):
            m.Entry.model_validate(entry(references={'reference': 'R-1', 'order-id': [], 'serial': None}))

    def test_amounts_are_exact_decimal_text(self):
        for amount in (120.5, 120, '120', '1.2e2', ' 120.50', '+120.50', '0120.50'):
            value = entry()
            value['postings'][0]['amount'] = amount
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                m.Entry.model_validate(value)

    def test_strict_types_and_values(self):
        cases = {'date': '2026-02-30', 'status': 'Confirmed', 'id': '0123456789ABCDEF', 'description': 'two\nlines',
                 'notes': ['one\nline'], 'postings': []}
        for key, value in cases.items():
            with self.subTest(key=key), self.assertRaises(ValidationError):
                m.Entry.model_validate(entry(**{key: value}))

    def test_groups_and_order_id_lists(self):
        m.Entry.model_validate(entry(references={'reference': None, 'order-id': ['A-1', 'A-2']}))
        for references in ({'reference': None, 'order-id': []}, {'reference': None, 'order-id': None},
                           {'reference': None, 'order-id': ['A-1', 'A-1']}, {'reference': None, 'order-id': 'A-1'}):
            with self.subTest(references=references), self.assertRaises(ValidationError):
                m.Entry.model_validate(entry(references=references))
        with self.assertRaises(ValidationError):
            m.Entry.model_validate(entry(statement={'end': None, 'serial': None, 'page': None, 'source-page': None, 'source-row': None}))
        with self.assertRaisesRegex(ValidationError, 'serial and page'):
            m.Entry.model_validate(entry(statement={'end': '2026-03-28', 'serial': None, 'page': 1, 'source-page': None, 'source-row': None}))

    def test_exact_balance_per_commodity(self):
        ok = [
            [posting('Expenses:Travel', '48.00', 'USD', cost={'amount': '4123.20', 'commodity': 'INR', 'per': 'total'}), posting('Liabilities:Card', '-4123.20')],
            [posting('Expenses:Travel', '2.000', 'USD', cost={'amount': '50.00', 'commodity': 'INR', 'per': 'unit'}), posting('Liabilities:Card', '-100.00')],
            [posting('Assets:Cash', '10.00'), posting('Equity:Opening Balances', '-10.00'), posting('Assets:Dollar', '5.00', 'USD'), posting('Equity:Opening Balances', '-5.00', 'USD')],
            [posting('Assets:Units', '-1.000', 'LOT', cost={'amount': '20.00', 'commodity': 'INR', 'per': 'total'}), posting('Assets:Cash', '20.00')],
        ]
        for postings in ok:
            with self.subTest(postings=postings):
                m.Entry.model_validate(entry(postings=postings))
        bad = [
            [posting('Expenses:Groceries', '120.51'), posting('Assets:Cash', '-120.50')],
            [posting('Expenses:Travel', '3.000', 'USD', cost={'amount': '33.333', 'commodity': 'INR', 'per': 'unit'}), posting('Liabilities:Card', '-100.00')],
            [posting('Expenses:Travel', '48.00', 'USD'), posting('Liabilities:Card', '-4123.20')],
            [posting('Expenses:Travel', '48.00', 'USD', cost={'amount': '48.00', 'commodity': 'USD', 'per': 'total'}), posting('Liabilities:Card', '-48.00', 'USD')],
        ]
        for postings in bad:
            with self.subTest(postings=postings), self.assertRaises(ValidationError):
                m.Entry.model_validate(entry(postings=postings))

    @needs_sources
    def test_rule_checks(self):
        rule = json.loads(next(text for name, _, text in examples() if name == 'Rule'))
        m.Rule.model_validate(rule)
        for change in ({'date': '2026-11-13'}, {'lifecycle': 'removed'}, {'amount': 'fixed'},
                       {'bill': {**rule['bill'], 'occurrence': '2026-10-12'}},
                       {'postings': [{**rule['postings'][0], 'assert': {'amount': '1.00', 'commodity': 'INR'}}, rule['postings'][1]]},
                       {'postings': [rule['postings'][0], {**rule['postings'][1], 'amount': '-1239.00'}]}):
            with self.subTest(change=list(change)), self.assertRaises(ValidationError):
                m.Rule.model_validate({**rule, **change})

    @needs_sources
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
        rule = json.loads(next(text for name, _, text in examples() if name == 'Rule'))
        m.Forecast.model_validate([rule])
        for extra in ({'possible-duplicate-of': []}, {'extensions': {}}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                m.Forecast.model_validate([{**rule, **extra}])
        m.RecurringSettings.model_validate({'never-automatic': ['Expenses:Housing']})
        with self.assertRaises(ValidationError):
            m.RecurringSettings.model_validate({'never-automatic': ['Expenses:Housing', 'Expenses:Housing']})


@needs_sources
class DocsTests(unittest.TestCase):
    def test_store_md_names_every_ledger_field(self):
        text = STORE_MD.read_text(encoding='utf-8')
        for model in m.LEDGER_DATA:
            for info in model.model_fields.values():
                with self.subTest(field=f'{model.__name__}.{info.alias}'):
                    self.assertIn(f'`{info.alias}`', text)

    def test_extensions_md_names_every_definition_key(self):
        text = EXTENSIONS_MD.read_text(encoding='utf-8')
        for model in (m.ExtensionDefinition, m.PlainField, m.ChoiceField):
            for info in model.model_fields.values():
                with self.subTest(field=f'{model.__name__}.{info.alias}'):
                    self.assertIn(f'`{info.alias}`', text)


if __name__ == '__main__':
    unittest.main()
