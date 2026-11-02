"""Checked, locked ledger additions with a durable rollback journal."""
from __future__ import annotations

from copy import deepcopy
from jsonschema import validate, ValidationError
import base64
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata

from draft_time import saved_local
from settings import APPLICATION

sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
from add_entry import add_entry
from store import dump, load, store_paths
from ledger_manifest import read_manifest, module_settings
from account_metadata import infer_kind, is_balance_sheet
from ledger_lock import ledger_lock, LOCK_FD_ENV
sys.path.insert(0, str(APPLICATION / 'scripts/receipts'))
from compress_images import compress


class AccountError(ValueError):
    """An account decision requires a person rather than a repair retry."""


class ProposalError(ValueError):
    """An agent can correct this error; infrastructure errors must not be retried."""


def jj(root, *args):
    # PDF evidence may exceed jj's default 1 MiB limit. Snapshot it, and
    # detect unrelated large files when checking for a clean working copy.
    return subprocess.check_output(['jj', '--config', 'snapshot.max-new-file-size=0', '-R', str(root), *args], cwd=root, text=True, stderr=subprocess.PIPE).strip()


def clean(root):
    return not jj(root, 'diff', '--summary')


def reject_invisible(value, field='answer'):
    if isinstance(value, str) and any(unicodedata.category(c) in ('Cc', 'Cf') for c in value):
        raise ProposalError(f'{field}: invisible or control character')
    if isinstance(value, dict):
        for key, item in value.items():
            reject_invisible(item, f'{field}.{key}')
    elif isinstance(value, list):
        for item in value:
            reject_invisible(item, field)


def amount(text):
    return text + '.00' if re.fullmatch(r'-?(0|[1-9][0-9]*)', text) else text


def to_store(answer, root, file_count):
    answer = deepcopy(answer)
    reject_invisible(answer)
    if len(answer['attachment_names']) != file_count:
        raise ProposalError('attachment_names must have one name per attachment')
    a = answer['entry']
    if not module_settings(read_manifest(root))['fuel'] and any(k in a for k in ('fuel_filled_litres', 'trip_km', 'fuel-filled-litres', 'trip-km')):
        raise ProposalError('Fuel fields are disabled by ledger.json modules.fuel')
    try:
        date.fromisoformat(a['date'])
    except ValueError as exc:
        raise ProposalError('Invalid entry date') from exc
    entry = {key: a[key] for key in ('date', 'status', 'description')}
    for key, field in (('order_id', 'order-id'), ('reference', 'reference'), ('classification', 'classification'),
                       ('fuel_filled_litres', 'fuel-filled-litres'), ('trip_km', 'trip-km')):
        if a.get(key):
            entry[field] = a[key]
    if a.get('original_amount'):
        entry['original-amount'] = {**a['original_amount'], 'amount': amount(a['original_amount']['amount'])}
    if a['notes']:
        entry['notes'] = a['notes']
    entry['postings'] = []
    for p in a['postings']:
        posting = {**{k: p[k] for k in ('account', 'commodity')}, 'amount': amount(p['amount'])}
        if p['cost']:
            posting['cost'] = {**p['cost'], 'amount': amount(p['cost']['amount']), 'per': 'total'}
        entry['postings'].append(posting)
    declared = load(root)[root / 'data/accounts.json']
    existing = set(declared['accounts'])
    paths = {':'.join(a.split(':')[:n]) for a in existing for n in range(1, len(a.split(':')) + 1)}
    new = sorted(set(answer['new_accounts']) - existing)
    for account in new:
        if not all(part.strip() == part and part for part in account.split(':')):
            raise AccountError(f'Invalid new account: {account}')
    accounts = dict(declared['accounts'])
    for account in new:
        metadata = {}
        if is_balance_sheet(account):
            explicit = answer.get('account_metadata', {}).get(account)
            kind = explicit.get('kind') if explicit else infer_kind(account)
            if kind is None:
                raise AccountError(f'Cannot infer kind for new account: {account}; choose a conventional account or declare its metadata in Accounts')
            currencies = {p['commodity'] for p in entry['postings'] if p['account']==account}
            metadata = explicit if explicit else {'kind': kind, 'currency': next(iter(currencies)) if len(currencies)==1 else read_manifest(root)['defaultCurrency']}
        accounts[account] = metadata
    payments = [p for p in entry['postings'] if accounts.get(p['account'], {}).get('currency') and Decimal(p['amount']) < 0]
    funding = next((p for p in payments if accounts[p['account']].get('kind') not in ('investment', 'person')), payments[0] if payments else None)
    if funding:
        currency = accounts[funding['account']]['currency']
        if entry.get('original-amount', {}).get('commodity') == currency:
            if Decimal(entry['original-amount']['amount']) == abs(Decimal(funding['amount'])):
                entry.pop('original-amount')
            else:
                raise ProposalError(f'original-amount must differ from the paying account currency {currency}')
        for p in entry['postings']:
            if p['account'].startswith('Expenses:') and p['commodity'] != currency:
                if not p.get('cost') or p['cost']['commodity'] != currency or Decimal(p['cost']['amount']) <= 0:
                    raise ProposalError(f'Expense in {p["commodity"]} on {funding["account"]} ({currency}) needs the amount charged or a rate as a cost in {currency}; ask the user when neither is known')
    unknown = sorted({p['account'] for p in entry['postings']} - set(accounts))
    if unknown:
        raise AccountError('Declare the proposed accounts: ' + ', '.join(unknown))
    supported = json.loads((APPLICATION / 'schema/currencies.json').read_text())
    for p in entry['postings']:
        unit_holding=accounts[p['account']].get('kind')=='investment' and p['commodity'] not in supported
        expected = accounts[p['account']].get('currency')
        if expected and expected != p['commodity'] and not unit_holding:
            raise AccountError('The posting must use its account currency '+expected+': ' + p['account'])
    commodities = list(declared['commodities'])
    supported = json.loads((APPLICATION / 'schema/currencies.json').read_text())
    registered = {directive.split()[0].strip(chr(34)) for directive in commodities}
    for posting in entry['postings']:
        for code in (posting['commodity'], posting.get('cost', {}).get('commodity')):
            if code in supported and code not in registered:
                commodities.append(code)
                registered.add(code)
    return entry, {**declared, 'accounts': accounts, 'commodities': commodities}, new


def check(entry, declarations, root):
    # All validation happens on isolated store files. Evidence isn't filed yet.
    with tempfile.TemporaryDirectory(prefix='expense-check-') as tmp:
        fixture = Path(tmp)
        for path in store_paths(root):
            dest = fixture / path.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
        shutil.copyfile(root / 'ledger.json', fixture / 'ledger.json')
        dump(fixture / 'data/accounts.json', declarations)
        candidate = {k: v for k, v in entry.items() if k != 'sources'}
        try:
            add_entry(candidate, root=fixture, dry_run=True)
        except (ValueError, TypeError, KeyError) as exc:
            raise ProposalError(str(exc)) from exc


def safe_name(name):
    name = re.sub(r'[^\w-]+', '-', unicodedata.normalize('NFKC', name), flags=re.UNICODE)
    return name.strip('-_')[:140] or 'Evidence'


def evidence_paths(answer, draft, root):
    folder = root / 'Bills' / answer['entry']['date']
    if not folder.resolve().is_relative_to((root / 'Bills').resolve()):
        raise ValueError('Evidence directory escapes Bills')
    paths, used = [], set()
    for base, file in zip(answer['attachment_names'], draft['files']):
        name, suffix = safe_name(base), file['extension']
        target = folder / (name + suffix)
        number = 2
        while target.exists() or target.is_symlink() or target in used:
            target = folder / f'{name}-{number}{suffix}'
            number += 1
        used.add(target)
        paths.append(target)
    return paths


def payment(entry):
    postings = [p for p in entry['postings'] if is_balance_sheet(p['account'])
                and Decimal(p['amount']) < 0]
    if not postings:
        postings = [p for p in entry['postings'] if Decimal(p['amount']) < 0]
    return {'total': ', '.join(f"{abs(Decimal(p['amount']))} {p['commodity']}" for p in postings),
            'payment_account': ', '.join(p['account'] for p in postings)}


def message_for(settings, draft, entry, new, answer):
    quote = lambda text: json.dumps(text, ensure_ascii=False)
    paid = payment(entry)
    lines = [f"Add {entry['description']} via dashboard", '', f"Typed: {quote(draft['text'])}",
             f'Saved: {saved_local(draft)}',
             f"Total: {paid['total']}, {paid['payment_account']}",
             'Evidence: ' + ', '.join(entry.get('sources', []))]
    lines += [f"Asked: {quote(q['question'])} Answered: {quote(q['answer'])}" for q in draft['questions'] if q.get('answer') is not None]
    # Escape line breaks so review metadata always remains one physical line.
    one_line = lambda text: text.replace('\n', '\\n').replace('\r', '\\r')
    lines += ['Agent doubts: ' + (one_line('; '.join(answer['doubts'])) or 'none'),
              'New accounts: ' + (', '.join(new) or 'none'), f"Draft: {draft['id']}",
              ('Added-by: user (usual payment, rule ' + draft['usual_payment']['rule_id'] + ')' if draft.get('usual_payment') else 'Added-by: user (finished by hand: ' + one_line(draft['finished_by']['reason']) + ')' if draft.get('finished_by') else f'Added-by: dashboard agent ({settings.model}, {settings.effort})')]
    return '\n'.join(lines)


class Transaction:
    def __init__(self, settings, drafts, draft):
        self.settings, self.drafts, self.draft = settings, drafts, draft
        self.root = settings.ledger
        self.journal = drafts.folder(draft['id']) / 'transaction.json'
        self.marker = 'Draft: ' + draft['id']

    def save_journal(self, journal):
        tmp = self.journal.with_suffix('.tmp')
        with tmp.open('w') as handle:
            json.dump(journal, handle)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(self.journal)

    def snapshot(self, targets):
        files = {}
        directories = set()
        for path in targets:
            if path.is_symlink():
                raise ValueError(f'Refusing to replace symlink: {path}')
            info = path.stat() if path.exists() else None
            files[str(path.relative_to(self.root))] = None if info is None else {
                'bytes': base64.b64encode(path.read_bytes()).decode(), 'mode': info.st_mode & 0o7777,
                'atime': info.st_atime_ns, 'mtime': info.st_mtime_ns}
            parent = path.parent
            while not parent.exists():
                directories.add(str(parent.relative_to(self.root)))
                parent = parent.parent
        journal = {'files': files, 'directories': sorted(directories),
                   'operation': jj(self.root, 'op', 'log', '--limit', '1', '--no-graph', '-T', 'id'),
                   'parent': jj(self.root, 'log', '-r', '@-', '--no-graph', '-T', 'commit_id'),
                   'result': None}
        self.save_journal(journal)
        return journal

    def rollback(self, journal):
        # Restore repository state too, including a commit/bookmark made just before
        # an interrupted response. Lock-holding writers cannot interleave here.
        jj(self.root, 'op', 'restore', journal['operation'])
        for rel, original in journal['files'].items():
            path = self.root / rel
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(base64.b64decode(original['bytes']))
                os.chmod(path, original['mode'])
                os.utime(path, ns=(original['atime'], original['mtime']))
        for rel in sorted(journal['directories'], key=len, reverse=True):
            path = self.root / rel
            if path.exists():
                path.rmdir()
        jj(self.root, 'status')
        self.journal.unlink()

    def recover(self):
        if not self.journal.exists():
            return None
        journal = json.loads(self.journal.read_text())
        revision = 'description(substring:' + json.dumps(self.marker) + ') & ancestors(@)'
        committed = jj(self.root, 'log', '-r', revision, '--no-graph', '-T', 'commit_id ++ "\n"').splitlines()
        if journal['result'] and committed:
            result = {**journal['result'], 'commit_id': committed[0]}
            self.draft['committed_result'] = result
            self.drafts.put(self.draft)
            self.journal.unlink()
            return result
        parent = jj(self.root, 'log', '-r', '@-', '--no-graph', '-T', 'commit_id')
        changed = jj(self.root, 'diff', '--name-only').splitlines()
        if parent != journal['parent'] or set(changed) - journal['files'].keys():
            raise RuntimeError('Interrupted transaction has later human changes; preserve transaction.json and reconcile manually')
        self.rollback(journal)
        return None

    def verified_commit(self, lock_fd, message):
        """Caller owns lock and rollback snapshot; strict verification is mandatory."""
        env = {**os.environ, 'LEDGER_DIR': str(self.root), LOCK_FD_ENV: str(lock_fd),
               'EXPENSES_COMMIT_MESSAGE': message}
        completed = subprocess.run(['make', 'commit'], cwd=self.settings.application, env=env,
                                   pass_fds=(lock_fd,), text=True, capture_output=True)
        if completed.returncode:
            raise RuntimeError('Ledger commit failed verification: ' + (completed.stdout+completed.stderr)[-5000:])
        return jj(self.root, 'log', '-r', '@-', '--no-graph', '-T', 'commit_id')

    def prepare(self, answer):
        """Pure preview: schema, conversion, declarations, strict hledger in scratch.

        Caller holds the draft mutex when retaining the result. Apply repeats this
        under the ledger lock; consent binds the complete wire proposal.
        """
        from agent import schema_for
        from proposal_consent import proposal_hash
        wire = {key:value for key,value in answer.items() if key!='account_metadata'} if self.draft.get('hand_form') or self.draft.get('finished_by') else answer
        try: validate(wire, schema_for(self.settings))
        except ValidationError as exc:
            raise ProposalError('Please check '+'.'.join(str(p) for p in exc.path)+': '+exc.message) from None
        if 'account_metadata' in answer and (not isinstance(answer['account_metadata'],dict) or any(not isinstance(v,dict) for v in answer['account_metadata'].values())):
            raise AccountError('New account metadata must map account names to kind and currency')
        if answer['status'] != 'ready' or not answer['entry']:
            raise ProposalError('A complete ready entry is required')
        entry, declarations, new = to_store(answer, self.root, len(self.draft['files']))
        check(entry, declarations, self.root)
        existing = load(self.root)[self.root/'data/accounts.json']['accounts']
        roots = {a.split(':')[0] for a in existing}
        return dict(entry=entry, declarations=declarations, new_accounts=new,
                    new_roots=[a for a in new if a.split(':')[0] not in roots],
                    proposal_hash=proposal_hash(answer))

    def apply(self, answer, lock_fd):
        from rules_service import policy
        policy(self.settings)
        prepared = self.prepare(answer)
        entry, declarations, new = prepared['entry'], prepared['declarations'], prepared['new_accounts']
        required = new if self.draft.get('finished_by') else prepared['new_roots']
        consent = self.draft.get('account_consent')
        if required and (not consent or consent['proposal_hash'] != prepared['proposal_hash'] or set(required)-set(consent['accounts'])):
            raise AccountError('Approve the exact proposal before creating these accounts')
        if answer['entry'].get('original_amount') and not entry.get('original-amount'):
            notes = self.draft['runs'][-1].setdefault('normalisations', []) if self.draft['runs'] else self.draft.setdefault('normalisations', [])
            note = 'Dropped original_amount equal to the payment in the paying account currency'
            if note not in notes: notes.append(note)
        targets = evidence_paths(answer, self.draft, self.root)
        if targets:
            entry['sources'] = [str(p.relative_to(self.root)) for p in targets]
        from outcomes import draft_event
        from audit import append, audit_path
        audit_record = draft_event(self.draft, 'added')
        journal = self.snapshot([audit_path(self.root, audit_record), *store_paths(self.root), self.root / 'data/expenses' / (entry['date'][:4] + '.json'),
                                 self.root / 'expense_ledger.ledger', *targets])
        try:
            for file, target in zip(self.draft['files'], targets):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.drafts.folder(self.draft['id']) / 'files' / file['name'], target)
                compress(target)
            dump(self.root / 'data/accounts.json', declarations)
            identity = add_entry(entry, root=self.root)
            from rules_service import addition_effects
            recurring = addition_effects(self.root, identity, self.draft)
            result = dict(outcome='ready', entry_id=identity, description=entry['description'],
                          new_accounts=new, sources=entry.get('sources', []), **payment(entry))
            if recurring['rule'] or recurring['resume_rules']:
                result.update(recurring, weekly_review_note='The weekly review will look at this payment.')
                result['audit_id'] = audit_record['id']
            if recurring['rule'] or recurring['resume_rules']:
                audit_record['rule_changes'] = recurring['rule_changes']
            if self.draft.get('usual_payment'):
                audit_record['usual_payment'] = deepcopy(self.draft['usual_payment'])
                result['usual_payment'] = deepcopy(self.draft['usual_payment'])
            audit_record['entry_id'] = identity
            audit_record['files'] = [dict(original_name=f['original_name'], **({'filed_path': result['sources'][i]} if i < len(result['sources']) else {})) for i, f in enumerate(self.draft['files'])]
            append(self.root, audit_record)
            journal['result'] = result
            self.save_journal(journal)
            result['commit_id'] = self.verified_commit(lock_fd, message_for(self.settings, self.draft, entry, new, answer))
            # Persist the receipt before the journal is removed.
            self.draft['committed_result'] = result
            self.drafts.put(self.draft)
            self.journal.unlink()
            return result
        except BaseException:
            self.rollback(journal)
            raise


def refresh(settings, result):
    try:
        run = subprocess.run(['npm', '--prefix', 'review-dashboard', 'run', 'data'],
                             cwd=settings.application, env={**os.environ, 'LEDGER_DIR': str(settings.ledger)},
                             text=True, capture_output=True, timeout=120)
        if run.returncode:
            raise RuntimeError((run.stdout + run.stderr)[-3000:])
        result['data_stale'] = False
    except Exception as exc:
        result.update(data_stale=True, refresh_error=str(exc))
    return result
