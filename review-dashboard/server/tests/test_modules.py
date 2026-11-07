import os
"""Module settings shape prompts, schemas and read-only MCP capabilities at runtime."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from support import Fixture, ready, SERVER
from agent import prompt_for, schema_for, lookup_names, command_for
from agent_check import inspect_prompt, NESTED_TOOLS
from agent_gate import fingerprint
from transactions import to_store, ProposalError
from store import read_json, dump
from ledger_manifest import deprecated_old_ledger


class ModuleServerTests(Fixture):
    def configure(self, *, fuel=None, **modules):
        manifest = read_json(self.ledger / 'ledger.json')
        manifest['modules'] = modules
        if fuel is not None:
            manifest['extensions'] = {'fuel': {'ask': True}} if fuel else {}
            rows = read_json(self.ledger / 'data/expenses/2026.json')
            dump(self.ledger / 'ledger.json', manifest)
            dump(self.ledger / 'data/expenses/2026.json', [{**row, 'extensions': {'fuel': None} if fuel else {}} for row in rows])
        else:
            dump(self.ledger / 'ledger.json', manifest)
        return replace(self.settings, old_ledger=None)

    def prompt(self, settings):
        draft = dict(files=[], text='lunch 100 cash', saved_at='2026-10-31T12:00:00+00:00', questions=[])
        with tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp')) as folder:
            return prompt_for(settings, draft, self.base, Path(folder))[0]

    def tools(self, settings):
        command = command_for(settings, '', [])
        args = json.loads(next(a.split('=', 1)[1] for a in command if a.startswith('mcp_servers.ledger.args=')))
        result = subprocess.run([sys.executable, '-S', *args], input=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list'})+'\n', text=True, capture_output=True, check=True)
        return {t['name'] for t in json.loads(result.stdout)['result']['tools']}

    def test_fuel_on_off_schema_prompt_and_proposal(self):
        settings = self.configure(fuel=True)
        schema = schema_for(settings)
        self.assertIn('extensions', schema['$defs']['AnswerEntry']['required'])
        self.assertEqual(set(schema['$defs']['AnswerFuelExtension']['properties']), {'litres', 'trip-km'})
        self.assertIn('## Fuel log', self.prompt(settings))
        self.assertIn('entry.extensions.fuel.litres', self.prompt(settings))
        answer = ready(new=False); answer['attachment-names'] = []
        answer['entry']['extensions'] = {'fuel': {'litres': '8.00', 'trip-km': None}}
        entry, _, _ = to_store(answer, self.ledger, 0)
        self.assertEqual(entry['extensions'], {'fuel': {'litres': '8.00', 'trip-km': None}})
        settings = self.configure(fuel=False, investments=False, weeklyReview=False)
        schema = schema_for(settings)
        self.assertNotIn('extensions', schema['$defs']['AnswerEntry']['properties'])
        self.assertNotIn('## Fuel log', self.prompt(settings))
        self.assertIn('# Personal rules for this ledger', self.prompt(settings))
        self.assertIn('## Personal rules', self.prompt(settings))
        self.assertNotIn('entry.extensions', self.prompt(settings))
        from answers import AnswerError, check
        with self.assertRaisesRegex(AnswerError, 'extensions'):
            check(answer, self.ledger, form=False)
        del answer['entry']['extensions']
        check(answer, self.ledger, form=False)
        self.assertEqual(to_store(answer, self.ledger, 0)[0]['extensions'], {})

    def test_history_readable_null_and_unreadable_gate_prompt_tools_and_cache(self):
        deprecated_old_ledger.cache_clear()
        self.addCleanup(deprecated_old_ledger.cache_clear)
        with patch.dict('os.environ', {'EXPENSES_OLD_LEDGER':''}):
            settings = self.configure(history={'oldLedger':None})
            names = lookup_names(settings)
            self.assertEqual(names, {'search_entries','entries_near','list_accounts'})
            self.assertEqual(self.tools(settings), names)
            self.assertNotIn('search_old_ledger', self.prompt(settings))
            before = fingerprint(settings, 'test')
            old = self.ledger / 'old.ledger';old.write_text('; fictional archive\n')
            settings = self.configure(history={'oldLedger':'old.ledger'})
            names = lookup_names(settings)
            self.assertIn('search_old_ledger', names)
            self.assertEqual(self.tools(settings), names)
            self.assertIn('search_old_ledger', self.prompt(settings))
            self.assertNotEqual(fingerprint(settings, 'test'), before)
            old.unlink()
            self.assertNotIn('search_old_ledger', self.tools(settings))
            self.assertNotIn('search_old_ledger', self.prompt(settings))

    def test_investments_and_weekly_add_no_agent_fields_or_tools(self):
        settings = self.configure(investments=False, weeklyReview=False)
        before = schema_for(settings), self.tools(settings), self.prompt(settings)
        settings = self.configure(investments={'prices':'mutual-fund-nav'}, weeklyReview=True)
        self.assertEqual((schema_for(settings), self.tools(settings), self.prompt(settings)), before)

    def test_diagnostic_allowlist_refuses_unoffered_history_tool(self):
        description = '\n'.join(f'### `{name}`\ndeclare const tools: {{ {name}(args: object): unknown; }};' for name in [*NESTED_TOOLS, 'mcp__ledger__search_old_ledger'])
        rendered = [{'type':'additional_tools','tools':[{'type':'namespace','name':'functions','tools':[{'type':'custom','name':'exec','description':description},{'type':'function','name':'wait'}]}]}]
        self.assertNotIn('extra-ledger-tools', inspect_prompt(rendered, lookups={'search_old_ledger'})[-1])
        self.assertIn('extra-ledger-tools', inspect_prompt(rendered, lookups={'list_accounts'})[-1])
