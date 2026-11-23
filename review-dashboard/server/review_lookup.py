"""Server-issued capability for a committed review batch."""
from contextlib import closing
from dataclasses import dataclass, replace
from functools import cached_property
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from settings import APPLICATION  # Install shared script paths.
import sys
sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
from audit import records
from audit_changes import IDENTITY, bounded_read, read_jj, require_repository
from store import entries, load

PAGE, SOURCE, PIXELS = 24 * 1024, 16 * 1024 * 1024, 4_000_000
COMMIT = r'[0-9a-f]{40}'
TOOLS = {
    'pending': ('List the selected audit lines and rule obligations', None, 'limit'),
    'audit_line': ('Read a selected audit record', 'line_id', None),
    'entry': ('Read a linked or discovered entry', 'entry_id', None),
    'rule': ('Read a linked rule and payment history', 'rule_id', None),
    'statement': ('Read a linked filed statement', 'statement_id', None),
    'revision': ('Inspect a selected audit revision and parent diff', 'line_id', 'parent_index'),
    'evidence': ('Read evidence by its issued ID', 'evidence_id', 'page'),
    'precedent': ('Find earlier payee or account examples and owner decisions', None, None),
    'period_checks': ('Check duplicates, balances and statements for the pinned period', None, None),
}
NAMES = {'review_' + name for name in TOOLS}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def git(root, *args, limit=SOURCE):
    require_repository(root)
    return bounded_read(['git', '--git-dir=' + str(root / '.git'), '--literal-pathspecs', *args], root, limit)


def committed_snapshot(root, work, revision, *, audit_only=False):
    target = work / 'snapshot'
    info = read_jj(root, 'log', '-r', revision + ' & ::main', '--no-graph', '-T', 'commit_id', limit=256)
    if info != revision:
        raise ValueError('revision_unavailable')
    target.mkdir()
    budget = 128 * 1024 * 1024
    paths = ('--', 'data/additions', 'ledger.json') if audit_only else ()
    for row in git(root, 'ls-tree', '-rz', revision, *paths).split(b'\0'):
        metadata, _, name = row.partition(b'\t')
        if not metadata.startswith((b'100644 blob ', b'100755 blob ')):
            continue
        path = name.decode()
        if path not in ('ledger.json', 'AGENTS.md', 'data/prices/mutual-funds.prices') and not re.fullmatch(
                r'(?:data/(?:expenses/|statements/)?[^/]+|extensions/[^/]+)\.json|data/additions/\d{4}\.jsonl', path):
            continue
        data = git(root, 'cat-file', 'blob', metadata.split()[2].decode(), limit=budget)
        budget -= len(data)
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    if not (target / 'ledger.json').is_file():
        raise ValueError('snapshot_unavailable')
    return target


@dataclass(frozen=True)
class ReviewGrant:
    snapshot: str
    revision: str
    lines: tuple[str, ...]
    rules: tuple[str, ...]
    evidence: dict
    expires: float
    key: str


def issue_grant(root, work, revision, lines, evidence=None, *, rules=(), timeout=180, snapshot=None):
    if not re.fullmatch(COMMIT, revision):
        raise ValueError('invalid_revision')
    if max(len(lines), len(rules)) > 3:
        raise ValueError('batch_limit')
    expires = time.time() + min(timeout, 180)
    snapshot = snapshot or committed_snapshot(root, work, revision)
    evidence = json.loads(encode(evidence or {}))
    scoped = {row['id']: row for row in records(snapshot) if row['id'] in lines}
    if len(scoped) != len(lines):
        raise ValueError('invalid_batch_lines')
    for row in scoped.values():
        paths = evidence_paths(row)
        sources = {path: revision for path in sorted(paths)}
        if row['kind'] == 'change' and (info := recorded_revision(root, revision, row)):
            for change in row['paths']:
                if change['path'].startswith('Bills/'):
                    sources[change['path']] = info['parents'][0] if change['status'] == 'removed' else info['commit']
                if change.get('source', '').startswith('Bills/'):
                    sources[change['source']] = info['parents'][0]
        for path, commit in sources.items():
            handle = hashlib.sha256(encode([row['id'], path]).encode()).hexdigest()[:32]
            evidence.setdefault(handle, {'path': path, 'line': row['id'], 'revision': commit})
    for source in evidence.values():
        evidence_path(source['path'])
        historical = source.get('revision', revision)
        if historical and (not re.fullmatch(COMMIT, historical) or read_jj(
                root, 'log', '-r', f'{historical} & ::{revision}', '--no-graph', '-T', 'commit_id', limit=256) != historical):
            raise ValueError('invalid_evidence_revision')
    return ReviewGrant(str(snapshot), revision, tuple(lines), tuple(rules), evidence, expires, secrets.token_hex(32))


def probe_grant(root):
    return ReviewGrant(str(root), '', (), (), {}, time.time()+180, secrets.token_hex(32))


def evidence_path(value):
    parts = value.split('/')
    if len(parts) < 2 or parts[0] != 'Bills' or any(p in ('', '.', '..') or '\\' in p or '\0' in p for p in parts):
        raise ValueError('invalid_evidence_path')
    return Path(*parts)



def source_records(line):
    fields = ('files', 'statement', 'before', 'after', 'bill', 'rule_changes', 'entries', 'rules', 'details')
    return [line[key] for key in fields if key in line]


def evidence_paths(row):
    value = source_records(row)
    return {path for key, many in [('filed_path', False), ('source', False), ('evidence', True), ('sources', True)]
            for path in identifiers(value, key, many=many) if path.startswith('Bills/')}


def identifiers(value, key, *, many=False):
    if isinstance(value, dict):
        for name, child in value.items():
            if name == key and isinstance(child, str):
                yield child
            elif many and name == key and isinstance(child, list):
                yield from (part for part in child if isinstance(part, str))
            elif isinstance(child, (dict, list)):
                yield from identifiers(child, key, many=many)
    elif isinstance(value, list):
        for child in value:
            yield from identifiers(child, key, many=many)


class ReviewLookup:
    def __init__(self, root, grant, visuals=None, receipts=None):
        self.root, self.grant = root, grant
        self.ledger = Path(grant.snapshot)
        self.visuals = visuals or {}
        self.discovered = set()
        self.receipts = receipts
        self.version = None

    @cached_property
    def lines(self):
        if not self.grant.lines:
            return {}
        index = {row['id']: row for row in records(self.ledger)}
        return {identity: index[identity] for identity in self.grant.lines}

    @cached_property
    def documents(self):
        return load(self.ledger)

    @cached_property
    def source_entries(self):
        from review_decisions import targets
        return set().union(*(targets(line)[0] for line in self.lines.values()))

    def registry(self):
        tools = {}
        for name, (description, identity, option) in TOOLS.items():
            args = {'cursor': {'type': 'string'}}
            if identity:
                args[identity] = {'type': 'string'}
            if option:
                args[option] = {'type': 'integer'}
            if name == 'precedent':
                args.update({key: {'type': 'string'} for key in ('entry_id', 'payee', 'account')})
            tools['review_' + name] = (getattr(self, name), description, args, [identity] if identity else [])
        return tools

    def precedent(self, store, entry_id=None, payee=None, account=None, cursor=None):
        from review_decisions import entry_issues

        def normalized(name):
            return re.sub(r'[^\w]+', ' ', name.casefold()).strip()

        def merchant(entry):
            return re.split(r'\s+[—–·]\s+', entry['description'], maxsplit=1)[0]

        if entry_id and entry_id not in self.source_entries | self.discovered:
            return {'status': 'outside_grant'}
        actual = {e['id']: e for e in store.VISIBLE}
        entry_id = entry_id or (next(iter(self.source_entries)) if len(self.source_entries) == 1 else None)
        anchor = actual.get(entry_id)
        if not payee and not account and anchor:
            payee = merchant(anchor)
        if not (payee or account) or account and account not in store.DECLARED:
            return {'status': 'invalid_arguments'}
        candidates = [e for e in actual.values() if e['id'] != entry_id and (not anchor or e['date'] <= anchor['date'])
                      and (not payee or normalized(merchant(e)) == normalized(payee))
                      and (not account or any(p['account'] == account for p in e['postings']))]
        ranked = store.search_entries(' '.join(filter(None, [payee, account])), limit=None, candidates=candidates)
        rows = records(self.ledger)
        by_entry, outcomes, matches, evidence = entry_issues(rows), {}, [], {}
        for row in rows:
            if 'review_issue_id' in row:
                outcomes.setdefault(row['review_issue_id'], []).append(row)
        for entry in ranked:
            history = []
            related = (r for issue in by_entry.get(entry['id'], ()) for r in outcomes.get(issue, ()))
            for row in sorted(related, key=lambda r: (r['time'], r['id'])):
                if row['kind'] == 'review-message' and row['actor'] != 'user':
                    continue
                item = {k: row[k] for k in ('id', 'kind', 'time', 'text', 'note', 'entry_id', 'rule_id') if k in row}
                item['evidence_ids'] = []
                for file in row.get('message_evidence', []):
                    handle = hashlib.sha256(file['path'].encode()).hexdigest()[:32]
                    evidence[handle] = file
                    item['evidence_ids'].append(handle)
                history.append(item)
            matches.append(dict(entry=entry, history=history))
        days = [e['date'] for e in ranked]
        result = self.page('precedent', [entry_id, payee, account],
                           dict(matches=matches, oldest=min(days, default=None), newest=max(days, default=None)), cursor)
        if result['status'] == 'available':
            self.discovered.update(e['id'] for e in ranked)
            self.grant.evidence.update(evidence)
        return result

    def period_checks(self, store, cursor=None):
        from check_store import check_card_statements
        from review import snapshot
        from strict_check import StrictCheckError, check
        if not self.lines:
            return {'status': 'outside_grant'}
        if not hasattr(self, '_period_report'):
            selected = [e for e in store.VISIBLE if e['id'] in self.source_entries]
            days = [e['date'] for e in selected] + [row['statement']['periodEnd'] for row in self.lines.values()
                    if row['kind'] == 'statement' and (row.get('statement') or {}).get('periodEnd')]
            days = days or [row['time'][:10] for row in self.lines.values()]
            start, end = min(days), max(days)
            mismatches, duplicates = [], {}

            def failure(kind, day, **details):
                where = 'period' if day and start <= day <= end else 'outside_period' if day else 'unknown'
                mismatches.append(dict(check=kind, scope=where, date=day, **details))

            try:
                check(self.documents, self.ledger)
            except StrictCheckError as exc:
                failure(exc.kind, (exc.refusal or {}).get('date'), balance=exc.refusal)
            try:
                statements = check_card_statements(self.documents, self.ledger)['statements']
            except ValueError as exc:
                days_in_error = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', str(exc))
                failure('statement_membership_or_balance', next(iter(days_in_error), None))
                statements = []
            state = snapshot(self.ledger, documents=self.documents, lines=records(self.ledger))
            for pair in state['items']:
                if pair['kind'] == 'duplicate' and any(start <= e['date'] <= end for e in pair['entries']):
                    ids = tuple(sorted(e['id'] for e in pair['entries']))
                    duplicates[ids] = pair['machineReason']
            for entry in selected:
                amount = state['entries'][entry['id']]['amount']
                for other in store.entries_near(entry['date'], amount=amount):
                    if other['id'] != entry['id']:
                        same_ref = bool(entry['references'] and entry['references'] == other.get('references'))
                        duplicates.setdefault(tuple(sorted((entry['id'], other['id']))),
                                              'matching_reference' if same_ref else 'matching_amount_near_date')
            self._period_report = dict(period=[start, end], mismatches=mismatches, statements=statements,
                                       suspected_duplicates=[dict(entries=ids, reason=why) for ids, why in sorted(duplicates.items())])
        return self.page('period_checks', [], self._period_report, cursor)

    def check(self):
        return {'status': 'expired_grant'} if time.time() >= self.grant.expires else None

    def continuation(self, tool, args, value, cursor):
        digest = hashlib.sha256(encode(value).encode()).hexdigest()
        binding = encode([tool, args, digest])
        def token(offset):
            mac = hmac.digest(bytes.fromhex(self.grant.key), (str(offset) + binding).encode(), 'sha256').hex()
            return f'{offset:x}.{mac}'
        if cursor is None:
            return 0, token
        if re.fullmatch(r'[0-9a-f]{1,12}\.[0-9a-f]{64}', cursor):
            offset = int(cursor.split('.')[0], 16)
            if hmac.compare_digest(cursor, token(offset)):
                return offset, token
        raise ValueError('invalid_cursor')

    def page(self, tool, args, value, cursor=None):
        # Chunk serialized JSON so even an oversized individual field can be read.
        raw = encode(value)
        self.version = hashlib.sha256(raw.encode()).hexdigest()
        try:
            offset, token = self.continuation(tool, args, value, cursor)
        except ValueError:
            return {'status': 'invalid_cursor'}
        end = min(len(raw), offset + PAGE // 2)
        while len(json.dumps(raw[offset:end]).encode()) > PAGE - 256:
            end = offset + (end - offset) * 3 // 4
        if self.receipts and end == len(raw):
            with self.receipts.open('a') as output:
                output.write(encode([tool, args]) + '\n')
        return {'status': 'available', 'data': raw[offset:end], 'next': token(end) if end < len(raw) else None}

    def pending(self, cursor=None, limit=3):
        if error := self.check():
            return error
        if not 1 <= limit <= 3:
            return {'status': 'invalid_arguments'}
        rows = [{k: row[k] for k in ('id', 'time', 'kind', 'outcome', 'entry_id', 'entry_ids', 'rule_id') if k in row}
                for row in self.lines.values()]
        for item in rows:
            source = self.lines[item['id']]
            item['evidence_ids'] = list(self.evidence_handles(source))
            if source['kind'] == 'statement':
                item['statement_id'] = next((s['id'] for s in self.documents[self.ledger/'data/card-statements.json']
                                             if statement_matches(source, s)), None)
        try:
            offset, token = self.continuation('pending', [limit], rows, cursor)
        except ValueError:
            return {'status': 'invalid_cursor'}
        end = offset + limit
        result = {'items': rows[offset:end], 'rules': list(self.grant.rules),
                  'next': token(end) if end < len(rows) else None}
        if len(encode(result).encode()) > PAGE:
            return {'status': 'page_limit', 'note': 'Read individual audit lines for all source handles.',
                    'items': [{'id': row['id']} for row in rows[offset:end]], 'next': result['next']}
        return result

    def evidence_handles(self, value):
        return {identity: {'label': source.get('label') or Path(source['path']).name, 'pages': source.get('pages')}
                for identity, source in self.grant.evidence.items()
                if source.get('line') == value.get('id') and 'line' in source
                or encode(source['path']) in encode(value)}

    def audit_line(self, line_id, cursor=None):
        if line_id not in self.lines:
            return {'status': 'outside_grant'}
        return self.page('audit_line', [line_id], dict(record=self.lines[line_id], evidence=self.evidence_handles(self.lines[line_id])), cursor)

    def entry(self, entry_id, cursor=None):
        changes = [change for line in self.lines.values()
                   for change in [line, *line.get('entries', [])] if change.get('entry_id', change.get('id')) == entry_id]
        if entry_id not in self.discovered and not changes and not any(
                entry_id in identifiers(source_records(line), 'entry-id') or entry_id in (line.get('entry_ids') or []) for line in self.lines.values()):
            return {'status': 'outside_grant'}
        item = next((e for e in entries(self.documents) if e['id'] == entry_id), None)
        accounts = self.documents[self.ledger/'data/accounts.json']['accounts']
        memberships = [s for s in self.documents[self.ledger/'data/card-statements.json']
                       if item and item.get('statement') and s['end'] == item['statement']['end']
                       and any(p['account'] == s['account'] for p in item['postings'])
                       or entry_id in identifiers(s, 'entry-id')]
        return self.page('entry', [entry_id], dict(entry=item, changes=changes, statements=memberships, evidence=self.evidence_handles(item or {}),
                         accounts={p['account']: accounts[p['account']] for p in item['postings']} if item else {}), cursor)

    def rule(self, rule_id, cursor=None):
        from recurring import payment_history, same_payment, varying_details
        from rules_service import digest, ignored_entries
        if rule_id not in self.grant.rules and not any(rule_id == line.get('rule_id') or
                any(x['id'] == rule_id for x in line.get('rule_changes', [])) for line in self.lines.values()):
            return {'status': 'outside_grant'}
        rule = next((r for r in self.documents[self.ledger/'data/forecast.json'] if r['id'] == rule_id), None)
        accounts = self.documents[self.ledger/'data/accounts.json']['accounts']
        lines = records(self.ledger)
        ignored = ignored_entries(lines)
        actual = [e for e in entries(self.documents) if e['id'] not in ignored]
        history = [line for line in lines if rule_id == line.get('rule_id') or
                   any(x['id'] == rule_id for x in line.get('rule_changes', []))]
        paid = [] if rule is None else payment_history(rule, actual, accounts) if rule['amount'] == 'varies' else [
            e for e in actual if (e.get('rule') or {}).get('id') == rule_id or same_payment(e, rule, accounts)]
        return self.page('rule', [rule_id], dict(rule=rule, history=history, evidence=self.evidence_handles(rule or {}),
                         settings=self.documents.get(self.ledger/'data/recurring-settings.json', {}),
                         **(dict(version=digest(rule), payments=paid,
                                 **varying_details(rule, actual, accounts)) if rule else {})), cursor)

    def statement(self, statement_id, cursor=None):
        from reconcile_statement import filed_details
        summaries = self.documents[self.ledger/'data/card-statements.json']
        summary = next((s for s in summaries if s['id'] == statement_id), None)
        if not summary or not any(statement_matches(line, summary)
                for line in self.lines.values()):
            return {'status': 'outside_grant'}
        result = filed_details(self.ledger, self.documents, summary)
        result['evidence'] = self.evidence_handles(summary)
        result['decisions'] = [line.get('decisions') for line in self.lines.values() if statement_matches(line, summary)]
        result['chain'] = [s for s in summaries if s['account'] == summary['account']]
        self.discovered.update(identifiers(result, 'entry_id'))
        return self.page('statement', [statement_id], result, cursor)

    def revision(self, line_id, parent_index=0, cursor=None):
        line = self.lines.get(line_id)
        if not line:
            return {'status': 'outside_grant'}
        if line['kind'] == 'baseline':
            return self.page('revision', [line_id, parent_index], line, cursor)
        info = recorded_revision(self.root, self.grant.revision, line)
        if not info:
            return {'status': 'revision_unavailable'}
        if not 0 <= parent_index < len(info['parents']):
            return {'status': 'invalid_parent'}
        patch = read_jj(self.root, 'diff', '--from', info['parents'][parent_index], '--to', info['commit'],
                        '--git', 'glob:data/** ~ glob:data/additions/** ~ glob:data/prices/** | glob:Bills/**', limit=SOURCE)
        return self.page('revision', [line_id, parent_index], dict(info, diff=patch, paths=line.get('paths'),
                         entries=line.get('entries'), rules=line.get('rules'), moved=bool(line.get('commit') and line['commit'] != info['commit'])), cursor)

    def source(self, path, revision):
        relative = evidence_path(path)
        if not revision:
            directory = os.open(self.root, os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for part in relative.parts[:-1]:
                    child = os.open(part, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    os.close(directory)
                    directory = child
                with os.fdopen(os.open(relative.name, os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory), 'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError('evidence_unavailable')
                    data = stream.read(SOURCE + 1)
            finally:
                os.close(directory)
        else:
            if not re.fullmatch(COMMIT, revision):
                raise ValueError('invalid_revision')
            listed = git(self.root, 'ls-tree', '-rz', '-l', revision, '--', path).rstrip(b'\0')
            metadata, _, name = listed.partition(b'\t')
            row = metadata.split()
            if name != path.encode() or len(row) != 4 or row[0] not in (b'100644', b'100755'):
                raise ValueError('evidence_unavailable')
            data = git(self.root, 'cat-file', 'blob', row[2].decode()) if int(row[3]) <= SOURCE else None
        return data if data is not None and len(data) <= SOURCE else None

    def evidence_pages(self, identity):
        """Available required pages, or None if any page cannot be supplied."""
        import pypdfium2 as pdfium
        source = self.grant.evidence[identity]
        visuals = {v['page'] for v in self.visuals.get(identity, [])}
        try:
            data = self.source(source['path'], source.get('revision', self.grant.revision))
            if data and data.startswith(b'%PDF'):
                with pdfium.PdfDocument(data) as pdf:
                    pages = source.get('pages', range(1, len(pdf)+1))
                    if not 0 < len(pdf) <= 100 or not pages:
                        return None
                    for index in set(pages) - visuals:
                        if not 1 <= index <= len(pdf):
                            return None
                        with closing(pdf[index-1]) as item, closing(item.get_textpage()) as text:
                            if not text.get_text_bounded().strip():
                                return None
                    return set(pages) - visuals
            return set() if visuals else None
        except (OSError, ValueError, pdfium.PdfiumError):
            return None

    def evidence(self, evidence_id, page=1, cursor=None):
        if evidence_id not in self.grant.evidence:
            return {'status': 'outside_grant'}
        import pypdfium2 as pdfium
        from PIL import Image, UnidentifiedImageError
        source = self.grant.evidence[evidence_id]
        path = source['path']
        label = source.get('label', Path(path).name)
        try:
            data = self.source(path, source.get('revision', self.grant.revision))
            if data is None:
                return {'status': 'oversized', 'label': label}
            result = dict(label=label, required=source.get('pages', 'all'), visuals=self.visuals.get(evidence_id, []))
            if data.startswith(b'%PDF'):
                with pdfium.PdfDocument(data) as pdf:
                    if not 0 < len(pdf) <= 100:
                        return {'status': 'oversized', 'label': label}
                    if not 1 <= page <= len(pdf):
                        return {'status': 'invalid_page'}
                    with closing(pdf[page-1]) as item, closing(item.get_textpage()) as text:
                        result.update(page=page, pages=len(pdf), text=text.get_text_bounded())
                        if not result['text'].strip() and not any(v['page'] == page for v in result['visuals']):
                            return {'status': 'visual_unavailable', 'label': label, 'page': page, 'pages': len(pdf)}
            elif not result['visuals']:
                with Image.open(io.BytesIO(data), formats=['PNG', 'JPEG', 'WEBP']) as image:
                    status = 'oversized' if image.width * image.height > PIXELS else 'visual_unavailable'
                return {'status': status, 'label': label}
        except Image.DecompressionBombError:
            return {'status': 'oversized', 'label': label}
        except UnidentifiedImageError:
            return {'status': 'unsupported', 'label': label}
        except pdfium.PdfiumError as exc:
            return {'status': 'password_required' if exc.err_code == 4 else 'evidence_unavailable', 'label': label}
        except (OSError, ValueError):
            return {'status': 'evidence_unavailable', 'label': label}
        return self.page('evidence', [evidence_id, page], result, cursor)

    def images(self, work):
        import pypdfium2 as pdfium
        from PIL import Image
        images, total = [], 0
        for identity, source in self.grant.evidence.items():
            if self.check() or len(images) == 8:
                break
            try:
                data = self.source(source['path'], source.get('revision', self.grant.revision))
                if not data:
                    continue
                if data.startswith(b'%PDF'):
                    with pdfium.PdfDocument(data) as pdf:
                        if not 0 < len(pdf) <= 100:
                            continue
                        with closing(pdf[0]) as first, closing(first.get_textpage()) as text:
                            default = [1] if text.get_text_bounded().strip() else range(1, len(pdf)+1)
                        renders = []
                        for i in source.get('pages', default)[:8-len(images)]:
                            if self.check():
                                break
                            if 1 <= i <= len(pdf):
                                renders.append((i, render(pdf, i)))
                else:
                    with Image.open(io.BytesIO(data), formats=['PNG', 'JPEG', 'WEBP']) as image:
                        if image.width * image.height > PIXELS:
                            continue
                        image.verify()
                        renders = [(1, (data, {'PNG':'png', 'JPEG':'jpg', 'WEBP':'webp'}[image.format]))]
                for page, (content, suffix) in renders:
                    if len(images) == 8 or total + len(content) > SOURCE:
                        break
                    target = work / f'review-image-{len(images)+1}.{suffix}'
                    target.write_bytes(content)
                    total += len(content)
                    images.append(target)
                    self.visuals.setdefault(identity, []).append(dict(page=page, image=len(images)))
            except (ValueError, OSError, SyntaxError, pdfium.PdfiumError, Image.DecompressionBombError):
                continue
        return tuple(images)


def recorded_revision(root, pinned, line):
    for candidate, pattern in ((line.get('commit'), COMMIT), (line.get('change_id'), '[k-z]{32}')):
        if candidate and re.fullmatch(pattern, candidate):
            result = read_jj(root, 'log', '-r', f'{candidate} & ::{pinned}', '--no-graph', '-T', IDENTITY, limit=SOURCE)
            if result:
                return json.loads(result)
    return None


def statement_matches(line, summary):
    source = line.get('statement') or {}
    return line['kind'] == 'statement' and (source.get('periodEnd'), source.get('account')) == (summary['end'], summary['account'])


def render(pdf, index):
    with closing(pdf[index-1]) as page:
        width, height = page.get_size()
        scale = min(2, int(PIXELS ** .5) / max(width, height))
        with closing(page.render(scale=scale)) as bitmap:
            output = io.BytesIO()
            bitmap.to_pil().save(output, format='PNG')
    return output.getvalue(), 'png'


def review_task(settings, work, grant, prompt, schema, deadline, *, prepared=None, **options):
    from adapters import get_adapter, require_isolation
    from adapters.base import LookupTools, Task
    from bookkeeping_policy import review_policy
    from ledger_manifest import read_manifest
    reviewer = settings.for_role('review')
    require_isolation(get_adapter(reviewer), reviewer)
    lookups = LookupTools.for_settings(reviewer, grant=grant, work=work, prepared=prepared)
    snapshot = Path(grant.snapshot)
    policy = review_policy(replace(reviewer, ledger=snapshot), fuel='fuel' in read_manifest(snapshot).get('extensions', {}))
    return Task('\n\n'.join((prompt, *policy)), lookups.images, lookups, schema, deadline, work=work, **options)
