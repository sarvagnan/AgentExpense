"""Server-owned review queue and recoverable, audited human decisions.

The audit log is the durable decision/history/idempotency store. Duplicate links
are canonical one-way edges on the smaller actual ID; settled edges aren't inferred
again. Import/readers can use link_pair for additional uncertainty reasons.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
import re
import secrets
import subprocess

from transactions import Transaction, clean, jj, refresh, reject_invisible
from currency_settings import SettingsRecords
from store import load, entries, dump, validate, store_paths
from audit import records, event, append, audit_path
from ledger_lock import ledger_lock, LOCK_FD_ENV

REASONS = {'amount-date-gap': 'Same amount, but the dates are more than three days apart.',
           'dissimilar-name': 'Same amount, but the names differ.',
           'several-candidates': 'Several entries could match this expense.',
           'overlapping-statements': 'Overlapping statements may contain the same row.',
           'imported-after-start': 'Later imported history may repeat this expense.'}


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def link_pair(actual, one, two, reason):
    if reason not in REASONS or one == two or one not in actual or two not in actual:
        raise ValueError('Invalid possible duplicate pair')
    owner, target = sorted((one, two))
    links = {link['id']: link for link in actual[owner].get('possible-duplicate-of', [])}
    links[target] = dict(id=target, reason=reason)
    actual[owner]['possible-duplicate-of'] = sorted(links.values(), key=lambda link: link['id'])


def unlink_pair(actual, one, two):
    owner, target = sorted((one, two))
    actual[owner]['possible-duplicate-of'] = [link for link in actual[owner]['possible-duplicate-of'] if link['id'] != target]


def decision_state(lines):
    decisions, touched = {}, {}
    for line in lines:
        if line['kind'] != 'review-action':
            continue
        for change in line['changes']:
            touched[change['entry_id']] = line['id']
            if change.get('other_id'):
                touched[change['other_id']] = line['id']
            if change.get('decision'):
                decisions[change['entry_id']] = change['decision']
    return decisions, touched


def version(entry, touched):
    return digest([entry, touched.get(entry['id'])])


def summary(entry, accounts, touched):
    payment = next((p for p in entry['postings'] if accounts.get(p['account'], {}).get('kind') in ('bank', 'card', 'cash', 'wallet') and Decimal(p['amount']) < 0), None)
    payment = payment or next((p for p in entry['postings'] if accounts.get(p['account'], {}).get('currency')), entry['postings'][-1])
    review = next((p for p in entry['postings'] if p['account'].split(':')[0] in ('Expenses', 'Income')), None)
    currency = payment['commodity']
    amount = abs(Decimal(payment['amount']))
    totals = {}
    for posting in entry['postings']:
        if accounts.get(posting['account'], {}).get('kind') in ('bank', 'card', 'cash', 'wallet'):
            totals[posting['commodity']] = totals.get(posting['commodity'], Decimal(0)) + Decimal(posting['amount'])
    amounts = {code: str(abs(total)) for code, total in totals.items() if total}
    if currency in amounts:
        amount = Decimal(amounts[currency])
    kind = 'Expense' if review and review['account'].startswith('Expenses') and Decimal(review['amount']) >= 0 else 'Refund' if review and review['account'].startswith('Expenses') else 'Income' if review else 'Transfer'
    return dict(amounts=amounts or {currency:str(amount)}, signedAmounts={code:str(-total) for code,total in totals.items() if total}, id=entry['id'], version=version(entry, touched), date=entry['date'], originalDate=entry['date'],
                description=entry['description'], amount=str(amount), signedAmount=str(-totals[currency] if totals.get(currency) else -Decimal(payment['amount'])), currency=currency,
                account=payment['account'], accountLabel=accounts.get(payment['account'], {}).get('label') or payment['account'].split(':')[-1],
                currentAccount=review['account'] if review else '', kind=kind,
                sources=entry['evidence'], source=next(iter(entry['evidence']), 'Derived ledger entry'),
                sourceReference=str(next((value for value in ((entry['statement'] or {}).get(key) for key in ('serial', 'source-row', 'page', 'source-page')) if value is not None), '')),
                statement=bool((entry['statement'] or {}).get('end')), notes=entry.get('notes', []), postings=entry['postings'], status=entry['status'])


def statement_members(documents, root):
    return {m['entry-id'] for s in documents[root/'data/card-statements.json'] for m in s.get('members',[]) if m.get('entry-id')}


def snapshot(root, *, clock=None):
    documents = load(root)
    actual = entries(documents)
    lines = records(root)
    decisions, touched = decision_state(lines)
    accounts = documents[root / 'data/accounts.json']['accounts']
    public = {e['id']: summary(e, accounts, touched) for e in actual}
    bank_members = statement_members(documents, root)
    for identity in bank_members:
        public[identity]['statement'] = True
    at = clock() if clock else datetime.now(timezone.utc)
    cutoff = at - timedelta(days=7)
    reviewed = {c['entry_id'] for line in lines if line['kind'] == 'review-action' for c in line['changes'] if c.get('decision', {}).get('status') == 'resolved'}
    verdicts = {line['line_id']: line['verdict'] for line in lines if line['kind'] == 'review'}
    accepted_audits = {identity for identity, verdict in verdicts.items() if verdict == 'ok'}
    doubts = {line['entry_id']: line for line in lines if line['id'] not in accepted_audits and line['kind'] == 'draft' and line.get('outcome') == 'added' and line.get('entry_id') in public and line.get('doubts') and cutoff <= datetime.fromisoformat(line['time']) <= at and line['entry_id'] not in reviewed}
    items, paired = [], {identity for s in documents[root/'data/card-statements.json'] for row in s.get('members',[]) if row['disposition']=='overlap-pending' for identity in row['review-candidates']}
    for entry in actual:
        for link in entry.get('possible-duplicate-of', []):
            other = public[link['id']]
            items.append(dict(id='pair:'+entry['id']+':'+other['id'], kind='duplicate', important=True, reason=REASONS[link['reason']], machineReason=link['reason'], kept_id=merge_order(entry, next(e for e in actual if e['id'] == other['id']), lines, root)[0]['id'], entries=[public[entry['id']], other]))
            paired.update((entry['id'], other['id']))
    for entry in actual:
        identity = entry['id']
        if identity in paired:
            continue
        if identity in doubts:
            items.append(dict(id=identity, kind='doubt', important=True, reason='Added this week — the agent wasn’t sure', doubts=doubts[identity]['doubts'], added_at=doubts[identity]['time'], entries=[public[identity]]))
        elif entry['status'] == 'pending':
            items.append(dict(id=identity, kind='pending', important=False, reason='Imported · category not chosen' if entry['evidence'] else 'Category not chosen', entries=[public[identity]]))
    for statement in documents[root/'data/card-statements.json']:
        for row in statement.get('members', []):
            if row['disposition'] == 'overlap-pending':
                items.append(dict(id='overlap:'+statement['id']+':'+row['serial'], kind='overlap', important=True,
                                  reason=REASONS['overlapping-statements'], machineReason='overlapping-statements',
                                  statement_id=statement['id'], row_serial=row['serial'], row=row,
                                  entries=[public[identity] for identity in row['review-candidates']]))
    items.sort(key=lambda item: not item['important'])
    history = [line for line in lines if line['kind'] == 'review-action']
    for line in history: line['canUndo'] = False  # Historical reversals remain readable.
    export_entries = deepcopy(public)
    originals = {}
    for line in history:
        for change in line['changes']:
            if change.get('original') and change['entry_id'] not in originals:
                originals[change['entry_id']] = change['original']
    export_entries.update({identity:original for identity, original in originals.items() if identity in public})
    return dict(export_entries=export_entries, count=len(items), important=sum(i['important'] for i in items), items=items, decisions=decisions,
                entries=public, accounts=list(accounts), history=list(reversed(history)))


def export_projection(entry, row):
    currency = row.get('currency', entry['currency'])
    if currency in entry.get('amounts', {}):
        return {**entry, 'currency':currency, 'amount':entry['amounts'][currency]}
    return entry


def same_export_field(row, entry, key):
    left, right = str(row.get(key, '')), str(entry[key])
    if key in ('description', 'sourceReference'):
        # The version-2 builder compacted whitespace before exporting identity.
        left, right = ' '.join(left.split()), ' '.join(right.split())
    return left == right


def import_preview(root, payload, *, clock=None):
    if not isinstance(payload, dict) or payload.get('reviewVersion', 2) != 2 or not isinstance(payload.get('transactions'), list):
        raise ValueError('Choose a version 2 review export')
    if payload.get('conflicts') and any(payload['conflicts'].values()):
        raise ValueError('Resolve conflicts in the exporting version first')
    drafts = payload.get('drafts', {})
    if not isinstance(drafts, dict) or any(not key or not isinstance(value, dict) or not all(isinstance(value.get(field), str) for field in ('account', 'label', 'notes')) for key,value in drafts.items()):
        raise ValueError('This export contains invalid unsent edits; correct them before importing')
    state = snapshot(root, clock=clock)
    candidates, seen, unknown, changed, already = [], {}, 0, 0, 0
    rows = list(payload['transactions'])
    unmatched = payload.get('unmatched', {})
    if not isinstance(unmatched, dict):
        raise ValueError('Invalid unmatched decisions')
    rows += [dict(id=key, decision=value) for key, value in unmatched.items()]
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str):
            raise ValueError('Invalid exported decision')
        decision = checked_decision(row.get('decision'), allow_unreviewed=True)
        if decision['status'] == 'unreviewed':
            continue
        key = row['id']
        if key in seen:
            if seen[key] != row:
                raise ValueError('Conflicting duplicate decisions in this export')
            continue
        seen[key] = row
        entry = state['entries'].get(row.get('storeId', key.split('|')[0]))
        if not entry:
            # Pre-application exports used derived IDs. Require a unique complete
            # identity match, never merchant/amount alone (currencies may differ).
            matches = [e for original in state['entries'].values() for e in [export_projection(original, row)] if all(same_export_field(row, e, k) for k in ('description', 'originalDate', 'source', 'sourceReference')) and 'amount' in row and Decimal(str(row['amount'])) == Decimal(e['amount']) and row.get('currentAccount') == e['currentAccount'] and (not row.get('currency') or row['currency'] == e['currency'])]
            entry = matches[0] if len(matches) == 1 else None
        if not entry:
            unknown += 1
            continue
        entry = export_projection(entry, row)
        old = state['decisions'].get(entry['id'])
        if old and all(old[k] == decision[k] for k in ('status', 'account', 'label', 'notes')):
            already += 1
            continue
        if row.get('version') and row['version'] != entry['version'] or any(k in row and not same_export_field(row, entry, k) for k in ('description', 'originalDate', 'source', 'sourceReference', 'currentAccount', 'currency')) or ('amount' in row and Decimal(str(row['amount'])) != Decimal(entry['amount'])) or (entry['status'] != 'pending' and not any(i['kind'] == 'doubt' and i['id'] == entry['id'] for i in state['items'])):
            changed += 1
            continue
        candidates.append(dict(entry_id=entry['id'], version=entry['version'], decision=decision))
    unique = {}
    for candidate in candidates:
        prior = unique.get(candidate['entry_id'])
        if prior and any(prior['decision'][key] != candidate['decision'][key] for key in ('status', 'account', 'label', 'notes')):
            raise ValueError('Conflicting decisions refer to the same entry; choose one classification before importing')
        unique.setdefault(candidate['entry_id'], candidate)
    candidates = list(unique.values())
    return dict(drafts=len(drafts), valid=len(candidates), unknown=unknown, changed=changed, already=already, decisions=candidates, token=digest(candidates))


def checked_decision(value, allow_unreviewed=False):
    if not isinstance(value, dict) or not all(isinstance(value.get(k), str) for k in ('status', 'account', 'label', 'notes', 'updatedAt')):
        raise ValueError('A decision needs status, account, label, notes and updatedAt')
    if value['status'] not in (('resolved', 'followup', 'unreviewed') if allow_unreviewed else ('resolved', 'followup')):
        raise ValueError('Choose Resolve or Follow-up')
    value = {k: value[k].strip() for k in ('status', 'account', 'label', 'notes', 'updatedAt')}
    for key in ('account', 'label'):
        reject_invisible(value[key], key)
    if value['status'] == 'resolved' and not value['account']:
        raise ValueError('Choose an account to resolve this entry')
    if any(len(v) > 100000 for v in value.values()):
        raise ValueError('Decision is too long')
    return value


class ReviewRecords(SettingsRecords):
    def __init__(self, root):
        super().__init__(root)
        self.root = root / 'drafts/review'
        if self.root.is_symlink():
            raise ValueError('Review records must not be a symlink')
        self.root.mkdir(parents=True, exist_ok=True)


def apply(settings, body, *, clock=None):
    if not isinstance(body, dict) or not re.fullmatch('[0-9a-f]{32}', str(body.get('request_id', ''))):
        raise ValueError('Use a unique request id for this action')
    root = settings.ledger
    identity, request_hash = body['request_id'], digest(body)
    store = ReviewRecords(root)
    with ledger_lock(root) as fd:
        # Read the selected ledger policy on every run, including recovery.
        import bookkeeping_policy
        bookkeeping_policy.read(settings)
        for folder in store.root.iterdir():
            if (folder / 'transaction.json').exists():
                draft = json.loads((folder / 'record.json').read_text())
                transaction = Transaction(settings, store, draft)
                transaction.marker = 'Review: ' + draft['id']
                transaction.recover()
        prior = next((r for r in records(root) if r.get('request_id') == identity), None)
        if prior:
            if prior['request_hash'] != request_hash:
                raise ValueError('This request id was already used for a different action')
            return dict(audit_id=prior['id'], replayed=True)
        if not clean(root):
            raise ValueError('The ledger has uncommitted changes; finish them before reviewing')
        state = snapshot(root, clock=clock)
        documents = load(root)
        rules_before = deepcopy(documents[root / 'data/forecast.json'])
        actual = {e['id']: e for e in entries(documents)}
        action = body.get('action')
        changes = []
        evidence_moves = []
        expected = deepcopy(body.get('versions', {}))
        if not isinstance(expected, dict):
            raise ValueError('Reload Review before applying this decision')

        def require(entry_id):
            if entry_id not in actual or expected.get(entry_id) != state['entries'][entry_id]['version']:
                raise ValueError('These entries changed since you loaded Review. Reload and check them again.')
            return actual[entry_id]

        def decide(entry_id, raw):
            entry = require(entry_id)
            decision = checked_decision(raw)
            if entry['status'] != 'pending' and not any(i['kind'] == 'doubt' and i['id'] == entry_id for i in state['items']):
                raise ValueError('This entry is no longer waiting for review')
            if decision['status'] == 'resolved':
                postings = [p for p in entry['postings'] if p['account'].split(':')[0] in ('Expenses', 'Income')]
                if not postings:
                    raise ValueError('This entry has no review posting')
                account = decision['account']
                if not all(part and part.strip() == part for part in account.split(':')):
                    raise ValueError('Choose a valid ledger account')
                declarations = documents[root / 'data/accounts.json']['accounts']
                if account not in declarations:
                    from account_metadata import empty, infer_kind, is_balance_sheet
                    from ledger_manifest import read_manifest
                    metadata = empty(account)
                    if is_balance_sheet(account):
                        kind = infer_kind(account)
                        if kind is None:
                            raise ValueError('Create this account with its kind and currency before choosing it')
                        metadata = empty(account, kind, read_manifest(root)['defaultCurrency'])
                    declarations[account] = metadata
                old_account = postings[0]['account']
                for posting in postings:
                    if posting['account'] == old_account:
                        posting['account'] = account
                entry['status'] = 'confirmed'
                if decision['label']:
                    entry['description'] = decision['label']
                if decision['notes']:
                    entry['notes'].extend(decision['notes'].splitlines())
            changes.append(dict(entry_id=entry_id, before_version=state['entries'][entry_id]['version'], after_version=digest(entry), decision=decision, original=state['entries'][entry_id]))

        if action in ('resolve', 'followup'):
            decision = {**body.get('decision', {}), 'status': 'resolved' if action == 'resolve' else 'followup'}
            decide(body.get('entry_id'), decision)
        elif action == 'acknowledge':
            entry = require(body.get('entry_id'))
            if body.get('statement_id'):
                statement = next((s for s in documents[root/'data/card-statements.json'] if s['id'] == body['statement_id']), None)
                row = next((r for r in statement.get('members',[]) if r['serial'] == body.get('row_serial') and r['disposition'] == 'overlap-pending'), None) if statement else None
                if not row or entry['id'] not in row['review-candidates']:
                    raise ValueError('This overlapping row is no longer waiting for review')
                if any(m.get('entry-id') == entry['id'] for m in statement['members']):
                    raise ValueError('That entry is already assigned to another row')
                row.update(disposition='already-in-ledger', **{'entry-id':entry['id'], 'review-candidates':[]})
                if statement['source'] not in entry['evidence']:
                    entry['evidence'].append(statement['source'])
                changes.append(dict(entry_id=entry['id'],before_version=state['entries'][entry['id']]['version'],after_version=digest(entry),reason='overlapping-statements'))
            elif entry['status'] == 'pending':
                raise ValueError('Choose a category to resolve this pending entry')
            elif not any(i['kind'] == 'doubt' and i['id'] == entry['id'] for i in state['items']):
                raise ValueError('This doubt is no longer waiting for review')
            if not body.get('statement_id'):
                changes.append(dict(entry_id=entry['id'], before_version=state['entries'][entry['id']]['version'], after_version=digest(entry), decision=dict(status='resolved', account=state['entries'][entry['id']]['currentAccount'] or entry['postings'][0]['account'], label='', notes='', updatedAt=datetime.now(timezone.utc).isoformat()), original=state['entries'][entry['id']]))
        elif action == 'import':
            preview = import_preview(root, body.get('payload'), clock=clock)
            if body.get('preview_token') != preview['token']:
                raise ValueError('The import preview changed. Preview this file again.')
            for item in preview['decisions']:
                expected[item['entry_id']] = item['version']
                entry = actual[item['entry_id']]
                decision = item['decision']
                if entry['status'] == 'confirmed' and not any(p['account'].split(':')[0] in ('Expenses', 'Income') for p in entry['postings']) and decision['status'] == 'resolved' and decision['account'] == entry['postings'][0]['account'] and not decision['label'] and not decision['notes']:
                    require(entry['id'])
                    changes.append(dict(entry_id=entry['id'], before_version=state['entries'][entry['id']]['version'], after_version=digest(entry), decision=decision, original=state['entries'][entry['id']]))
                else:
                    decide(item['entry_id'], decision)
            if not changes:
                raise ValueError('No valid decisions to apply')
        elif action in ('separate', 'merge'):
            one, two = body.get('entry_id'), body.get('other_id')
            first, second = require(one), require(two)
            if one == two:
                raise ValueError('Choose two different entries')
            owner, target = sorted((one, two))
            link = next((l for l in actual[owner].get('possible-duplicate-of', []) if l['id'] == target), None)
            change = dict(entry_id=one, other_id=two, before_version=digest([state['entries'][one]['version'], state['entries'][two]['version']]), after_version='')
            if not link:
                raise ValueError('This duplicate was already settled. Reload Review.')
            change['reason'] = link['reason']
            unlink_pair(actual, one, two)
            if action == 'merge':
                kept, removed = merge_order(first, second, records(root), root)
                bank_members = statement_members(documents, root)
                if ((kept['statement'] or {}).get('end') or kept['id'] in bank_members) and ((removed['statement'] or {}).get('end') or removed['id'] in bank_members):
                    raise ValueError('Both entries belong to filed statements. Reconcile their membership before merging.')
                kept_before, removed_before = deepcopy(kept), deepcopy(removed)
                kept = deepcopy(kept)
                if kept.get('rule') and removed.get('rule') and kept['rule'] != removed['rule']:
                    raise ValueError('These entries link different rule occurrences. Reconcile their rules before merging.')
                if removed.get('rule') and not kept.get('rule'):
                    kept['rule'] = deepcopy(removed['rule'])
                for rule in documents[root / 'data/forecast.json']:
                    for field in ('origin', 'removal'):
                        if (rule[field] or {}).get('entry-ids'):
                            rule[field]['entry-ids'] = list(dict.fromkeys(kept['id'] if identity == removed['id'] else identity for identity in rule[field]['entry-ids']))
                kept['evidence'] = list(dict.fromkeys(kept['evidence'] + removed['evidence']))
                kept['notes'] = list(dict.fromkeys(kept['notes'] + removed['notes']))
                from replacements import replace_entry, entry_hash, move_evidence
                kept = replace_entry(documents,root,kept_before,kept,expected=entry_hash(kept_before),remove=removed_before,removed_expected=entry_hash(removed_before))
                actual[kept['id']]=kept
                evidence_moves = move_evidence(documents,root,kept_before,kept)
                # Retarget other unsettled edges without leaving dangling IDs.
                edges = [(e['id'], l['id'], l['reason']) for e in actual.values() for l in e['possible-duplicate-of']]
                for e in actual.values():
                    e['possible-duplicate-of'] = []
                for a, b, reason in edges:
                    a = kept['id'] if a == removed['id'] else a
                    b = kept['id'] if b == removed['id'] else b
                    if a != b:
                        link_pair(actual, a, b, reason)
                actual.pop(removed['id'])
                change.update(kept_id=kept['id'], removed_id=removed['id'], sources=removed['evidence'], notes=removed['notes'])
            change['after_version'] = digest([actual.get(one), actual.get(two)])
            changes.append(change)
        else:
            raise ValueError('Unknown review action')
        validate(documents, root)
        record = event('review-action', action=action, request_id=identity, request_hash=request_hash, changes=changes)
        from rules_service import rule_changes
        recurring_changes = rule_changes(rules_before, documents[root / 'data/forecast.json'])
        if recurring_changes:
            record['rule_changes'] = recurring_changes
        _, touched = decision_state(records(root))
        for change in changes:
            touched[change['entry_id']] = record['id']
            if change.get('other_id'):
                touched[change['other_id']] = record['id']
        for change in changes:
            one = actual.get(change['entry_id'])
            if change.get('other_id'):
                two = actual.get(change['other_id'])
                change['after_version'] = digest([version(one, touched) if one else None, version(two, touched) if two else None])
            else:
                change['after_version'] = version(one, touched)
        draft = dict(id=identity)
        store.put(draft)
        transaction = Transaction(settings, store, draft)
        transaction.marker = 'Review: ' + identity
        result = dict(audit_id=record['id'], action=action, changes=changes)
        transaction.publish(documents, record, result, fd,
                            f'Review: {action}\n\n{transaction.marker}\nAudit: {record["id"]}\nDecisions: {len(changes)}',
                            moves=evidence_moves)
    return refresh(settings, result)


def merge_order(first, second, lines, root=None):
    bank_members = statement_members(load(root), root) if root else set()
    first_member, second_member = bool((first['statement'] or {}).get('end')) or first['id'] in bank_members, bool((second['statement'] or {}).get('end')) or second['id'] in bank_members
    if first_member != second_member:
        return (first, second) if first_member else (second, first)
    # Existing imported entries have no addition event and predate live additions.
    additions = {line.get('entry_id'): (line['time'], index) for index, line in enumerate(lines) if line['kind'] == 'draft' and line.get('outcome') == 'added'}
    imported = {}
    if root is not None and any(e['id'] not in additions for e in (first, second)):
        # The server primes this cached read-only history at startup; maintenance
        # refreshes it. Comparing a pair never scans the repository per request.
        from retention_service import history_for
        imported = history_for(root).first_commits
    def order(entry):
        if entry['id'] in imported:
            return (0, imported[entry['id']], entry['id'])
        return (1, *additions.get(entry['id'], ('', -1)), entry['id'])
    return (first, second) if order(first) < order(second) else (second, first)
