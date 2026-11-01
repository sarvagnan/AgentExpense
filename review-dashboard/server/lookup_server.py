"""Read-only ledger lookups for the trial agent, as a dependency-free MCP stdio server.

Production sees all history. Tests may select a historical view: entries dated before
--cutoff, plus same-day entries other than --exclude.
"""
import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--ledger', type=Path, required=True)
parser.add_argument('--old-ledger', type=Path)
parser.add_argument('--cutoff', default='9999-12-31')
parser.add_argument('--exclude', action='append', default=[])
args = parser.parse_args()

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from ledger_manifest import read_manifest, module_settings, old_ledger_path
from lookups import LookupStore
store = LookupStore(args.ledger, args.old_ledger, args.cutoff, args.exclude)
TOOLS = store.tools

def reply(msg_id, result=None, error=None):
    body = {'jsonrpc': '2.0', 'id': msg_id}
    body.update({'error': error} if error else {'result': result})
    sys.stdout.write(json.dumps(body) + '\n')
    sys.stdout.flush()


for line in sys.stdin:
    if not line.strip():
        continue
    msg = json.loads(line)
    method, msg_id = msg.get('method'), msg.get('id')
    if msg_id is None:
        continue
    if method == 'initialize':
        reply(msg_id, {'protocolVersion': msg['params'].get('protocolVersion', '2025-06-18'),
                       'capabilities': {'tools': {}}, 'serverInfo': {'name': 'ledger-lookups', 'version': '0.1'}})
    elif method == 'ping':
        reply(msg_id, {})
    elif method == 'tools/list':
        reply(msg_id, {'tools': store.definitions()})
    elif method == 'tools/call':
        name, arguments = msg['params']['name'], msg['params'].get('arguments') or {}
        try:
            read_manifest(args.ledger)
            result = TOOLS[name][0](**arguments)
            reply(msg_id, {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}], 'isError': False})
        except Exception as exc:  # report tool errors to the agent
            reply(msg_id, {'content': [{'type': 'text', 'text': f'error: {exc}'}], 'isError': True})
    else:
        reply(msg_id, error={'code': -32601, 'message': f'unknown method {method}'})
