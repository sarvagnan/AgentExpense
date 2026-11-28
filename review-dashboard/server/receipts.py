"""Post-save receipt readings; financial disagreements always need a human choice."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import secrets
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import agent
from corrections import apply, wire_entry
from replacements import entry_hash, safe_evidence
from store import load, entries
from ledger_lock import ledger_lock
from transactions import payment_value


def date_order(locale):
    # Locale context is optional. Resolve its numeric field order through the
    # installed project Node's Intl data rather than assuming a region.
    import subprocess
    from node_toolchain import node_environment
    try:
        result = subprocess.check_output(['node','-e',"const l=process.argv[1]; if(Intl.DateTimeFormat.supportedLocalesOf([l]).length){const p=new Intl.DateTimeFormat(l).formatToParts(new Date(2001,10,22)); console.log(p.find(x=>x.type==='day'||x.type==='month').type)}",locale], env=node_environment(), text=True, stderr=subprocess.DEVNULL,timeout=5).strip()
        return result
    except (ValueError,OSError,subprocess.SubprocessError): return None



def printed_payment_time(raw):
    if not isinstance(raw, str) or raw[10:11] not in ('T', ' '):
        raise ValueError('Expected a date and time')
    stamp, _, zone = (raw[:10] + 'T' + raw[11:]).partition(' ')
    payment = datetime.fromisoformat(stamp)
    if not zone:
        return payment
    if payment.tzinfo is not None:
        raise ValueError('Expected one time zone')
    if zone.startswith(('+', '-')):
        return datetime.fromisoformat(stamp + zone)
    if zone in ('PST', 'PDT'):
        offset = -8 if zone == 'PST' else -7
        return payment.replace(tzinfo=timezone(timedelta(hours=offset)))
    return payment.replace(tzinfo=ZoneInfo(zone))


def validate_reading(answer, locale=None, client_zone=None):
    """Normalize uncertain printed facts independently; one bad fact never loses a receipt."""
    from decimal import InvalidOperation
    notes = answer.get('notes') or []
    def unknown(field, label):
        raw = answer.get(field)
        if raw is not None:
            import unicodedata
            readable=''.join(f'\\u{ord(c):04x}' if unicodedata.category(c) in ('Cc','Cf') else c for c in str(raw))
            notes.append(f'Receipt {label} not understood: {readable}')
        answer[field] = None
    raw = answer.get('date')
    if raw is not None:
        try:
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
                answer['date'] = date.fromisoformat(raw).isoformat()
            else:
                match = re.fullmatch(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', raw)
                if not match: raise ValueError()
                first, second, year = map(int, match.groups())
                if first > 12: day, month = first, second
                elif second > 12: month, day = first, second
                elif locale:
                    order = date_order(locale)
                    if order == 'day': day, month = first, second
                    elif order == 'month': month, day = first, second
                    else: raise ValueError()
                else: raise ValueError()
                answer['date'] = date(year, month, day).isoformat()
        except (ValueError, TypeError, LookupError): unknown('date', 'date')
    if answer.get('amount') is not None:
        try:
            value = Decimal(answer['amount'])
            if not value.is_finite() or value < 0: raise ValueError()
            answer['amount'] = format(value, 'f')
        except (InvalidOperation, ValueError, TypeError): unknown('amount', 'amount')
    if answer.get('payment_datetime') is not None:
        try:
            payment = printed_payment_time(answer['payment_datetime'])
            if payment.tzinfo is not None and client_zone:
                answer['date'] = payment.astimezone(ZoneInfo(client_zone)).date().isoformat()
        except (ValueError, ZoneInfoNotFoundError, OverflowError):
            unknown('payment_datetime', 'payment time')
    answer['notes'] = notes if notes else (None if answer.get('notes') is None else [])
    from transactions import reject_invisible
    reject_invisible(answer)


def payment_rows(entry, accounts):
    rows = []
    for posting in entry['postings']:
        kind = accounts.get(posting['account'], {}).get('kind')
        redeemed = kind == 'points' and bool(posting['cost'])
        if (kind in ('bank', 'card', 'cash', 'wallet') or redeemed) and Decimal(posting['amount']) < 0:
            code, amount = payment_value(posting, points=redeemed)
            rows.append(dict(posting, commodity=code, amount=format(amount, 'f')) if redeemed else posting)
    return rows


def payment_totals(payments):
    totals = {}
    for row in payments:
        code = row['commodity']
        totals[code] = format(Decimal(totals.get(code, '0')) - Decimal(row['amount']), 'f')
    return totals


def printed_currency_total(entry, totals, currency):
    if not currency:
        return next(iter(totals.values())) if len(totals) == 1 else None
    if currency in totals:
        return totals[currency]
    expenses = [p for p in entry['postings'] if p['account'].startswith('Expenses:')]
    if len(expenses) == 1:
        expense = expenses[0]
        if expense['commodity'] == currency and (expense['cost'] or {}).get('commodity') in totals:
            return expense['amount']
    return None


def amount_differs(answer, total):
    if total is None:
        return bool(answer['currency'] or answer['amount'] is not None)
    return answer['amount'] is not None and Decimal(answer['amount']) != Decimal(total)


def compare_entry(reading, entry, accounts):
    answer = reading['answer']
    payments = payment_rows(entry, accounts)
    totals = payment_totals(payments)
    currency = answer['currency']
    total = printed_currency_total(entry, totals, currency)
    disagrees = (answer['date'] and answer['date'] != entry['date']
                 or amount_differs(answer, total)
                 or answer['paying_account'] and {row['account'] for row in payments} != {answer['paying_account']})
    reading.update(version=entry_hash(entry), entry=deepcopy(entry), entry_totals=totals,
                   state='question' if disagrees else 'ready')
    reading['entry_expense'] = dict(amount=total, currency=currency) if currency and currency not in totals and total is not None else None


def resume_cancelled(worker, identity, run=None):
    with worker.drafts.mutex:
        latest=worker.drafts.get(identity,include_discarded=True)
        if latest.get('receipt_reading',{}).get('state')=='reading':
            reading=latest['receipt_reading']
            reading.update(state='queued', consecutive_timeouts=0)
            if run:
                run.update(outcome='interrupted', error='Receipt reading interrupted')
                reading['run']=run
            worker.drafts.put(latest)


def read(worker, draft):
    import agent_timeouts
    settings=worker.settings
    with worker.drafts.mutex:
        try: draft=worker.drafts.get(draft['id'])
        except FileNotFoundError: return
        reading=draft['receipt_reading']
        ready=reading['state']=='ready'
        if not ready:
            if reading['state']!='queued': return
            reading.update(state='reading',run=dict(start=agent.now(),end=None,model=settings.model,effort=settings.effort,outcome=None,question=None,doubts=[],error=None,duration=None,usage={},lookups=[]))
            worker.drafts.put(draft)
    if ready:
        choose(settings,worker.drafts,draft,'agree');return
    for source in reading['sources']: safe_evidence(settings.ledger,source)
    import threading
    cancel = threading.Event()
    worker.receipt_cancellations[reading['entry_id']] = cancel
    task={**draft,'receipt_task':reading}
    try:
        answer,run=agent.run(settings,task,worker.drafts.folder(draft['id']),cancel=cancel)
    except agent.AgentFailure as exc:
        worker.receipt_cancellations.pop(reading['entry_id'],None)
        if cancel.is_set():
            resume_cancelled(worker,draft['id'],exc.record)
            return
        reading['run']=exc.record
        reading.setdefault('runs', []).append(exc.record)
        reading.setdefault('attempt_runs', []).append(exc.record)
        if exc.kind == 'timeout':
            if agent_timeouts.failed(reading, exc.kind):
                message = agent_timeouts.reason(settings.timeout, receipt=True)
                reading.update(state='failed', message=message)
                from outcomes import queue_outcome
                snapshot = {**draft, 'runs': reading['attempt_runs'], 'error': message, 'result': {'sources': reading['sources']}}
                queue_outcome(snapshot, 'failed')
                draft['audit_pending'] = snapshot['audit_pending']
            else: reading['state']='queued'
        elif exc.kind!='bad_answer':
            agent_timeouts.reset(reading)
            worker.pause.fail(exc.kind,exc.reset_at);reading['state']='queued'
        else:
            agent_timeouts.reset(reading)
            reading.update(state='unreadable',message='The receipt could not be read. Use Edit entry to add its details.')
        worker.drafts.put(draft);return
    worker.receipt_cancellations.pop(reading['entry_id'],None)
    if cancel.is_set():
        resume_cancelled(worker,draft['id'])
        return
    with worker.drafts.mutex:
        latest=worker.drafts.get(draft['id'],include_discarded=True)
        if latest.get('receipt_reading',{}).get('state') == 'cancelled': return
    agent_timeouts.reset(reading)
    validate_reading(answer, draft.get('client_locale'), draft['client_zone'])
    reading.setdefault('runs', []).append(run)
    reading.setdefault('attempt_runs', []).append(run)
    worker.pause.success();reading.update(answer=answer,run=run)
    if not answer['read_ok']:
        reading.update(state='unreadable',message='The receipt could not be read. Use Edit entry to add its details.');worker.drafts.put(draft);return
    with ledger_lock(settings.ledger):
        documents=load(settings.ledger);entry=next((e for e in entries(documents) if e['id']==reading['entry_id']),None)
        if not entry: return
        # Keep both sides structured; the page supplies its own date/money formats.
        compare_entry(reading, entry, documents[settings.ledger/'data/accounts.json']['accounts'])
        reading['operation_id'] = reading.get('operation_id') or secrets.token_hex(16)
        worker.drafts.put(draft)
    if reading['state'] == 'ready': choose(settings,worker.drafts,draft,'agree')


def choose(settings, drafts, draft, choice, version=None):
    draft=drafts.get(draft['id'])
    reading=draft.get('receipt_reading',{})
    if choice not in ('use','keep','agree'): raise ValueError('Choose Use the receipt or Keep the entry')
    if reading.get('state')=='done':
        if reading.get('choice') not in (choice, 'agree'): raise ValueError('This receipt question was already answered')
        return draft
    if reading.get('state') not in ('question','ready'): raise ValueError('The receipt is not waiting for an answer')
    if choice=='agree' and reading['state']!='ready': raise ValueError('A receipt disagreement needs a choice')
    if reading['state'] == 'ready':
        choice = 'agree'
    elif version != reading['version'] and (version is not None or reading.get('reask')):
        # A replay of the old choice cannot answer the refreshed question.
        return draft

    def prepare_receipt(documents, current):
        nonlocal choice
        accounts = documents[settings.ledger/'data/accounts.json']['accounts']
        if entry_hash(current) != reading['version']:
            compare_entry(reading, current, accounts)
            reading.pop('refusal', None)
            reading['reask'] = 'The entry changed. Check the updated details.'
            draft['waiting'] = None
            drafts.put(draft)
            if reading['state'] == 'question':
                return None
            choice = 'agree'
        return correction_body(settings, reading, choice, accounts)

    entry_id = reading['entry_id']
    body = dict(version=reading['version'], receipt=dict(choice=choice))
    result = apply(settings, entry_id, reading['operation_id'], body, drafts=drafts, actor='receipt reader',
                   trailer=f'Added-by: {settings.adapter}/{settings.model} (receipt details for {entry_id})',
                   prepare_receipt=prepare_receipt)
    if result is None:
        return draft
    # Fetch the latest draft so a failed response cannot erase a retained answer.
    draft=drafts.get(draft['id'],include_discarded=True);draft['receipt_reading'].pop('refusal',None);draft['receipt_reading'].update(state='done',choice=choice,result=result,message='Receipt details saved.' if choice!='keep' else 'Entry kept. Receipt notes saved.')
    draft['waiting']=None
    drafts.put(draft)
    return draft


class ReceiptEditRequired(ValueError):
    pass


def correction_body(settings, reading, choice, accounts):
    before=reading['entry'];answer=reading['answer'];proposal=wire_entry(settings,before);entry=proposal['entry']
    notes=list(entry['notes'])
    for note in [*(answer['notes'] or []), *(['Receipt payee: '+answer['payee']] if answer['payee'] else []), *(['Receipt payment time: '+answer['payment_datetime']] if answer['payment_datetime'] else [])]:
        if note not in notes: notes.append(note)
    references=answer['references'] or []
    if references:
        group=entry['references'] or {'reference':None,'order-id':[]}
        if not group['reference']: group['reference']=references[0]
        for reference in references:
            if reference!=group['reference'] and 'Receipt reference: '+reference not in notes: notes.append('Receipt reference: '+reference)
        entry['references']=group
    entry['notes']=notes
    if choice=='use':
        payers=payment_rows(before,accounts)
        total = printed_currency_total(before, payment_totals(payers), answer['currency'])
        financial_change = (amount_differs(answer, total) or
                            answer['paying_account'] and {p['account'] for p in payers} != {answer['paying_account']})
        if financial_change and (len(entry['postings'])!=2 or len(payers)!=1 or any(p['cost'] for p in entry['postings']) or len({p['commodity'] for p in entry['postings']})!=1):
            raise ReceiptEditRequired('The receipt changes a split payment or currency conversion. Use Edit entry to allocate it, or Keep the entry.')
        if answer['date']: entry['date']=answer['date']
        if financial_change:
            code=answer['currency'] or payers[0]['commodity'];account=answer['paying_account'] or payers[0]['account']
            if accounts.get(account,{}).get('kind') not in ('bank','card','cash','wallet') or accounts[account]['currency']!=code:
                raise ReceiptEditRequired('Use Edit entry to change the receipt account or currency, or Keep the entry.')
            value=answer['amount'] or str(-Decimal(payers[0]['amount']))
            for p in entry['postings']:
                if p['account']==payers[0]['account']: p.update(account=account,amount='-'+value,commodity=code)
                else: p.update(amount=value,commodity=code)
    return dict(version=reading['version'],proposal=proposal,sources=before['evidence'],reason='Receipt details',approved_accounts=[],receipt=dict(answer=answer,choice=choice,run=reading.get('run'),entry_totals=reading['entry_totals']))
