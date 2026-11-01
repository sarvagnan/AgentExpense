"""Instance-scoped read-only lookups shared by MCP and direct API adapters."""
import json
import re
from datetime import date, timedelta
from ledger_manifest import read_manifest, module_settings, old_ledger_path


class LookupStore:
    def __init__(self, ledger, old=None, cutoff='9999-12-31', exclude=()):
        self.ledger = ledger
        self.manifest = read_manifest(ledger)
        self.old_path = old_ledger_path(ledger, fallback=old)
        self.ALL = [e for f in sorted((self.ledger / 'data/expenses').glob('*.json')) for e in json.loads(f.read_text())]
        self.VISIBLE = [e for e in self.ALL if (e['date'] < cutoff or e['date'] == cutoff) and e['id'] not in exclude]
        self.USED_ANYWHERE = {p['account'] for e in self.ALL for p in e['postings']}
        self.USED_VISIBLE = {p['account'] for e in self.VISIBLE for p in e['postings']}
        for rule in json.loads((self.ledger / 'data/forecast.json').read_text()):
            self.USED_VISIBLE.update(p['account'] for p in rule.get('postings', []))
        self.DECLARED = json.loads((self.ledger / 'data/accounts.json').read_text())['accounts']
        self.ACCOUNTS = [a for a in self.DECLARED if a in self.USED_VISIBLE or a not in self.USED_ANYWHERE]
        try:
            self.OLD = self.old_path.read_text().splitlines() if self.old_path else []
        except OSError:
            self.old_path, self.OLD = None, []
        self.tools = {
            'search_entries': (self.search_entries, 'Search store entries by words: merchant, item, person, account words, order id or amount. Best matches first.',
                               {'query': {'type': 'string'}, 'limit': {'type': 'integer'}}, ['query']),
            'entries_near': (self.entries_near, 'Store entries within some days of a date, optionally only those with a posting of this amount. Use it to check for duplicates.',
                             {'day': {'type': 'string', 'description': 'YYYY-MM-DD'}, 'amount': {'type': 'string'}, 'days': {'type': 'integer'}}, ['day']),
            'list_accounts': (self.list_accounts, 'Declared ledger accounts, optionally only those containing some text.',
                              {'contains': {'type': 'string'}, 'limit': {'type': 'integer'}}, []),
            'search_old_ledger': (self.search_old_ledger, "Search the user's older hand-kept ledger for lines containing all the words; returns each hit with nearby lines.",
                                  {'query': {'type': 'string'}, 'limit': {'type': 'integer'}}, ['query']),
        }

        if not self.old_path:
            del self.tools['search_old_ledger']

    def posting_text(self, p):
        text = f"{p['account']}  {p.get('amount', '')} {p.get('commodity', '')}".rstrip()
        if p.get('cost'):
            text += f" @@ {p['cost']['amount']} {p['cost']['commodity']}"
        return text


    def compact(self, e):
        out = {'date': e['date'], 'status': e['status'], 'description': e['description'],
               'postings': [self.posting_text(p) for p in e['postings']]}
        for k in ('order-id', 'classification', 'original-amount', 'fuel-filled-litres', 'trip-km'):
            if k in e and (k not in ('fuel-filled-litres', 'trip-km') or module_settings(self.manifest)['fuel']):
                out[k] = e[k]
        if e.get('notes'):
            out['notes'] = [n[:200] for n in e['notes'][:6]]
        return out


    def tokens(self, text):
        return {t for t in re.findall(r'[a-z0-9]+', text.lower()) if len(t) > 1}


    def haystack(self, e):
        parts = [e['description'], e.get('classification', ''), e.get('order-id', ''), *e.get('notes', []),
                 *(p['account'] for p in e['postings']), *(p.get('amount', '') for p in e['postings'])]
        return self.tokens(' '.join(parts))


    def search_entries(self, query, limit=8):
        wanted = self.tokens(query)
        scored = [(len(wanted & self.haystack(e)), e['date'], e) for e in self.VISIBLE]
        hits = sorted((s for s in scored if s[0]), key=lambda s: (s[0], s[1]), reverse=True)[:min(int(limit), 20)]
        return [self.compact(e) for _, _, e in hits]


    def entries_near(self, day, amount=None, days=3):
        centre = date.fromisoformat(day)
        lo, hi = (centre - timedelta(days=int(days))).isoformat(), (centre + timedelta(days=int(days))).isoformat()
        hits = [e for e in self.VISIBLE if lo <= e['date'] <= hi]
        if amount:
            want = abs(float(amount))
            hits = [e for e in hits if any(abs(abs(float(p.get('amount') or 0)) - want) < 0.005 for p in e['postings'])]
        return [self.compact(e) for e in hits[-30:]]


    def list_accounts(self, contains='', limit=150):
        needle = contains.lower()
        return [dict(account=a, **{k: self.DECLARED[a][k] for k in ('kind', 'currency', 'label', 'aliases') if k in self.DECLARED[a]})
                for a in self.ACCOUNTS if needle in ' '.join([a, self.DECLARED[a].get('label', ''), *self.DECLARED[a].get('aliases', [])]).lower()][:min(int(limit), 400)]


    def search_old_ledger(self, query, limit=10):
        if not self.old_path:
            return ['Old ledger unavailable; no historical search performed']
        wanted = self.tokens(query)
        hits = []
        for i, line in enumerate(self.OLD):
            if wanted and wanted <= self.tokens(line) | self.tokens(self.OLD[i - 1] if i else ''):
                hits.append('\n'.join(self.OLD[max(0, i - 2):i + 4]))
                if len(hits) >= min(int(limit), 20):
                    break
        return hits or ['no match; try fewer or different words']

    def definitions(self):
        return [{'name': name, 'description': desc,
                 'inputSchema': {'type': 'object', 'properties': props, 'required': req, 'additionalProperties': False},
                 'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}}
                for name, (_, desc, props, req) in self.tools.items()]

    def call(self, name, arguments):
        from jsonschema import validate
        definition = next((d for d in self.definitions() if d['name'] == name), None)
        if definition is None:
            raise ValueError('Tool outside lookup allow-list')
        validate(arguments, definition['inputSchema'])
        read_manifest(self.ledger)
        return self.tools[name][0](**arguments)
