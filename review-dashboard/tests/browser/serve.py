"""Disposable browser-test host. Test controls never enter the production app."""
import json
import os
from pathlib import Path
import secrets
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server'))
from app import create_app
from settings import Settings
from agent import now
from currency_settings import update_settings
from ledger_lock import ledger_lock
from store import read_json
import uvicorn

settings = Settings.from_env()
assert settings.ledger.resolve().is_relative_to(Path('/tmp').resolve())
assert settings.state_dir.is_relative_to(Path('/tmp').resolve())
assert settings.codex_home.is_relative_to(Path('/tmp').resolve())
assert Path(settings.codex).name == 'fake-agent'
app = create_app(settings)
config = Path(os.environ['EXPENSES_FAKE_CONFIG'])
original = {path: read_json(path) for path in [settings.ledger/'ledger.json', *sorted((settings.ledger/'data/expenses').glob('*.json'))]}


@app.post('/__test/gate')
def gate(body: dict):
    mode = body['state']
    assert mode in ('ready', 'signed out', 'paused', 'unavailable')
    # Serialize fixture configuration with the real startup/login checks. The
    # login watcher is exercised in gate unit tests; it must not join an older
    # check while this host changes the fake CLI's capabilities.
    app.state.agent_gate.close()
    app.state.agent_gate.stop.clear()
    answer = dict(status='question', question='Which account did you pay from?', explanation=None,
                  attachment_names=[], doubts=[], new_accounts=[], entry=None)
    config.write_text(json.dumps(dict(version='fixture-'+secrets.token_hex(4), steps=[dict(answer=answer)],
                                     signed_out=mode == 'signed out', extra_tool='exec_command' if mode == 'paused' else None,
                                     probe_fail=mode == 'unavailable')))
    if body.get('check') is False:
        return app.state.agent_gate.status()
    try:
        app.state.agent_gate.require_ready()
    except ValueError:
        pass
    return app.state.agent_gate.status()


@app.post('/__test/drafts')
def seed_drafts(body: dict):
    drafts, statements = app.state.drafts, app.state.statements
    with drafts.mutex, statements.mutex:
        for draft in drafts.all(include_discarded=True):
            assert not draft.get('audit_pending')
            shutil.rmtree(drafts.folder(draft['id']))
        for session in statements.all():
            shutil.rmtree(statements.folder(session['id']))
        if body.get('empty'):
            return dict(attention=0, ids=[])
        states = ['question', 'failed'] if body['persona'] == 'asha' else ['question'] if body['persona'] == 'sam' else ['question', 'question', 'failed']
        ids=[]
        for i, state in enumerate([*states, 'working']):
            draft=drafts.create(['Juniper Table · needs charged amount', 'Paper Kite Market · payment account', 'Cedar Taxi', 'Lantern Printworks'][i])
            draft.update(state=state, saved_at=now(), error='Connection interrupted.' if state=='failed' else None)
            if state=='question':
                draft['questions']=[dict(question='Which account did you pay from?', answer=None)]
            if state=='working':
                draft['text']='Lantern Printworks · reading receipt'
            drafts.put(draft);ids.append(draft['id'])
        if body['persona']=='jordan':
            statements.put(dict(id=secrets.token_hex(16),state='review',original_name='lumen-october.pdf',created_at=now(),error=None,waiting=None,plan=dict(items=[],counts={},unmatched=[])))
        return dict(attention=len(states)+(body['persona']=='jordan'), ids=ids)


@app.post('/__test/openings')
def openings(body: dict):
    assert os.environ['BROWSER_PERSONA']=='empty'
    if body['enabled']:
        manifest={**original[settings.ledger/'ledger.json']}
        accounts=read_json(settings.ledger/'data/accounts.json')['accounts']
        rows=[]
        for i,(label,amount) in enumerate([('Alder Checking','1840.25'),('Meadow Savings','4650.00'),('Lumen Card','-337.50')],1):
            account=next(name for name,meta in accounts.items() if meta.get('label')==label)
            from decimal import Decimal
            rows.append(dict(id=f'{i:016x}',date=manifest['startDate'],status='confirmed',description=label+' opening balance',postings=[dict(account=account,amount=amount,commodity='USD',assert_=None),dict(account='Equity:Opening Balances',amount=str(-Decimal(amount)),commodity='USD')]))
            rows[-1]['postings'][0].pop('assert_')
            rows[-1]['postings'][0]['assert']=dict(amount=amount,commodity='USD')
        changes={settings.ledger/'data/expenses/2026.json':rows}
    else:
        changes=original
    return update_settings(settings,lambda:changes,dict(openings=body['enabled']),'Browser fixture opening balances')


uvicorn.run(app,host='127.0.0.1',port=settings.port,log_level='warning')
