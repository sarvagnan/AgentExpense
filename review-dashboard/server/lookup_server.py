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
from ledger_manifest import read_manifest
read_manifest(args.ledger)

ALL = [e for f in sorted((args.ledger / 'data/expenses').glob('*.json')) for e in json.loads(f.read_text())]
VISIBLE = [e for e in ALL if (e['date'] < args.cutoff or e['date'] == args.cutoff) and e['id'] not in args.exclude]
USED_ANYWHERE = {p['account'] for e in ALL for p in e['postings']}
USED_VISIBLE = {p['account'] for e in VISIBLE for p in e['postings']}
for rule in json.loads((args.ledger / 'data/forecast.json').read_text()):
    USED_VISIBLE.update(p['account'] for p in rule.get('postings', []))
DECLARED = json.loads((args.ledger / 'data/accounts.json').read_text())['accounts']
ACCOUNTS = [a for a in DECLARED if a in USED_VISIBLE or a not in USED_ANYWHERE]
OLD = args.old_ledger.read_text().splitlines() if args.old_ledger and args.old_ledger.exists() else []


def posting_text(p):
    text = f"{p['account']}  {p.get('amount', '')} {p.get('commodity', '')}".rstrip()
    if p.get('cost'):
        text += f" @@ {p['cost']['amount']} {p['cost']['commodity']}"
    return text


def compact(e):
    out = {'date': e['date'], 'status': e['status'], 'description': e['description'],
           'postings': [posting_text(p) for p in e['postings']]}
    for k in ('order-id', 'classification', 'original-amount', 'fuel-filled-litres', 'trip-km'):
        if k in e:
            out[k] = e[k]
    if e.get('notes'):
        out['notes'] = [n[:200] for n in e['notes'][:6]]
    return out


def tokens(text):
    return {t for t in re.findall(r'[a-z0-9]+', text.lower()) if len(t) > 1}


def haystack(e):
    parts = [e['description'], e.get('classification', ''), e.get('order-id', ''), *e.get('notes', []),
             *(p['account'] for p in e['postings']), *(p.get('amount', '') for p in e['postings'])]
    return tokens(' '.join(parts))


def search_entries(query, limit=8):
    wanted = tokens(query)
    scored = [(len(wanted & haystack(e)), e['date'], e) for e in VISIBLE]
    hits = sorted((s for s in scored if s[0]), key=lambda s: (s[0], s[1]), reverse=True)[:min(int(limit), 20)]
    return [compact(e) for _, _, e in hits]


def entries_near(day, amount=None, days=3):
    centre = date.fromisoformat(day)
    lo, hi = (centre - timedelta(days=int(days))).isoformat(), (centre + timedelta(days=int(days))).isoformat()
    hits = [e for e in VISIBLE if lo <= e['date'] <= hi]
    if amount:
        want = abs(float(amount))
        hits = [e for e in hits if any(abs(abs(float(p.get('amount') or 0)) - want) < 0.005 for p in e['postings'])]
    return [compact(e) for e in hits[-30:]]


def list_accounts(contains='', limit=150):
    needle = contains.lower()
    return [dict(account=a, **{k: DECLARED[a][k] for k in ('kind', 'currency', 'label', 'aliases') if k in DECLARED[a]})
            for a in ACCOUNTS if needle in ' '.join([a, DECLARED[a].get('label', ''), *DECLARED[a].get('aliases', [])]).lower()][:min(int(limit), 400)]


def search_old_ledger(query, limit=10):
    if not args.old_ledger or not args.old_ledger.is_file():
        return [f'Old ledger unavailable at {args.old_ledger}; no historical search performed']
    wanted = tokens(query)
    hits = []
    for i, line in enumerate(OLD):
        if wanted and wanted <= tokens(line) | tokens(OLD[i - 1] if i else ''):
            hits.append('\n'.join(OLD[max(0, i - 2):i + 4]))
            if len(hits) >= min(int(limit), 20):
                break
    return hits or ['no match; try fewer or different words']


TOOLS = {
    'search_entries': (search_entries, 'Search store entries by words: merchant, item, person, account words, order id or amount. Best matches first.',
                       {'query': {'type': 'string'}, 'limit': {'type': 'integer'}}, ['query']),
    'entries_near': (entries_near, 'Store entries within some days of a date, optionally only those with a posting of this amount. Use it to check for duplicates.',
                     {'day': {'type': 'string', 'description': 'YYYY-MM-DD'}, 'amount': {'type': 'string'}, 'days': {'type': 'integer'}}, ['day']),
    'list_accounts': (list_accounts, 'Declared ledger accounts, optionally only those containing some text.',
                      {'contains': {'type': 'string'}, 'limit': {'type': 'integer'}}, []),
    'search_old_ledger': (search_old_ledger, "Search the user's older hand-kept ledger for lines containing all the words; returns each hit with nearby lines.",
                          {'query': {'type': 'string'}, 'limit': {'type': 'integer'}}, ['query']),
}


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
        reply(msg_id, {'tools': [{'name': name, 'description': desc,
                                  'inputSchema': {'type': 'object', 'properties': props, 'required': req},
                                  'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}}
                                 for name, (_, desc, props, req) in TOOLS.items()]})
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
