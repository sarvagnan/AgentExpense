"""Isolation checks require neither a model nor version-control fixtures."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent import AgentFailure, SCHEMA, schema_for, codex_environment, command_for, run
from agent_check import (DiagnosticUnavailable, NESTED_TOOLS, OFFLINE_PROMPT, PolicyFailure,
                         PROBE_SCHEMA, check_live, check_offline,
                         configured_features, debug_command, fixture, home_problems,
                         inspect_events, inspect_features, inspect_prompt, main)
from settings import Settings


def message(text, role='user'):
    return {'type': 'message', 'role': role, 'content': [{'type': 'input_text', 'text': text}]}


def rendered_prompt():
    description = '\n'.join(f'### `{name}`\ndeclare const tools: {{ {name}(args: object): unknown; }};'
                            for name in sorted(NESTED_TOOLS))
    return [
        {'type': 'additional_tools', 'tools': [
            {'type': 'namespace', 'name': 'functions', 'tools': [
                {'type': 'custom', 'name': 'exec', 'description': description},
                {'type': 'function', 'name': 'wait'}]},
            {'type': 'namespace', 'name': 'collaboration', 'tools': [
                {'type': 'function', 'name': 'spawn_agent'}]}]},
        message('You are Codex, fixture base prompt.', 'developer'),
        message('<permissions instructions>fixture</permissions instructions>', 'developer'),
        message('<multi_agent_role>fixture</multi_agent_role>', 'developer'),
        message('<multi_agent_mode>fixture</multi_agent_mode>', 'developer'),
        message('<environment_context>fixture</environment_context>'),
        message(OFFLINE_PROMPT),
    ]


def feature_output(command):
    return '\n'.join(name + ' stable false' for name in sorted(configured_features(command)))


def events(*items):
    return '\n'.join(json.dumps(event) for event in [
        {'type': 'thread.started', 'thread_id': 'fixture'}, {'type': 'turn.started'},
        *({'type': 'item.completed', 'item': item} for item in items),
        {'type': 'turn.completed', 'usage': {}},
    ])


def lookup(account='Assets:AgentCheck:fixture'):
    return {'type': 'mcp_tool_call', 'server': 'ledger', 'tool': 'list_accounts',
            'arguments': {'contains': 'AgentCheck'}, 'status': 'completed', 'error': None,
            'result': {'content': [{'type': 'text', 'text': json.dumps([account])}]}}


class AgentIsolationTests(unittest.TestCase):
    def test_command_isolation_and_ledger_approval(self):
        folder = tempfile.TemporaryDirectory(dir='/tmp', prefix='expense-isolation-command-')
        self.addCleanup(folder.cleanup)
        ledger, old, _ = fixture(Path(folder.name))
        settings = Settings(ledger=ledger, old_ledger=old)
        args = command_for(settings, 'unchanged prompt', [Path('/fixture/image.png')])
        self.assertEqual(args[:6], [settings.codex, 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only'])
        self.assertIn('--ignore-user-config', args)
        self.assertIn('--ignore-rules', args)
        self.assertFalse(any(arg == '--enable' for arg in args))
        self.assertEqual(configured_features(args), {
            'shell_tool', 'unified_exec', 'plugins', 'remote_plugin', 'apps', 'tool_suggest',
            'sleep_tool', 'view_image', 'image_generation', 'memories', 'hooks', 'computer_use', 'browser_use'})
        configs = {args[i + 1] for i, arg in enumerate(args) if arg == '-c'}
        self.assertEqual(configs, {
            'skills.include_instructions=false', 'include_collaboration_mode_instructions=false',
            'tools.experimental_request_user_input.enabled=false', 'agents.max_threads=1',
            'web_search="disabled"', 'model_reasoning_effort="xhigh"',
            'mcp_servers.ledger.command="python3"', 'mcp_servers.ledger.startup_timeout_sec=20',
            'mcp_servers.ledger.default_tools_approval_mode="approve"',
            next(a for a in args if a.startswith('mcp_servers.ledger.args='))})
        mcp = json.loads(next(a.split('=', 1)[1] for a in args if a.startswith('mcp_servers.ledger.args=')))
        self.assertEqual(Path(mcp[0]).name, 'lookup_server.py')
        self.assertEqual(mcp[1:], ['--ledger', str(ledger), '--old-ledger', str(old.resolve())])
        self.assertEqual(args[args.index('-m') + 1], settings.model)
        self.assertEqual(args[-4:], ['-i', 'image.png', '--', 'unchanged prompt'])
        self.assertEqual(args[args.index('--output-schema') + 1], 'schema.json')
        self.assertEqual(args[args.index('-o') + 1], 'answer.json')
        self.assertNotIn('CODEX_HOME', ' '.join(args))

    def test_service_home_is_only_in_child_environment(self):
        with patch.dict(os.environ, {'CODEX_HOME': '/parent-home', 'EXPENSES_CODEX_HOME': 'service-home'}):
            settings = Settings.from_env()
            self.assertEqual(settings.codex_home, Path('service-home').resolve())
            child = codex_environment(settings)
            self.assertEqual(child['CODEX_HOME'], str(settings.codex_home))
            self.assertEqual(os.environ['CODEX_HOME'], '/parent-home')
            self.assertEqual(codex_environment(Settings())['CODEX_HOME'], str(Settings().codex_home))

    def test_renderer_adapter_omits_exec_only_flags_and_keeps_settings(self):
        folder = tempfile.TemporaryDirectory(dir='/tmp', prefix='expense-render-command-')
        self.addCleanup(folder.cleanup)
        ledger, _, _ = fixture(Path(folder.name))
        command = command_for(Settings(ledger=ledger), OFFLINE_PROMPT, [])
        debug = debug_command(command)
        self.assertNotIn('--ephemeral', debug)
        self.assertNotIn('--ignore-user-config', debug)
        self.assertNotIn('--ignore-rules', debug)
        self.assertIn('sandbox_mode="read-only"', debug)
        self.assertIn('model="gpt-6.1-terra"', debug)
        self.assertIn('mcp_servers.ledger.default_tools_approval_mode="approve"', debug)
        self.assertEqual(configured_features(command), configured_features(debug))
        self.assertEqual(debug[-2:], ['--', OFFLINE_PROMPT])

    def test_home_inspection_uses_names_and_allows_only_system_skills(self):
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='expense-home-check-') as folder:
            home = Path(folder).resolve()
            # Contents are irrelevant; the checker never opens authentication.
            system = home / 'skills/.system'
            system.mkdir(parents=True)
            (system / 'SKILL.md').write_text('bundled fixture')
            self.assertFalse(home_problems(home))
            for name in ('config.toml', 'AGENTS.md', 'AGENTS.override.md', 'rules', 'plugins'):
                path = home / name
                path.touch()
                self.assertEqual(home_problems(home), {name})
                path.unlink()
            custom = home / 'skills/custom'
            custom.mkdir()
            self.assertEqual(home_problems(home), {'skills/custom'})
            custom.rmdir()
            (system / 'SKILL.md').unlink()
            system.rmdir()
            system.symlink_to(home, target_is_directory=True)
            self.assertEqual(home_problems(home), {'skills/.system'})
            self.assertEqual(home_problems(home / 'missing'), {'missing-home'})

    def test_feature_check_rejects_unknown_enable_and_disable_names(self):
        command = command_for(Settings(), '--disable prompt-is-not-a-flag', [])
        self.assertFalse(inspect_features(feature_output(command), command))
        self.assertFalse(inspect_features(feature_output(command) + '\nguardianv2.thread_context removed false', command))
        self.assertEqual(inspect_features('shell_tool stable false', command),
                         configured_features(command) - {'shell_tool'})
        command[2:2] = ['--enable', 'new_feature']
        self.assertEqual(inspect_features(feature_output(command).replace('new_feature stable false\n', ''), command),
                         {'new_feature'})
        for output in ('', 'unexpected JSON', 'shell_tool stable unknown'):
            with self.assertRaises(ValueError):
                inspect_features(output, command)

    def test_prompt_allowlists_sections_and_both_tool_levels(self):
        sections, tools, nested, problems = inspect_prompt(rendered_prompt())
        self.assertFalse(problems)
        self.assertEqual(sections, dict(base_prompt=1, permissions=1, multi_agent_role=1,
                                        multi_agent_mode=1, environment_context=1, prompt=1))
        self.assertEqual(tools, {'functions.exec', 'functions.wait', 'collaboration.spawn_agent'})
        self.assertEqual(nested, NESTED_TOOLS)
        for role, text in [
                ('user', '# AGENTS.md instructions for /host\n<INSTRUCTIONS>fixture</INSTRUCTIONS>'),
                ('user', 'unwrapped host instructions'),
                ('developer', '## Skills\nfixture'),
                ('developer', '<recommended_plugins>fixture</recommended_plugins>'),
                ('developer', '<memory_summary>fixture</memory_summary>'),
                ('developer', '<future_instructions>fixture</future_instructions>'),
                ('system', 'unknown message')]:
            with self.subTest(role=role, text=text):
                self.assertTrue(inspect_prompt(rendered_prompt() + [message(text, role)])[3])
        self.assertTrue(inspect_prompt(rendered_prompt()[1:])[3])
        self.assertTrue(inspect_prompt(rendered_prompt() + [message(OFFLINE_PROMPT)])[3])
        for namespace, name in [('functions', 'exec_command'), ('web', 'search')]:
            data = rendered_prompt()
            data[0]['tools'].append({'type': 'namespace', 'name': namespace,
                                     'tools': [{'type': 'function', 'name': name}]})
            self.assertIn('extra-top-level-tools', inspect_prompt(data)[3])
        for name, expected in [('mcp__other__lookup', 'extra-nested-tools'),
                               ('exec_command', 'extra-nested-tools'),
                               ('view_image', 'extra-nested-tools'), ('mcp__ledger__list_accounts', None)]:
            data = rendered_prompt()
            data[0]['tools'][0]['tools'][0]['description'] += f'\n### `{name}`\ndeclare const tools: {{ {name}(args: object): unknown; }};'
            problems = inspect_prompt(data)[3]
            self.assertEqual(problems, {expected} if expected else set())
        data = rendered_prompt()
        data[0]['tools'][0]['tools'][0]['description'] += '\n### `future_tool`'
        self.assertIn('unknown-nested-tool-format', inspect_prompt(data)[3])
        data[0]['tools'][0]['tools'][0]['description'] = ''
        self.assertIn('missing-nested-tools', inspect_prompt(data)[3])
        for data in ([], {'input': rendered_prompt()}):
            with self.assertRaises(ValueError):
                inspect_prompt(data)

    def test_offline_checks_every_allowlist_even_when_login_fails(self):
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='expense-offline-check-') as folder:
            home = Path(folder).resolve()
            ledger, _, _ = fixture(home)
            settings = Settings(ledger=ledger, codex_home=home, model='fixture-model')
            command = command_for(settings, OFFLINE_PROMPT, [])
            def invoke_result(args, *rest, **kwargs):
                if args[1:3] == ['login', 'status']:
                    raise ValueError('not logged in')
                if args[1:3] == ['features', 'list']:
                    return feature_output(command)
                return json.dumps(rendered_prompt())
            output = io.StringIO()
            with redirect_stdout(output), patch('agent_check.require_codex'), patch('agent_check.invoke', side_effect=invoke_result) as invoke:
                with self.assertRaisesRegex(ValueError, '^login$'):
                    check_offline(settings, home)
            self.assertEqual(invoke.call_count, 3)
            self.assertIn('PASS feature names:', output.getvalue())
            self.assertIn('PASS render', output.getvalue())
            self.assertTrue(all('exec' not in call.args[0] for call in invoke.call_args_list))
            self.assertTrue(all(call.kwargs['environment']['CODEX_HOME'] == str(home) for call in invoke.call_args_list))
            self.assertIn('model="fixture-model"', invoke.call_args.args[0])
            with redirect_stdout(io.StringIO()), patch('agent_check.require_codex'), patch('agent_check.invoke', side_effect=[
                    '', feature_output(command), json.dumps(rendered_prompt())]):
                check_offline(settings, home)
            (home / 'config.toml').touch()
            with patch('agent_check.require_codex'), patch('agent_check.invoke') as invoke:
                with self.assertRaisesRegex(ValueError, 'Home names: config.toml'):
                    check_offline(settings, home)
            invoke.assert_not_called()

    def test_defaults_and_missing_binary(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as folder, patch.dict(os.environ, {}, clear=True):
            with patch('pathlib.Path.home', return_value=Path(folder)):
                settings = Settings.from_env()
            self.assertEqual(settings.state_dir, Path(folder).resolve() / '.local/state/agentexpense')
            self.assertEqual(settings.codex_home, settings.state_dir / 'codex-home')
            self.assertEqual(settings.codex, str(settings.application / 'review-dashboard/node_modules/.bin/codex'))
            settings = replace(settings, codex=str(Path(folder) / 'missing'))
            with patch('agent_check.Settings.from_env', return_value=settings), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(), 1)
            self.assertIn('Run: make dashboard', output.getvalue())
            self.assertFalse(settings.codex_home.exists())

    def test_state_directory_precedence_and_dedicated_agent_home(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as folder:
            home = Path(folder).resolve()
            cases = [
                ({}, home / '.local/state/agentexpense'),
                ({'XDG_STATE_HOME': str(home / 'xdg')}, home / 'xdg/agentexpense'),
                ({'EXPENSES_STATE_DIR': str(home / 'override')}, home / 'override'),
                ({'XDG_STATE_HOME': str(home / 'xdg'),
                  'EXPENSES_STATE_DIR': str(home / 'override')}, home / 'override'),
                ({'XDG_STATE_HOME': '', 'EXPENSES_STATE_DIR': ''}, home / '.local/state/agentexpense'),
                ({'XDG_STATE_HOME': str(home / 'xdg'), 'EXPENSES_STATE_DIR': ''}, home / 'xdg/agentexpense'),
            ]
            for environment, expected in cases:
                with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                        patch('pathlib.Path.home', return_value=home):
                    for settings in (Settings(), Settings.from_env()):
                        self.assertEqual(settings.state_dir, expected)
                        self.assertEqual(settings.agent_home, expected / 'codex-home')
                        self.assertFalse(settings.state_dir.exists())

    def test_events_reject_all_nonledger_tools_failures_and_missing_lookup(self):
        self.assertEqual(len(inspect_events(events(lookup()))), 1)
        for bad in (dict(lookup(), server='other'), dict(lookup(), tool='shell'),
                    {'type': 'command_execution'},
                    {'type': 'web_search'}, {'type': 'image_generation'},
                    {'type': 'file_change'}):
            with self.subTest(item=bad):
                with self.assertRaises(PolicyFailure):
                    inspect_events(events(lookup(), bad))
        for bad in (dict(lookup(), status='failed'), dict(lookup(), error={'message': 'timed out'})):
            with self.subTest(item=bad):
                with self.assertRaisesRegex(DiagnosticUnavailable, 'Probe ledger lookup failed'):
                    inspect_events(events(bad))
        for log in (events(), events(dict(lookup(), tool='entries_near'))):
            with self.assertRaisesRegex(DiagnosticUnavailable, 'Probe did not complete one turn with an account lookup'):
                inspect_events(log)
        for log in (events({'type': 'unknown_tool'}), events(lookup()) + '\n{"type":"error"}',
                    events(lookup()) + '\n{"type":"unknown_tool_event"}',
                    json.dumps({'type': 'item.completed', 'item': lookup()}),
                    '[]', '{"type":"item.completed","item":[1]}', 'not JSON'):
            with self.assertRaises(ValueError) as raised:
                inspect_events(log)
            self.assertNotIsInstance(raised.exception, (PolicyFailure, DiagnosticUnavailable))

    def test_fixture_lookup_server_and_mocked_live_probe(self):
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='expense-agent-check-test-') as folder:
            work = Path(folder).resolve()
            ledger, old, account = fixture(work)
            settings = replace(Settings(), ledger=ledger, old_ledger=old, codex_home=work / 'service-home')
            command = command_for(settings, 'fixture', [])
            lookup_args = json.loads(next(a.split('=', 1)[1] for a in command if a.startswith('mcp_servers.ledger.args=')))
            requests = [dict(jsonrpc='2.0', id=1, method='tools/list'),
                        dict(jsonrpc='2.0', id=2, method='tools/call',
                             params={'name': 'list_accounts', 'arguments': {'contains': 'AgentCheck'}})]
            result = subprocess.run([sys.executable, '-S', *lookup_args],
                                    input='\n'.join(map(json.dumps, requests)),
                                    capture_output=True, text=True, timeout=5, check=True)
            listed, response = map(json.loads, result.stdout.splitlines())
            self.assertEqual(len(listed['result']['tools']), 4)
            self.assertEqual(json.loads(response['result']['content'][0]['text']), [dict(account=account,kind='cash',currency='INR')])
            (work / 'answer.json').write_text(json.dumps({'account': account, 'shell_unavailable': True}))
            with redirect_stdout(io.StringIO()), patch('agent_check.invoke', return_value=events(lookup(account))) as invoke:
                check_live(settings, work, account)
            self.assertEqual(invoke.call_count, 1)
            self.assertEqual(invoke.call_args.kwargs['environment']['CODEX_HOME'], str(settings.codex_home))
            args = invoke.call_args.args[0]
            self.assertIn('model_reasoning_effort="low"', args)
            self.assertIn('--ignore-user-config', args)
            self.assertEqual(json.loads((work / 'schema.json').read_text()), PROBE_SCHEMA)
            self.assertEqual(settings.effort, 'xhigh')
            for answer in ({'account': account, 'shell_unavailable': False},
                           {'account': 'invented', 'shell_unavailable': True}):
                (work / 'answer.json').write_text(json.dumps(answer))
                with patch('agent_check.invoke', return_value=events(lookup(account))):
                    with self.assertRaisesRegex(DiagnosticUnavailable, 'Probe account or shell-unavailable assertion failed'):
                        check_live(settings, work, account)
            (work / 'answer.json').write_text(json.dumps({'account': account, 'shell_unavailable': True}))
            with patch('agent_check.invoke', return_value=events(lookup('invented'))):
                with self.assertRaisesRegex(DiagnosticUnavailable, 'Probe event log did not evidence the fixture account'):
                    check_live(settings, work, account)

    def test_production_runner_with_offline_protocol_double(self):
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='expense-agent-run-test-') as folder:
            work = Path(folder).resolve()
            ledger, old, _ = fixture(work)
            (ledger / 'AGENTS.md').write_text('## Before adding\nFixture policy.\n## Rebuild and verify\n')
            fake = work / 'fake-codex'
            source = Path(__file__).with_name('fake_agent.py').read_text().split('\n', 1)[1]
            home = work / 'service-home'
            fake.write_text(f'#!{sys.executable}\nimport os\nassert os.environ[\"CODEX_HOME\"] == {str(home)!r}\n' + source)
            fake.chmod(0o755)
            answer = dict(status='not_expense', question=None, entry=None, new_accounts=[],
                          doubts=[], attachment_names=[], explanation='Fixture diagnostic')
            config = work / 'fake.json'
            config.write_text(json.dumps({'steps': [{'answer': answer}]}))
            settings = replace(Settings(), ledger=ledger, old_ledger=old, codex=str(fake), codex_home=home, state_dir=work / 'state')
            draft = dict(files=[], saved_at='2026-10-31T10:00:00+05:30', text='fixture', questions=[])
            with patch.dict(os.environ, {'EXPENSES_FAKE_CONFIG': str(config), 'CODEX_HOME': '/parent-home'}):
                actual, record = run(settings, draft, work)
                self.assertEqual(os.environ['CODEX_HOME'], '/parent-home')
            self.assertEqual(actual, answer)
            self.assertEqual(record['outcome'], 'not_expense')
            call = json.loads(config.with_suffix('.calls').read_text())
            self.assertTrue(call['stdin_closed'])
            self.assertEqual(call['schema'], schema_for(settings))
            self.assertIn('--ignore-user-config', call['argv'])
            self.assertIn('mcp_servers.ledger.default_tools_approval_mode="approve"', call['argv'])
            self.assertFalse(Path(call['cwd']).exists())

    def test_unlogged_service_home_error_does_not_fall_back(self):
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='expense-login-error-') as folder:
            work = Path(folder).resolve()
            home = work / 'service-home'
            fake = work / 'fake-codex'
            source = Path(__file__).with_name('fake_agent.py').read_text().split('\n', 1)[1]
            fake.write_text(f'#!{sys.executable}\n' + source)
            config = work / 'fake.json'
            config.write_text(json.dumps({'signed_out': True, 'steps': []}))
            fake.chmod(0o755)
            settings = Settings(codex=str(fake), codex_home=home, state_dir=work / 'state')
            with patch('agent.prompt_for', return_value=('fixture', [])), \
                    patch.dict(os.environ, {'CODEX_HOME': '/parent-home', 'EXPENSES_FAKE_CONFIG': str(config)}):
                for draft in ({}, {'statement_task': True}):
                    with self.assertRaisesRegex(AgentFailure, "The agent isn't signed in. Run: make agent-login"):
                        run(settings, draft, work)
                    self.assertEqual(os.environ['CODEX_HOME'], '/parent-home')
            self.assertTrue(home.is_dir())
            self.assertFalse((work / 'state/agent-check.json').exists())


if __name__ == '__main__':
    unittest.main()
