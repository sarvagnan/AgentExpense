"""Checked, locked ledger additions with a durable rollback journal."""
from __future__ import annotations

from copy import deepcopy
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
import save_timing

sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
from add_entry import add_entry
from confirmed_balances import BalanceRefusal
from store import copy_settings, dump, empty_entry, load, posting, store_paths, entries
from ledger_manifest import read_manifest
import account_metadata
from account_metadata import infer_kind, is_balance_sheet
from ledger_lock import ledger_lock, LOCK_FD_ENV
from node_toolchain import node_environment
from dashboard_data import check_source
sys.path.insert(0, str(APPLICATION / 'scripts/receipts'))
from compress_images import compress


class AccountError(ValueError):
    """An account decision requires a person rather than a repair retry."""


class LedgerBusy(ValueError):
    """A checked operation may retry after unrelated ledger work finishes."""


class ProposalError(ValueError):
    """An agent can correct this error; infrastructure errors must not be retried."""


def jj(root, *args):
    # PDF evidence may exceed jj's default 1 MiB limit. Snapshot it, and
    # detect unrelated large files when checking for a clean working copy.
    with save_timing.step('jj'):
        return subprocess.check_output(['jj', '--config', 'snapshot.max-new-file-size=0', '-R', str(root), *args], cwd=root, text=True, stderr=subprocess.PIPE).strip()


def clean(root):
    return not jj(root, 'diff', '--summary')


def command_failure(exc):
    """One line for a failed command: what ran, its status and its last error line."""
    output = exc.stderr if isinstance(exc.stderr, str) and exc.stderr.strip() else exc.output if isinstance(exc.output, str) else ''
    last = output.strip().splitlines()[-1] if output and output.strip() else 'no output'
    command = exc.cmd if isinstance(exc.cmd, (list, tuple)) else [str(exc.cmd)]
    name = ' '.join(str(part) for part in command if str(part) not in ('--config', 'snapshot.max-new-file-size=0'))
    return f'{name} exited {exc.returncode}: {last}'


def reject_invisible(value, field='answer'):
    if isinstance(value, str) and any(unicodedata.category(c) in ('Cc', 'Cf') for c in value):
        raise ProposalError(f'{field}: invisible or control character')
    if isinstance(value, dict):
        for key, item in value.items():
            reject_invisible(item, f'{field}.{key}')
    elif isinstance(value, list):
        for item in value:
            reject_invisible(item, field)


def to_store(answer, root, file_count):
    """The format 2 entry an answer or form describes, with the declarations it needs and its new accounts."""
    from answers import entry_extensions
    answer = deepcopy(answer)
    reject_invisible(answer)
    if len(answer['attachment-names']) != file_count:
        raise ProposalError('attachment-names must have one name per attachment')
    a = answer['entry']
    entry = empty_entry(root, date=a['date'], status=a['status'], description=a['description'], references=a['references'],
                        classification=a['classification'], **{'original-amount': a['original-amount']},
                        extensions=entry_extensions(root, a.get('extensions')), notes=list(a['notes']),
                        postings=[posting(p['account'], p['amount'], p['commodity'],
                                          cost={**p['cost'], 'per': 'total'} if p['cost'] else None) for p in a['postings']])
    declared = load(root)[root / 'data/accounts.json']
    existing = set(declared['accounts'])
    new = sorted(set(answer['new-accounts']) - existing)
    for account in new:
        if not all(part.strip() == part and part for part in account.split(':')):
            raise AccountError(f'Invalid new account: {account}')
    accounts = dict(declared['accounts'])
    for account in new:
        metadata = account_metadata.empty(account)
        if is_balance_sheet(account):
            explicit = answer.get('account-metadata', {}).get(account)
            kind = explicit.get('kind') if explicit else infer_kind(account)
            if kind is None:
                raise AccountError(f'Cannot infer kind for new account: {account}; choose a conventional account or declare its metadata in Accounts')
            currencies = {p['commodity'] for p in entry['postings'] if p['account']==account}
            metadata = {**metadata, 'kind': kind, 'currency': explicit['currency'] if explicit else next(iter(currencies)) if len(currencies)==1 else read_manifest(root)['defaultCurrency']}
        accounts[account] = metadata
    payments = [p for p in entry['postings'] if accounts.get(p['account'], {}).get('currency') and Decimal(p['amount']) < 0]
    funding = next((p for p in payments if accounts[p['account']].get('kind') not in ('investment', 'person')), payments[0] if payments else None)
    if funding:
        currency = accounts[funding['account']]['currency']
        if (entry['original-amount'] or {}).get('commodity') == currency:
            if Decimal(entry['original-amount']['amount']) == abs(Decimal(funding['amount'])):
                entry['original-amount'] = None
            else:
                raise ProposalError(f'original-amount must differ from the paying account currency {currency}')
        for p in entry['postings']:
            if p['account'].startswith('Expenses:') and p['commodity'] != currency:
                if not p['cost'] or p['cost']['commodity'] != currency or Decimal(p['cost']['amount']) <= 0:
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
    registered = {directive.split()[0].strip(chr(34)) for directive in commodities}
    for posting_ in entry['postings']:
        for code in (posting_['commodity'], (posting_['cost'] or {}).get('commodity')):
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
        copy_settings(root, fixture)
        dump(fixture / 'data/accounts.json', declarations)
        candidate = {**entry, 'evidence': []}
        try:
            add_entry(candidate, root=fixture, dry_run=True)
        except BalanceRefusal:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise ProposalError(str(exc)) from exc


def safe_name(name):
    name = re.sub(r'[^\w-]+', '-', unicodedata.normalize('NFKC', name), flags=re.UNICODE)
    return name.strip('-_')[:140] or 'Evidence'


def evidence_paths(answer, draft, root):
    folder = root / 'Bills' / answer['entry']['date']
    if (root/'Bills').is_symlink() or folder.is_symlink() or not folder.resolve().is_relative_to((root / 'Bills').resolve()):
        raise ValueError('Evidence directory escapes Bills')
    paths, used = [], set()
    for base, file in zip(answer['attachment-names'], draft['files']):
        name, suffix = safe_name(base), file['extension']
        target = folder / (name + suffix)
        number = 2
        while target.exists() or target.is_symlink() or target in used:
            target = folder / f'{name}-{number}{suffix}'
            number += 1
        used.add(target)
        paths.append(target)
    return paths


def payment(entry, accounts=None):
    # Allocation/category credits are never payment legs. Refunds and repayments
    # with no outflow name the incoming balance-sheet account once.
    balance = [p for p in entry['postings'] if is_balance_sheet(p['account']) and (accounts.get(p['account'],{}).get('kind') if accounts is not None else infer_kind(p['account'])) in ('bank','card','cash','wallet','loan')]
    postings = [p for p in balance if Decimal(p['amount']) < 0]
    if not postings: postings = [p for p in balance if Decimal(p['amount']) > 0]
    totals = {}
    for p in postings: totals[p['commodity']] = totals.get(p['commodity'], Decimal(0)) + abs(Decimal(p['amount']))
    return {'total': ', '.join(f"{value} {code}" for code,value in totals.items()),
            'payment_account': ', '.join(dict.fromkeys(p['account'] for p in postings))}


def message_for(settings, draft, entry, new, answer):
    quote = lambda text: json.dumps(text, ensure_ascii=False)
    paid = payment(entry,load(settings.ledger)[settings.ledger/'data/accounts.json']['accounts'])
    lines = [f"Add {entry['description']} via dashboard", '', f"Typed: {quote(draft['text'])}",
             f'Saved: {saved_local(draft)}',
             f"Total: {paid['total']}, {paid['payment_account']}",
             'Evidence: ' + ', '.join(entry['evidence'])]
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
        completed = save_timing.make_commit(self.settings.application, env, lock_fd)
        if completed.returncode:
            raise RuntimeError('Ledger commit failed verification: ' + (completed.stdout+completed.stderr)[-5000:])
        return jj(self.root, 'log', '-r', '@-', '--no-graph', '-T', 'commit_id')

    def prepare(self, answer, *, person=None):
        """Pure preview: schema, conversion, declarations, strict hledger in scratch.

        Caller holds the draft mutex when retaining the result. Apply repeats this
        under the ledger lock; consent binds the complete wire proposal. ``person`` says
        whether ``answer`` is the person's own form (the hand form, a correction or a
        receipt reading) rather than the agent's answer; by default, whether the person
        finished the draft. Agent answers are always checked with the answer model.
        """
        with save_timing.step('prepare'):
            return self._prepare(answer, bool(self.draft.get('finished_by')) if person is None else person)

    def _prepare(self, answer, form):
        from answers import AnswerError, check as check_answer
        from proposal_consent import proposal_hash
        from answers import person_amounts
        if not form and 'account-metadata' in answer:
            raise AccountError('New account metadata belongs to the hand form')
        # The person's whole amounts are written as decimals; the consent hash stays on what they sent.
        checked = person_amounts(answer) if form else answer
        try: check_answer(checked, self.root, form=form)
        except AnswerError as exc:
            raise ProposalError(str(exc)) from None
        if checked['status'] != 'ready' or not checked['entry']:
            raise ProposalError('A complete ready entry is required')
        entry, declarations, new = to_store(checked, self.root, len(self.draft['files']))
        if self.draft.get('correction'):
            from corrections import prepare_replacement
            prepare_replacement(self.settings, self.draft, entry, declarations)
        else:
            check(entry, declarations, self.root)
        existing = load(self.root)[self.root/'data/accounts.json']['accounts']
        roots = {a.split(':')[0] for a in existing}
        return dict(entry=entry, declarations=declarations, new_accounts=new,
                    new_roots=[a for a in new if a.split(':')[0] not in roots],
                    proposal_hash=proposal_hash(answer))

    def publish(self, documents, record, result, lock_fd, message, *, copies=(), moves=(), removals=()):
        """Publish a replacement/merge through the same checked recovery boundary."""
        from audit import append, audit_path
        from replacements import check_documents
        with save_timing.step('prepare'):
            check_documents(documents, self.root)
        paths=[*store_paths(self.root), *documents, self.root/'expense_ledger.ledger', audit_path(self.root,record)]
        paths += [p for pair in moves for p in pair] + [target for source,target in copies] + list(removals)
        journal=self.snapshot(list(dict.fromkeys(paths)))
        try:
            for source,target in copies:
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,target);compress(target)
            for source,target in moves:
                target.parent.mkdir(parents=True,exist_ok=True)
                source.rename(target)
            for path in removals: path.unlink()
            for path,value in documents.items(): dump(path,value)
            append(self.root,record)
            journal['result']=result;self.save_journal(journal)
            result['commit_id']=self.verified_commit(lock_fd,message)
            self.draft['committed_result']=result;self.drafts.put(self.draft)
            self.journal.unlink()
            return result
        except BaseException:
            self.rollback(journal)
            raise

    def apply(self, answer, lock_fd, *, person=None):
        if self.draft.get('correction'): raise ValueError('Save this form through the correction service to keep the entry identity')
        from rules_service import policy
        policy(self.settings)
        prepared = self.prepare(answer, person=person)
        entry, declarations, new = prepared['entry'], prepared['declarations'], prepared['new_accounts']
        required = new if self.draft.get('finished_by') else prepared['new_roots']
        consent = self.draft.get('account_consent')
        if required and (not consent or consent['proposal_hash'] != prepared['proposal_hash'] or set(required)-set(consent['accounts'])):
            raise AccountError('Approve the exact proposal before creating these accounts')
        if answer['entry'].get('original-amount') and not entry['original-amount']:
            notes = self.draft['runs'][-1].setdefault('normalisations', []) if self.draft['runs'] else self.draft.setdefault('normalisations', [])
            note = 'Dropped original-amount equal to the payment in the paying account currency'
            if note not in notes: notes.append(note)
        targets = evidence_paths(answer, self.draft, self.root)
        entry['evidence'] = [str(p.relative_to(self.root)) for p in targets]
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
            entry=next(e for e in entries(load(self.root)) if e['id']==identity)
            result = dict(outcome='ready', entry_id=identity, description=entry['description'],
                          new_accounts=new, sources=entry['evidence'], **payment(entry,declarations['accounts']))
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
            if recurring.get('paid_bill'):
                from audit import event
                from rules_service import digest
                append(self.root,event('rule',rule_action='bill-paid',rule_id=recurring['rule']['id'],
                    request_id=self.draft['id'],request_hash=digest(dict(draft=self.draft['id'],bill=recurring['paid_bill'])),
                    rule_changes=recurring['rule_changes'],bill=recurring['paid_bill'],text=self.draft['text'],
                    files=audit_record['files'],doubts=audit_record['doubts'],entry_id=identity))
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
    with save_timing.step('data'):
        return _refresh(settings, result)


def _refresh(settings, result):
    try:
        check_source(settings.dashboard_data, settings.ledger)
        env = node_environment(settings.application)
        env.update(LEDGER_DIR=str(settings.ledger.resolve()), DASHBOARD_DATA_DIR=str(settings.dashboard_data))
        run = subprocess.run(['npm', '--prefix', 'review-dashboard', 'run', 'data'],
                             cwd=settings.application, env=env,
                             text=True, capture_output=True, timeout=120)
        if run.returncode:
            raise RuntimeError((run.stdout + run.stderr)[-3000:])
        result['data_stale'] = False
    except Exception as exc:
        result.update(data_stale=True, refresh_error=str(exc))
    return result
