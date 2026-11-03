"""Disposable browser-test host. Test controls never enter the production app."""
from datetime import datetime, timezone
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
app = create_app(settings, review_clock=lambda: datetime(2026,11,1,12,tzinfo=timezone.utc), agent_clock=lambda: datetime.now(timezone.utc))
from fastapi.responses import Response

@app.post('/__test/formatting')
def formatting_module():
    source = (settings.application / 'review-dashboard/scripts/formatting.mjs').read_text()
    for name, file in [('currencyCodes', 'currencies.json'), ('currencyMinorUnits', 'currency-minor-units.json')]:
        source = source.replace(f"import {name} from '../../schema/{file}' with {{type: 'json'}};", f'const {name} = ' + (settings.application / 'schema' / file).read_text() + ';')
    return Response(source, media_type='text/javascript')

config = Path(os.environ['EXPENSES_FAKE_CONFIG'])
original_audit = {path:path.read_bytes() for path in (settings.ledger/'data/additions').glob('*.jsonl')}
original = {path: read_json(path) for path in [settings.ledger/'data/accounts.json',settings.ledger/'data/card-statements.json',settings.ledger/'data/forecast.json',settings.ledger/'ledger.json', *sorted((settings.ledger/'data/expenses').glob('*.json'))]}


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


@app.post('/__test/scenario')
def scenario(body: dict):
    from copy import deepcopy
    from ledger_manifest import module_settings, read_manifest
    documents = read_json(settings.ledger/'data/accounts.json')
    accounts = documents['accounts']
    original_card = next(a for a,m in accounts.items() if m.get('kind')=='card')
    currency = accounts[original_card]['currency']
    account = next((a for a in accounts if a.startswith('Liabilities:Credit Cards:Workflow ')), 'Liabilities:Credit Cards:Workflow '+secrets.token_hex(4))
    workflow_new = account not in accounts
    accounts[account] = dict(kind='card',currency=currency,label=accounts[original_card].get('label','Workflow Card'),statement=dict(reader='example-card-pdf',identifier='9001'))
    foreign = 'EUR' if currency != 'EUR' else 'GBP'
    expense = next(a for a in accounts if a.startswith('Expenses:'))
    source = next(a for a,m in accounts.items() if m.get('kind')=='bank')
    destination = next((a for a,m in accounts.items() if m.get('kind')=='bank' and m['currency']!=accounts[source]['currency']),None)
    changed=workflow_new
    for code in (foreign,currency):
        if code not in documents['commodities']:
            documents['commodities'].append(code);changed=True
    if not destination:
        destination='Assets:Bank:Willow Transfer'
        accounts[destination]=dict(kind='bank',currency=foreign,label='Willow Transfer')
        changed=True
    if changed:
        changes={settings.ledger/'data/accounts.json':documents}
        if workflow_new:
            path=settings.ledger/'data/expenses/2026.json'
            rows=read_json(path)
            if not any(p['account']==destination for file in (settings.ledger/'data/expenses').glob('*.json') for row in read_json(file) for p in row['postings']):
                rows.append(dict(id='e'*16,date='2026-11-01',status='confirmed',description='Workflow destination opening',postings=[dict(account=destination,amount='0.00',commodity=accounts[destination]['currency']),dict(account='Equity:Opening Balances',amount='0.00',commodity=accounts[destination]['currency'])]))
            rows.append(dict(id='f'*16,date='2026-11-01',status='confirmed',description='Workflow card opening',postings=[dict(account=account,amount='0.00',commodity=currency),dict(account='Equity:Opening Balances',amount='0.00',commodity=currency)]))
            changes[path]=rows
        update_settings(settings,lambda:changes,dict(fixture=True),'Browser currency workflow accounts')
    answer=dict(status='ready',question=None,explanation=None,attachment_names=[body['attachment']+'-'+account.split()[-1]] if body.get('attachment') else [],doubts=[],new_accounts=[],
                entry=dict(date='2026-11-02',status='confirmed',description='Copper Finch Café',order_id=None,reference=None,classification=None,original_amount=None,notes=[],
                           postings=[dict(account=expense,amount='18.60',commodity=currency,cost=None),dict(account=account,amount='-18.60',commodity=currency,cost=None)]))
    if module_settings(read_manifest(settings.ledger))['fuel']:
        answer['entry'].update(fuel_filled_litres=None,trip_km=None)
    mode=body['mode']
    if mode=='new-root':
        answer['entry']['postings'][0]['account']='Shared:Accounts Receivable:Finch'
        answer['new_accounts']=['Shared:Accounts Receivable:Finch']
    if mode=='question':
        answer.update(status='question',question='Which account did you pay from?',entry=None)
    elif mode=='conversion':
        answer.update(status='question',question='How much was charged?')
        answer['entry'].update(description='Juniper Table',original_amount=dict(amount='46.80',commodity=foreign))
        answer['entry']['postings'][0].update(amount='46.80',commodity=foreign)
        answer['entry']['postings'][1]['amount']='0'
    elif mode=='transfer':
        answer.update(status='question',question='How much arrived?')
        answer['entry']['description']='Move money between accounts'
        answer['entry']['postings']=[dict(account=source,amount='-1000',commodity=accounts[source]['currency'],cost=None),dict(account=destination,amount='0',commodity=accounts[destination]['currency'],cost=None)]
    data=json.loads(config.read_text());data['steps']=[dict(kind='exit')] if mode=='failure' else [dict(answer=answer)]
    config.write_text(json.dumps(data));config.with_suffix('.count').unlink(missing_ok=True)
    return dict(account=account,currency=currency,foreign=foreign,source=source,destination=destination,sourceCurrency=accounts[source]['currency'],destinationCurrency=accounts[destination]['currency'])


@app.post('/__test/reset-workflows')
def reset_workflows():
    # Called only once all real worker operations/audits have completed.
    with app.state.drafts.mutex,app.state.statements.mutex:
        assert not any(d.get('audit_pending') for d in app.state.drafts.all(include_discarded=True))
        for d in app.state.drafts.all(include_discarded=True):
            shutil.rmtree(app.state.drafts.folder(d['id']))
        for session in app.state.statements.all():
            shutil.rmtree(app.state.statements.folder(session['id']))
    return update_settings(settings,lambda:original,dict(reset=True),'Restore browser workflow baseline')


@app.post('/__test/reset-review')
def reset_review():
    from store import dump
    from transactions import jj
    # Test-only reset in a disposable repository. Production audit is append-only;
    # tests restore the complete seed before exercising the next matrix case.
    with ledger_lock(settings.ledger):
        for path in (settings.ledger/'data/additions').glob('*.jsonl'):
            if path not in original_audit:
                path.unlink()
        for path,content in original_audit.items():
            path.write_bytes(content)
        for path,document in original.items():
            dump(path,document)
        jj(settings.ledger,'commit','-m','Restore fictional browser review baseline')
        jj(settings.ledger,'bookmark','set','main','-r','@-')
    from transactions import refresh
    return refresh(settings,dict(reset=True))


@app.post('/__test/receipt')
def receipt_file():
    from fastapi.responses import Response
    from PIL import Image
    import io
    output=io.BytesIO();Image.new('RGB',(64,64),'orange').save(output,format='PNG')
    return Response(output.getvalue(),media_type='image/png')


@app.post('/__test/statement')
def statement_file(currency: str, foreign: str, identifier: str='5678'):
    from fastapi.responses import Response
    sys.path.insert(0,str(settings.application/'review-dashboard/server/tests'))
    from statement_fixtures import statement_pdf
    assert currency.isalpha() and len(currency)==3 and foreign.isalpha() and len(foreign)==3
    text=f"""Example Card Statement
Statement currency: {currency}
Statement period: 2026-11-01 to 2026-11-28
Card last four digits: {identifier}
Previous balance: 0.00
Purchases: 78.47
Cash advances: 0.00
Credits: 0.00
Total due: 78.47
Minimum due: 7.85
Due date: 2026-12-15
Date | Serial | Description | Amount | Direction | Foreign amount | Currency
2026-11-02 | WF-001 | Juniper Table | 51.62 | Debit | 46.80 | {foreign}
2026-11-03 | WF-002 | Copper Finch Café | 18.60 | Debit | |
2026-11-04 | WF-003 | Cloudberry Music | 8.25 | Debit | |
""".replace('Café','Cafe')
    return Response(statement_pdf(text),media_type='application/pdf')


@app.post('/__test/reader-scenario')
def reader_scenario(body: dict):
    from copy import deepcopy
    extension = body['extension']
    assert extension in ('csv','xlsx','xls')
    documents = read_json(settings.ledger/'data/accounts.json')
    currency = read_json(settings.ledger/'ledger.json')['defaultCurrency']
    tag = body.get('tag','development')
    assert isinstance(tag,str) and len(tag)<100 and all(c.isalnum() or c=='-' for c in tag)
    account = 'Assets:Bank:Statement Workflow '+tag+' '+extension
    documents['accounts'][account] = dict(kind='bank',currency=currency,label='Statement Workflow',statement=dict(reader='generic-csv' if extension=='csv' else 'generic-sheet',options=dict(header_row=2,sheet='Activity',columns=dict(date='Date',description='Description',debit='Debit',credit='Credit',balance='Balance',reference='Reference'),period=dict(start='2026-10-28',end='2026-11-28'))))
    manifest=read_json(settings.ledger/'ledger.json');manifest['startDate']='2026-11-01'
    update_settings(settings,lambda:{settings.ledger/'data/accounts.json':documents,settings.ledger/'ledger.json':manifest},dict(reader=True),'Fictional statement reader account')
    return dict(account=account,currency=currency,reader='Generic CSV' if extension=='csv' else 'Generic spreadsheet')


@app.post('/__test/reader-file')
def reader_file(extension: str):
    from fastapi.responses import Response
    import io
    import csv
    assert extension in ('csv','xlsx','xls')
    rows = [['Fictional account export'],['Date','Description','Debit','Credit','Balance','Reference'],
            ['2026-10-31','Covered Fable purchase','50.00','','1000.00','WF-BANK-OLD'],
            ['2026-11-02','Paper Kite Market','24.50','','975.50','WF-BANK-001'],
            ['2026-11-03','Lantern Printworks','15.00','','960.50','WF-BANK-002'],
            ['2026-11-04','Fable refund','','4.50','965.00','WF-BANK-003']]
    if extension == 'csv':
        output=io.StringIO();csv.writer(output).writerows(rows)
        return Response(output.getvalue().encode(),media_type='text/csv')
    output=io.BytesIO()
    if extension == 'xlsx':
        import openpyxl
        book=openpyxl.Workbook();sheet=book.active;sheet.title='Activity'
        for row in rows:sheet.append(row)
        book.save(output)
    else:
        import xlwt
        book=xlwt.Workbook();sheet=book.add_sheet('Activity')
        for r,row in enumerate(rows):
            for c,value in enumerate(row):sheet.write(r,c,value)
        book.save(output)
    return Response(output.getvalue(),media_type='application/octet-stream')




@app.post('/__test/finish')
def finish_controls(body: dict):
    worker=app.state.worker
    if body.get('clear'):
        worker.pause.success();worker.wake.set();return dict(ok=True)
    worker.pause.fail('usage_limit')
    draft=app.state.drafts.create('Copper Finch Café, finish by hand')
    form=json.loads(config.read_text())['steps'][0]['answer']
    draft.update(state='failed',queued_at=now(),saved_at=now(),last_proposal=form,error='Usage limit',failure_kind='usage_limit')
    app.state.drafts.put(draft)
    return dict(id=draft['id'])

@app.post('/__test/rules')
def recurring_controls(body: dict):
    from copy import deepcopy
    from ledger_manifest import module_settings, read_manifest
    from decimal import Decimal
    from audit import append, event, audit_path
    from store import dump
    currency=read_manifest(settings.ledger)['defaultCurrency']
    declarations=read_json(settings.ledger/'data/accounts.json')
    accounts=declarations['accounts']
    pay=next(a for a,m in accounts.items() if m.get('kind')=='bank' and m['currency']==currency)
    foreign='EUR' if currency!='EUR' else 'GBP'
    if foreign not in declarations['commodities']: declarations['commodities'].append(foreign)
    accounts['Equity:Rule Unassigned']={}
    rules=[]
    labels=['Birch rent','Clover music','Clover lessons','Fable support','Lantern journal','Cedar archive','Fern rent','Hawthorn dues','Iris cover','Juniper ended']
    for i,label in enumerate(labels,1):
        category='Expenses:Rule Workflow:'+label
        accounts[category]={}
        amount=['100.00','40.00','41.00','15.00','10.00','20.00','60.00','30.00','25.00','18.00'][i-1]
        day='2026-11-02' if i in (1,8) else '2026-11-01' if i in (7,9) else '2026-11-15'
        r=dict(id=f'f00000000000a{i:03x}',date=day,status='confirmed',description=label,lifecycle='paused' if i==6 else 'ended' if i==10 else 'active',origin=dict(kind='owner'),schedule=dict(every='month',**{'from':day}),postings=[dict(account=category,amount=amount,commodity=currency),dict(account=pay,amount='-'+amount,commodity=currency)])
        if i==4:r['postings'][-1]['account']='Equity:Rule Unassigned'
        if i==5:
            r['postings'][0].update(commodity=foreign,cost=dict(amount='12.00',commodity=currency,per='total'))
            r['postings'][-1]['amount']='-12.00'
        rules.append(r)
    path=settings.ledger/'data/expenses/2026.json'
    rows=read_json(path)
    stale=rules[6]
    rows.append(dict(id='000000000000a001',date='2026-11-01',status='confirmed',description=stale['description'],postings=deepcopy(stale['postings'])))
    # Weak history is a proposal; dismissal must survive reload.
    for tag in ('Mallow workshop','Olive workshop'):
        category='Expenses:Rule Workflow:'+tag;accounts[category]={}
        for i,day in enumerate(('2026-09-03','2026-10-22')):
            rows.append(dict(id=f'000000000000a{(2 if tag.startswith("Mallow") else 4)+i:03x}',date=day,status='confirmed',description=tag,postings=[dict(account=category,amount='12.00',commodity=currency),dict(account=pay,amount='-12.00',commodity=currency)]))
    rows.sort(key=lambda r:r['date'])
    # A rule made automatically with an outstanding review proposal.
    automatic=rules[8];automatic['origin']=dict(kind='subscription-words',words='annual cover subscription')
    proposal=event('rule-review',rule_id=automatic['id'],rule_verdict='propose-removal',reason='The owner should check whether this cover is still needed.',reviewer='Offline fixture reviewer')
    changes={settings.ledger/'data/accounts.json':declarations,settings.ledger/'data/forecast.json':rules,path:rows}
    audit=audit_path(settings.ledger,proposal)
    prior=audit.read_text() if audit.exists() else ''
    # update_settings writes JSON documents. Append this fixture verdict under its
    # mutation callback so the same verified test-only commit owns all seed data.
    def install():
        append(settings.ledger,proposal)
        return changes
    try:update_settings(settings,install,dict(rules=True),'Fictional recurring browser rules')
    except BaseException:
        with ledger_lock(settings.ledger):
            if prior:audit.write_text(prior)
            else:audit.unlink(missing_ok=True)
        raise
    # Configure only the existing offline fake, with a complete balanced payment.
    mode=body.get('agent','automatic')
    if mode=='paused':
        template=rules[5];description=template['description'];postings=deepcopy(template['postings'])
    else:
        category='Expenses:Rule Workflow:New membership';declarations['accounts'][category]={}
        update_settings(settings,lambda:{settings.ledger/'data/accounts.json':declarations},dict(category=True),'Fictional recurring agent category')
        description='Pine studio';postings=[dict(account=category,amount='22.00',commodity=currency),dict(account=pay,amount='-22.00',commodity=currency)]
    answer=dict(status='ready',question=None,explanation=None,attachment_names=[],doubts=[],new_accounts=[],entry=dict(date='2026-11-02',status='confirmed',description=description,order_id=None,reference=None,classification=None,original_amount=None,notes=[],postings=[dict(p,cost=None) for p in postings]))
    if module_settings(read_manifest(settings.ledger))['fuel']:answer['entry'].update(fuel_filled_litres=None,trip_km=None)
    data=json.loads(config.read_text());data['steps']=[dict(answer=answer)];config.write_text(json.dumps(data));config.with_suffix('.count').unlink(missing_ok=True)
    return dict(currency=currency,pay=pay,foreign=foreign,rules=rules)


@app.post('/__test/rule-agent')
def rule_agent(body: dict):
    from ledger_manifest import module_settings, read_manifest
    rule=next(r for r in read_json(settings.ledger/'data/forecast.json') if r['description']=='Cedar archive')
    answer=dict(status='ready',question=None,explanation=None,attachment_names=[],doubts=[],new_accounts=[],entry=dict(date='2026-11-02',status='confirmed',description=rule['description'],order_id=None,reference=None,classification=None,original_amount=None,notes=[],postings=[dict(p,cost=None) for p in rule['postings']]))
    if module_settings(read_manifest(settings.ledger))['fuel']:answer['entry'].update(fuel_filled_litres=None,trip_km=None)
    data=json.loads(config.read_text());data['steps']=[dict(answer=answer)];config.write_text(json.dumps(data));config.with_suffix('.count').unlink(missing_ok=True)
    return dict(ok=True)


uvicorn.run(app,host='127.0.0.1',port=settings.port,log_level='warning')
