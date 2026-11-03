"""Post-save receipt readings; financial disagreements always need a human choice."""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import secrets
import re

import agent
from corrections import apply, wire_entry
from replacements import entry_hash, safe_evidence
from store import load, entries
from ledger_lock import ledger_lock


def validate_reading(answer):
    if answer['date']:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',answer['date']): raise ValueError('Invalid receipt date')
        date.fromisoformat(answer['date'])
    if answer['amount'] is not None and (not Decimal(answer['amount']).is_finite() or Decimal(answer['amount'])<0): raise ValueError('Invalid receipt amount')
    if answer['payment_datetime']:
        # A printed local time may have no offset; never supply an invented zone.
        if 'T' not in answer['payment_datetime']: raise ValueError('Receipt payment time must include a time')
        datetime.fromisoformat(answer['payment_datetime'])
    from transactions import reject_invisible
    reject_invisible(answer)


def payment_rows(entry, accounts):
    return [p for p in entry['postings'] if accounts.get(p['account'],{}).get('kind') in ('bank','card','cash','wallet') and Decimal(p['amount'])<0]


def read(worker, draft):
    settings=worker.settings
    with worker.drafts.mutex:
        try: draft=worker.drafts.get(draft['id'])
        except FileNotFoundError: return
        reading=draft['receipt_reading']
        ready=reading['state']=='ready'
        if not ready:
            if reading['state']!='queued': return
            reading['state']='reading';worker.drafts.put(draft)
    if ready:
        choose(settings,worker.drafts,draft,'agree');return
    for source in reading['sources']: safe_evidence(settings.ledger,source)
    task={**draft,'receipt_task':reading}
    try:
        answer,run=agent.run(settings,task,worker.drafts.folder(draft['id']),cancel=worker.stop_event)
    except agent.AgentFailure as exc:
        reading['run']=exc.record
        if exc.kind!='bad_answer':
            worker.pause.fail(exc.kind,exc.reset_at);reading['state']='queued'
        else: reading.update(state='unreadable',message='The receipt could not be read. Use Correct this entry to add its details.')
        worker.drafts.put(draft);return
    worker.pause.success();reading.update(answer=answer,run=run)
    if not answer['read_ok']:
        reading.update(state='unreadable',message='The receipt could not be read. Use Correct this entry to add its details.');worker.drafts.put(draft);return
    with ledger_lock(settings.ledger):
        documents=load(settings.ledger);entry=next((e for e in entries(documents) if e['id']==reading['entry_id']),None)
        if not entry: raise ValueError('The saved entry no longer exists')
        accounts=documents[settings.ledger/'data/accounts.json']['accounts'];payments=payment_rows(entry,accounts)
        reading.update(version=entry_hash(entry),entry=deepcopy(entry),operation_id=reading.get('operation_id') or secrets.token_hex(16))
        # Keep both sides structured; the page supplies its own date/money formats.
        totals={}
        for p in payments: totals[p['commodity']]=format(Decimal(totals.get(p['commodity'],'0'))-Decimal(p['amount']),'f')
        disagrees=bool(answer['date'] and answer['date']!=entry['date'] or answer['amount'] is not None and (answer['currency'] and (answer['currency'] not in totals or Decimal(answer['amount'])!=Decimal(totals[answer['currency']])) or not answer['currency'] and (len(totals)!=1 or Decimal(answer['amount'])!=Decimal(next(iter(totals.values()))))) or answer['paying_account'] and {p['account'] for p in payments}!={answer['paying_account']} or answer['currency'] and answer['currency'] not in totals)
        reading['entry_totals']=totals
    reading['state']='question' if disagrees else 'ready';worker.drafts.put(draft)
    if not disagrees: choose(settings,worker.drafts,draft,'agree')


def choose(settings, drafts, draft, choice):
    draft=drafts.get(draft['id'])
    reading=draft.get('receipt_reading',{})
    if choice not in ('use','keep','agree'): raise ValueError('Choose Use the receipt or Keep the entry')
    if reading.get('state')=='done':
        if reading.get('choice')!=choice: raise ValueError('This receipt question was already answered')
        return draft
    if reading.get('state') not in ('question','ready'): raise ValueError('The receipt is not waiting for an answer')
    if choice=='agree' and reading['state']!='ready': raise ValueError('A receipt disagreement needs a choice')
    before=reading['entry'];answer=reading['answer'];proposal=wire_entry(settings,before);entry=proposal['entry']
    notes=list(entry['notes'])
    for note in [*(answer['notes'] or []), *(['Receipt payee: '+answer['payee']] if answer['payee'] else []), *(['Receipt payment time: '+answer['payment_datetime']] if answer['payment_datetime'] else [])]:
        if note not in notes: notes.append(note)
    references=answer['references'] or []
    if references:
        if not entry['reference']: entry['reference']=references[0]
        for reference in references:
            if reference!=entry['reference'] and 'Receipt reference: '+reference not in notes: notes.append('Receipt reference: '+reference)
    entry['notes']=notes
    if choice=='use':
        accounts=load(settings.ledger)[settings.ledger/'data/accounts.json']['accounts'];payers=payment_rows(before,accounts)
        financial_change=answer['amount'] is not None and (len(payers)!=1 or Decimal(answer['amount'])!=-Decimal(payers[0]['amount'])) or answer['currency'] and any(p['commodity']!=answer['currency'] for p in payers) or answer['paying_account'] and {p['account'] for p in payers}!={answer['paying_account']}
        if financial_change and (len(entry['postings'])!=2 or len(payers)!=1 or any(p['cost'] for p in entry['postings']) or len({p['commodity'] for p in entry['postings']})!=1):
            raise ValueError('The receipt changes a split payment or currency conversion. Use Correct this entry to allocate it, or Keep the entry.')
        if answer['date']: entry['date']=answer['date']
        if financial_change:
            code=answer['currency'] or payers[0]['commodity'];account=answer['paying_account'] or payers[0]['account']
            if accounts.get(account,{}).get('kind') not in ('bank','card','cash','wallet') or accounts[account]['currency']!=code: raise ValueError('The receipt account or currency needs Correct this entry.')
            value=answer['amount'] or str(-Decimal(payers[0]['amount']))
            for p in entry['postings']:
                if p['account']==payers[0]['account']: p.update(account=account,amount='-'+value,commodity=code)
                else: p.update(amount=value,commodity=code)
    body=dict(version=reading['version'],proposal=proposal,sources=before.get('sources',[]),reason='Receipt details',approved_accounts=[],receipt=dict(answer=answer,choice=choice,run=reading.get('run'),entry_totals=reading['entry_totals']))
    result=apply(settings,before['id'],reading['operation_id'],body,drafts=drafts,actor='receipt reader',trailer=f'Added-by: {settings.adapter}/{settings.model} (receipt details for {before["id"]})')
    # Fetch the latest draft so a failed response cannot erase a retained answer.
    draft=drafts.get(draft['id'],include_discarded=True);draft['receipt_reading'].update(state='done',choice=choice,result=result,message='Receipt details saved.' if choice!='keep' else 'Entry kept. Receipt notes saved.')
    draft['waiting']=None
    drafts.put(draft)
    return draft
