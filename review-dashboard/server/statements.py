"""Durable statement sessions and one checked, rollback-capable ledger change."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
from pypdf import PdfReader, PdfWriter
from agent import now
from draft_time import client_context, ensure_context
from settings import APPLICATION
from transactions import Transaction, jj, clean, refresh
from ledger_lock import ledger_lock, LOCK_FD_ENV
from store import load, entries, dump, new_id, store_paths
from add_entry import add_entry
from audit import event, append, audit_path
sys.path.insert(0, str(APPLICATION / 'scripts/statements'))
from statement_pdf import extract_text, recognised, card_identifier, printed_currency, account_currency
from statement_rules import plan, convert
from reconcile_statement import select_account, reconciliation_path
from statement_readers import Registry, RowDateError, check_statement, signed, money_text, continuity_entries, movement_start
from ledger_manifest import read_manifest


def fingerprint(root):
    return sha256(b''.join(str(p.relative_to(root)).encode() + b'\0' + p.read_bytes() for p in [root/'ledger.json', *store_paths(root)])).hexdigest()


def statement_opening(documents, root, statement):
    """A first statement can establish an unused account's printed starting balance.

    This proposal lives only in the session until the user explicitly applies it.
    Existing activity must have its original opening/statement chain.
    """
    account = statement['account']
    if any(p['account']==account for e in entries(documents) for p in e['postings']):
        return None
    if any(s['account']==account for s in documents[root/'data/card-statements.json']):
        return None
    currency = statement['currency']
    balance = Decimal(statement['openingBalance']) if statement.get('kind') == 'bank' else -Decimal(statement['previousBalance'])
    opening_date = movement_start(statement, read_manifest(root).get('startDate'))
    if statement.get('kind') == 'bank':
        balance += sum((signed(r) for r in statement['rows'] if r['date'] < opening_date), Decimal(0))
    path = root/'data/expenses'/f"{opening_date[:4]}.json"
    used = {e['id'] for e in entries(documents)}
    row = dict(id=new_id(used), date=opening_date, status='confirmed', description='Statement starting balance',
               postings=[dict(account=account, amount=money_text(balance), commodity=currency, **{'assert':dict(amount=money_text(balance),commodity=currency)}),
                         dict(account='Equity:Opening Balances', amount=money_text(-balance), commodity=currency)])
    documents[root/'data/accounts.json']['accounts'].setdefault('Equity:Opening Balances',{})
    documents.setdefault(path,[]).append(row)
    return row


class StaleChecklist(ValueError):
    pass


RECOVERY_HOURS = 24


class Statements:
    def __init__(self, settings):
        self.settings, self.ledger = settings, settings.ledger
        self.root = self.ledger / 'drafts/statements'
        self.root.mkdir(parents=True, exist_ok=True)
        self.mutex = threading.RLock()
        self.changed = threading.Condition(self.mutex)
        self.in_flight = set()

    def folder(self, identity):
        if not re.fullmatch('[a-f0-9]{32}', identity):
            raise FileNotFoundError('Unknown statement session')
        path = self.root / identity
        if path.is_symlink():
            raise FileNotFoundError('Invalid statement session')
        return path

    def put(self, session, *, force=False):
        with self.mutex:
            folder = self.folder(session['id'])
            folder.mkdir(exist_ok=True)
            current = folder / 'session.json'
            if current.exists() and not force:
                prior = json.loads(current.read_text())
                if prior.get('generation', 0) != session.get('generation', 0) or prior.get('cancelled') and not session.get('cancelled'):
                    return
                # A reader may finish after a handover extension. Preserve its
                # recovery metadata rather than replacing it with an old snapshot.
                if prior.get('handover_until', '') > session.get('handover_until', ''):
                    session['handover_until'] = prior['handover_until']
                    session['handover_draft'] = prior['handover_draft']
            session['updated_at'] = now()
            temporary = folder / 'session.tmp'
            with temporary.open('w') as handle:
                json.dump(session, handle, ensure_ascii=False, allow_nan=False, indent=2)
                handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
            temporary.replace(folder / 'session.json')
            self.changed.notify_all()

    def get(self, identity, *, include_discarded=False):
        with self.mutex:
            session = json.loads((self.folder(identity) / 'session.json').read_text())
            if session.get('discarded_until') and not include_discarded:
                raise FileNotFoundError('Discarded statement session')
            return ensure_context(session)

    def all(self):
        with self.mutex:
            return [json.loads(p.read_text()) for p in self.root.glob('*/session.json')]

    def purge(self):
        with self.mutex:
            for s in self.all():
                completed = s['state'] in ('applied','already_filed') and datetime.fromisoformat(s.get('updated_at',s['created_at'])) <= datetime.now(timezone.utc)-timedelta(hours=RECOVERY_HOURS)
                discarded = s.get('discarded_until') and datetime.fromisoformat(s['discarded_until']) <= datetime.now(timezone.utc)
                if s.get('trashed_at'): continue
                if s['id'] not in self.in_flight and (completed or discarded):
                    shutil.rmtree(self.folder(s['id']))

    def create(self, data, original_name, upload_id=None, *, client_time=None, client_zone=None):
        context = client_context(client_time, client_zone)
        name = Path((original_name or 'Statement.pdf').replace('\\', '/')).name
        name = re.sub(r'[\x00-\x1f\x7f]', '', name)[:200]
        suffix = Path(name).suffix.lower()
        supported = {extension for reader in Registry(self.ledger).readers.values() for extension in reader.extensions}
        if suffix not in supported:
            raise ValueError('Choose a supported statement file: PDF, CSV, XLSX or XLS.')
        locked = False
        if suffix == '.pdf':
            if not data.startswith(b'%PDF-'):
                raise ValueError('This PDF could not be opened. Choose a valid statement PDF.')
            try:
                reader = PdfReader(io.BytesIO(data))
                locked = reader.is_encrypted and not reader.decrypt('')
                if not locked and not len(reader.pages):
                    raise ValueError()
            except Exception:
                raise ValueError('This PDF could not be opened. Choose a valid statement PDF.') from None
        session = dict(id=secrets.token_hex(16), state='locked' if locked else 'reading', original_name=name,
                       created_at=now(), queued_at=now(), decisions=[], result=None, error=None, waiting=None, runs=[], upload_id=upload_id, pdf_hash=sha256(data).hexdigest(), file_suffix=suffix, **context)
        with self.mutex:
            if upload_id and self.upload_marker(upload_id).exists():
                raise ValueError('This upload was cancelled. Choose the file again to start a new upload.')
            self.put(session)
            (self.folder(session['id']) / ('original'+suffix)).write_bytes(data)
            if suffix != '.pdf':
                (self.folder(session['id']) / ('read'+suffix)).write_bytes(data)
            elif not locked:
                self.open_pdf(session, reader)
        return session

    def remove_files(self, session):
        suffix = session.get('file_suffix','.pdf')
        for prefix in ('original','read'):
            (self.folder(session['id'])/(prefix+suffix)).unlink(missing_ok=True)

    def upload_marker(self, token):
        if not re.fullmatch('[a-f0-9]{32}', token):
            raise ValueError('Invalid upload token')
        folder = self.root / '.cancelled-uploads'
        folder.mkdir(exist_ok=True)
        return folder / token

    def cancel_upload(self, token):
        with self.mutex:
            self.upload_marker(token).touch()
            for s in self.all():
                if s.get('upload_id') == token and not s.get('discarded_until'):
                    self.discard(s['id'])

    def discard(self, identity):
        with self.mutex:
            s = self.get(identity)
            if s.get('apply_started') or (self.folder(identity)/'transaction.json').exists() or s['state']=='applying' and s.get('committed_result'):
                raise ValueError('The checked apply has started. It will finish or roll back safely.')
            if s['state'] == 'applying':
                s.update(state='review', waiting=None)
            s.update(cancelled=True, generation=s.get('generation', 0)+1,
                     discarded_until=(datetime.now(timezone.utc)+timedelta(hours=RECOVERY_HOURS)).isoformat())
            self.put(s, force=True)
            return s

    def restore(self, identity):
        with self.mutex:
            self.purge()
            s = self.get(identity, include_discarded=True)
            if not s.pop('discarded_until', None):
                raise ValueError('This session is not discarded.')
            s.update(cancelled=False, generation=s.get('generation', 0)+1)
            self.put(s, force=True)
            return s

    def cancel_apply(self, identity):
        with self.mutex:
            s = self.get(identity)
            if s['state'] != 'applying' or s.get('apply_started') or (self.folder(identity)/'transaction.json').exists() or s.get('committed_result'):
                raise ValueError('The checked apply has started or already finished. It cannot be cancelled.')
            s.update(state='review', waiting=None, generation=s.get('generation', 0)+1)
            self.put(s, force=True)
            return s

    def open_pdf(self, session, reader):
        # A password never enters metadata, logs, responses, a subprocess or disk.
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        with (self.folder(session['id']) / 'read.pdf').open('wb') as handle:
            writer.write(handle)
        session.update(state='reading', queued_at=now(), error=None)
        self.put(session)

    def unlock(self, identity, password):
        with self.mutex:
            session = self.get(identity)
            if session['state'] != 'locked':
                raise ValueError('This statement is not waiting for a password.')
            try:
                reader = PdfReader(self.folder(identity) / 'original.pdf')
                if not reader.decrypt(password):
                    raise ValueError()
                self.open_pdf(session, reader)
            except Exception:
                raise ValueError("That password didn't open the file. Try again.") from None
            return session

    def current_generation(self, session):
        try:
            latest = self.get(session['id'], include_discarded=True)
        except FileNotFoundError:
            return False
        return not latest.get('cancelled') and latest.get('generation', 0) == session.get('generation', 0)

    def read(self, session, *, cancel=None):
        if not self.current_generation(session):
            return
        suffix = session.get('file_suffix', '.pdf')
        path = self.folder(session['id']) / ('read' + suffix)
        from statement_reading import safe_source
        safe_source(self.ledger, path)
        registry = Registry(self.ledger)
        metadata = load(self.ledger)[self.ledger/'data/accounts.json']['accounts']
        assigned = session.get('assigned_account')
        if assigned and session.get('reader_config'):
            config = deepcopy(session['reader_config'])
            expected = metadata[assigned].get('statement',{}).get('identifier')
            if expected:
                config['identifier'] = expected
            metadata[assigned] = {**metadata[assigned], 'statement':config}
        if session.get('agent_requested'):
            return self.read_agent(session, cancel=cancel)
        if suffix == '.pdf' and (not assigned or metadata[assigned].get('statement', {}).get('reader', 'example-card-pdf') == 'example-card-pdf'):
            # Keep the example PDF's identifier and mismatch behavior. Personal
            # PDF readers take precedence only when configured and recognised.
            configured = {m.get('statement',{}).get('reader') for m in metadata.values()}
            personal = [r for r in registry.readers.values() if not assigned and r.personal and r.id in configured and r.recognise(path)['confidence'] > 0]
            if not personal:
                return self.read_card(session, cancel=cancel)
        documents = load(self.ledger)
        candidates = []
        for account, meta in metadata.items():
            config = meta.get('statement', {})
            if not config.get('reader') or assigned and account != assigned:
                continue
            reader = registry.get(config['reader'])
            if suffix not in reader.extensions or meta.get('kind') not in reader.kinds:
                continue
            recognised_file = reader.recognise(path)
            if recognised_file['confidence']:
                candidates.append((account, reader, recognised_file))
        if not candidates:
            if session.get('recognised_reader'):
                raise ValueError('The recognised reader needs a compatible account mapping; nothing was read by the agent')
            return self.read_agent(session, cancel=cancel)
        if len(candidates) > 1:
            identified = []
            for candidate, candidate_reader, recognition in candidates:
                try:
                    parsed = candidate_reader.read(path, metadata[candidate])
                    printed = parsed.get('identifier',parsed.get('card_last4'))
                    printed_currency = parsed.get('currency')
                    if ((printed and printed == metadata[candidate].get('statement',{}).get('identifier')) or printed_currency) and (not printed_currency or printed_currency == metadata[candidate]['currency']):
                        identified.append((candidate,candidate_reader,recognition))
                except (ValueError,KeyError,TypeError,OSError):
                    continue
            if len(identified)==1:
                candidates=identified
        if len(candidates) != 1:
            session.update(state='account_needed', candidate_accounts=[a for a, _, _ in candidates], error='Choose the account for this statement.')
            self.put(session)
            return
        account, reader, recognition = candidates[0]
        meta = metadata[account]
        statement = reader.read(path, meta)
        identifier = statement.get('identifier', statement.get('card_last4'))
        if identifier and meta.get('statement',{}).get('identifier') not in (None,identifier):
            raise ValueError('The printed identifier does not match the selected account')
        statement.update(account=account, accountLabel=meta.get('label') or account.split(':')[-1], accountAssigned=bool(assigned), readerConfig=deepcopy(meta['statement']))
        session.update(reader=reader.id, reader_name=reader.name, recognition=recognition, reader_config=deepcopy(meta['statement']))
        if statement.get('currency') and statement['currency'] != meta['currency']:
            session.update(state='currency_mismatch', file_currency=statement['currency'], suggested_account=account,
                           error='Choose an account in the statement currency before continuing.')
            self.put(session)
            return
        self.finish_read(session, statement, account, documents, meta)

    def finish_read(self, session, statement, account, documents, meta):
        statements = documents[self.ledger/'data/card-statements.json']
        filed = next((s for s in statements if s['account'] == account and s['end'] == statement['periodEnd']), None)
        if filed:
            session.update(state='already_filed', statement=statement, filed=filed)
            self.put(session)
            self.remove_files(session)
            return
        previous = [s for s in statements if s['account'] == account]
        previous = max(previous, key=lambda s:s['end']) if previous else None
        if previous and statement['periodEnd'] <= previous['end']:
            raise ValueError('This statement predates the latest filed statement.')
        start_date = read_manifest(self.ledger).get('startDate')
        effective, prior = continuity_entries(entries(documents), statement, start_date)
        opening = dict(date=effective, balance=str(sum((Decimal(p['amount']) for e in prior for p in e['postings'] if p['account'] == account), Decimal(0)))) if any(p['account'] == account for e in prior for p in e['postings']) else None
        initial_checks = []
        previous_balance = previous['closing-balance'] if previous and 'closing-balance' in previous else money_text(-Decimal(previous['total-due'])) if previous else None
        if statement.get('kind') != 'bank':
            initial_checks = check_statement(statement,account=meta,previous=dict(balance=money_text(-Decimal(previous_balance))) if previous_balance is not None else None)
            statement.update(kind='bank',sourceBalanceStyle='debt',openingBalance=money_text(-Decimal(statement['previousBalance'])),closingBalance=money_text(-Decimal(statement['totalDue'])),debits=statement['purchasesCharges'],credits=statement['paymentsCredits'])
        tested = initial_checks + check_statement(statement, account=meta, previous=dict(end=previous['end'],balance=previous_balance) if previous else None,
                                                  start_date=start_date, opening=opening)
        if statement.get('kind') == 'bank':
            statement.update(previousBalance=statement['openingBalance'], totalDue=statement['closingBalance'], purchasesCharges=statement['debits'], paymentsCredits=statement['credits'])
        opening_entry = statement_opening(documents, self.ledger, statement)
        checklist = plan(documents, statement, self.ledger)
        session.update(state='found', statement=statement, checks=tested, extraction='agent' if session.get('agent_verified') else 'reader', parser_error=None,
                       opening=opening_entry, plan=checklist, fingerprint=fingerprint(self.ledger))
        duplicates = [other for other in self.all() if other['id'] != session['id'] and not other.get('discarded_until') and other['state'] in ('found','review') and other.get('statement',{}).get('account') == account and other['statement']['periodEnd'] == statement['periodEnd']]
        if duplicates:
            session['duplicate_of'] = min(duplicates, key=lambda s:s['created_at'])['id']
        self.put(session)

    def read_agent(self, session, *, cancel=None):
        """Unknown formats use the same FIFO/pause, with no parser repair fallback."""
        import agent
        session['agent_requested'] = True
        from statement_reading import verify, learn_mapping
        documents = load(self.ledger)
        metadata = documents[self.ledger/'data/accounts.json']['accounts']
        account = session.get('assigned_account')
        candidates = [a for a,m in metadata.items() if m.get('kind') in ('bank','card')]
        if not account:
            session.update(state='account_needed', candidate_accounts=candidates,
                           error='Choose the account before the agent reads this statement.')
            self.put(session)
            return
        meta = metadata.get(account, {})
        if meta.get('kind') not in ('bank','card'):
            raise ValueError('Choose a bank or card account for this statement')
        if getattr(self, 'agent_pause', None) and self.agent_pause.blocked():
            session.update(state='agent_waiting');self.put(session);return
        path = self.folder(session['id'])/('read'+session.get('file_suffix','.pdf'))
        locale = meta.get('locale') or read_manifest(self.ledger).get('locale','en')
        task = dict(statement_reading=dict(path=str(path),account=meta,locale=locale))
        try:
            answer, record = agent.run(self.settings, task, self.folder(session['id']), cancel=cancel)
        except agent.AgentFailure as exc:
            session['runs'].append(exc.record)
            if exc.kind != 'bad_answer' and getattr(self, 'agent_pause', None):
                self.agent_pause.fail(exc.kind, exc.reset_at)
                session.update(state='agent_waiting');self.put(session);return
            self.put(session)
            raise ValueError('The agent could not verify this statement. ' + str(exc)) from None
        if not self.current_generation(session): return
        if getattr(self, 'agent_pause', None): self.agent_pause.success()
        session['runs'].append(record)
        statement = verify(answer, task['_statement_source'], meta, locale)
        # A model reading can outlast a cooperating CLI/price writer. Match a
        # fresh snapshot and bind its fingerprint under the same ledger lock;
        # never bless an old checklist with the new store's fingerprint.
        with ledger_lock(self.ledger):
            if not self.current_generation(session): return
            documents = load(self.ledger)
            current = documents[self.ledger/'data/accounts.json']['accounts'].get(account)
            current_locale = (current or {}).get('locale') or read_manifest(self.ledger).get('locale','en')
            if current != meta or current_locale != locale:
                raise ValueError('The statement account or locale changed during the reading. Read this file again before continuing.')
            mapping = learn_mapping(answer, task['_statement_source'], statement, current, locale)
            statement.update(account=account,accountLabel=current.get('label') or account.split(':')[-1],accountAssigned=True)
            session.update(agent_requested=True,agent_verified=True,agent_answer=answer,source_hash=sha256(path.read_bytes()).hexdigest(),
                           learning=mapping,learn_mapping=False,reader='agent',reader_name='the agent')
            self.finish_read(session, statement, account, documents, current)

    def learn(self, identity, enabled):
        with self.mutex:
            session = self.get(identity)
            if session['state'] not in ('found','review') or not session.get('agent_verified') or not session.get('learning'):
                raise ValueError('This statement has no verified reusable mapping')
            if type(enabled) is not bool: raise ValueError('Choose whether to save this mapping')
            session['learn_mapping'] = enabled
            self.put(session)
            return session

    def read_card(self, session, *, cancel=None):
        if not self.current_generation(session):
            return
        path = self.folder(session['id']) / 'read.pdf'
        extraction_error = None
        try:
            text = extract_text(path)
        except Exception as exc:
            text, extraction_error = None, str(exc)
        if not self.current_generation(session):
            return
        if text is not None and not recognised(text) and not session.get('agent_requested'):
            return self.read_agent(session, cancel=cancel)
        documents = load(self.ledger)
        statements = documents[self.ledger / 'data/card-statements.json']
        account = session.get('assigned_account')
        if text is not None and not account:
            try:
                identifier = card_identifier(text)
            except ValueError:
                identifier = None
            try:
                account = select_account(documents, self.ledger, identifier)
            except ValueError:
                if not printed_currency(text):
                    raise
                account = None
            session['printed_identifier'] = identifier
        currency = printed_currency(text) if text is not None else None
        metadata = documents[self.ledger / 'data/accounts.json']['accounts']
        if text is not None and (not account or currency and currency != metadata[account]['currency']):
            session.update(state='currency_mismatch', file_currency=currency, suggested_account=account,
                           error='Choose an account in the statement currency before continuing.')
            self.put(session)
            return
        parser_error = None
        parsed = False
        try:
            if extraction_error:
                raise ValueError('PDF text extraction failed: ' + extraction_error)
            statement = Registry(self.ledger).get('example-card-pdf').read(path, metadata[account])
            account = session.get('assigned_account') or select_account(documents, self.ledger, statement.get('card_last4'), account)
            metadata = documents[self.ledger / 'data/accounts.json']['accounts'][account]
            account_currency(statement, metadata['currency'])
            statement.update(account=account, accountLabel=metadata.get('label') or account.split(':')[-1], **({'accountAssigned':True} if session.get('assigned_account') else {}))
            earlier = [s for s in statements if s['account'] == account and s['end'] < statement['periodEnd']]
            prior = max(earlier, key=lambda s:s['end']) if earlier else None
            previous = (prior['total-due'] if 'total-due' in prior else money_text(-Decimal(prior['closing-balance']))) if prior else None
            parsed = True
            tested = check_statement(statement, account=metadata, previous=dict(balance=previous) if previous is not None else None)
            if not all(c['ok'] for c in tested):
                raise ValueError('; '.join(c['text'] for c in tested if not c['ok']))
            method = 'parser'
        except ValueError as exc:
            if parsed:
                problem = "A row's date doesn't fit this statement, so nothing was saved. " if isinstance(exc, RowDateError) else "The printed totals don't add up, so nothing was saved. "
                session.update(state='couldnt_read', error=problem + str(exc), failure_reason='check_failed')
                self.put(session)
                return
            session.update(state='couldnt_read', error='The recognised reader could not read this statement. ' + str(exc), failure_reason='read_failed')
            self.put(session)
            return
        if 'reader' not in statement:
            statement.update(reader='example-card-pdf', readerName='Example card PDF', readerPersonal=False, declaredChecks=['printed_totals'])
        filed = next((s for s in statements if s['account'] == account and s['end'] == statement['periodEnd']), None)
        start_date = read_manifest(self.ledger).get('startDate')
        # An opening balance already covers pre-start card rows. Use explicit
        # balance-style membership for this import and its subsequent statements;
        # previously filed card summaries and journal comments remain untouched.
        if not filed and ((start_date and any(r['date'] < start_date for r in statement['rows'])) or any(s.get('kind')=='bank' and s['account']==account for s in statements)):
            statement.update(kind='bank',sourceBalanceStyle='debt',openingBalance=money_text(-Decimal(statement['previousBalance'])),closingBalance=money_text(-Decimal(statement['totalDue'])),debits=statement['purchasesCharges'],credits=statement['paymentsCredits'])
            for row in statement['rows']:
                row.setdefault('reference',row['serial'])
            effective, prior_entries = continuity_entries(entries(documents), statement, start_date)
            opening_anchor = dict(date=effective,balance=money_text(sum((Decimal(p['amount']) for e in prior_entries for p in e['postings'] if p['account']==account),Decimal(0)))) if any(p['account']==account for e in prior_entries for p in e['postings']) else None
            earlier = [s for s in statements if s['account']==account and s['end'] < statement['periodEnd']]
            prior = max(earlier,key=lambda s:s['end']) if earlier else None
            tested.extend(check_statement(statement,account=metadata,start_date=start_date,opening=opening_anchor,previous=dict(end=prior['end'],balance=prior['closing-balance'] if 'closing-balance' in prior else money_text(-Decimal(prior['total-due']))) if prior else None))
            statement.update(previousBalance=statement['openingBalance'],totalDue=statement['closingBalance'])
        session.update(statement=statement, checks=tested, extraction=method, parser_error=parser_error)
        if filed:
            session.update(state='already_filed', filed=filed)
        else:
            earlier_filed = [s for s in statements if s['account'] == account]
            if earlier_filed and statement['periodEnd'] <= max(s['end'] for s in earlier_filed):
                raise ValueError('This statement predates the latest filed statement. Resolve the missing history before applying it.')
            opening = statement_opening(documents, self.ledger, statement)
            checklist = plan(documents, statement, self.ledger)
            for item in checklist['items']:
                if item.get('conversion'):
                    item['needs_user'] = True
            session.update(state='found', opening=opening, plan=checklist, fingerprint=fingerprint(self.ledger))
            duplicates = sorted((other for other in self.all() if other['id'] != session['id'] and not other.get('discarded_until') and other['state'] in ('found', 'review', 'failed') and other.get('fingerprint') == session['fingerprint'] and other.get('failure_reason') != 'stale_checklist' and other.get('statement', {}).get('account') == account and other.get('statement', {}).get('periodEnd') == statement['periodEnd']), key=lambda other: (other['created_at'], other['id']))
            if duplicates:
                session['duplicate_of'] = duplicates[0]['id']
        self.put(session)
        if filed:
            self.remove_files(session)

    def assign(self, identity, account):
        with self.mutex:
            session = self.get(identity)
            if session['state'] not in ('currency_mismatch', 'account_needed'):
                raise ValueError('This statement is not waiting for an account')
            metadata = load(self.ledger)[self.ledger / 'data/accounts.json']['accounts'].get(account, {})
            if metadata.get('kind') not in ('bank', 'card') or (session.get('file_currency') and metadata.get('currency') != session['file_currency']) or (session.get('candidate_accounts') and account not in session['candidate_accounts']):
                raise ValueError('Choose a bank or card account in the statement currency')
            session.update(assigned_account=account, state='reading', queued_at=now(), error=None)
            self.put(session)
            return session

    def decide(self, identity, serial, body):
        with self.mutex:
            s = self.get(identity)
            if s['state'] not in ('found', 'review', 'failed') or 'plan' not in s:
                raise ValueError('This statement is not ready for decisions.')
            item = next((i for i in s['plan']['items'] if i['row']['serial'] == serial), None)
            if not item:
                raise ValueError('Unknown statement row')
            action = body.get('action')
            decision = None
            if action == 'conversion':
                if not item.get('conversion') or not item.get('entry_id'):
                    raise ValueError('This row has no currency estimate to replace')
                decision = dict(action=action)
            elif action == 'match':
                identity = body.get('entry_id')
                if identity not in [c['entryId'] for c in item['candidates']]:
                    raise ValueError('Choose one of this row’s candidate entries.')
                if any(i['row']['serial'] != serial and (i['entry_id'] == identity or (i['decision'] or {}).get('entry_id') == identity) for i in s['plan']['items']):
                    raise ValueError('That entry is already assigned to another row.')
                decision = dict(action=action, entry_id=identity)
            elif action in ('pending', 'account'):
                account = 'Expenses:Uncategorized' if action == 'pending' else body.get('account')
                declared = load(self.ledger)[self.ledger / 'data/accounts.json']['accounts']
                if action == 'account' and (not isinstance(account,str) or account not in declared or account == s['statement']['account'] or account.startswith('Equity:')):
                    raise ValueError('Choose a declared expense, asset, income or person account.')
                decision = dict(action=action, account=account)
            elif action != 'reset':
                raise ValueError('Choose match, account or pending.')
            item['decision'] = decision
            s['decisions'].append(dict(serial=serial, time=now(), **(decision or {'action':'reset'})))
            s.update(state='review', error=None)
            self.put(s)
            return s

    def queue_apply(self, identity):
        with self.mutex:
            s = self.get(identity)
            if s['state'] not in ('found', 'review', 'failed') or 'plan' not in s:
                raise ValueError('This statement is not ready to apply.')
            if s.get('failure_reason') == 'stale_checklist':
                raise ValueError('Upload this statement again to rebuild the checklist.')
            if any(i['needs_user'] and not i['decision'] for i in s['plan']['items']):
                raise ValueError('Decide every row that needs you before applying.')
            if s['plan']['items'] and all(i['rule']=='pre_start' for i in s['plan']['items']):
                raise ValueError('All rows are covered by opening balances; no financial changes should be applied.')
            s.update(state='applying', queued_at=now(), error=None)
            self.put(s)
            return s

    def recover_work(self):
        for s in self.all():
            if s['state'] == 'applying':
                s['waiting'] = None
                s['apply_started'] = bool(s.get('apply_started') or s.get('committed_result') or (self.folder(s['id'])/'transaction.json').exists())
                self.put(s)

    def work(self, stop_event, before=None):
        with self.mutex:
            self.purge()
            for waiting in self.all():
                if waiting['state']=='agent_waiting' and not waiting.get('discarded_until') and not self.agent_pause.blocked():
                    waiting['state']='reading';self.put(waiting)
            queued = sorted((s for s in self.all() if not s.get('discarded_until') and s['state'] in ('reading','applying') and not s.get('waiting')), key=lambda s:(s['queued_at'],s['id']))
            # Waiting sessions periodically rejoin; they must not block other work.
            if not queued:
                queued = [s for s in self.all() if s['state']=='applying' and s.get('waiting') and not s.get('discarded_until')]
                if queued and datetime.now(timezone.utc).timestamp() - queued[0].get('last_check', 0) < .5:
                    return False
            if not queued or before and queued[0]['queued_at'] > before:
                return False
            s = queued[0]
            self.in_flight.add(s['id'])
        try:
            if s['state']=='reading':
                self.read(s, cancel=stop_event)
            else:
                with ledger_lock(self.ledger) as fd:
                    with self.mutex:
                        latest = self.get(s['id'], include_discarded=True)
                        if latest.get('discarded_until') or latest['state'] != 'applying' or latest.get('generation', 0) != s.get('generation', 0):
                            return True
                        s = latest
                    transaction = StatementTransaction(self.settings, self, s)
                    recovered = s.get('committed_result') or transaction.recover()
                    if recovered:
                        s.update(state='applied', result=refresh(self.settings,recovered), waiting=None, apply_started=False)
                        self.put(s)
                    elif not clean(self.ledger):
                        s.update(waiting='Waiting for other ledger changes to finish.', last_check=datetime.now(timezone.utc).timestamp())
                        self.put(s)
                    else:
                        with self.mutex:
                            latest = self.get(s['id'], include_discarded=True)
                            if latest.get('discarded_until') or latest['state'] != 'applying' or latest.get('generation', 0) != s.get('generation', 0):
                                return True
                            s.update(apply_started=True)
                            self.put(s)
                        result = transaction.apply_statement(fd)
                        s.update(state='applied', result=refresh(self.settings,result), waiting=None, apply_started=False)
                        self.put(s)
                if s['state']=='applied':
                    # Other attempts at this period are now history too. Do this
                    # only after a successful commit, never from an uncommitted
                    # summary that a concurrent list request might observe.
                    with self.mutex:
                        summary = next(item for item in load(self.ledger)[self.ledger/'data/card-statements.json'] if item['account']==s['statement']['account'] and item['end']==s['statement']['periodEnd'])
                        for other in self.all():
                            if other['id']!=s['id'] and not other.get('discarded_until') and other['state'] not in ('applied','already_filed') and other.get('statement',{}).get('account')==s['statement']['account'] and other.get('statement',{}).get('periodEnd')==s['statement']['periodEnd']:
                                other.update(state='already_filed', filed=summary, apply_started=False, waiting=None, generation=other.get('generation',0)+1)
                                self.put(other, force=True)
                                self.remove_files(other)
                    self.remove_files(s)
        except Exception as exc:
            if stop_event.is_set() and s['state']=='reading':
                s.update(error=None, waiting=None)
                self.put(s)
                return True
            s.update(state='failed' if 'plan' in s else 'couldnt_read', error=str(exc), waiting=None, apply_started=False, failure_reason='stale_checklist' if isinstance(exc, StaleChecklist) else 'apply_failed' if 'plan' in s else 'read_failed')
            self.put(s)
        finally:
            self.in_flight.discard(s['id'])
        return True


class StatementTransaction(Transaction):
    def __init__(self, settings, sessions, session):
        super().__init__(settings, sessions, session)
        self.marker = 'Statement: ' + session['id']

    def apply_statement(self, lock_fd):
        s = self.draft
        import bookkeeping_policy
        bookkeeping_policy.read(self.settings)
        documents = load(self.root)
        if fingerprint(self.root) != s['fingerprint']:
            raise StaleChecklist('The ledger changed. Upload this statement again to rebuild the checklist.')
        statement, checklist = s['statement'], s['plan']['items']
        if s.get('agent_verified'):
            import tempfile
            from statement_reading import safe_source, extract, verify
            path = safe_source(self.root, self.drafts.folder(s['id'])/('read'+s.get('file_suffix','.pdf')))
            if sha256(path.read_bytes()).hexdigest() != s['source_hash']:
                raise ValueError('The statement source changed; read it again before applying')
            meta = documents[self.root/'data/accounts.json']['accounts'][statement['account']]
            with tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP','/tmp'),prefix='expense-statement-check-') as temporary:
                source = extract(path,Path(temporary),meta.get('statement',{}).get('options'),render_images=False)
                verified = verify(s['agent_answer'],source,meta,meta.get('locale') or read_manifest(self.root).get('locale','en'))
            for key in ('rows','periodStart','periodEnd','currency','openingBalance','closingBalance','debits','credits'):
                if verified[key] != statement[key]:
                    raise ValueError('The verified statement changed; read it again before applying')
            if s.get('learn_mapping'):
                statement['readerConfig'] = deepcopy(s['learning'])
        if statement.get('kind') == 'bank':
            from bank_statements import apply_bank
            return apply_bank(self, documents, lock_fd)
        account = select_account(documents, self.root, None if statement.get('accountAssigned') else statement.get('card_last4'), statement['account'])
        metadata = documents[self.root/'data/accounts.json']['accounts'][account]
        currency = metadata['currency']
        account_currency(statement, currency)
        label = metadata.get('label') or account.split(':')[-1]
        if any(i['needs_user'] and not i['decision'] for i in checklist):
            raise ValueError('There are undecided statement rows')
        target = self.root / 'Bills' / statement['periodEnd'] / s['original_name']
        if not target.resolve().is_relative_to((self.root / 'Bills').resolve()) or not (self.root / 'Bills').resolve().is_relative_to(self.root.resolve()):
            raise ValueError('Evidence directory escapes Bills')
        if target.exists() or target.is_symlink():
            raise ValueError('A PDF with this name is already filed. Check the existing evidence first.')
        source = str(target.relative_to(self.root))
        reconciliation = reconciliation_path(self.root, statement['periodEnd'], account, metadata)
        if reconciliation.exists():
            raise ValueError('This reconciliation file already exists')
        opening = statement_opening(documents, self.root, statement)
        if opening:
            opening['sources'] = [source]
        record = event('statement', statement={**statement,'source':source}, counts=s['plan']['counts'], decisions=s['decisions'], entry_ids=[], extraction=s['extraction'], runs=s['runs'])
        journal = self.snapshot([*store_paths(self.root), self.root/'expense_ledger.ledger', target, reconciliation, audit_path(self.root,record),
                                 *{self.root / 'data/expenses' / (i['row']['date'][:4]+'.json') for i in checklist},
                                 self.root/'data/expenses'/f"{statement['periodStart'][:4]}.json"])
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.drafts.folder(s['id']) / 'read.pdf', target)
            actuals = {e['id']:e for e in entries(documents)}
            changed, added, used = [], [], set()
            new_entries = []
            uncertain = []
            for item in checklist:
                row, decision = item['row'], item['decision']
                identity = decision.get('entry_id') if decision and decision['action']=='match' else item['entry_id'] if not decision or decision['action']=='conversion' else None
                if identity:
                    if identity in used:
                        raise ValueError('An entry was assigned more than once')
                    used.add(identity)
                    entry = actuals[identity]
                    if item['rule']=='foreign_currency' and (not decision or decision['action']=='conversion'):
                        convert(entry,row,account)
                    if entry['date']!=row['date']:
                        entry.setdefault('notes',[]).append(f"statement printed date: {row['date']}; replaces recorded date {entry['date']}")
                        entry['date']=row['date']
                    changed.append(identity)
                else:
                    if decision:
                        amount=Decimal(row['amount'])*(1 if row['direction']=='Debit' else -1)
                        posting=dict(account=decision['account'],amount=f'{amount:.2f}',commodity=currency)
                        if row.get('foreignAmount'):
                            original=Decimal(row['foreignAmount'])*(1 if row['direction']=='Debit' else -1)
                            posting.update(amount=f'{original:.2f}',commodity=row['foreignCurrency'],cost=dict(amount=row['amount'],commodity=currency,per='total'))
                        postings=[posting,dict(account=account,amount=f'{-amount:.2f}',commodity=currency)]
                    else:
                        postings=deepcopy(item['postings'])
                    if not postings:
                        raise ValueError('New row has no approved postings')
                    entry=dict(date=row['date'],status='pending' if decision and decision['action']=='pending' else 'confirmed',description=row['description'],postings=postings)
                    if row.get('foreignAmount'):
                        entry['original-amount']=dict(amount=f"{Decimal(row['foreignAmount']):.2f}",commodity=row['foreignCurrency'])
                    new_entries.append(entry)
                    if decision and decision['action'] == 'pending' and item.get('candidates'):
                        uncertain.append((entry, item['candidates']))
                entry.update({'statement-end':statement['periodEnd'],'serial-number':row['serial'],'statement-page':row['page']})
                entry.setdefault('sources',[])
                if source not in entry['sources']:
                    entry['sources'].append(source)
                entry.setdefault('notes',[]).extend(['statement merchant: '+row['description'],f"statement charge: {row['amount']} {currency} ({row['direction']})"])
            # Re-sort all years after printed-date corrections; preserve IDs and
            # relocate a cross-year match rather than leaving it in the old file.
            actual = list(actuals.values())
            for path, document in documents.items():
                if path.parent.name=='expenses':
                    documents[path]=sorted((e for e in actual if e['date'][:4]==path.stem),key=lambda e:e['date'])
            for year in {e['date'][:4] for e in actual}:
                path=self.root/f'data/expenses/{year}.json'
                if path not in documents:
                    documents[path]=sorted((e for e in actual if e['date'][:4]==year),key=lambda e:e['date'])
            declared=documents[self.root/'data/accounts.json']
            if any(p['account']=='Expenses:Uncategorized' for e in new_entries for p in e['postings']):
                declared['accounts'].setdefault('Expenses:Uncategorized', {})
            supported = json.loads((APPLICATION/'schema/currencies.json').read_text())
            registered = {directive.split()[0].strip(chr(34)) for directive in declared['commodities']}
            for entry in new_entries:
                for posting in entry['postings']:
                    for code in (posting['commodity'],posting.get('cost',{}).get('commodity')):
                        if code in supported and code not in registered:
                            declared['commodities'].append(code)
                            registered.add(code)
            used_ids={e['id'] for items in documents.values() if isinstance(items,list) for e in items if 'id' in e}
            summary=dict(id=new_id(used_ids),account=account,start=statement['periodStart'],end=statement['periodEnd'],source=source,
                         **{'previous-balance':statement['previousBalance'],'purchases':statement['purchasesCharges'],'credits':statement['paymentsCredits'],'total-due':statement['totalDue'],'rows':len(statement['rows'])})
            if Decimal(statement.get('cashAdvances','0')):
                summary['cash-advances']=statement['cashAdvances']
            documents[self.root/'data/card-statements.json'].append(summary)
            for path, document in documents.items():
                dump(path,document)
            for entry in new_entries:
                added.append(add_entry(entry,root=self.root))
            if uncertain:
                from review import link_pair
                linked_documents = load(self.root)
                linked = {e['id']: e for e in entries(linked_documents)}
                for proposed, candidates in uncertain:
                    identity = added[new_entries.index(proposed)]
                    reason = 'several-candidates' if len(candidates) > 1 else 'amount-date-gap' if candidates[0]['dayDistance'] > 3 else 'dissimilar-name'
                    for candidate in candidates:
                        link_pair(linked, identity, candidate['entryId'], reason)
                for path, document in linked_documents.items():
                    if path.parent.name == 'expenses':
                        dump(path, document)
            dump(reconciliation,{key:statement[key] for key in ('account','accountLabel','currency','card_last4','periodStart','periodEnd','previousBalance','purchasesCharges','paymentsCredits','totalDue','rows')}|{'source':source})
            record['entry_ids']=changed+added+([opening['id']] if opening else [])
            append(self.root,record)
            result=dict(entry_ids=record['entry_ids'],changed=len(changed),added=len(added),converted=sum(i['rule']=='foreign_currency' and (not i['decision'] or i['decision']['action']=='conversion') for i in checklist),source=source,end=statement['periodEnd'],audit_id=record['id'])
            journal['result']=result
            self.save_journal(journal)
            message=(f"Reconcile {label} statement ending {statement['periodEnd']}\n\nMatched {len(changed)} entries; converted {result['converted']}; added {len(added)}.\nSource: {source}\n{self.marker}\nAdded-by: dashboard statements ({s['extraction']})")
            env={**os.environ,'LEDGER_DIR':str(self.root),LOCK_FD_ENV:str(lock_fd),'EXPENSES_COMMIT_MESSAGE':message}
            completed=subprocess.run(['make','commit'],cwd=self.settings.application,env=env,pass_fds=(lock_fd,),capture_output=True,text=True)
            if completed.returncode:
                raise RuntimeError('The checked statement change failed; nothing was applied. '+(completed.stdout+completed.stderr)[-4000:])
            result['commit_id']=jj(self.root,'log','-r','@-','--no-graph','-T','commit_id')
            s['committed_result']=result
            self.drafts.put(s)
            self.journal.unlink()
            return result
        except BaseException:
            self.rollback(journal)
            raise
