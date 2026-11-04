"""Live expense backend. Run from this directory with uv run app.py."""
from contextlib import asynccontextmanager, ExitStack
from datetime import datetime, timedelta, timezone
import re
import io
import json
from pathlib import Path
import secrets
import shutil
import time
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Body
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
import pypdfium2 as pdfium

from agent import now
from draft_time import client_context
from agent_gate import get_gate
from drafts import Drafts
from settings import Settings
from dashboard_data import public_data
from ledger_manifest import read_manifest
from worker import Worker
from transactions import BalanceRefusal, ledger_lock
from statements import Statements

def error_detail(exc):
    return getattr(exc, 'refusal', None) or str(exc)


MAX_BYTES = 25 * 1024 * 1024
MAX_FILES = 10
FORMATS = {'JPEG': ('.jpg', 'image/jpeg'), 'PNG': ('.png', 'image/png'), 'WEBP': ('.webp', 'image/webp')}


class SaveTime(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_time: str | None = None
    client_zone: str | None = None


class Edit(SaveTime):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(default='', max_length=100_000)
    pieces: dict | None = None
    client_updated_at: int = Field(default=0, ge=0)
    declined_rules: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator('declined_rules')
    @classmethod
    def valid_declines(cls, values):
        if len(set(values)) != len(values) or any(not re.fullmatch('[0-9a-f]{16}', value) for value in values):
            raise ValueError('Declined rules must be unique rule IDs')
        return values


class Answer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    answer: str = Field(min_length=1, max_length=100_000)


class UnsentAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question_index: int = Field(ge=0)
    updated_at: int = Field(default=0, ge=0)
    answer: str = Field(max_length=100_000)


class CurrencyChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    currency: str = Field(pattern=r'^[A-Z]{3}$')


class AccountCurrencyChange(CurrencyChange):
    account: str = Field(min_length=1)


class NameChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)


class AccountCreate(CurrencyChange):
    kind: str
    name: str = Field(min_length=1, max_length=100)


class AccountSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(min_length=1, max_length=300)


class CurrencyAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    amount: str | None = None
    rate: str | None = None
    estimate: bool = True


def sniff(data, filename=None):
    try:
        with Image.open(io.BytesIO(data)) as image:
            kind = FORMATS.get(image.format)
            image.verify()
            if kind:
                return kind
    except (OSError, ValueError, SyntaxError):
        pass
    if data.startswith(b'%PDF-'):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(''):
                return '.pdf', 'application/pdf'
            with pdfium.PdfDocument(data) as pdf:
                if len(pdf):
                    return '.pdf', 'application/pdf'
        except Exception:
            pass
    suffix = Path(filename or '').suffix.lower()
    if suffix == '.csv':
        # The configured reader owns encoding and column validation. Exports may
        # use a legacy encoding or UTF-16; none is sent to an expense agent.
        return '.csv', 'text/csv'
    if suffix == '.xlsx' and data.startswith(b'PK\x03\x04'):
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if '[Content_Types].xml' in archive.namelist() and 'xl/workbook.xml' in archive.namelist():
                    return '.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        except zipfile.BadZipFile: pass
    if suffix == '.xls' and data.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
        return '.xls', 'application/vnd.ms-excel'
    raise HTTPException(415, 'Choose a valid photo, PDF, CSV or spreadsheet file')


class DashboardGZipMiddleware(GZipMiddleware):
    async def __call__(self, scope, receive, send):
        if scope.get('path', '').endswith('.gz'):
            await self.app(scope, receive, send)
        else:
            await super().__call__(scope, receive, send)


def create_app(settings=None, *, start_worker=True, review_clock=None, agent_clock=None):
    settings = settings or Settings.from_env()
    read_manifest(settings.ledger)
    drafts = Drafts(settings.ledger, clock=agent_clock)
    worker = Worker(settings, drafts)
    from retention_service import Retention
    retention = Retention(settings, drafts, agent_clock)
    worker.retention = retention
    statements = Statements(settings)
    retention.statements = statements
    worker.statements = statements
    if agent_clock: worker.pause.clock = agent_clock
    statements.agent_pause = worker.pause
    gate = get_gate(settings)

    @asynccontextmanager
    async def lifespan(app):
        drafts.purge()
        gate.start()
        if start_worker:
            worker.start()
        try:
            yield
        finally:
            gate.close()
            if start_worker:
                worker.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.retention = retention
    app.state.settings, app.state.drafts, app.state.worker = settings, drafts, worker
    app.state.statements = statements
    app.state.agent_gate = gate
    app.add_middleware(DashboardGZipMiddleware, minimum_size=1024)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.hosts))

    @app.middleware('http')
    async def access(request: Request, call_next):
        # Accept both direct base-prefixed requests and Tailscale's stripped paths.
        base = settings.base_path.rstrip('/')
        path = request.scope['path']
        decoded = path
        for _ in range(5):
            further = unquote(decoded)
            if further == decoded:
                break
            decoded = further
        if '\\' in decoded or '..' in decoded.split('/') or '\x00' in decoded:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail': 'Invalid path'}, status_code=400)
        if base and (path == base or path.startswith(base + '/')):
            request.scope['path'] = path[len(base):] or '/'
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            from fastapi.responses import JSONResponse
            if not settings.dev and request.headers.get('tailscale-user-login') not in settings.allowed_logins:
                return JSONResponse({'detail': 'An allowed Tailscale login is required'}, status_code=403)
            origin = request.headers.get('origin')
            if origin:
                try:
                    parsed = urlsplit(origin)
                    valid = (parsed.scheme in ('http', 'https') and parsed.hostname in settings.hosts
                             and parsed.username is None and parsed.password is None
                             and parsed.path in ('', '/') and not parsed.query and not parsed.fragment)
                except ValueError:
                    valid = False
                if not valid:
                    return JSONResponse({'detail': 'Foreign Origin'}, status_code=403)
        try:
            read_manifest(settings.ledger)
        except (ValueError, OSError) as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail': str(exc)}, status_code=409)
        return await call_next(request)

    def get(identity):
        try:
            return drafts.get(identity)
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, 'Unknown item')

    def require(draft, *states):
        if draft.get('intake_complete'):
            raise HTTPException(409,'This submission is already in Queue')
        if draft.get('claim'):
            raise HTTPException(409, 'Another page is finishing this item')
        if draft['state'] not in states:
            raise HTTPException(409, f"Item is {draft['state']}; expected {', '.join(states)}")

    def queue(draft, proposal=None):
        draft.pop('refusal', None)
        draft.update(state='queued', queued_at=(draft.get('queued_at') or now()) if draft.get('usual_payment') or draft.get('bill_request') else now(), error=None, waiting=None, proposal=proposal, answer_failures=0)
        drafts.put(draft)
        worker.wake.set()
        return public(draft)

    def public(draft, receipt_lines=None):
        result = dict(draft)
        if result.get('claim'):
            result['claim'] = {'expires_at': result['claim']['expires_at']}
        result['agent_pause'] = worker.pause.status()
        if isinstance(result.get('result'), dict) and result['result'].get('audit_id'):
            if receipt_lines is None:
                return public_many([draft])[0]
            if receipt_lines is not False:
                line = next((r for r in receipt_lines if r['id'] == result['result']['audit_id']), None)
                if line and result['result'].get('resume_rules'):
                    answered = {r.get('rule_id') for r in receipt_lines if r['kind']=='rule' and r['rule_action'] in ('kept','resumed') and r['time']>=line['time']}
                    result['result']['resume_rules'] = [r for r in result['result']['resume_rules'] if r['id'] not in answered]
        return result

    def public_many(values):
        values = list(values)
        if not any(isinstance(d.get('result'), dict) and d['result'].get('audit_id') for d in values):
            return [public(d, receipt_lines=False) for d in values]
        from ledger_lock import ledger_lock
        from audit import records
        deadline = time.monotonic() + 1
        with ExitStack() as stack:
            while True:
                try:
                    # jj snapshots the working copy even for its cleanliness read.
                    stack.enter_context(ledger_lock(settings.ledger, blocking=False))
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        return [public(d, receipt_lines=False) for d in values]
                    time.sleep(.01)
            # One wait budget and one stable audit snapshot for the whole response.
            # A brief competing reader must not starve receipt eligibility polling.
            lines = records(settings.ledger)
            return [public(d, receipt_lines=lines) for d in values]

    @app.get('/api/rules')
    def list_rules():
        from rules_service import snapshot
        from ledger_lock import ledger_lock
        with ledger_lock(settings.ledger):
            return snapshot(settings, clock=agent_clock)

    @app.get('/api/rules/match')
    def match_rules(text: str = '', today: str | None = None, currency: str | None = None):
        from rules_service import match
        from ledger_lock import ledger_lock
        try:
            with ledger_lock(settings.ledger):
                return match(settings, text, today, currency)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get('/api/rules/from-entry/{identity}')
    def rule_from_entry(identity: str):
        from rules_service import from_entry
        from ledger_lock import ledger_lock
        try:
            with ledger_lock(settings.ledger):
                return from_entry(settings, identity)
        except ValueError as exc: raise HTTPException(404, str(exc)) from exc

    @app.post('/api/rules/operations/{identity}')
    def rule_operation(identity: str, body: dict):
        from rules_service import operate
        try: return worker.call(lambda: operate(settings, identity, body, clock=agent_clock))
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            raise HTTPException(409, error_detail(exc)) from exc

    @app.post('/api/drafts/{identity}/bill')
    def save_bill(identity: str, body: dict):
        from rules_service import queue_bill, AmountCheck
        from ledger_lock import ledger_lock
        with ledger_lock(settings.ledger), drafts.mutex:
            draft=get(identity)
            if draft['state']=='editing':
                from add_intake import interpret
                if any(f['kind']=='statement' for f in interpret(settings,drafts,draft)['files']):
                    raise HTTPException(409,'Read this statement through Continue')
            try: queue_bill(settings,drafts,draft,body)
            except AmountCheck as exc: raise HTTPException(409,dict(kind='amount_check',**exc.check)) from exc
            except (ValueError, TypeError, KeyError, ArithmeticError) as exc: raise HTTPException(409,error_detail(exc)) from exc
            worker.wake.set()
            return public(draft)

    @app.post('/api/drafts/{identity}/usual')
    def usual_payment(identity: str, body: dict):
        from rules_service import queue_usual, AmountCheck
        from ledger_lock import ledger_lock
        with ledger_lock(settings.ledger), drafts.mutex:
            draft = get(identity)
            from add_intake import interpret
            if any(f['kind']=='statement' for f in interpret(settings,drafts,draft)['files']):
                raise HTTPException(409,'Use Continue to read this statement before applying its rows')
            try: queue_usual(settings, drafts, draft, body)
            except AmountCheck as exc:
                raise HTTPException(409,dict(kind='amount_check',**exc.check)) from exc
            except BalanceRefusal:
                worker.wake.set()
                return public(draft)
            except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
                raise HTTPException(409, error_detail(exc)) from exc
            worker.wake.set()
            return public(draft)

    @app.post('/api/entries/{entry_id}/correction-form')
    def correction_form(entry_id: str):
        from corrections import open_form
        try: return public(open_form(settings,drafts,entry_id))
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc

    @app.post('/api/entries/{entry_id}/corrections/{operation_id}')
    def correct_entry(entry_id: str, operation_id: str, body: dict):
        from corrections import apply
        try: return worker.call(lambda: apply(settings,entry_id,operation_id,body,drafts=drafts))
        except (ValueError,TypeError,KeyError,ArithmeticError) as exc: raise HTTPException(409,error_detail(exc)) from exc

    @app.post('/api/drafts/{identity}/receipt-choice')
    def receipt_choice(identity: str, body: dict):
        from receipts import choose
        draft=get(identity)
        try: return public(worker.call(lambda: choose(settings,drafts,draft,body.get('choice'))))
        except BalanceRefusal as exc:
            with drafts.mutex:
                latest=get(identity)
                latest['receipt_reading']['refusal']=exc.refusal
                drafts.put(latest)
                return public(latest)
        except (ValueError,TypeError,KeyError,ArithmeticError,FileNotFoundError) as exc: raise HTTPException(409,error_detail(exc)) from exc

    @app.get('/api/health')
    def health():
        return {'ok': True, 'base_path': settings.base_path, 'worker': bool(worker.thread and worker.thread.is_alive()),
                'agent': gate.status(), 'agent_pause': worker.pause.status()}

    @app.get('/api/agent-status')
    def agent_status():
        # Reading status must never start a check or run an agent.
        from adapters import status_details
        return {**gate.status(), **status_details(settings), 'agent_pause': worker.pause.status()}

    @app.post('/api/settings/name')
    def ledger_name(body: NameChange):
        from currency_settings import update_name
        try:
            return update_name(settings, body.name)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/accounts/details')
    def account_details_read(account: str):
        from currency_settings import account_details
        try:
            return account_details(settings, account)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post('/api/settings/default-currency')
    def default_currency(body: CurrencyChange):
        from currency_settings import update_currency
        try:
            return update_currency(settings, body.currency)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post('/api/accounts/currency')
    def account_currency_change(body: AccountCurrencyChange):
        from currency_settings import update_currency
        try:
            return update_currency(settings, body.currency, body.account)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post('/api/accounts', status_code=201)
    def account_create(body: AccountCreate):
        from currency_settings import create_account
        try:
            return create_account(settings, body.kind, body.name, body.currency)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/review')
    def review_queue():
        from review import snapshot
        from ledger_lock import ledger_lock
        with ledger_lock(settings.ledger):
            return snapshot(settings.ledger, clock=review_clock)

    @app.post('/api/review/import-preview')
    def review_import_preview(body: dict):
        from review import import_preview
        from ledger_lock import ledger_lock
        try:
            with ledger_lock(settings.ledger):
                return import_preview(settings.ledger, body, clock=review_clock)
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post('/api/review/actions')
    def review_action(body: dict):
        from review import apply
        try:
            return worker.call(lambda: apply(settings, body, clock=review_clock))
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post('/api/drafts', status_code=201)
    def create(body: Edit | None = None):
        body = body or Edit()
        try:
            draft = drafts.create(body.text, body.pieces, body.client_time, body.client_zone)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        draft['client_updated_at'] = body.client_updated_at
        draft['declined_rules'] = body.declined_rules
        drafts.put(draft)
        return public(draft)

    @app.get('/api/drafts')
    def list_drafts(include_added: bool = False):
        drafts.purge()
        return sorted(public_many(d for d in drafts.all() if include_added or d['state'] != 'added' or d.get('receipt_reading',{}).get('state') in ('queued','reading','ready','question')), key=lambda d: d['created_at'])

    @app.get('/api/drafts/{identity}')
    def read(identity: str):
        try:
            value = drafts.get(identity, include_discarded=True)
            return public(value)
        except FileNotFoundError:
            raise HTTPException(404, 'Unknown item')

    @app.patch('/api/drafts/{identity}')
    def update(identity: str, body: Edit):
        with drafts.mutex:
            draft = get(identity)
            require(draft, 'editing')
            values = body.model_dump(exclude_unset=True)
            if 'client_time' in values or 'client_zone' in values:
                try:
                    values.update(client_context(body.client_time, body.client_zone))
                except ValueError as exc:
                    raise HTTPException(422, str(exc))
            if 'declined_rules' in values:
                values['declined_rules'] = sorted(set(draft.get('declined_rules',[])) | set(values['declined_rules']))
            draft.update(values, updated_at=drafts.clock().isoformat())
            drafts.put(draft)
            return public(draft)

    @app.post('/api/drafts/{identity}/files', status_code=201)
    def upload(identity: str, files: list[UploadFile] = File(...), upload_id: str | None = Form(None), claim_token: str | None = Form(None)):
        with drafts.mutex:
            draft = get(identity)
            if claim_token:
                try: drafts.require_claim(draft, claim_token)
                except ValueError as exc: raise HTTPException(409,str(exc))
                draft.pop('account_consent', None);draft.pop('hand_checks', None)
            else: require(draft, 'editing')
            if upload_id is not None:
                if not re.fullmatch('[a-f0-9]{32}', upload_id) or len(files) != 1:
                    raise HTTPException(422, 'An upload id identifies one file')
                if upload_id in draft.get('cancelled_uploads', []):
                    return public(draft)
                if any(f['id'] == upload_id for f in draft['files']):
                    return public(draft)
            if len(files) + len(draft['files']) > MAX_FILES:
                raise HTTPException(413, 'At most 10 files per draft')
            staged = []
            for file in files:
                data = file.file.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise HTTPException(413, 'At most 25 MB per file')
                extension, media = sniff(data, None if claim_token else file.filename)
                info = dict(id=upload_id or secrets.token_hex(16), original_name=file.filename or 'Attachment',
                            extension=extension, media_type=media, size=len(data))
                info['name'] = info['id'] + extension
                staged.append((info, data))
            folder = drafts.folder(identity) / 'files'
            try:
                for info, data in staged:
                    (folder / info['name']).write_bytes(data)
                draft['files'].extend(info for info, _ in staged)
                drafts.put(draft)
            except BaseException:
                for info, _ in staged:
                    (folder / info['name']).unlink(missing_ok=True)
                raise
            return public(draft)

    @app.get('/api/drafts/{identity}/files/{file_id}')
    def read_file(identity: str, file_id: str):
        draft = get(identity)
        file = next((f for f in draft['files'] if f['id'] == file_id), None)
        path = drafts.folder(identity) / 'files' / file['name'] if file else None
        if not path or not path.is_file() or path.is_symlink():
            raise HTTPException(404, 'Unknown file')
        return FileResponse(path, media_type=file['media_type'], headers={'Cache-Control': 'no-store'})

    @app.delete('/api/drafts/{identity}/files/{file_id}')
    def remove_file(identity: str, file_id: str, claim_token: str | None = None):
        with drafts.mutex:
            draft = get(identity)
            if claim_token:
                try: drafts.require_claim(draft, claim_token)
                except ValueError as exc: raise HTTPException(409,str(exc))
                draft.pop('account_consent', None);draft.pop('hand_checks', None)
            else: require(draft, 'editing')
            file = next((f for f in draft['files'] if f['id'] == file_id), None)
            if not file:
                raise HTTPException(404, 'Unknown file')
            draft['files'].remove(file)
            drafts.put(draft)
            (drafts.folder(identity) / 'files' / file['name']).unlink(missing_ok=True)
            return public(draft)

    @app.delete('/api/drafts/{identity}/uploads/{upload_id}')
    def cancel_upload(identity: str, upload_id: str):
        if not re.fullmatch('[a-f0-9]{32}', upload_id):
            raise HTTPException(422, 'Invalid upload id')
        with drafts.mutex:
            draft = get(identity)
            require(draft, 'editing')
            cancelled = draft.setdefault('cancelled_uploads', [])
            if upload_id not in cancelled:
                cancelled.append(upload_id)
            removed = [f for f in draft['files'] if f['id'] == upload_id]
            draft['files'] = [f for f in draft['files'] if f['id'] != upload_id]
            drafts.put(draft)
            for file in removed:
                (drafts.folder(identity) / 'files' / file['name']).unlink(missing_ok=True)
            return public(draft)

    @app.patch('/api/drafts/{identity}/unsent-answer')
    def keep_answer(identity: str, body: UnsentAnswer):
        with drafts.mutex:
            draft = get(identity)
            require(draft, 'question')
            if body.question_index != len(draft['questions']) - 1 or draft['questions'][-1]['answer'] is not None:
                raise HTTPException(409, 'This question has already changed')
            draft['unsent_answer'] = body.model_dump()
            drafts.put(draft)
            return public(draft)

    @app.post('/api/add/interpret')
    def interpret_words(body: dict):
        from add_intake import word_statement
        from store import load
        return word_statement(str(body.get('text',''))[:100000], load(settings.ledger)[settings.ledger/'data/accounts.json']['accounts'], read_manifest(settings.ledger).get('locale','en'))

    async def intake_body(request):
        # Avoid validation errors echoing passwords into responses or diagnostics.
        body = await request.json()
        if not isinstance(body,dict) or not isinstance(body.get('choices',{}),dict) or not isinstance(body.get('passwords',{}),dict):
            raise HTTPException(422,'Invalid file choices')
        return body

    @app.post('/api/drafts/{identity}/interpret')
    async def interpret_add(identity: str, request: Request):
        from add_intake import interpret
        body = await intake_body(request)
        with drafts.mutex:
            draft=get(identity); require(draft,'editing')
            try: return interpret(settings,drafts,{**draft,'text':str(body.get('text',draft['text']))[:100000]},body.get('choices'),body.get('passwords'))
            except ValueError as exc: raise HTTPException(422,str(exc)) from None

    @app.post('/api/drafts/{identity}/continue')
    async def continue_add(identity: str, request: Request):
        from add_intake import interpret
        from currency_settings import create_account
        body = await intake_body(request)
        def dispatch():
            with drafts.mutex:
                draft=get(identity)
                if draft.get('intake_result'): return draft['intake_result']
                require(draft,'editing')
                previews=interpret(settings,drafts,draft,body.get('choices'),body.get('passwords'))
                blocked=[f for f in previews['files'] if f['kind']=='ambiguous' or f.get('mismatch') or f.get('locked')]
                if blocked:
                    return dict(blocked=True,interpretation=previews,statements=[],drafts=[])
                statements_out=[]
                for preview in previews['files']:
                    if preview['kind']!='statement': continue
                    file=next(f for f in draft['files'] if f['id']==preview['id'])
                    account=preview.get('account')
                    if preview.get('new_account'):
                        spec=preview['new_account']
                        staged=draft.setdefault('intake_accounts',{})
                        if file['id'] not in staged:
                            staged[file['id']]=create_account(settings,spec['kind'],spec['name'],spec['currency'])['account']
                            drafts.put(draft)
                        account=staged[file['id']]
                    with statements.mutex:
                        existing=next((s for s in statements.all() if s.get('intake_file')==file['id'] and s.get('intake_draft')==identity),None)
                        if existing: session=existing
                        else:
                            session=statements.create((drafts.folder(identity)/'files'/file['name']).read_bytes(),file['original_name'],
                                client_time=draft['client_time'],client_zone=draft['client_zone'])
                            session.update(intake_draft=identity,intake_file=file['id'],assigned_account=account,agent_requested=not bool(preview.get('reader')),recognised_reader=preview.get('reader'))
                            if preview.get('reader'):
                                from store import load
                                metadata=load(settings.ledger)[settings.ledger/'data/accounts.json']['accounts']
                                source=preview.get('source_account') or preview.get('account')
                                config=metadata.get(source,{}).get('statement')
                                if config: session['reader_config']=config
                            statements.put(session)
                            if session['state']=='locked':
                                session=statements.unlock(session['id'],body.get('passwords',{}).get(file['id'],''))
                        statements_out.append(session)
                statement_ids={f['id'] for f in previews['files'] if f['kind']=='statement'}
                draft['files']=[f for f in draft['files'] if f['id'] not in statement_ids]
                expense=bool(draft['files']) or (bool(draft['text'].strip()) and previews['words']['kind']!='statement')
                draft_out=[]
                if expense:
                    drafts.save_time(draft,body.get('client_time'),body.get('client_zone'))
                    draft_out=[queue(draft)]
                else:
                    draft['intake_complete']=True
                    drafts.put(draft)
                result=dict(blocked=False,statements=statements_out,drafts=draft_out)
                draft['intake_result']=result; drafts.put(draft)
                worker.wake.set()
                return result
        try: return await run_in_threadpool(worker.call,dispatch)
        except ValueError as exc: raise HTTPException(409,str(exc)) from None

    @app.post('/api/drafts/{identity}/save')
    def save(identity: str, body: SaveTime | None = None):
        with drafts.mutex:
            draft = get(identity)
            require(draft, 'editing')
            if any(f.get('extension') in ('.csv','.xlsx','.xls') for f in draft['files']):
                raise HTTPException(409,'Use Continue to read statement files')
            body = body or SaveTime()
            try:
                drafts.save_time(draft, body.client_time, body.client_zone)
            except ValueError as exc:
                raise HTTPException(422, str(exc))
            return queue(draft)

    @app.post('/api/drafts/{identity}/answer')
    def answer(identity: str, body: Answer):
        if not body.answer.strip():
            raise HTTPException(422, 'Answer must not be blank')
        with drafts.mutex:
            draft = get(identity)
            require(draft, 'question')
            draft.pop('account_question', None)
            draft.pop('account_proposal', None)
            draft.pop('account_consent', None)
            draft.pop('currency_request', None)
            draft.pop('currency_proposal', None)
            draft['questions'][-1].update(answer=body.answer, answered_at=now())
            draft.pop('unsent_answer', None)
            return queue(draft)

    @app.post('/api/drafts/{identity}/currency')
    def currency_answer(identity: str, body: CurrencyAnswer):
        from currency_drafts import resolve
        with drafts.mutex:
            draft = get(identity)
            require(draft, 'question')
            try:
                resolve(draft, settings.ledger, **body.model_dump())
                draft['questions'][-1]['answered_at'] = now()
            except (ValueError, ArithmeticError, KeyError) as exc:
                raise HTTPException(422, str(exc)) from exc
            return queue(draft, draft['proposal'])

    @app.post('/api/drafts/{identity}/approve-accounts')
    def approve_accounts(identity: str, body: dict):
        from proposal_consent import proposal_hash
        with drafts.mutex:
            draft = get(identity);require(draft, 'question')
            proposal = draft.get('account_proposal')
            if not proposal or body.get('proposal_hash') != proposal_hash(proposal):
                raise HTTPException(409, 'The proposal changed; review it again')
            from transactions import Transaction
            try: prepared = Transaction(settings,drafts,draft).prepare(proposal)
            except (ValueError, TypeError, ArithmeticError) as exc: raise HTTPException(409,error_detail(exc))
            draft['account_consent'] = dict(proposal_hash=prepared['proposal_hash'], accounts=prepared['new_roots'], method='create-and-add')
            draft.pop('account_question', None)
            draft['questions'][-1].update(answer='Create and add', answered_at=now())
            return queue(draft, proposal)

    @app.post('/api/drafts/{identity}/claim')
    def claim_draft(identity: str, body: dict):
        from hand_finish import prefill
        with drafts.mutex:
            draft = get(identity)
            if draft.get('bill_request'): raise HTTPException(409,'This item is saving a bill; retry the bill or discard the item')
            try: token = drafts.claim(draft, body.get('claim_token'))
            except ValueError as exc: raise HTTPException(409,str(exc))
            draft.setdefault('hand_reason', (worker.pause.status() or {}).get('reason') or (draft.get('runs') or [{}])[-1].get('failure_kind') or {'queued':'waiting for the agent','question':'agent question','failed':'agent could not finish'}[draft['state']])
            drafts.put(draft)
            return dict(draft=public(draft), claim_token=token, form=prefill(settings,draft))

    @app.post('/api/drafts/{identity}/hand-form')
    def save_hand_form(identity: str, body: dict):
        from hand_finish import preview
        with drafts.mutex:
            draft=get(identity)
            try: drafts.require_claim(draft,body.get('claim_token',''))
            except ValueError as exc: raise HTTPException(409,str(exc))
            if not isinstance(body.get('proposal'),dict): raise HTTPException(422,'A proposal is required')
            if draft.get('correction'):
                if 'sources' in body: draft['correction']['sources']=body['sources']
                if 'reason' in body:
                    if not isinstance(body['reason'],str) or len(body['reason'])>10000: raise HTTPException(422,'Enter a short correction reason')
                    draft['correction']['reason']=body['reason']
            return preview(settings,drafts,draft,body['proposal'])

    @app.post('/api/drafts/{identity}/hand-add')
    def add_hand_form(identity: str, body: dict):
        if get(identity).get('correction'): raise HTTPException(409,'Use Save correction to keep the saved entry identity')
        from hand_finish import preview
        from proposal_consent import proposal_hash
        with drafts.mutex:
            draft=get(identity)
            try: drafts.require_claim(draft,body.get('claim_token',''))
            except ValueError as exc: raise HTTPException(409,error_detail(exc))
            proposal=draft.get('hand_form')
            if not proposal or body.get('proposal_hash') != proposal_hash(proposal):
                raise HTTPException(409,'The form changed; check it again')
            checked=preview(settings,drafts,draft,proposal)
            if not checked['ok']: raise HTTPException(422,checked.get('refusal') or checked['checks'][0])
            accounts=body.get('approved_accounts',[])
            if not isinstance(accounts,list) or set(accounts)!=set(checked['new_accounts']):
                raise HTTPException(409,'Tick every new account before adding')
            drafts.release_claim(draft)
            draft.pop('usual_payment', None)
            draft.pop('usual_rule_hash', None)
            draft['finished_by']=dict(actor='user',reason=draft['hand_reason'].replace('_',' '))
            draft['account_consent']=dict(proposal_hash=checked['proposal_hash'],accounts=accounts,method='hand-ticks')
            draft.pop('account_question', None)
            draft.pop('currency_request', None)
            draft.pop('refusal', None)
            # Retain the original FIFO position. Human proposals bypass agent pauses.
            draft.update(state='queued',queued_at=draft.get('queued_at') or now(),proposal=proposal,error=None,waiting=None)
            drafts.put(draft);worker.wake.set()
            return public(draft)

    @app.post('/api/drafts/{identity}/release')
    def release_draft(identity: str, body: dict):
        with drafts.mutex:
            draft=get(identity)
            try: drafts.require_claim(draft,body.get('claim_token',''))
            except ValueError as exc: raise HTTPException(409,str(exc))
            if isinstance(body.get('proposal'),dict): draft['hand_form']=body['proposal']
            drafts.release_claim(draft);drafts.put(draft);worker.wake.set()
            return public(draft)

    @app.post('/api/drafts/{identity}/resolve-refusal')
    def resolve_refusal(identity: str, body: dict):
        with drafts.mutex:
            draft = get(identity)
            require(draft, 'failed')
            if not draft.get('refusal'): raise HTTPException(409, 'This draft has no confirmed-balance refusal')
            if body.get('action') == 'answer':
                for key in ('usual_payment','usual_rule_hash','finished_by','account_consent'):
                    draft.pop(key, None)
                draft['proposal'] = None
                draft['questions'].append(dict(question='Which account or date should be used instead?', answer=None, asked_at=now()))
                draft['state'] = 'question'
            elif body.get('action') == 'edit':
                draft.update(state='editing', proposal=None, hand_form=None, error=None)
                for key in ('usual_payment','usual_rule_hash','usual_request','finished_by','account_consent','intake_result'):
                    draft.pop(key, None)
                draft.pop('refusal', None)
            else: raise HTTPException(422, 'Choose edit or answer')
            drafts.put(draft)
            return public(draft)

    @app.post('/api/drafts/{identity}/retry')
    def retry(identity: str):
        with drafts.mutex:
            draft = get(identity)
            require(draft, 'failed')
            if draft.get('refusal'): raise HTTPException(409, 'Change the account or date, or answer the agent before sending this item again')
            draft['check_errors'] = []
            return queue(draft, draft.get('proposal') if draft.get('usual_payment') else draft.get('hand_form') if draft.get('finished_by') else None)

    @app.get('/api/entries/{entry_id}/retention')
    def entry_retention(entry_id: str):
        try: return retention.eligibility(entry_id)
        except ValueError as exc: raise HTTPException(409, str(exc))

    @app.get('/api/trash')
    def trash_list():
        with ledger_lock(settings.ledger): return retention.listing()

    @app.get('/api/settings/retention')
    def retention_settings():
        from retention_service import limits
        return limits(read_manifest(settings.ledger))

    @app.post('/api/settings/retention-preview')
    def retention_preview(body: dict = Body(...)):
        try:
            with ledger_lock(settings.ledger): return retention.preview(body)
        except ValueError as exc: raise HTTPException(422, str(exc))

    @app.post('/api/retention/operations/{operation_id}')
    def retention_operation(operation_id: str, body: dict = Body(...)):
        try:
            if body.get('action') == 'delete':
                try: status=retention.eligibility(body.get('entry_id'))
                except ValueError: status=None  # A committed retry replays below.
                if status and status['offered'] and status['version']==body.get('version'): worker.cancel_receipt(body.get('entry_id'))
            result = worker.call(lambda: retention.operate(operation_id, body))
            worker.wake.set()
            return result
        except ValueError as exc: raise HTTPException(409, str(exc))

    @app.delete('/api/drafts/{identity}', status_code=204)
    def discard(identity: str):
        # Durable Trash keeps GET safe for an already in-flight poll. All writers
        # still require an active item, so a late autosave cannot resurrect it.
        with drafts.mutex:
            draft=get(identity)
            if draft['state']=='working' or draft.get('claim'):
                raise HTTPException(409,'Release the form or wait for the reading to finish')
            if draft['state']=='added' and not draft.get('statement_file'):
                raise HTTPException(409,'Added entries leave the Queue; use the entry inspector')
            draft['discard_requested']=True;drafts.put(draft)
        try: worker.call(lambda: retention.operate(secrets.token_hex(16), dict(action='queue-discard', item_id=identity)))
        except ValueError as exc: raise HTTPException(409, str(exc))
        finally:
            with drafts.mutex:
                try:
                    latest=drafts.get(identity,include_discarded=True)
                    if latest.pop('discard_requested',None):drafts.put(latest)
                except FileNotFoundError:pass

    @app.post('/api/drafts/{identity}/restore')
    def restore(identity: str):
        try:
            worker.call(lambda: retention.operate(secrets.token_hex(16), dict(action='queue-restore', item_id=identity)))
            return public(get(identity))
        except ValueError as exc: raise HTTPException(409, str(exc))

    def statement_get(identity, **kwargs):
        try:
            return statements.get(identity, **kwargs)
        except (FileNotFoundError,ValueError):
            raise HTTPException(404, 'Unknown statement session')

    @app.get('/api/statements')
    def statement_list():
        from store import load
        statements.purge()
        documents = load(settings.ledger)
        return dict(sessions=[s for s in statements.all() if not s.get('discarded_until') and s['state'] not in ('applied','already_filed')], recoverable=[s for s in statements.all() if s.get('discarded_until') and not s.get('trashed_at') and not s.get('handover_undone')], filed=documents[settings.ledger/'data/card-statements.json'], accounts=list(documents[settings.ledger/'data/accounts.json']['accounts']), readers=__import__('statement_readers').Registry(settings.ledger).describe())

    @app.get('/api/statements/filed/{end}')
    def filed_statement(end: str, account: str | None = None):
        from store import load
        documents = load(settings.ledger)
        matches = [s for s in documents[settings.ledger/'data/card-statements.json'] if s['end']==end and (account is None or s['account']==account)]
        if len(matches) > 1:
            raise HTTPException(409, 'Choose the statement account.')
        summary = matches[0] if matches else None
        if not summary:
            raise HTTPException(404,'Unknown filed statement')
        month=datetime.fromisoformat(end).strftime('%B%Y')
        from reconcile_statement import reconciliation_path
        path=reconciliation_path(settings.ledger,end,summary['account'],documents[settings.ledger/'data/accounts.json']['accounts'][summary['account']])
        legacy=settings.ledger/f'data/statements/{month}-card-reconciliation.json'
        if not path.is_file() and legacy.is_file():
            path=legacy
        if path.is_file():
            rows=json.loads(path.read_text())['rows']
        else:
            from reconcile_statement import extract_rows
            rows,_=extract_rows(settings.ledger,end,summary['account'])
        from store import entries
        members = {e.get('serial-number'): e for e in entries(load(settings.ledger)) if e.get('statement-end') == end and any(p['account'] == summary['account'] for p in e['postings'])}
        if summary.get('kind') == 'bank':
            actual = {e['id']:e for e in entries(documents)}
            membership = {m['serial']:m for m in summary['members']}
            return dict(summary=summary, rows=[{**r, 'disposition':membership[r['serial']]['disposition'], **({'entry_id':membership[r['serial']]['entry-id'], 'entry_description':actual[membership[r['serial']]['entry-id']]['description']} if membership[r['serial']].get('entry-id') else {})} for r in rows])
        return dict(summary=summary, rows=[{**r, 'entry_id': members[r['serial']]['id'], 'entry_description': members[r['serial']]['description'], 'conversion_notes': [n for n in members[r['serial']].get('notes', []) if 'replaces' in n and 'statement charge:' in n]} if r['serial'] in members else r for r in rows])

    @app.post('/api/statements',status_code=201)
    def create_statement(file: UploadFile=File(...), upload_id: str=Form(''),
                         client_time: str | None=Form(None), client_zone: str | None=Form(None)):
        try:
            context = client_context(client_time, client_zone)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        data=file.file.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES:
            raise HTTPException(413,'At most 25 MB per statement')
        try:
            session=statements.create(data,file.filename,upload_id or None, **context)
        except ValueError as exc:
            raise HTTPException(409 if 'cancelled' in str(exc) else 415,str(exc))
        worker.wake.set()
        return session

    @app.get('/api/statements/{identity}')
    def read_statement(identity: str):
        return statement_get(identity, include_discarded=True)

    @app.post('/api/statements/{identity}/unlock')
    async def unlock_statement(identity: str, request: Request):
        # Do not use a validation model whose error response could echo a secret.
        statement_get(identity)
        try:
            data=await request.json()
            password=data.get('password') if isinstance(data,dict) else None
            if not isinstance(password,str) or len(password)>1000:
                raise ValueError('Enter the PDF password.')
            session=statements.unlock(identity,password)
        except ValueError as exc:
            raise HTTPException(422,str(exc))
        worker.wake.set()
        return session

    @app.post('/api/statements/{identity}/account')
    def assign_statement(identity: str, body: AccountSelection):
        try:
            session = statements.assign(identity, body.account)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except FileNotFoundError:
            raise HTTPException(404, 'Unknown statement session') from None
        worker.wake.set()
        return session

    @app.post('/api/statements/{identity}/mapping')
    def learn_statement(identity: str, body: dict):
        try:
            return statements.learn(identity, body.get('enabled'))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except FileNotFoundError:
            raise HTTPException(404, 'Unknown statement session') from None

    @app.post('/api/statements/{identity}/retry')
    def retry_statement(identity: str):
        with statements.mutex:
            session=statement_get(identity)
            if session['state'] not in ('wrong_file','couldnt_read','saved'):
                raise HTTPException(409,'This statement is not waiting for another reading')
            session.update(state='reading',queued_at=now(),error=None,waiting=None,cancelled=False,generation=session.get('generation',0)+1)
            statements.put(session)
        worker.wake.set()
        return session

    @app.post('/api/statements/{identity}/review')
    def review_statement(identity: str):
        with statements.mutex:
            s=statement_get(identity)
            if s['state']!='found':
                raise HTTPException(409,'The checklist is not ready.')
            s['state']='review';statements.put(s)
            return s

    @app.patch('/api/statements/{identity}/rows/{serial}')
    def decide_statement(identity: str,serial: str,body: dict):
        statement_get(identity)
        try:
            return statements.decide(identity,serial,body)
        except ValueError as exc:
            raise HTTPException(409,str(exc))

    @app.post('/api/statements/{identity}/apply',status_code=202)
    def apply_statement(identity: str):
        statement_get(identity)
        try:
            session=statements.queue_apply(identity)
        except ValueError as exc:
            raise HTTPException(409,str(exc))
        worker.wake.set()
        return session

    @app.delete('/api/statements/uploads/{token}', status_code=204)
    def cancel_statement_upload(token: str):
        try:
            statements.cancel_upload(token)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post('/api/statements/{identity}/cancel')
    def cancel_statement_apply(identity: str):
        statement_get(identity)
        try:
            return statements.cancel_apply(identity)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.delete('/api/statements/{identity}', status_code=204)
    def discard_statement(identity: str):
        with statements.mutex:
            saved=statement_get(identity)
            if saved.get('apply_started') or saved['state']=='applying': raise HTTPException(409,'The checked apply has started')
            saved.update(cancelled=True,generation=saved.get('generation',0)+1)
            statements.put(saved,force=True)
        try:
            worker.call(lambda: retention.operate(secrets.token_hex(16),dict(action='queue-discard',item_id=identity)))
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post('/api/statements/{identity}/keep')
    def keep_statement(identity: str):
        # Use the same lock order as handover/restore.
        with drafts.mutex, statements.mutex:
            statements.purge()
            s = statement_get(identity, include_discarded=True)
            if s.get('trashed_at'): raise HTTPException(409,'Change the shared time limits in Settings')
            until = (datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()
            if s.get('discarded_until'):
                s['discarded_until'] = until
            elif not s.get('handover_draft') or s['state'] in ('applying','applied'):
                raise HTTPException(409, 'There is no recoverable handover or discard.')
            if s.get('handover_draft'):
                try:
                    draft = drafts.get(s['handover_draft'], include_discarded=True)
                except FileNotFoundError:
                    raise HTTPException(409, 'The handover recovery time has ended.')
                if not draft.get('discarded_until') or datetime.fromisoformat(draft['discarded_until']) <= datetime.now(timezone.utc):
                    raise HTTPException(409, 'The handover recovery time has ended.')
                s['handover_until'] = until
                draft['discarded_until'] = until
                drafts.put(draft)
            statements.put(s)
            return s

    @app.post('/api/statements/{identity}/restore')
    def restore_statement(identity: str):
        statement_get(identity, include_discarded=True)
        try:
            worker.call(lambda: retention.operate(secrets.token_hex(16),dict(action='queue-restore',item_id=identity)))
            s = statements.get(identity)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        except FileNotFoundError:
            raise HTTPException(404, 'The recovery time has ended.')
        worker.wake.set()
        return s

    @app.post('/api/drafts/{identity}/statements',status_code=201)
    def handover_statement(identity: str):
        with drafts.mutex, statements.mutex:
            d=get(identity)
            if d.get('handover_session'):
                return statement_get(d['handover_session'])
            if d['state']=='working' or d.get('claim'):
                raise HTTPException(409,'Release the hand form or wait for the draft to finish reading.')
            file=next((f for f in d['files'] if f['id']==d.get('statement_file')),None)
            if not file:
                raise HTTPException(409,'This draft has no recognised statement.')
            session=statements.create((drafts.folder(identity)/'files'/file['name']).read_bytes(),file['original_name'],
                                      client_time=d['client_time'], client_zone=d['client_zone'])
            session['handover_draft']=identity
            session['handover_until']=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()
            statements.put(session)
            d['handover_session']=session['id']
            d['discarded_until']=session['handover_until']
            drafts.put(d)
        worker.wake.set()
        return session

    @app.api_route('/{path:path}', methods=['GET', 'HEAD'])
    def static(path: str):
        dash = settings.application / 'review-dashboard'
        parts = Path(path).parts
        if any(part.startswith('.') for part in parts):
            raise HTTPException(404)
        if path.startswith('data/'):
            base = public_data(settings.dashboard_data)
            target = base / path.removeprefix('data/')
            allowed = target.resolve().is_relative_to(base.resolve())
        else:
            base = dash / 'dist'
            target = base / path
            if path == 'bills' or path.startswith('bills/'):
                link = base / 'bills'
                allowed = ((settings.ledger / 'Bills').resolve().is_relative_to(settings.ledger.resolve())
                           and link.is_symlink() and link.resolve() == (settings.ledger / 'Bills').resolve()
                           and target.resolve().is_relative_to((settings.ledger / 'Bills').resolve()))
            elif path.startswith('statements/') and Path(path).suffix.lower() in ('.xls', '.xlsx'):
                name = parts[1] if len(parts) == 2 else ''
                link = base / 'statements' / name
                allowed = (bool(name) and Path(name).suffix.lower() in ('.xls', '.xlsx') and link.is_symlink()
                           and link.resolve() == (settings.ledger / name).resolve() and not (settings.ledger / name).is_symlink())
            else:
                allowed = target.resolve().is_relative_to(base.resolve())
        if target.is_dir():
            target = target / 'index.html'
            allowed = allowed and target.resolve().is_relative_to(base.resolve())
        if not allowed or not target.is_file():
            raise HTTPException(404)
        return FileResponse(target, headers={'Cache-Control': 'no-store'} if path.startswith('data/') else None)

    return app


if __name__ == '__main__':
    import uvicorn
    config = Settings.from_env()
    uvicorn.run(create_app(config), host='127.0.0.1', port=config.port)
