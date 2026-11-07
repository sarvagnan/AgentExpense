"""Read ledger format 1: the 1 → 2 migration and older audit snapshots.

Nothing else reads format 1. ``convert`` turns a whole format 1 ledger into
format 2 documents in memory and counts what it did and what it can't do;
``rule``, ``bill`` and ``entry`` convert one snapshot from an older audit line
(they leave a format 2 snapshot unchanged).

Counts never include names, amounts, dates, descriptions, accounts or paths.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import store_models as m

# Format 1 fields with their journal labels, in journal order.
LABELS = {
    'serial-number': 'serial-number', 'source-row': 'source-row', 'source-page': 'source-page',
    'statement-page': 'statement-page', 'statement-end': 'statement-end', 'reference': 'reference',
    'order-id': 'order-id', 'classification': 'classification', 'original-amount': 'original amount',
    'fuel-filled-litres': 'fuel-filled-litres', 'trip-km': 'trip-km', 'reimbursement-group': 'reimbursement-group',
    'reimbursement-label': 'reimbursement-label', 'variable-estimate': 'variable-estimate', 'policy-term': 'policy-term',
}
ENTRY_ONLY = ('serial-number', 'source-row', 'source-page', 'statement-page', 'statement-end', 'fuel-filled-litres',
              'trip-km', 'policy-term', 'rule', 'possible-duplicate-of')
POLICY = {
    'name': 'policy',
    'description': 'Insurance and other policies paid for several years at once.',
    'fields': {'term-years': {'type': 'whole-number', 'filled-by': 'person',
                              'description': 'The number of years the policy payment covers.', 'journal-tag': 'policy-term'}},
}
# What a problem count says; any problem stops the migration with the ledger unchanged.
PROBLEMS = {
    'repeated-label': 'entries or rules repeating a label other than order-id in their notes',
    'repeated-order-id-value': 'entries repeating the same order-id value',
    'unbalanced': 'entries or rules that do not balance exactly in each commodity',
    'unit-cost-ambiguous': 'unbalanced entries with several unit costs in one commodity',
    'rule-entry-field': 'rules carrying a statement field, a fuel reading, a policy term, a rule link or a duplicate link',
    'rule-assertion': 'rule postings with an assertion',
    'label-without-group': 'entries with reimbursement-label but no reimbursement-group',
    'fuel-without-module': 'entries with fuel readings in a ledger without the fuel module',
    'invalid': 'documents the format 2 models cannot hold',
}


def group(**values: Any) -> dict | None:
    return values if any(value not in (None, []) for value in values.values()) else None


def posting(value: dict) -> dict:
    cost = value.get('cost')
    return {'account': value['account'], 'amount': value['amount'], 'commodity': value['commodity'],
            'cost': {'amount': cost['amount'], 'commodity': cost['commodity'], 'per': cost['per']} if cost else None,
            'assert': deepcopy(value.get('assert')), 'notes': list(value.get('notes', []))}


def _repeats(value: dict, counts: Counter) -> tuple[list[str], list[str]]:
    """The order-id values in order, and the notes left after removing repeated order-ids."""
    notes = list(value.get('notes', []))
    for name, label in LABELS.items():
        if name in value and name != 'order-id' and any(note.startswith(label + ': ') for note in notes):
            counts['problem:repeated-label'] += 1
    orders = [value['order-id']] if 'order-id' in value else []
    if orders:
        repeats = [note for note in notes if note.startswith('order-id: ')]
        if repeats:
            counts['order-id repeats made into lists'] += 1
            extra = [note[len('order-id: '):] for note in repeats]
            if len(set(orders + extra)) != len(orders + extra) or any(not item for item in extra):
                counts['problem:repeated-order-id-value'] += 1
            orders += extra
            notes = [note for note in notes if not note.startswith('order-id: ')]
    return orders, notes


def _costs(value: dict, postings: list[dict], counts: Counter) -> None:
    """Unit costs that don't balance exactly become the total that does."""
    units = [p for p in postings if p['cost'] and p['cost']['per'] == 'unit']
    unbalanced = m.balance(postings)
    if not units:
        if unbalanced:
            counts['problem:unbalanced'] += 1
        return
    if not unbalanced:
        counts['unit costs kept (they balance exactly)'] += len(units)
        return
    for commodity in list(unbalanced):
        candidates = [p for p in units if p['cost']['commodity'] == commodity]
        if not candidates:
            counts['problem:unbalanced'] += 1
            return
        if len(candidates) > 1:
            if not _share_totals(postings, candidates, commodity):
                counts['problem:unit-cost-ambiguous'] += 1
                return
            counts['unit costs converted to total costs'] += len(candidates)
            continue
        target = candidates[0]
        others = m.balance([p for p in postings if p is not target]).get(commodity, Decimal(0))
        sign = 1 if Decimal(target['amount']) > 0 else -1
        total = -others * sign
        if total <= 0:
            counts['problem:unbalanced'] += 1
            return
        target['cost'] = {'amount': format(total, 'f'), 'commodity': commodity, 'per': 'total'}
        counts['unit costs converted to total costs'] += 1
    if m.balance(postings):
        counts['problem:unbalanced'] += 1


def _share_totals(postings: list[dict], candidates: list[dict], commodity: str) -> bool:
    """Give several unit-cost postings rounded total costs that sum exactly to the rest.

    Each total is its exact value rounded to the decimal places of the entry's other
    postings in ``commodity``; the leftover steps go to the largest remainders, so no
    total moves by more than one step. All candidates must share a sign, the leftover
    must be whole steps within one per posting, and every total positive; otherwise
    nothing changes and the caller stops the migration.
    """
    others = [p for p in postings if not any(p is c for c in candidates)]
    places = [-Decimal(p['amount'] if p['cost'] is None else p['cost']['amount']).as_tuple().exponent
              for p in others if (p['cost'] or {}).get('commodity', p['commodity']) == commodity]
    signs = {Decimal(p['amount']) > 0 for p in candidates}
    if not places or len(signs) != 1:
        return False
    step = Decimal(1).scaleb(-max(places))
    rest = m.balance(others).get(commodity, Decimal(0))
    if (rest > 0) == signs.pop():
        return False
    needed = abs(rest)
    exact = [abs(Decimal(p['cost']['amount']) * Decimal(p['amount'])) for p in candidates]
    floors = [value.quantize(step, rounding='ROUND_FLOOR') for value in exact]
    left = (needed - sum(floors)) / step
    if left != left.to_integral_value() or not 0 <= left <= len(candidates):
        return False
    order = sorted(range(len(candidates)), key=lambda i: (-(exact[i] - floors[i]), i))
    totals = list(floors)
    for i in order[:int(left)]:
        totals[i] += step
    if any(total <= 0 for total in totals):
        return False
    for posting, total in zip(candidates, totals):
        posting['cost'] = {'amount': format(total, 'f'), 'commodity': commodity, 'per': 'total'}
    return True


# Format 1 allowed "" in these optional fields; format 2 holds null instead.
EMPTY_CLEARED = ('classification', 'reference', 'order-id', 'serial-number', 'reimbursement-group', 'reimbursement-label')


def _cleared(value: dict, counts: Counter) -> dict:
    """The entry or rule with empty optional strings removed, and empty paths dropped from sources.

    Each cleared value is counted as "empty values cleared". An empty reimbursement group
    with a label left is not guessed: it stops the migration as a label without a group.
    """
    value = dict(value)
    for key in EMPTY_CLEARED:
        if value.get(key) == '':
            del value[key]
            counts['empty values cleared'] += 1
        elif isinstance(value.get(key), list) and '' in value[key]:
            counts['empty values cleared'] += value[key].count('')
            value[key] = [item for item in value[key] if item != ''] or None
            if value[key] is None:
                del value[key]
    if '' in value.get('sources', []):
        counts['empty values cleared'] += value['sources'].count('')
        value['sources'] = [source for source in value['sources'] if source != '']
    return value


def _describing(value: dict, counts: Counter) -> dict:
    """The descriptive fields shared by entries and rules."""
    orders, notes = _repeats(value, counts)
    if 'reimbursement-label' in value and 'reimbursement-group' not in value:
        counts['problem:label-without-group'] += 1
    postings = [posting(p) for p in value['postings']]
    _costs(value, postings, counts)
    return {
        'evidence': list(value.get('sources', [])),
        'references': group(reference=value.get('reference'), **{'order-id': orders}),
        'classification': value.get('classification'),
        'original-amount': deepcopy(value.get('original-amount')),
        'reimbursement': {'group': value['reimbursement-group'], 'label': value.get('reimbursement-label')}
        if 'reimbursement-group' in value else None,
        'excluded-from-estimate': {'reason': value['variable-estimate']['reason']} if 'variable-estimate' in value else None,
        'notes': notes,
        'postings': postings,
    }


def entry(value: dict, extensions: tuple[str, ...] = ('fuel', 'policy'), counts: Counter | None = None) -> dict:
    """One format 1 entry as format 2, with groups for the listed ``extensions``."""
    if 'extensions' in value:
        return deepcopy(value)
    counts = Counter() if counts is None else counts
    value = _cleared(value, counts)
    shared = _describing(value, counts)
    groups = {
        'fuel': group(litres=value.get('fuel-filled-litres'), **{'trip-km': value.get('trip-km')}),
        'policy': group(**{'term-years': value['policy-term']['years']}) if 'policy-term' in value else None,
    }
    for name, held in groups.items():
        if held is not None:
            counts[f'entries with {name} values'] += 1
    return {
        'id': value['id'], 'date': value['date'], 'status': value['status'], 'description': value['description'],
        'evidence': shared['evidence'],
        'statement': group(end=value.get('statement-end'), serial=value.get('serial-number'), page=value.get('statement-page'),
                           **{'source-page': value.get('source-page'), 'source-row': value.get('source-row')}),
        'references': shared['references'], 'classification': shared['classification'],
        'original-amount': shared['original-amount'], 'reimbursement': shared['reimbursement'],
        'excluded-from-estimate': shared['excluded-from-estimate'],
        'extensions': {name: groups.get(name) for name in extensions},
        'rule': deepcopy(value.get('rule')),
        'possible-duplicate-of': deepcopy(value.get('possible-duplicate-of', [])),
        'notes': shared['notes'], 'postings': shared['postings'],
    }


def bill(value: dict | None) -> dict | None:
    """A rule's bill, from either format."""
    if value is None or 'evidence' in value:
        return deepcopy(value)
    return {'amount': value['amount'], 'received': value['received'], 'pay-by': value.get('pay_by'),
            'occurrence': value['occurrence'], 'evidence': list(value['sources'])}


def rule(value: dict | None, counts: Counter | None = None) -> dict | None:
    """One format 1 rule as format 2 (a format 2 rule is returned unchanged)."""
    if value is None or 'excluded-from-estimate' in value:
        return deepcopy(value)
    counts = Counter() if counts is None else counts
    value = _cleared(value, counts)
    if any(key in value for key in ENTRY_ONLY):
        counts['problem:rule-entry-field'] += 1
    if any(p.get('assert') for p in value['postings']):
        counts['problem:rule-assertion'] += 1
    shared = _describing(value, counts)
    schedule, origin, removal = value['schedule'], value.get('origin'), value.get('removal')
    if origin and ' ' in origin['kind']:
        # Older ledgers spelled origin kinds with spaces ("subscription words").
        origin = {**origin, 'kind': origin['kind'].replace(' ', '-')}
        counts['rule origin kinds respelled with hyphens'] += 1
    return {
        'id': value['id'], 'date': value['date'], 'status': value['status'], 'description': value['description'],
        'schedule': {'every': schedule['every'], 'from': schedule['from'], 'interval': schedule.get('interval', 1),
                     'anchor-day': schedule.get('anchor-day')},
        'amount': value.get('amount', 'fixed'), 'lifecycle': value.get('lifecycle', 'active'),
        'origin': {'kind': origin['kind'], 'words': origin.get('words'), 'entry-ids': list(origin.get('evidence', [])),
                   'came-back': bool(origin.get('came_back', False))} if origin else None,
        'created': value.get('created'),
        'removal': {'date': removal['date'], 'entry-ids': list(removal['evidence'])} if removal else None,
        'bill': bill(value.get('bill')),
        **{key: shared[key] for key in ('evidence', 'references', 'classification', 'original-amount', 'reimbursement',
                                        'excluded-from-estimate', 'notes', 'postings')},
    }


def statement(value: dict) -> dict:
    if value.get('kind') == 'bank':
        return {'kind': 'bank', **{key: value[key] for key in ('id', 'account', 'start', 'end', 'source')},
                'source-balance-style': value.get('source-balance-style'),
                **{key: value[key] for key in ('opening-balance', 'closing-balance', 'debits', 'credits', 'rows')},
                'members': [{**{key: row[key] for key in ('date', 'serial', 'description', 'amount', 'direction', 'citation')},
                             'reference': row.get('reference'), 'balance': row.get('balance'), 'entry-id': row.get('entry-id'),
                             'disposition': row['disposition'], 'review-candidates': list(row.get('review-candidates', []))}
                            for row in value['members']],
                'assertion-id': value.get('assertion-id'), 'reader': value['reader']}
    return {'kind': 'card', **{key: value[key] for key in ('id', 'account', 'start', 'end', 'source', 'previous-balance', 'purchases')},
            'cash-advances': value.get('cash-advances'), **{key: value[key] for key in ('credits', 'total-due', 'rows')}}


def accounts(value: dict) -> dict:
    result = {}
    for name, meta in value['accounts'].items():
        root = name.split(':')[0]
        # Other keys are kept, so the format 2 models refuse what format 1 refused.
        if root in ('Income', 'Equity'):
            result[name] = deepcopy(meta)
        elif root == 'Expenses':
            result[name] = {'category': None, **deepcopy(meta)}
        else:
            reader = meta.get('statement')
            result[name] = {'label': None, 'aliases': [], **deepcopy(meta),
                            'statement': {'reader': reader.get('reader'), 'identifier': reader.get('identifier'),
                                          'options': deepcopy(reader.get('options')), **{k: v for k, v in reader.items() if k not in ('reader', 'identifier', 'options')}}
                            if isinstance(reader, dict) else reader}
    return {'commodities': list(value['commodities']), 'accounts': result}


def trash(items: list, extensions: tuple[str, ...], counts: Counter) -> list:
    return [{'id': item['id'], 'entry': entry(item['entry'], extensions, counts), 'removed-at': item['removed_at'],
             'added-at': item.get('added_at'), 'reason': item['reason'], 'index': item['index'],
             'files': deepcopy(item['files']), 'kept': deepcopy(item['kept']),
             'rule-changes': [{'id': change['id'], 'before': rule(change.get('before'), counts),
                               'after': rule(change.get('after'), counts)} for change in item['rule_changes']],
             'duplicate-links': deepcopy(item['duplicate_links'])} for item in items]


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def listed_extensions(root: Path, manifest: dict, counts: Counter | None = None) -> tuple[dict, dict]:
    """``ledger.json`` extensions for a format 1 ledger, and the definitions the migration writes into it.

    Fuel is listed only when the owner had the fuel module on. Entries (or Trash items)
    holding fuel readings while it was off are counted as a problem: the migration never
    lists an extension the owner didn't turn on. The policy extension is made only when
    an entry has a policy term.
    """
    raw = [e for path in sorted((root / 'data/expenses').glob('*.json')) for e in read(path)]
    trashed = [item['entry'] for item in (read(root / 'data/trash.json') if (root / 'data/trash.json').exists() else [])]
    fuel_on = bool(manifest.get('modules', {}).get('fuel', False))
    readings = sum(1 for e in raw + trashed if 'fuel-filled-litres' in e or 'trip-km' in e)
    listed, definitions = {}, {}
    if fuel_on:
        listed['fuel'] = {'ask': True}
    elif readings and counts is not None:
        counts['problem:fuel-without-module'] += readings
    if any('policy-term' in e for e in raw + trashed):
        listed['policy'] = {'ask': False}
        definitions['policy'] = POLICY
    return listed, definitions


def convert(root: Path, manifest: dict) -> tuple[dict, dict, dict, Counter]:
    """A format 1 ledger as format 2: (manifest, {relative path: document}, {name: definition}, counts)."""
    counts = Counter()
    listed, definitions = listed_extensions(root, manifest, counts)
    names = tuple(listed)
    documents = {}
    for path in sorted((root / 'data/expenses').glob('*.json')):
        items = read(path)
        counts['entries'] += len(items)
        documents[f'data/expenses/{path.name}'] = [entry(item, names, counts) for item in items]
    rules = read(root / 'data/forecast.json')
    counts['rules'] += len(rules)
    documents['data/forecast.json'] = [rule(item, counts) for item in rules]
    statements = read(root / 'data/card-statements.json') if (root / 'data/card-statements.json').exists() else []
    counts['statements'] += len(statements)
    documents['data/card-statements.json'] = [statement(item) for item in statements]
    documents['data/accounts.json'] = accounts(read(root / 'data/accounts.json'))
    counts['accounts'] += len(documents['data/accounts.json']['accounts'])
    if (root / 'data/recurring-settings.json').exists():
        documents['data/recurring-settings.json'] = read(root / 'data/recurring-settings.json')
    if (root / 'data/trash.json').exists():
        items = read(root / 'data/trash.json')
        counts['trash items'] += len(items)
        inner = Counter()
        documents['data/trash.json'] = trash(items, names, inner)
        counts.update({key: value for key, value in inner.items() if key.startswith('problem:') or key == 'empty values cleared'})
    modules = {key: value for key, value in manifest.get('modules', {}).items() if key != 'fuel'}
    upgraded = {**{key: value for key, value in manifest.items() if key not in ('modules',)}, 'format': 2,
                'modules': modules, 'extensions': listed}
    if 'modules' not in manifest:
        upgraded.pop('modules')
    for name in listed:
        counts[f'extension listed: {name}'] += 1
    return upgraded, documents, definitions, counts


# The format 1 layout, for the 0 → 1 migration's own writes -------------------------------

_ACCOUNT_KEYS = ('kind', 'currency', 'label', 'aliases', 'statement')


def _ordered(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {'commodities', 'accounts'}:
            return {'commodities': value['commodities'],
                    'accounts': {name: {key: _ordered(meta[key]) for key in sorted(meta, key=lambda key: (
                        _ACCOUNT_KEYS.index(key) if key in _ACCOUNT_KEYS else 999, key))}
                        for name, meta in value['accounts'].items()}}
        return {key: _ordered(value[key]) for key in value}
    if isinstance(value, list):
        return [_ordered(item) for item in value]
    return value


def text(value: Any) -> str:
    """Format 1's canonical layout: two-space indentation; each posting on one line."""
    def render(item: Any, level: int, compact: bool = False) -> str:
        if compact or not isinstance(item, (dict, list)) or not item:
            return json.dumps(item, ensure_ascii=False, separators=(', ', ': '), allow_nan=False)
        pad, inner = '  ' * level, '  ' * (level + 1)
        if isinstance(item, list):
            return '[\n' + ',\n'.join(inner + render(x, level + 1) for x in item) + '\n' + pad + ']'
        parts = []
        for key, val in item.items():
            if key == 'postings':
                body = '[\n' + ',\n'.join('  ' * (level + 2) + render(x, 0, True) for x in val) + '\n' + inner + ']'
            else:
                body = render(val, level + 1)
            parts.append(inner + json.dumps(key) + ': ' + body)
        return '{\n' + ',\n'.join(parts) + '\n' + pad + '}'
    return render(_ordered(value), 0) + '\n'


def dump(path: Path, value: Any) -> None:
    """Write a format 1 document (the 0 → 1 migration writes ledger.json and data/accounts.json)."""
    from ledger_manifest import ordered_manifest
    from store import write_text
    if isinstance(value, dict) and {'format', 'id', 'name', 'defaultCurrency'} <= value.keys():
        value = ordered_manifest(value)
    write_text(Path(path), text(value))


# The format 1 journal of actual entries, the baseline the 1 → 2 migration is compared with --

def _field_text(name: str, value: Any) -> str:
    if name == 'original-amount':
        return f"{value['amount']} {value['commodity']}"
    if name == 'variable-estimate':
        return f"excluded, {value['reason']}"
    if name == 'policy-term':
        years = value['years']
        return f"{years} {'year' if years == 1 else 'years'}"
    return str(value)


def _amount(value: dict) -> str:
    symbol = value['commodity']
    return f"{value['amount']} {symbol if symbol.isalpha() else json.dumps(symbol, ensure_ascii=False)}"


def render_entry(value: dict) -> list[str]:
    """One format 1 actual entry as format 1 rendered it."""
    lines = [f"{value['date']} {'*' if value['status'] == 'confirmed' else '!'} {value['description']}",
             f"    ; id: {value['id']}"]
    lines += [f'    ; source: {source}' for source in value.get('sources', [])]
    lines += [f'    ; {label}: {_field_text(name, value[name])}' for name, label in LABELS.items() if name in value]
    lines += [f'    ; {note}' for note in value.get('notes', [])]
    width = max(len(p['account']) for p in value['postings']) + 4
    for p in value['postings']:
        line = f"    {p['account']:<{width}}{_amount(p)}"
        if 'cost' in p:
            line += (' @@ ' if p['cost']['per'] == 'total' else ' @ ') + _amount(p['cost'])
        if 'assert' in p:
            line += ' = ' + _amount(p['assert'])
        lines.append(line)
        lines += [f'    ; {note}' for note in p.get('notes', [])]
    return [*lines, '']
