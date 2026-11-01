"""Live expense backend. Run from this directory with uv run app.py."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import re
import io
import json
from pathlib import Path
import secrets
import shutil
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
import pypdfium2 as pdfium

from agent import now
from draft_time import client_context
from agent_gate import get_gate
from drafts import Drafts
from settings import Settings
from ledger_manifest import read_manifest
from worker import Worker
from statements import Statements

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


def sniff(data):
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
    raise HTTPException(415, 'Only valid JPEG, PNG, WebP and PDF files are allowed')


class DashboardGZipMiddleware(GZipMiddleware):
    async def __call__(self, scope, receive, send):
        if scope.get('path', '').endswith('.gz'):
            await self.app(scope, receive, send)
        else:
            await super().__call__(scope, receive, send)


def create_app(settings=None, *, start_worker=True, review_clock=None):
    settings = settings or Settings.from_env()
    read_manifest(settings.ledger)
    drafts = Drafts(settings.ledger)
    worker = Worker(settings, drafts)
    statements = Statements(settings)
    worker.statements = statements
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
            raise HTTPException(404, 'Unknown draft')

    def require(draft, *states):
        if draft['state'] not in states:
            raise HTTPException(409, f"Draft is {draft['state']}; expected {', '.join(states)}")

    def queue(draft, proposal=None):
        draft.update(state='queued', queued_at=now(), error=None, waiting=None, proposal=proposal)
        drafts.put(draft)
        worker.wake.set()
        return draft

    @app.get('/api/health')
    def health():
        return {'ok': True, 'base_path': settings.base_path, 'worker': bool(worker.thread and worker.thread.is_alive()),
                'agent': gate.status()}

    @app.get('/api/agent-status')
    def agent_status():
        # Reading status must never start a check or run an agent.
        return gate.status()

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
            return apply(settings, body, clock=review_clock)
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
        drafts.put(draft)
        return draft

    @app.get('/api/drafts')
    def list_drafts(include_added: bool = False):
        drafts.purge()
        return sorted((d for d in drafts.all() if include_added or d['state'] != 'added'), key=lambda d: d['created_at'])

    @app.get('/api/drafts/{identity}')
    def read(identity: str):
        return get(identity)

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
            draft.update(values)
            drafts.put(draft)
            return draft

    @app.post('/api/drafts/{identity}/files', status_code=201)
    def upload(identity: str, files: list[UploadFile] = File(...), upload_id: str | None = Form(None)):
        with drafts.mutex:
            draft = get(identity)
            require(draft, 'editing')
            if upload_id is not None:
                if not re.fullmatch('[a-f0-9]{32}', upload_id) or len(files) != 1:
                    raise HTTPException(422, 'An upload id identifies one file')
                if upload_id in draft.get('cancelled_uploads', []):
                    return draft
                if any(f['id'] == upload_id for f in draft['files']):
                    return draft
            if len(files) + len(draft['files']) > MAX_FILES:
                raise HTTPException(413, 'At most 10 files per draft')
            staged = []
            for file in files:
                data = file.file.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise HTTPException(413, 'At most 25 MB per file')
                extension, media = sniff(data)
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
            return draft

    @app.get('/api/drafts/{identity}/files/{file_id}')
    def read_file(identity: str, file_id: str):
        draft = get(identity)
        file = next((f for f in draft['files'] if f['id'] == file_id), None)
        path = drafts.folder(identity) / 'files' / file['name'] if file else None
        if not path or not path.is_file() or path.is_symlink():
            raise HTTPException(404, 'Unknown file')
        return FileResponse(path, media_type=file['media_type'], headers={'Cache-Control': 'no-store'})

    @app.delete('/api/drafts/{identity}/files/{file_id}')
    def remove_file(identity: str, file_id: str):
        with drafts.mutex:
            draft = get(identity)
            require(draft, 'editing')
            file = next((f for f in draft['files'] if f['id'] == file_id), None)
            if not file:
                raise HTTPException(404, 'Unknown file')
            draft['files'].remove(file)
            drafts.put(draft)
            (drafts.folder(identity) / 'files' / file['name']).unlink(missing_ok=True)
            return draft

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
            return draft

    @app.patch('/api/drafts/{identity}/unsent-answer')
    def keep_answer(identity: str, body: UnsentAnswer):
        with drafts.mutex:
            draft = get(identity)
            require(draft, 'question')
            if body.question_index != len(draft['questions']) - 1 or draft['questions'][-1]['answer'] is not None:
                raise HTTPException(409, 'This question has already changed')
            draft['unsent_answer'] = body.model_dump()
            drafts.put(draft)
            return draft

    @app.post('/api/drafts/{identity}/save')
    def save(identity: str, body: SaveTime | None = None):
        with drafts.mutex:
            draft = get(identity)
            require(draft, 'editing')
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

    @app.post('/api/drafts/{identity}/retry')
    def retry(identity: str):
        with drafts.mutex:
            draft = get(identity)
            require(draft, 'failed')
            draft['check_errors'] = []
            return queue(draft)

    @app.delete('/api/drafts/{identity}', status_code=204)
    def discard(identity: str):
        with drafts.mutex:
            draft = get(identity)
            if draft['state'] == 'working':
                raise HTTPException(409, 'Cannot discard a working draft')
            draft['discarded_until'] = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
            drafts.put(draft)

    @app.post('/api/drafts/{identity}/restore')
    def restore(identity: str):
        with drafts.mutex:
            drafts.purge()
            try:
                draft = drafts.get(identity, include_discarded=True)
            except (FileNotFoundError, ValueError):
                raise HTTPException(404, 'The Undo time has ended')
            if draft.get('discarded_until') and datetime.fromisoformat(draft['discarded_until']) <= datetime.now(timezone.utc):
                raise HTTPException(404, 'The Undo time has ended')
            if not draft.pop('discarded_until', None):
                raise HTTPException(409, 'Draft is not discarded')
            handed=draft.get('handover_session')
            if handed:
                with statements.mutex:
                    session=statements.get(handed,include_discarded=True)
                    if session['state'] in ('applying','applied'):
                        draft['discarded_until']=(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()
                        raise HTTPException(409,'The statement has started applying; the handover cannot be undone.')
                    session.update(cancelled=True,handover_undone=True,generation=session.get('generation',0)+1,discarded_until=datetime.now(timezone.utc).isoformat())
                    statements.put(session, force=True)
                    draft.pop('handover_session',None)
            drafts.put(draft)
            worker.wake.set()
            return draft

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
        return dict(sessions=[s for s in statements.all() if not s.get('discarded_until') and s['state'] not in ('applied','already_filed')], recoverable=[s for s in statements.all() if s.get('discarded_until') and not s.get('handover_undone')], filed=documents[settings.ledger/'data/card-statements.json'], accounts=list(documents[settings.ledger/'data/accounts.json']['accounts']))

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
        return statement_get(identity)

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
        statement_get(identity)
        try:
            statements.discard(identity)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post('/api/statements/{identity}/keep')
    def keep_statement(identity: str):
        # Use the same lock order as handover/restore.
        with drafts.mutex, statements.mutex:
            statements.purge()
            s = statement_get(identity, include_discarded=True)
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
            s = statements.restore(identity)
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
            if d['state']=='working':
                raise HTTPException(409,'Wait for the draft to finish reading.')
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
            base = dash / 'public/data'
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
