"""Frozen weekly review plans and one synchronous, restartable batch."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import shutil
from tempfile import TemporaryDirectory
import time

from adapters.base import AdapterError, review_json
from transactions import Transaction, clean, reject_invisible

import audit
import audit_changes
from ledger_lock import ledger_lock
from review_lookup import (ReviewLookup, committed_snapshot, encode, evidence_paths,
                           git, issue_grant, recorded_revision, review_task)
from review_decisions import QUESTION, check_suggestion, rule_form_schema, suggestions_current
from store import entries, ledger_models, load, read_json
from weekly_review_report import carry_answers, carry_events, carry_plan, carry_scope, reject_repeated_finding, CARRY_SCHEMA


def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as output:
        output.write(encode(value))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def batches(lines, rules, entry_rules):
    active = {r['id'] for r in rules if r['lifecycle'] != 'removed'}
    groups, group = [], []
    for row in lines:
        paths = evidence_paths(row)
        solo = row['kind'] in ('statement', 'change') or len(paths) > 1 or any(p.lower().endswith('.pdf') for p in paths)
        if group and (solo or len(group) == 3):
            groups.append(group)
            group = []
        if solo:
            groups.append([row])
        else:
            group.append(row)
    if group:
        groups.append(group)
    for group in groups:
        obligations = {}
        for row in group:
            if row['kind'] != 'change':
                linked = {row.get('rule_id'), entry_rules.get(row.get('entry_id')), *(c['id'] for c in row.get('rule_changes', []))}
                for identity in sorted(linked & active):
                    obligations.setdefault(identity, []).append(row['id'])
        items = list(obligations.items())
        yield dict(lines=[row['id'] for row in group], rules=dict(items[:3]))
        yield from (dict(lines=[], rules=dict(items[i:i+3])) for i in range(3, len(items), 3))


def period(lines):
    return [min(lines, key=datetime.fromisoformat), max(lines, key=datetime.fromisoformat)] if lines else [None, None]


def freeze_run(settings, *, trigger='manual', slot=None, run=None, started=None):
    root, run = settings.ledger, run or secrets.token_hex(16)
    folder = root / 'drafts' / 'weekly-review' / run
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    revision = audit_changes.identity(root, 'main')['commit']
    for abandoned in folder.glob('work-*'):
        shutil.rmtree(abandoned)
    with TemporaryDirectory(dir=folder, prefix='work-') as scratch:
        snapshot = committed_snapshot(root, Path(scratch), revision)
        records = audit.records(snapshot)
        pending = sorted(audit.unreviewed(records), key=lambda r: (datetime.fromisoformat(r['time']), r['id']))
        try:
            history = audit_changes.unrecorded(root, records, snapshot=snapshot, revision=revision)
        except audit_changes.HistoryUnavailable as exc:
            history = [dict(reason=str(exc))]
        documents = load(snapshot)
        rules = documents[snapshot / 'data/forecast.json']
        entry_rules = {e['id']: e['rule']['id'] for e in entries(documents) if e['rule']}
    plan = dict(run_id=run, trigger=trigger, slot=slot, started=started or audit.stamp(), period=period([r['time'] for r in pending]),
                missing_actors=[r['commit'] for r in history if not r.get('reason')],
                history_errors=[r['reason'] for r in history if r.get('reason')],
                batches=carry_plan(list(batches(pending, rules, entry_rules)), records, revision))
    save(folder / 'plan.json', plan)
    return folder / 'plan.json'


def remaining(batch, records, rules, plan):
    pending = {r['id'] for r in audit.unreviewed(records)}
    related = {plan['run_id'], *plan.get('previous_runs', [])}
    eligible = pending | {r['line_id'] for r in records if r['kind'] == 'review' and r.get('run_id') in related}
    closed = {(r['rule_id'], source) for r in records if r['kind'] == 'rule-review' for source in r.get('rule_sources', [])}
    manual = {r['rule_id'] for r in records if r['kind'] == 'rule-review' and not r.get('run_id')
              and datetime.fromisoformat(r['time']) >= datetime.fromisoformat(plan['started'])}
    active = {r['id'] for r in rules if r['lifecycle'] != 'removed'} - manual
    sources = {rule: [line for line in lines if line in eligible and (rule, line) not in closed]
               for rule, lines in batch['rules'].items() if rule in active}
    selected = dict(lines=[line for line in batch['lines'] if line in pending], rules={r: s for r, s in sources.items() if s})
    if batch.get('carry'):
        from review_decisions import open_issues
        active_issues = open_issues(records)
        selected['carry'] = [item for item in batch['carry'] if item['id'] in active_issues]
    return selected


def scope(root, work, revision, snapshot, batch):
    lines = list(dict.fromkeys([*batch['lines'], *(s for sources in batch['rules'].values() for s in sources)]))
    grant = issue_grant(root, work, revision, lines, rules=batch['rules'], snapshot=snapshot)
    review = ReviewLookup(root, grant)
    required = {line: [('audit_line', [line])] for line in lines}
    for item in review.lines.values():
        line = item['id']
        need = required[line]
        if entry_id := item.get('entry_id'):
            need.append(('entry', [entry_id]))
        if item['kind'] == 'statement':
            from review_lookup import statement_matches
            statements = review.documents[snapshot / 'data/card-statements.json']
            need.append(('statement', [next((s['id'] for s in statements if statement_matches(item, s)), None)]))
        if item['kind'] in ('change', 'baseline'):
            revision_info = recorded_revision(root, revision, review.lines[line]) if item['kind'] == 'change' else None
            need.extend(('revision', [line, index]) for index in range(len(revision_info['parents']) if revision_info else 1))
    versions = {'policy': git(root, 'ls-tree', '-z', revision, '--', 'AGENTS.md', 'ledger.json').hex()}
    for name, args in [*(item for needs in required.values() for item in needs), *(('rule', [r]) for r in batch['rules'])]:
        review.version = None
        try:
            getattr(review, name)(*args)
        except (ValueError, OSError, KeyError):
            pass
        versions[encode([name, args])] = review.version
    for handle, source in grant.evidence.items():
        versions[handle] = git(root, 'ls-tree', '-lz', source['revision'], '--', source['path']).hex()
    return review, required, versions


def object_schema(**properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


TEXT = dict(type='string', minLength=1, maxLength=600)
ID = dict(type='string', pattern='^[0-9a-f]{32}$')
TARGET = dict(type='string', pattern='^[0-9a-f]{16}$')
SUGGESTION = dict(anyOf=[{'const': QUESTION},
                        object_schema(kind={'const': 'entry'}, target=TARGET, answer={'type': 'object'}),
                        object_schema(kind={'const': 'rule'}, target=TARGET, form={'type': 'object'})])
LINE = object_schema(line=ID, verdict={'const': 'ok'}, note=TEXT)
ISSUE = object_schema(line=ID, verdict={'const': 'issue'}, note=TEXT, decide=TEXT,
                      evidence=dict(type='array', items=ID, uniqueItems=True), suggestion=SUGGESTION,
                      choices=dict(anyOf=[dict(type='array', items=TEXT, minItems=2, maxItems=2), {'type': 'null'}]))
ISSUE['required'].remove('choices')
SCHEMA = object_schema(lines=dict(type='array', maxItems=3, items=dict(anyOf=[LINE, ISSUE])),
                       rules=dict(type='array', maxItems=3, items=object_schema(
                           rule=dict(type='string', pattern='^[0-9a-f]{16}$'),
                           verdict={'enum': ['confirm', 'propose-removal']}, reason=TEXT)))


def output_schema(root):
    schema = deepcopy(SCHEMA)
    issue = schema['properties']['lines']['items']['anyOf'][1]
    issue['required'].append('choices')
    _, entry, rule = issue['properties']['suggestion']['anyOf']
    answer, form = ledger_models(root).answer_schema(), rule_form_schema()
    schema['$defs'] = {**answer.pop('$defs', {}), **form.pop('$defs')}
    answer.pop('$schema')
    entry['properties']['answer'], rule['properties']['form'] = answer, form
    return schema


def validate(raw, batch, review, needed, receipt_path):
    from jsonschema import Draft202012Validator
    answer = review_json(encode(raw) if isinstance(raw, dict) else raw)
    reject_invisible(answer)
    carry = carry_answers(answer, batch.get('carry', []))
    if not Draft202012Validator(SCHEMA).is_valid(answer):
        raise ValueError('Use only the fields and verdicts in the review schema')
    seen = set(receipt_path.read_text().splitlines()) if receipt_path.exists() else set()
    uncovered = {line for line, calls in needed.items() if any(encode(call) not in seen for call in calls)}
    for kind, identifier in [('lines', 'line'), ('rules', 'rule')]:
        expected = batch[kind]
        if sorted(item[identifier] for item in answer[kind]) != sorted(expected):
            raise ValueError('Give exactly one verdict for every selected ID')
        for item in answer[kind]:
            for key in {'note', 'reason', 'decide'} & item.keys():
                item[key] = item[key].strip()
                if not item[key]:
                    raise ValueError('Notes, reasons and questions need concrete text')
            if item.get('choices') is None:
                item.pop('choices', None)
            else:
                item['choices'] = [choice.strip() for choice in item['choices']]
                if not all(item['choices']):
                    raise ValueError('Both offered choices need text')
            identity = item[identifier]
            if item['verdict'] == 'issue':
                reject_repeated_finding(item, batch.get('carry', []))
                allowed = {e for e, source in review.grant.evidence.items() if source.get('line') == identity}
                if set(item['evidence']) - allowed or (allowed and not item['evidence']):
                    raise ValueError('Cite evidence IDs issued for that audit line')
            elif item['verdict'] == 'ok' and identity in uncovered or item['verdict'] == 'confirm' and (
                    encode(['rule', [identity]]) not in seen or uncovered.intersection(expected[identity])):
                raise ValueError('Check all required records and evidence, or report an issue')
    return {**answer, 'carry': carry}


def run_batch(settings, plan_path, batch_number, *, batch=None, one_turn=False, cancel=None):
    """Run or recover one batch. The caller serializes runs for this ledger."""
    attempt = plan_path.parent / str(batch_number) / 'attempt.json'
    preparing = one_turn and not (attempt.exists() and read_json(attempt).get('answer'))
    deadline = time.monotonic() + min(settings.timeout, 180)
    token = audit_changes.read_deadline.set(deadline) if preparing else None
    try:
        return _run_batch(settings, plan_path, batch_number, batch, one_turn, cancel, deadline)
    except audit_changes.HistoryUnavailable:
        if preparing and time.monotonic() >= deadline:
            raise AdapterError('Review preparation reached its deadline', kind='timeout') from None
        raise
    finally:
        if token is not None:
            audit_changes.read_deadline.reset(token)


def _run_batch(settings, plan_path, batch_number, batch, one_turn, cancel, deadline):
    from adapters import get_adapter
    root, plan = settings.ledger, read_json(plan_path)
    batch = batch if batch is not None else plan['batches'][batch_number - 1]
    folder = plan_path.parent / str(batch_number)
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / 'attempt.json'
    state = read_json(path) if path.exists() else {}
    tx = Transaction(settings, None, {'id': plan['run_id']}, journal=folder / 'transaction.json')
    reviewer = settings.for_role('review')
    timeout = min(reviewer.timeout, 180)
    for abandoned in folder.glob('work-*'):
        shutil.rmtree(abandoned)
    with TemporaryDirectory(dir=folder, prefix='work-') as scratch:
        work = Path(scratch)
        revision = audit_changes.identity(root, 'main')['commit']
        snapshot = committed_snapshot(root, work, revision)
        records = audit.records(snapshot)
        marker = next((r for r in records if r['kind'] == 'weekly-review-run' and r.get('batch') == batch_number
                       and r['run_id'] == plan['run_id']), None)
        if tx.journal.exists() or marker:
            try:
                with ledger_lock(root, blocking=False):
                    if audit_changes.identity(root, 'main')['commit'] != revision:
                        return dict(status='busy')
                    if marker:
                        if tx.journal.exists():
                            tx.finish_commit()
                    else:
                        tx.recover_audit(revision)
            except BlockingIOError:
                return dict(status='busy')
            if marker:
                return dict(status='committed', marker=marker)
        selected = remaining(batch, records, load(snapshot)[snapshot / 'data/forecast.json'], plan)
        if not any(selected.values()):
            if state.get('answer'):
                save(path, {})
                return dict(status='stale', remaining=0)
            return dict(status='empty')
        review, required, versions = scope(root, work, revision, snapshot, selected)
        signature = dict(selected=selected, versions=versions, profile=[reviewer.adapter, reviewer.model, reviewer.effort])
        if state.get('answer') and (state['signature'] != signature or
                                    state.get('revision') != revision and not suggestions_current(settings, review, state['answer'])):
            save(path, {})
            return dict(status='stale', remaining=len(selected['lines']))
        error = state.get('error', '') if one_turn and state.get('signature') == signature else ''
        if not state.get('answer'):
            failures = state.get('failures', 0) if error else 0
            if failures >= 3:
                return dict(status='failed', error=error)
            for retry in range(1 if one_turn else 3):
                with TemporaryDirectory(dir=work) as attempt:
                    attempt = Path(attempt)
                    state = dict(signature=signature, started=audit.stamp(), attempt=state.get('attempt', 0) + 1,
                                 error=error, failures=failures)
                    save(path, state)
                    if retry:
                        deadline = time.monotonic() + timeout
                    lookup = ReviewLookup(root, replace(review.grant, expires=time.time() + deadline - time.monotonic()), receipts=attempt / 'receipts.jsonl')
                    context = carry_scope(lookup, selected.get('carry', []))
                    images = lookup.images(attempt)
                    unavailable, needed = set(), {line: list(calls) for line, calls in required.items()}
                    for handle, source in lookup.grant.evidence.items():
                        line = source['line']
                        if line not in needed:
                            continue
                        pages = lookup.evidence_pages(handle)
                        if pages is None:
                            unavailable.add(line)
                        needed[line].extend(('evidence', [handle, page]) for page in sorted({0} if pages is None else pages))
                    rules = {r: sources for r, sources in selected['rules'].items() if not unavailable.intersection(sources)}
                    effective = {**selected, 'rules': rules}
                    lookup.grant = replace(lookup.grant, rules=tuple(rules))
                    if time.monotonic() >= deadline:
                        raise AdapterError('Review preparation reached its deadline', kind='timeout')
                    if not any(effective.values()):
                        return dict(status='empty')
                    prompt = ('Review the committed batch. Use review_pending and inspect all required records and evidence. '
                              'Give verdicts only for these line IDs and rule IDs: '
                              + encode([selected['lines'], list(rules)]) + '. Other source lines are context. '
                              'For issues, propose a complete ready entry answer or a complete rule form '
                              '(description, date, status, postings, schedule, amount, notes) only for a target from that source. '
                              'Use question-only when unsure and report any unavailable source. ' + error)
                    schema = output_schema(snapshot)
                    if effective.get('carry'):
                        prompt += context
                        schema['properties']['carry'] = CARRY_SCHEMA
                        schema['required'].append('carry')
                    task = review_task(settings, attempt, lookup.grant, prompt, schema, deadline,
                                       prepared=(lookup, images), **({'cancel': cancel} if cancel is not None else {}))
                    try:
                        task.remaining()
                        result = get_adapter(reviewer).run(task)
                        task.remaining()
                        answer = validate(result.answer, effective, lookup, needed, lookup.receipts)
                        for item in answer['lines']:
                            if item['verdict'] == 'issue':
                                item['suggestion'] = check_suggestion(settings, lookup, item)
                                item['evidence_refs'] = [dict(id=handle, **{k: lookup.grant.evidence[handle][k]
                                                          for k in ('path', 'revision')}) for handle in item['evidence']]
                        state.update(answer=answer, rules=rules, revision=revision)
                        task.remaining()
                        save(path, state)
                        if one_turn:
                            return dict(status='prepared')
                        break
                    except (ValueError, TypeError, RecursionError) as exc:
                        if isinstance(exc, AdapterError) and exc.kind != 'bad_answer':
                            raise
                        task.remaining()
                        error = 'The previous answer failed the server checks. Check verdict coverage, evidence and field types.'
                        failures += 1
            else:
                save(path, {**state, 'error': error, 'failures': failures})
                return dict(status='failed' if failures >= 3 else 'retry', error=error)
        try:
            with ledger_lock(root, blocking=False) as fd:
                current = audit_changes.identity(root, 'main')['commit']
                if not clean(root) or audit_changes.identity(root, '@-')['commit'] != current:
                    return dict(status='dirty')
                if current != revision:
                    with TemporaryDirectory(dir=work) as latest:
                        latest = Path(latest)
                        snap = committed_snapshot(root, latest, current)
                        updated = remaining(batch, audit.records(snap), load(snap)[snap/'data/forecast.json'], plan)
                        latest_review, _, latest_versions = (scope(root, latest, current, snap, updated)
                                                              if updated == selected else (None, None, None))
                        if latest_versions != versions or not suggestions_current(settings, latest_review, state['answer']):
                            save(path, {})
                            return dict(status='stale', remaining=len(updated['lines']))
                answer = state['answer']
                marker = audit.event('weekly-review-run', **{k: plan[k] for k in ('run_id', 'trigger', 'slot', 'started')},
                                     batch=batch_number, status='batch', ended=audit.stamp(), adapter=reviewer.adapter,
                                     model=reviewer.model, effort=reviewer.effort,
                                     period=period([review.lines[line]['time'] for line in selected['lines']]),
                                     checked=len(answer['lines']), issues=sum(r['verdict'] == 'issue' for r in answer['lines']))
                audit.review(root, answer['lines'] + answer['rules'], f'{reviewer.adapter}/{reviewer.model}', 'Weekly review batch',
                             lock_fd=fd, marker=marker, rule_sources=state['rules'], publish=tx.publish_audit,
                             carry=carry_events(plan['run_id'], selected.get('carry', []), answer.get('carry', [])))
        except BlockingIOError:
            return dict(status='busy')
    return dict(status='committed', marker=marker)
