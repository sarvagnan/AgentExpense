"""Read-only ledger lookups for the trial agent, as a dependency-free MCP stdio server.

Production sees all history. Tests may select a historical view: entries dated before
--cutoff, plus same-day entries other than --exclude.
"""
import argparse
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--ledger', type=Path, required=True)
parser.add_argument('--old-ledger', type=Path)
parser.add_argument('--cutoff', default='9999-12-31')
parser.add_argument('--exclude', action='append', default=[])
parser.add_argument('--review-grant', type=Path)
parser.add_argument('--review-probe', action='store_true')
args = parser.parse_args()

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from lookups import LookupStore
review = None
if args.review_grant or args.review_probe:
    from review_lookup import ReviewGrant, ReviewLookup, probe_grant
    if args.review_grant:
        config = json.loads(args.review_grant.read_text())
        review = ReviewLookup(args.ledger, ReviewGrant(**config['grant']), config['visuals'],
                              Path(config['receipts']) if config.get('receipts') else None)
    else:
        review = ReviewLookup(args.ledger, probe_grant(args.ledger))
store = LookupStore(args.ledger, args.old_ledger, args.cutoff, args.exclude, review=review)

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
        try:
            result = store.call(msg['params']['name'], msg['params'].get('arguments', {}))
            reply(msg_id, {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}], 'isError': False})
        except Exception as exc:  # report tool errors to the agent
            reply(msg_id, {'content': [{'type': 'text', 'text': 'invalid_lookup' if review else f'error: {exc}'}], 'isError': True})
    else:
        reply(msg_id, error={'code': -32601, 'message': f'unknown method {method}'})
