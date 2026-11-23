"""Checked weekly-review suggestions and owner decisions."""
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import re
import secrets
from urllib.parse import urlencode

from audit_changes import HistoryUnavailable, identity, unrecorded
from corrections import wire_entry
from jsonschema import Draft202012Validator
from ledger_lock import ledger_lock
from replacements import entry_hash, safe_evidence
from rules_service import EDIT_FIELDS, digest, ignored_entries, prepare_edit
from store import entries, load
from transactions import LedgerBusy, Transaction, clean, reject_invisible

import answers
import audit


QUESTION = {'kind': 'question-only'}
STALE = 'Suggestion is out of date'
REFUSALS = (ValueError, TypeError, KeyError, OSError, ArithmeticError, StopIteration)


@lru_cache(maxsize=4)
def published(root, revision):
    from review_lookup import committed_snapshot
    with TemporaryDirectory() as work:
        return audit.records(committed_snapshot(root, Path(work), revision, audit_only=True))


def open_issues(rows):
    closed = {row.get('review_issue_id') for row in rows if row['kind'] in ('review-resolution', 'correction', 'rule')}
    return {row['id']: row for row in rows if row['kind'] == 'review' and row['verdict'] == 'issue' and row['id'] not in closed}


def current_suggestion(rows, link, *, kind=None, target=None):
    issue = open_issues(rows).get(link.get('review_issue_id'), {})
    suggestion = issue.get('suggestion', QUESTION)
    if (suggestion['kind'] == 'question-only' or suggestion['id'] != link.get('review_suggestion_id') or
            kind is not None and (suggestion['kind'], suggestion['target']) != (kind, target)):
        raise ValueError(STALE)
    return suggestion


def open_suggestion(settings, link, *, kind=None, target=None):
    root = settings.ledger
    suggestion = current_suggestion(published(root, identity(root, 'main')['commit']), link, kind=kind, target=target)
    try:
        version, baseline = preview(settings, suggestion['kind'], suggestion['target'], suggestion['form'])
    except REFUSALS:
        raise ValueError(STALE) from None
    if version != suggestion['version']:
        raise ValueError(STALE)
    return {**suggestion, 'baseline': baseline}


def save_link(rows, body, kind, target, version, form, *, extras=False):
    link = {key: body[key] for key in ('review_issue_id', 'review_suggestion_id') if key in body}
    if link:
        suggestion = current_suggestion(rows, link, kind=kind, target=target)
        if version != suggestion['version'] or body.get('version') != version:
            raise ValueError(STALE)
        link['review_suggestion_edited'] = extras or form != suggestion['form']
    return link


def targets(source):
    """Target IDs come from typed audit relationships on the issue's source."""
    entry_ids = {source.get('entry_id'), *source.get('entry_ids', []),
                 *(change['id'] for change in source.get('entries', []))}
    if source.get('retention_action') in ('deleted', 'restored', 'removed-for-good'):
        entry_ids.update(source.get('selected', []))
    rule_ids = {source.get('rule_id'), *(change['id'] for change in source.get('rule_changes', [])),
                *(change['id'] for change in source.get('rules', []))}
    return entry_ids - {None}, rule_ids - {None}


def preview(settings, kind, target, form, *, check_source=safe_evidence):
    root = settings.ledger
    documents = load(root)
    if kind == 'entry':
        entry = next(e for e in entries(documents) if e['id'] == target)
        version = entry_hash(entry)
        draft = dict(id=target, files=[], hand_form=form, correction=dict(entry_id=target, version=version))
        Transaction(settings, None, draft, journal=root / 'unused').prepare(form, person=True, check_source=check_source)
        return version, wire_entry(settings, entry)
    rule = next(r for r in documents[root / 'data/forecast.json'] if r['id'] == target and r['lifecycle'] != 'removed')
    baseline = {k: deepcopy(rule[k]) for k in EDIT_FIELDS}
    version = digest(rule)
    ignored = ignored_entries(audit.records(root))
    prepare_edit(documents, root, rule, form, [e for e in entries(documents) if e['id'] not in ignored],
                 documents[root / 'data/accounts.json']['accounts'])
    return version, baseline


def frozen_preview(settings, review, suggestion, form):
    from review_lookup import evidence_path, git

    def committed_source(root, path):
        row = git(review.root, 'ls-tree', '-z', review.grant.revision, '--', str(evidence_path(path)))
        if not row.startswith((b'100644 blob ', b'100755 blob ')):
            raise ValueError('A saved evidence file is unavailable')

    return preview(replace(settings, ledger=review.ledger), suggestion['kind'], suggestion['target'],
                   form, check_source=committed_source)


def rule_form_schema():
    from store_models import Rule, wire_schema
    schema = wire_schema(Rule)
    fields = {key: schema['properties'][key] for key in sorted(EDIT_FIELDS)}
    return dict(type='object', properties=fields, required=list(fields), additionalProperties=False,
                **{'$defs': {key: schema['$defs'][key] for key in ('Cost', 'Assertion', 'Posting', 'Schedule')}})


def check_suggestion(settings, review, item):
    proposal = item['suggestion']
    kind = proposal['kind']
    if kind == 'question-only':
        return proposal
    try:
        entry_ids, rule_ids = targets(review.lines[item['line']])
        if kind == 'entry':
            allowed = entry_ids
            answers.check(proposal['answer'], review.ledger, form=False)
            form = answers.to_form(proposal['answer'], review.ledger)
        else:
            related = (e['rule']['id'] for e in entries(review.documents) if e['id'] in entry_ids and e['rule'])
            allowed = rule_ids.union(related)
            form = proposal['form']
            if not Draft202012Validator(rule_form_schema()).is_valid(form):
                raise ValueError('Invalid rule form')
        if proposal['target'] not in allowed:
            raise ValueError('Target outside the source')
        version, _ = frozen_preview(settings, review, proposal, form)
        return dict(kind=kind, target=proposal['target'], form=form, version=version, id=secrets.token_hex(16))
    except REFUSALS as exc:
        reason = 'The proposed entry did not pass the correction checks.'
        if kind == 'rule':
            reason = 'The proposed rule did not pass the forecast checks.'
        elif str(exc).startswith('This entry belongs to a filed statement.'):
            reason = 'The filed statement locks the proposed financial changes.'
        return dict(QUESTION, reason=reason)


def suggestions_current(settings, review, answer):
    for row in answer['lines']:
        suggestion = row.get('suggestion', QUESTION)
        if suggestion['kind'] == 'question-only':
            continue
        try:
            version, _ = frozen_preview(settings, review, suggestion, suggestion['form'])
            if version != suggestion['version']:
                return False
        except REFUSALS:
            return False
    return True


@lru_cache(maxsize=4)
def queue(settings, revision):
    from review_lookup import committed_snapshot, evidence_paths, statement_matches
    from rules_service import review_proposals
    root, base = settings.ledger, settings.base_path
    with TemporaryDirectory() as work:
        snapshot = committed_snapshot(root, Path(work), revision)
        rows = audit.records(snapshot)
        documents = load(snapshot)
        try:
            history = unrecorded(root, rows, snapshot=snapshot, revision=revision)
        except HistoryUnavailable as exc:
            history = [dict(commit='history', reason=str(exc))]
    by_id = {row['id']: row for row in rows}
    rules = {r['id'] for r in documents[snapshot / 'data/forecast.json'] if r['lifecycle'] != 'removed'}
    actual = {e['id']: e for e in entries(documents)}
    statements = documents[snapshot / 'data/card-statements.json']
    items = []
    for row in open_issues(rows).values():
        source_id = row['line_id']
        source = by_id.get(source_id, {'kind': 'unknown'})
        entry_ids, rule_ids = targets(source)
        links = [dict(href=base + 'explorer/?' + urlencode(dict(month=actual[i]['date'][:7], entry=i)),
                      kind='entry', id=i) for i in sorted(entry_ids & actual.keys())]
        links.extend(dict(href=base + 'rules/#rule-' + i, kind='rule', id=i) for i in sorted(rule_ids & rules))
        links.extend(dict(href=base + 'queue/?' + urlencode(dict(filed=s['end'], account=s['account'])),
                          kind='statement', id=s['id']) for s in statements if statement_matches(source, s))
        evidence = row.get('evidence_refs', [dict(path=p) for p in sorted(evidence_paths(source))])
        items.append(dict(id=row['id'], kind='review-issue', time=row['time'], note=row['note'],
                          decide=row.get('decide', 'Review the cited record.'), choices=row.get('choices'),
                          suggestion=row.get('suggestion', QUESTION), evidence=evidence, links=links,
                          source_href=base + 'api/review/sources/' + source_id))
    reviews, _ = review_proposals(rows)
    items.extend(dict(id=row['id'], kind='rule-removal', time=row['time'], rule_id=rule, note=row['reason'],
                      href=base + 'rules/#rule-' + rule, source_href=base + 'api/review/sources/' + row['id'])
                 for rule, row in reviews.items() if rule in rules and row['rule_verdict'] == 'propose-removal')
    for row in history:
        if row.get('reason'):
            items.append(dict(id=row['commit'], kind='history-diagnostic', note=row['reason']))
        else:
            items.append(dict(id=row['commit'], kind='missing-actor', decide='Who made this change?',
                              note=row['message'].rstrip(), actors=['user', 'agent']))
    return dict(revision=revision, items=items, needs_you=len(items))


def owner_transaction(settings):
    path = settings.ledger / 'drafts/weekly-review/owner-transaction.json'
    return Transaction(settings, None, {'id': 'review-owner'}, journal=path)


def recover_owner(tx):
    if not tx.journal.exists():
        return
    root = tx.root
    revision = identity(root, 'main')['commit']
    journal = json.loads(tx.journal.read_text())
    pending = {json.loads(line)['id'] for suffix in journal.get('audit_suffix', {}).values()
               for line in suffix.splitlines()}
    if pending and pending <= {row['id'] for row in published(root, revision)}:
        tx.finish_commit()
    else:
        tx.recover_audit(revision)


def owner_records(tx):
    root = tx.root
    recover_owner(tx)
    if not clean(root) or identity(root, '@-')['commit'] != identity(root, 'main')['commit']:
        raise LedgerBusy('Waiting for a clean ledger before saving the decision')
    tx.journal.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return audit.records(root)


def keep(settings, issue_id, operation_id, note):
    if not re.fullmatch('[0-9a-f]{32}', operation_id) or len(note) > 2000:
        raise ValueError('Supply an operation ID and a note of up to 2,000 characters')
    reject_invisible(note)
    note = note.strip()
    tx = owner_transaction(settings)
    with ledger_lock(settings.ledger) as fd:
        rows = owner_records(tx)
        for row in rows:
            if row['kind'] == 'review-resolution' and row['operation_id'] == operation_id:
                if (row['review_issue_id'], row.get('note', '')) != (issue_id, note):
                    raise ValueError('This operation id was used for a different payload')
                return dict(audit_id=row['id'], replayed=True)
        if issue_id not in open_issues(rows):
            raise ValueError('This review issue is already closed or unavailable')
        row = audit.event('review-resolution', operation_id=operation_id, review_issue_id=issue_id, actor='user',
                          **({'note': note} if note else {}))
        tx.publish_audit([row], 'Keep as is after weekly review', fd)
        return dict(audit_id=row['id'])


def attribute(settings, commit, actor):
    stale = 'This commit is no longer waiting for attribution'
    if not re.fullmatch('[0-9a-f]{40}', commit) or actor not in ('user', 'agent'):
        raise ValueError(stale)
    tx = owner_transaction(settings)
    with ledger_lock(tx.root) as fd:
        rows = owner_records(tx)
        if prior := next((r for r in rows if r['kind'] == 'change' and r['commit'] == commit), None):
            return dict(audit_id=prior['id'], actor=prior['actor'], replayed=True)
        if not any(r['commit'] == commit and not r.get('reason') for r in unrecorded(tx.root, rows)):
            raise ValueError(stale)
        _, (audit_id,) = audit.record(tx.root, [dict(commit=commit, actor=actor)], lock_fd=fd, publish=tx.publish_audit)
        return dict(audit_id=audit_id, actor=actor)
