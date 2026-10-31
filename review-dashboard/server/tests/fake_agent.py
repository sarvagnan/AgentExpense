#!/usr/bin/env python3
"""Offline Codex protocol double. Never imports or starts a model."""
import json
import os
from pathlib import Path
import sys
import subprocess
import time

# A real caller must close stdin; deliberately block otherwise.
assert sys.stdin.read() == ''
config = Path(os.environ['EXPENSES_FAKE_CONFIG'])
settings = json.loads(config.read_text())
# The startup gate uses this same executable, never a real Codex/model. Keep
# diagnostics/probes separate from the existing production-call assertions.
def diagnostic():
    args = sys.argv[1:]
    if args[0] == 'exec':
        schema = json.loads(Path('schema.json').read_text())
        if schema.get('required') != ['account', 'shell_unavailable']:
            return False
        assert 'model_reasoning_effort="low"' in args
        mcp = json.loads(next(a.split('=', 1)[1] for a in args if a.startswith('mcp_servers.ledger.args=')))
        ledger = Path(mcp[mcp.index('--ledger') + 1])
        assert ledger.name == 'fixture-ledger'
        account = json.loads((ledger / 'data/accounts.json').read_text())['accounts'][0]
        if settings.get('probe_fail'):
            print('PRIVATE NETWORK ERROR', file=sys.stderr)
            sys.exit(1)
        mode = settings.get('probe_mode')
        if mode == 'slow':
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            config.with_suffix('.probe').write_text(json.dumps([os.getpid(), child.pid]))
            time.sleep(30)
        answer = {'account': account, 'shell_unavailable': True}
        if mode == 'wrong_account':
            answer['account'] = 'invented'
        if mode == 'shell_available':
            answer['shell_unavailable'] = False
        if mode == 'invalid_schema':
            answer = {'private': 'PRIVATE CONTENT'}
        if mode != 'missing_answer':
            Path('answer.json').write_text('PRIVATE INVALID JSON' if mode == 'invalid_json' else json.dumps(answer))
        item = {'type': 'mcp_tool_call', 'server': 'ledger', 'tool': 'list_accounts',
                'status': 'completed', 'result': {'account': account}}
        if mode == 'nonledger':
            item['server'] = 'other'
        if mode == 'shell':
            item = {'type': 'command_execution'}
        if mode == 'lookup_failed':
            item['status'] = 'failed'
        if mode == 'missing_evidence':
            item['result'] = {'account': 'invented'}
        if mode == 'unknown_item':
            item = {'type': 'future_item'}
        if mode != 'missing_lookup':
            print(json.dumps({'type': 'item.completed', 'item': item}))
        if mode == 'malformed_log':
            print('PRIVATE INVALID JSON')
        if mode in ('network_error', 'rate_limit'):
            print(json.dumps({'type': 'error', 'message': 'PRIVATE NETWORK ERROR'}))
        if mode == 'failed_turn':
            print(json.dumps({'type': 'turn.failed', 'error': {'message': 'PRIVATE ERROR'}}))
            return True
        print(json.dumps({'type': 'turn.completed'}))
    elif args == ['--version']:
        print('codex-cli ' + settings.get('version', '0.166.0'))
    elif args == ['login', 'status']:
        sys.exit(1 if settings.get('signed_out') else 0)
    elif args == ['features', 'list']:
        for name in ('shell_tool unified_exec plugins remote_plugin apps tool_suggest sleep_tool view_image '
                     'image_generation memories hooks computer_use browser_use').split():
            if name != settings.get('missing_feature'):
                print(name + ' stable false')
    elif args[:2] == ['debug', 'prompt-input']:
        nested = ('apply_patch list_mcp_resource_templates list_mcp_resources read_mcp_resource clock__curr_time').split()
        if settings.get('extra_tool'):
            nested.append(settings['extra_tool'])
        description = '\n'.join(f'### `{name}`\ndeclare const tools: {{ {name}(args: object): unknown; }};' for name in nested)
        def message(text, role='developer'):
            return {'type': 'message', 'role': role, 'content': [{'type': 'input_text', 'text': text}]}
        data = [{'type': 'additional_tools', 'tools': [{'type': 'namespace', 'name': 'functions', 'tools': [
            {'type': 'custom', 'name': 'exec', 'description': description}, {'type': 'function', 'name': 'wait'}]}]},
            message('You are Codex, fixture.'),
            *(message(f'<{tag}>fixture</{tag}>') for tag in ('permissions instructions', 'multi_agent_role', 'multi_agent_mode')),
            message('<environment_context>fixture</environment_context>', 'user'), message(args[-1], 'user')]
        if settings.get('extra_section'):
            data.append(message('<future_section>PRIVATE CONTENT</future_section>'))
        print(json.dumps(data))
    elif args == ['login', '--device-auth']:
        # Tests only: the real interactive target is never exercised here.
        Path(os.environ['CODEX_HOME'], 'fixture-login').touch()
    else:
        raise AssertionError('Unknown fake diagnostic')
    return True

if sys.argv[1] != 'exec' or json.loads(Path('schema.json').read_text()).get('required') == ['account', 'shell_unavailable']:
    with config.with_suffix('.checks').open('a') as handle:
        handle.write(json.dumps({'argv': sys.argv[1:], 'home': os.environ['CODEX_HOME'], 'cwd': os.getcwd()}) + '\n')
    diagnostic()
    sys.exit(0)

count_file = config.with_suffix('.count')
count = int(count_file.read_text()) if count_file.exists() else 0
count_file.write_text(str(count + 1))
with config.with_suffix('.calls').open('a') as handle:
    handle.write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'stdin_closed': True,
                             'images': [p.name for p in Path.cwd().glob('a*')],
                             'schema': json.loads(Path('schema.json').read_text())}) + '\n')
step = settings['steps'][min(count, len(settings['steps']) - 1)]
print(json.dumps({'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'server': 'ledger',
      'tool': 'entries_near', 'arguments': {'day': '2026-10-29'}, 'status': 'completed'}}), flush=True)
print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 123, 'output_tokens': 45}}), flush=True)
kind = step.get('kind', 'answer')
if kind == 'exit':
    sys.exit(7)
if kind == 'timeout':
    time.sleep(30)
if kind == 'noanswer':
    sys.exit(0)
if kind == 'invalid':
    Path(sys.argv[sys.argv.index('-o') + 1]).write_text('{"status": "oops"}')
else:
    Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(step['answer']))
