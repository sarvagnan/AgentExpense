"""Recurring payment identities, calendar schedules and conservative detection.

Pure functions share one contract across HTTP, CLI and checked additions. Amounts
remain exact decimals in their posting commodities; preferences come from the ledger.
"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from calendar import monthrange
from math import gcd
from functools import lru_cache
import json
from pathlib import Path
import re
import secrets

PAYING_KINDS = {'bank', 'card', 'cash', 'wallet'}
ANCILLARY = {'fee', 'fees', 'tax', 'taxes', 'discount', 'discounts', 'rounding'}
GENERIC = {'subscription', 'membership', 'renewal', 'monthly', 'yearly', 'annual', 'payment', 'paid', 'the', 'a', 'by', 'on', 'for'}


def active(rule):
    return rule.get('lifecycle', 'active') == 'active'


def words(text):
    return set(re.findall(r'[^\W\d_]+', text.casefold(), re.UNICODE))


def main_postings(entry):
    return [p for p in entry['postings'] if p['account'].split(':')[0] == 'Expenses'
            and Decimal(p['amount']) > 0 and not words(':'.join(p['account'].split(':')[1:])) & ANCILLARY]


def paying(entry, accounts):
    rows = [p for p in entry['postings'] if accounts.get(p['account'], {}).get('kind') in PAYING_KINDS and Decimal(p['amount']) < 0]
    return rows[0] if len(rows) == 1 else None


def identity(entry, accounts):
    main, payer = main_postings(entry), paying(entry, accounts)
    if not main or not payer:
        return None
    return tuple(sorted({p['account'] for p in main})), payer['account']


def same_payment(entry, rule, accounts):
    one, two = identity(entry, accounts), identity(rule, accounts)
    # A deliberately unassigned rule still has a balanced placeholder posting.
    if not two and main_postings(rule):
        return bool(one and one[0] == tuple(sorted({p['account'] for p in main_postings(rule)})))
    return one is not None and one == two


def amounts(entry):
    result = {}
    for p in main_postings(entry):
        result[p['commodity']] = result.get(p['commodity'], Decimal(0)) + Decimal(p['amount'])
    return result


def within_ten(one, two):
    a, b = amounts(one), amounts(two)
    return bool(a and a.keys() == b.keys() and all(abs(a[c]-b[c]) <= abs(b[c])*Decimal('.1') for c in a))


def payee_match(one, two):
    return bool((words(one['description']) - GENERIC) & (words(two['description']) - GENERIC))


def step_months(rule):
    s = rule['schedule']
    return s.get('interval', 1) * (12 if s['every'] == 'year' else 1)


def occurrence(rule, index=0):
    s = rule['schedule']; start = date.fromisoformat(s['from'])
    month = start.year*12 + start.month-1 + index*step_months(rule)
    year, month0 = divmod(month, 12)
    anchor = s.get('anchor-day', start.day)
    return date(year, month0+1, min(anchor, monthrange(year, month0+1)[1])).isoformat()


def advance(rule, through):
    """First anchored date strictly after the payment; never manufacture arrears."""
    through = date.fromisoformat(through)
    start = date.fromisoformat(rule['schedule']['from'])
    index = max(0, ((through.year-start.year)*12+through.month-start.month)//step_months(rule))
    while occurrence(rule, index) <= through.isoformat(): index += 1
    result = deepcopy(rule)
    result['schedule']['anchor-day'] = rule['schedule'].get('anchor-day', start.day)
    result['date'] = result['schedule']['from'] = occurrence(rule, index)
    return result


def period_for(rule, payment_date):
    """Occurrence in the payment's cadence bucket (also detects earlier paid periods)."""
    start, day = date.fromisoformat(rule['schedule']['from']), date.fromisoformat(payment_date)
    index = ((day.year-start.year)*12 + day.month-start.month)//step_months(rule)
    return occurrence(rule, index)


def periodic_schedules(rule):
    """Keep native, unbounded cadences; split only explicit February year anchors."""
    s = rule['schedule']; start = date.fromisoformat(s['from'])
    anchor = s.get('anchor-day', start.day)
    # Native schedules already clamp from their original day without drifting.
    # In particular, leave legacy schedules byte-identical, even on the 29th–31st.
    if 'anchor-day' not in s or anchor < 29 or s['every'] == 'week':
        return [s]
    step = step_months(rule)
    if step % 12 == 0:
        if start.month != 2:
            return [s]
        year_step = step // 12
        phases = 4 // gcd(year_step, 4)
        return [{'every': 'year', 'interval': year_step * phases,
                 'from': occurrence(rule, phase)} for phase in range(phases)]
    if anchor == start.day or step == 1:
        return [s]
    # A multi-month cadence advanced into a short month needs an unclamped
    # reference date in the same phase. Project backwards, never past a complete
    # calendar cycle; hledger's forecast window supplies the reporting boundary.
    for index in range(1, 12 // gcd(step, 12) + 1):
        reference = occurrence(rule, -index)
        if int(reference[-2:]) == anchor:
            return [{**s, 'from': reference}]
    raise ValueError('No unclamped reference date for recurring schedule')


@lru_cache(maxsize=1)
def calendar_words():
    return json.loads((Path(__file__).resolve().parents[2] / 'schema/recurring-locales.json').read_text())


@lru_cache(maxsize=1)
def currency_codes():
    return set(json.loads((Path(__file__).resolve().parents[2] / 'schema/currencies.json').read_text()))


def text_amounts(text, locale=None):
    # Strip calendar expressions before looking at quantities, in every common order.
    months = r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
    text = re.sub(r'\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b', ' ', text)
    text = re.sub(r'\b\d{1,2}[/]\d{1,2}\b', ' ', text)
    text = re.sub(rf'\b(?:\d{{1,2}}(?:st|nd|rd|th)?[\s-]+{months}(?:[\s-]+\d{{4}})?|{months}[\s-]+\d{{1,2}}(?:st|nd|rd|th)?(?:,?[\s-]+\d{{4}})?)\b', ' ', text, flags=re.I)
    local = calendar_words().get((locale or 'en').split('-')[0], calendar_words()['en'])
    names = '|'.join(re.escape(n) for n in sorted(local['months'], key=len, reverse=True))
    if names:
        text = re.sub(rf'(?<!\w)(?:\d{{1,2}}[.\s-]+(?:{names})\.?|(?:{names})\.?[\s-]+\d{{1,2}})(?:[,\s-]+\d{{4}})?(?!\w)', ' ', text, flags=re.I)
    text = re.sub(r'\d{4}年\d{1,2}月\d{1,2}日', ' ', text)
    if local['decimal'] == ',':
        if local['group']: text = text.replace(local['group'], '')
        return [Decimal(n.replace(',', '.')) for n in re.findall(r'(?<![\w./-])\d+(?:,\d+)?(?![\w./-])', text)]
    return [Decimal(n.replace(',', '')) for n in re.findall(r'(?<![\w./-])\d+(?:,\d{2,3})*(?:\.\d+)?(?![\w./-])', text)]


def match_text(rules, text, accounts, today, currency=None, locale=None):
    tokens = words(text); quantities = text_amounts(text, locale)
    commodities = currency_codes() | {p['commodity'] for rule in rules for p in rule['postings']}
    explicit = {c.upper() for c in re.findall(r'\b[A-Za-z]{3}\b', text)} & commodities
    payment_words = set().union(*(words(alias) for meta in accounts.values() if meta.get('kind') in PAYING_KINDS
                                  for alias in [meta.get('kind',''),meta.get('label',''),*meta.get('aliases',[])]))
    ranked = []
    for rule in rules:
        if not active(rule): continue
        names = words(rule['description']) | set().union(*(words(p['account'].split(':')[-1]) for p in main_postings(rule)))
        name_words = names - GENERIC - payment_words
        if not tokens & name_words: continue
        score = 4*len(tokens & name_words)
        matched_alias = None
        matched_amounts = []
        payer = paying(rule, accounts)
        if payer:
            meta = accounts[payer['account']]
            matched_alias = next((alias for alias in [meta.get('label', ''), *meta.get('aliases', [])] if alias and words(alias) <= tokens), None)
            if matched_alias: score += 3
        compared = amounts(rule)
        if payer: compared.setdefault(payer['commodity'], abs(Decimal(payer['amount'])))
        for code, value in compared.items():
            if explicit and code in explicit or not explicit and code == currency:
                near = [q for q in quantities if abs(q-value) <= abs(value)*Decimal('.1')]
                if near:
                    score += 2
                    matched_amounts.append(dict(amount=str(near[0]),currency=code))
        distance = abs((date.fromisoformat(rule['date'])-date.fromisoformat(today)).days)
        score += max(0, 1-distance/40)
        ranked.append(dict(id=rule['id'], description=rule['description'], score=score, next_due=rule['date'],
                           matched_words=sorted(tokens & name_words), matched_alias=matched_alias, matched_amounts=matched_amounts))
    ranked.sort(key=lambda r:(-r['score'],r['id']))
    return dict(matches=ranked, pick_list=len(ranked)>1 and ranked[0]['score']-ranked[1]['score']<2,
                rule_id=ranked[0]['id'] if ranked and not (len(ranked)>1 and ranked[0]['score']-ranked[1]['score']<2) else None)


def suppressed(entry, rules, accounts):
    removed = [r for r in rules if r.get('lifecycle') == 'removed' and same_payment(entry,r,accounts) and payee_match(entry,r)]
    return max(removed, key=lambda r:r['removal']['date']) if removed else None


def candidate(entry, actual, rules, accounts, settings, today, text=''):
    """Return a new rule or a weaker suggestion; never writes or calls an agent."""
    if entry.get('status') != 'confirmed' or not identity(entry, accounts): return None
    removed_evidence = {identity for rule in rules if rule.get('lifecycle') == 'removed'
                        for identity in rule['removal']['evidence']}
    if entry['id'] in removed_evidence: return None
    main_accounts = {p['account'] for p in main_postings(entry)}
    if any(active(r) and main_accounts & {p['account'] for p in main_postings(r)} for r in rules): return None
    if any(r.get('lifecycle') in ('paused','ended') and same_payment(entry,r,accounts) and payee_match(entry,r) for r in rules): return None
    if any(p['account'] == prefix or p['account'].startswith(prefix+':') for prefix in settings.get('never-automatic',[]) for p in main_postings(entry)): return None
    removal = suppressed(entry,rules,accounts)
    history = [e for e in actual if e['id'] != entry['id'] and e['id'] not in removed_evidence and e['date'] < entry['date'] and e.get('status') == 'confirmed'
               and same_payment(e,entry,accounts) and payee_match(e,entry) and within_ten(entry,e)]
    if removal:
        if entry['date'] <= removal['removal']['date']: return None
        history = [e for e in history if e['date'] > removal['removal']['date'] and e['id'] not in removal['removal']['evidence']]
    qualifying_words = bool(words(text) & {'subscription','membership','renewal'}) and not any(w.startswith('cancel') for w in words(text))
    previous = max(history,key=lambda e:e['date']) if history else None
    if removal and not previous: return None
    if not qualifying_words and not previous: return None
    days = (date.fromisoformat(entry['date'])-date.fromisoformat(previous['date'])).days if previous else 0
    consecutive = previous and ((date.fromisoformat(entry['date']).year-date.fromisoformat(previous['date']).year)*12+int(entry['date'][5:7])-int(previous['date'][5:7]) == 1)
    recent = 0 <= (date.fromisoformat(today)-date.fromisoformat(entry['date'])).days <= 40
    automatic = qualifying_words or recent and consecutive and 25 <= days <= 35
    if removal: automatic = bool(recent and consecutive and 25 <= days <= 35)
    origin = dict(kind='subscription-words' if qualifying_words else 'monthly-history', evidence=[previous['id'],entry['id']] if previous else [entry['id']])
    if qualifying_words: origin['words'] = text
    if removal: origin['came_back'] = True
    rule = dict(id=secrets.token_hex(8), date=entry['date'], status='confirmed', description=entry['description'],
                schedule={'every':'year' if qualifying_words and words(text)&{'yearly','annual'} else 'month','from':entry['date']},
                lifecycle='active',origin=origin,created=today,postings=deepcopy(entry['postings']))
    for p in rule['postings']: p.pop('assert',None)
    return dict(rule=advance(rule,entry['date']), automatic=bool(automatic), reason='monthly-history' if automatic else 'Confirm an irregular or older payment pattern')


def detect(actual, rules, accounts, settings, today):
    made, suggestions = [], []
    for entry in sorted(actual,key=lambda e:e['date'],reverse=True):
        found = candidate(entry,actual,[*rules,*made],accounts,settings,today)
        if found:
            if found['automatic']: made.append(found['rule'])
            elif not any(same_payment(entry,s['rule'],accounts) for s in suggestions): suggestions.append(found)
    return made,suggestions


def stale_rules(actual, rules, accounts):
    result = []
    for rule in rules:
        if not active(rule): continue
        def paid_date(entry):
            link = entry.get('rule', {})
            return max(entry['date'], link['occurrence']) if link.get('id') == rule['id'] else entry['date']
        matches = [e for e in actual if same_payment(e,rule,accounts) and paid_date(e) >= rule['date']]
        if matches: result.append((rule,max(matches,key=paid_date)))
    return result
