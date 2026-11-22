"""Recurring-rule services; all financial writes use Transaction and the ledger lock."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
import secrets
import shutil

from transactions import BalanceRefusal, LedgerBusy, Transaction, clean, jj, refresh
from currency_settings import SettingsRecords
from store import load, entries, dump, validate, store_paths, new_id
from recurring import (active, advance, amounts, candidate, detect, identity, main_postings,
                       match_text, paying, payee_match, period_for, same_payment, stale_rules,
                       simple_payment, payment_history, recompute_estimate, varying_details, amount_check, amount_doubt,
                       new_rule, rule_postings, occurrence as calendar_occurrence)
import format1
from audit import records, append, event, audit_path
from ledger_lock import ledger_lock
from ledger_manifest import read_manifest


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class AmountCheck(ValueError):
    def __init__(self, check):
        super().__init__('This amount is outside the recent payment range; explicitly add it anyway')
        self.check=check


def today(clock=None):
    return (clock() if clock else datetime.now().astimezone()).date().isoformat()


def current_time(clock=None):
    return clock() if clock else datetime.now(timezone.utc)


def policy(settings):
    import bookkeeping_policy
    bookkeeping_policy.read(settings)


def ignored_entries(lines):
    undone = {r.get('undoes') for r in lines if r['kind']=='rule' and r['rule_action']=='undone'}
    originals = {r['entry_id'] for r in lines if r['id'] in undone and r.get('entry_id')}
    return originals | {r['reversal_id'] for r in lines if r['kind']=='rule' and r.get('reversal_id')}


def rule_changes(before, after):
    old, new = {r['id']:r for r in before}, {r['id']:r for r in after}
    return [dict(id=i, before=deepcopy(old.get(i)), after=deepcopy(new.get(i))) for i in sorted(old.keys()|new.keys()) if old.get(i)!=new.get(i)]


def decimal_text(value):
    """An amount the server writes, with the decimal point the store requires."""
    return value if '.' in value else value + '.00'


def complete_posting(value):
    """A posting from a rule form, written in full (format 2)."""
    if not isinstance(value,dict): return value
    return {'account':value.get('account'),'amount':value.get('amount'),'commodity':value.get('commodity'),
            'cost':value.get('cost'),'assert':value.get('assert'),'notes':value.get('notes') or []}


def complete_rule(value, day):
    """A new rule from the rule form or a suggestion, written in full: the server fills what the form leaves out."""
    rule=new_rule(**{key:deepcopy(item) for key,item in value.items() if key in new_rule()})
    rule['created']=rule['created'] or day
    rule['origin']=rule['origin'] or {'kind':'owner','words':None,'entry-ids':[],'came-back':False}
    if isinstance(rule['schedule'],dict):
        schedule=rule['schedule']
        rule['schedule']={'every':schedule.get('every'),'from':schedule.get('from'),'interval':schedule.get('interval') or 1,'anchor-day':schedule.get('anchor-day')}
    rule['postings']=[complete_posting(p) for p in rule['postings']]
    return rule


class StatementPayments:
    """One locked batch's ordered actuals, signatures and linked history.

    Postings do not change while linking. Only the linked-rule buckets and the
    current rule objects need updating as each chronological payment is consumed.
    """
    def __init__(self, actual, rules, accounts):
        self.actual=actual
        self.pending=set()
        self.by_id={entry['id']:entry for entry in actual}
        self.order={entry['id']:index for index,entry in enumerate(actual)}
        self.signatures={}
        self.by_payment,self.by_destination,self.by_rule={},{},{}
        self.rule_by_id={rule['id']:rule for rule in rules}
        self.positions={rule['id']:index for index,rule in enumerate(rules)}
        self.rules_by_payment,self.unassigned={},{}
        for entry in actual:
            signature=identity(entry,accounts);self.signatures[entry['id']]=signature
            if signature:
                self.by_payment.setdefault(signature,[]).append(entry)
                self.by_destination.setdefault(signature[0],[]).append(entry)
            if entry['rule']:self.by_rule.setdefault(entry['rule']['id'],{})[entry['id']]=entry
        for rule in rules:
            signature=identity(rule,accounts)
            if signature:self.rules_by_payment.setdefault(signature,[]).append(rule['id'])
            elif main_postings(rule,accounts):
                destinations=tuple(sorted({p['account'] for p in main_postings(rule,accounts)}))
                self.unassigned.setdefault(destinations,[]).append(rule['id'])

    def matching_rules(self, entry):
        signature=self.signatures[entry['id']]
        if not signature:return []
        ids=[*self.rules_by_payment.get(signature,[]),*self.unassigned.get(signature[0],[])]
        return [self.rule_by_id[identity] for identity in ids]

    def history(self, rule, accounts):
        signature=identity(rule,accounts)
        if signature:compatible=self.by_payment.get(signature,[])
        else:
            destinations=tuple(sorted({p['account'] for p in main_postings(rule,accounts)}))
            compatible=self.by_destination.get(destinations,[])
        result={e['id']:e for e in compatible}
        result.update(self.by_rule.get(rule['id'],{}))
        return sorted(result.values(),key=lambda entry:self.order[entry['id']])

    def linked(self, entry, rule):
        self.by_rule.setdefault(rule['id'],{})[entry['id']]=entry
        self.rule_by_id[rule['id']]=rule


RULE_CHOICES = ('This payment only', 'Update the rule too')


def linked_rule(entry, rules, accounts, declined, *, indexed=False):
    matches = [r for r in rules if r['id'] not in declined and active(r) and (indexed or same_payment(entry,r,accounts))
               and (r.get('amount') != 'varies' or simple_payment(entry,accounts) and payee_match(entry,r)
                    and main_postings(entry, accounts)[0]['commodity']==main_postings(r, accounts)[0]['commodity'])]
    return matches[0] if len(matches) == 1 else None


def payment_changes(rule, entry, accounts, period):
    old, paid = amounts(rule, accounts), amounts(entry, accounts)
    price = (old, paid) if rule['amount'] != 'varies' and old != paid else None
    day = entry['date']
    moved = (day[-2:] != period[-2:] and day > (date.fromisoformat(period) + timedelta(days=3)).isoformat()
             and (not rule['bill'] or rule['bill']['occurrence'] == period))
    return price, moved


def payment_choice(root, draft, prepared):
    """Return the current choice or put the checked proposal back in Queue for an answer."""
    accounts = prepared['declarations']['accounts']
    entry = prepared['entry']
    rule = linked_rule(entry, prepared['documents'][root/'data/forecast.json'], accounts, draft.get('declined_rules', ()))
    if not rule:
        return False
    period = period_for(rule, entry['date'])
    changes = []
    price, moved = payment_changes(rule, entry, accounts, period)
    if price:
        label = lambda totals: ' + '.join(f'{value} {code}' for code, value in sorted(totals.items()))
        changes.append(f'Amount from {label(price[0])} to {label(price[1])}')
    if moved:
        changes.append(f'Date from {period} to {entry["date"]}')
    if not changes:
        return False
    key = digest([rule, prepared['proposal_hash'], period])
    current = draft.get('rule_question')
    if current and current['id'] == key:
        answer = draft['questions'][current['index']]['answer']
        if answer is not None:
            return answer == RULE_CHOICES[1]
        draft.update(state='question', waiting=None)
        return None
    from agent import now
    draft.update(state='question', waiting=None, rule_question=dict(id=key, index=len(draft['questions'])))
    draft['questions'].append(dict(question='Update the rule from this payment? ' + '. '.join(changes) + '.',
                                   answer=None, asked_at=now()))
    return None


def addition_effects(root, entry_id, draft, *, documents=None, lines=None, persist=True, statement=False, batch=None, billing_period=None, update_rule=False):
    """Checked addition effects; statement batches validate and persist once at the end."""
    documents=load(root) if documents is None else documents
    rules=documents[root/'data/forecast.json']; before=deepcopy(rules) if batch is None else []
    lines=records(root) if lines is None else lines
    ignored=ignored_entries(lines) if batch is None else set()
    actual=batch.actual if batch is not None else [e for e in entries(documents) if e['id'] not in ignored]
    entry=batch.by_id.get(entry_id) if batch is not None else next((e for e in actual if e['id']==entry_id),None)
    if entry is None:
        raise ValueError('This payment is missing or was undone and cannot be linked to a recurring rule')
    entry_before=deepcopy(entry)
    accounts=documents[root/'data/accounts.json']['accounts']
    link=None; resumed=[]; paid_bill=None; usual=draft.get('usual_payment')
    declined=set(draft.get('declined_rules', []))
    if usual:
        rule=next((r for r in rules if r['id']==usual['rule_id']),None)
        if not rule or not active(rule) or rule['id'] in declined: raise ValueError('This rule is no longer available for this draft')
        # The proposal is frozen at queue time; concurrent edits must be reviewed.
        if digest(rule)!=draft['usual_rule_hash']: raise ValueError('The rule changed; choose Add as usual again')
        period=usual['occurrence']
        duplicates=[e for e in actual if e['id']!=entry_id and (e['rule']==dict(id=rule['id'],occurrence=period) or
                    not e.get('rule') and same_payment(e,rule,accounts) and period_for(rule,e['date'])==period)]
        if duplicates and not usual['another_anyway']: raise ValueError('This period already has a payment; choose Add another anyway')
        link=rule
        update_rule = usual['update_rule']
    else:
        possible=batch.matching_rules(entry) if batch is not None else rules
        link=linked_rule(entry,possible,accounts,declined,indexed=batch is not None)
    # A statement identifies a saved bill by its exact money amount and payee,
    # rather than guessing its occurrence from an early or late payment date.
    billed_occurrence=None
    if statement and link and link['bill']:
        bill=link['bill']
        if (simple_payment(entry,accounts) and simple_payment(link,accounts) and payee_match(entry,link)
                and main_postings(entry,accounts)[0]['commodity']==main_postings(link,accounts)[0]['commodity']
                and Decimal(main_postings(entry,accounts)[0]['amount'])==Decimal(bill['amount'])):
            billed_occurrence=bill['occurrence']
        else:
            link=None
    if link:
        if batch is not None:before=[deepcopy(link)]
        occurrence=usual['occurrence'] if usual else billed_occurrence or period_for(link,entry['date'])
        entry['rule']=dict(id=link['id'],occurrence=occurrence)
        another=usual and usual['another_anyway']
        price, moved = payment_changes(link, entry, accounts, occurrence) if update_rule else (None, False)
        remember = usual and usual['remember_account']
        if price:
            updated = deepcopy(rule_postings(entry, accounts))
            if not remember:
                original_payer = paying(link,accounts) or next((p for p in link['postings'] if Decimal(p['amount'])<0),None)
                selected = paying(entry,accounts)
                if original_payer and selected:
                    next(p for p in updated if p==selected)['account'] = original_payer['account']
            link['postings']=updated
        elif remember:
            selected=paying(entry,accounts)
            placeholder=paying(link,accounts) or next((p for p in link['postings'] if Decimal(p['amount'])<0),None)
            if not placeholder or not selected: raise ValueError('Choose a paying account')
            if placeholder['commodity']!=selected['commodity']: raise ValueError('The saved paying account needs a conversion; edit the rule postings first')
            placeholder['account']=selected['account']
        if not another and (link['bill'] or {}).get('occurrence') == occurrence:
            paid_bill=deepcopy(link['bill']);link['bill']=None
            entry['evidence']=list(dict.fromkeys([*entry['evidence'],*paid_bill['evidence']]))
        history=batch.history(link,accounts) if batch is not None else actual
        latest=max((e['date'] for e in history if e['id']!=entry_id
                    and (batch is None or e['id'] not in batch.pending)
                    and ((e['rule'] or {}).get('id')==link['id'] or same_payment(e,link,accounts))),default='')
        if paid_bill:
            # A saved bill identifies exactly one occurrence, even if its
            # payment is backdated or arrives after the following due date.
            index=batch.positions[link['id']] if batch is not None else rules.index(link);rules[index]=link=advance(link,occurrence)
        elif not another and entry['date']>=latest and max(entry['date'],occurrence)>=link['date']:
            index=batch.positions[link['id']] if batch is not None else rules.index(link);rules[index]=link=advance(link,max(entry['date'],occurrence))
        if moved and not another:
            link['schedule']['anchor-day'] = int(entry['date'][-2:])
            link['date'] = link['schedule']['from'] = calendar_occurrence(link)
            if not paid_bill and link['date'] <= entry['date']:
                rules[rules.index(link)] = link = advance(link, entry['date'])
        if batch is not None:batch.linked(entry,link)
        recompute_estimate(link,batch.history(link,accounts) if batch is not None else actual,accounts)
    elif not statement:
        resumed=[dict(id=r['id'],description=r['description']) for r in rules if r['id'] not in declined and r.get('lifecycle')=='paused' and same_payment(entry,r,accounts)]
    found=None
    if not statement:
        settings=documents.get(root/'data/recurring-settings.json',{'never-automatic':[]})
        covered=any(r['id'] in declined and same_payment(entry,r,accounts) for r in rules)
        if not link and not covered:
            found=candidate(entry,actual,rules,accounts,settings,draft['client_time'][:10],draft['text'],billing_period=billing_period)
    made=[]
    if found and found['automatic']:
        made=[found['rule']]; rules.extend(made)
        entry['rule']=dict(id=made[0]['id'],occurrence=entry['date'])
    if persist:
        validate(documents,root)
        if entry != entry_before:
            path=root/'data/expenses'/f"{entry['date'][:4]}.json";dump(path,documents[path])
        if rules != before:dump(root/'data/forecast.json',rules)
    after=rules if batch is None else [link] if link else []
    return dict(rule_changes=rule_changes(before,after), made_rules=[r['id'] for r in made],
                rule=entry['rule'], next_due=next((r['date'] for r in rules if (entry['rule'] or {}).get('id')==r['id']),None), resume_rules=resumed,
                **({'paid_bill':paid_bill} if paid_bill else {}))


def statement_effects(root, entry_ids, session, *, documents, lines=None):
    """Link a statement's unlinked payments in its existing checked transaction.

    Reuse the caller's documents and validate/persist the final batch once. The
    caller holds the lock and snapshots the store and today's audit for rollback.
    Existing explicit links and the owner's classification remain unchanged.
    """
    rules = documents[root/'data/forecast.json']
    if not rules or not entry_ids:
        return []
    before = deepcopy(rules)
    accounts = documents[root/'data/accounts.json']['accounts']
    entry_ids = set(entry_ids)
    lines = records(root) if lines is None else lines
    ignored = ignored_entries(lines)
    actual = [e for e in entries(documents) if e['id'] not in ignored]
    batch = StatementPayments(actual,rules,accounts)
    payments = [e for e in actual if e['id'] in entry_ids and not e['rule'] and batch.signatures[e['id']]]
    if not payments:
        return []
    saved = session.get('client_time') or session.get('saved_at') or session.get('created_at')
    context = dict(client_time=saved or current_time().astimezone().isoformat(), text='')
    changed_years, bills = set(), []
    # Future rows in this Apply must not make the current payment look backdated.
    batch.pending.update(entry['id'] for entry in payments)
    for entry in sorted(payments, key=lambda e: e['date']):
        batch.pending.remove(entry['id'])
        previous = deepcopy(entry)
        result = addition_effects(root, entry['id'], context, documents=documents, lines=lines, persist=False, statement=True, batch=batch)
        if entry != previous:
            changed_years.add(entry['date'][:4])
        if result.get('paid_bill'):
            bills.append((entry['id'], result))
    changes = rule_changes(before, rules)
    if changed_years or changes:
        validate(documents, root)
        for year in sorted(changed_years):
            path = root/'data/expenses'/f'{year}.json'
            dump(path, documents[path])
        if changes:
            dump(root/'data/forecast.json', rules)
    for entry_id, result in bills:
        append(root, event('rule', rule_action='bill-paid', rule_id=result['rule']['id'],
            request_id=session['id'], request_hash=digest(dict(statement=session['id'], entry=entry_id, bill=result['paid_bill'])),
            rule_changes=result['rule_changes'], bill=result['paid_bill'], text='', entry_id=entry_id,
            files=[dict(original_name=session['original_name'],
                        filed_path=f"Bills/{session['statement']['periodEnd']}/{session['original_name']}")],
            doubts=[]), lines=lines)
    return changes


def wire_proposal(rule, day, filenames, root, body):
    """Full balanced rule postings -> the existing agent-neutral checked wire shape."""
    postings=deepcopy(rule['postings']); accounts=load(root)[root/'data/accounts.json']['accounts']
    payer=paying(rule,accounts)
    if 'account' in body or not payer:
        selected=body.get('account')
        if accounts.get(selected,{}).get('kind') not in ('bank','card','cash','wallet'):
            raise ValueError('Choose a paying account')
        placeholder=payer or next((p for p in postings if Decimal(p['amount'])<0),None)
        if not placeholder: raise ValueError('Edit this rule to include a balanced payment placeholder')
        # Locate by position, since the payer came from the original rule.
        index=next(i for i,p in enumerate(postings) if p==placeholder)
        if accounts[selected]['currency'] != postings[index]['commodity']:
            raise ValueError('Choose an account in the planned payment currency, or edit the rule conversion')
        postings[index]['account']=selected
    if body.get('amount') is not None:
        value=body['amount']
        if not isinstance(value,str) or not re.fullmatch(r'(0|[1-9]\d*)(\.\d+)?',value) or Decimal(value)<=0:
            raise ValueError('Enter a positive decimal amount')
        value=decimal_text(value)
        totals=amounts(rule,accounts)
        original=sum(totals.values(),Decimal(0))
        currencies=set(totals)
        if not original or len(currencies)!=1: raise ValueError('Edit the postings for this complex rule')
        if rule.get('amount')=='varies':
            if not simple_payment(rule,accounts): raise ValueError('Only simple two-posting payments in one currency can vary')
            for p in postings:
                p['amount']=('-' if Decimal(p['amount'])<0 else '')+value
        else:
            scale=Decimal(value)/original
            minor=json.loads((__import__('settings').APPLICATION/'schema/currency-minor-units.json').read_text())
            from decimal import ROUND_HALF_UP
            for p in postings:
                precision=int(minor.get(p['commodity'],2))
                p['amount']=decimal_text(format((Decimal(p['amount'])*scale).quantize(Decimal(10)**-precision,rounding=ROUND_HALF_UP),'f'))
                if p.get('cost') and p['cost']['per']=='total':
                    p['cost']['amount']=format((Decimal(p['cost']['amount'])*scale).quantize(Decimal(10)**-int(minor.get(p['cost']['commodity'],2)),rounding=ROUND_HALF_UP),'f')
    estimated=any(p['cost'] and p['commodity']!=p['cost']['commodity'] for p in postings)
    notes=list(rule['notes'])
    if estimated: notes.append('Currency estimate: planned conversion; statement reconciliation will correct it.')
    wire=[]
    for p in postings:
        cost=p['cost']
        if cost:
            value=abs(Decimal(cost['amount'])*(Decimal(p['amount']) if cost['per']=='unit' else 1))
            cost=dict(amount=format(value,'f'),commodity=cost['commodity'])
        wire.append(dict(account=p['account'],amount=p['amount'],commodity=p['commodity'],cost=cost))
    from answers import answer_extensions
    entry={'date':day,'status':'confirmed','description':rule['description'],'references':None,'classification':None,'original-amount':None}
    extensions=answer_extensions(root)
    if extensions is not None: entry['extensions']=extensions
    entry.update(notes=notes,postings=wire)
    return {'status':'ready','question':None,'entry':entry,'new-accounts':[],'doubts':[],'attachment-names':filenames,'explanation':None},estimated


def queue_usual(settings, drafts, draft, body):
    if draft.get('correction'): raise ValueError('A correction form cannot add another usual payment')
    if draft.get('bill_request'): raise ValueError('This item is saving a bill')
    allowed={'rule_id','version','date','amount','update_rule','account','remember_account','another_anyway','amount_anyway','client_time','client_zone'}
    if not isinstance(body,dict) or set(body)-allowed: raise ValueError('Unknown usual payment field')
    for key in ('update_rule','remember_account','another_anyway','amount_anyway'):
        if key in body and type(body[key]) is not bool: raise ValueError(key+' must be boolean')
    request_hash=digest(body)
    if draft.get('refusal'): raise ValueError('Change the account or date before resubmitting this refused payment')
    if draft.get('usual_payment'):
        if draft['usual_payment']['request_hash']!=request_hash: raise ValueError('This draft already has a different usual payment')
        if draft['state']=='failed' and draft.get('proposal'):
            draft.update(state='queued',error=None,waiting=None)
            drafts.put(draft)
        return draft
    if draft['state'] not in ('editing','queued','failed','question') or draft.get('claim'): raise ValueError('This draft cannot add a usual payment now')
    policy(settings)
    documents=load(settings.ledger); rule=next((r for r in documents[settings.ledger/'data/forecast.json'] if r['id']==body.get('rule_id')),None)
    if not rule or not active(rule) or rule['id'] in draft.get('declined_rules',[]): raise ValueError('Choose an active rule that has not been declined')
    version = digest(rule)
    if body.get('version') not in (None, version):
        raise ValueError('The rule changed; choose Add as usual again')
    from draft_time import client_context
    context = client_context(body.get('client_time'),body.get('client_zone')) if 'client_time' in body or 'client_zone' in body else None
    day=body.get('date') or (draft.get('pieces') or {}).get('fields',{}).get('date',{}).get('value') or (context or draft)['client_time'][:10]
    if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day: raise ValueError('Choose a calendar payment date')
    effective=deepcopy(body);check=None
    if rule['amount'] == 'varies':
        if rule['bill']:
            effective['amount']=rule['bill']['amount']
        elif not body.get('amount'): raise ValueError('Needs an amount')
        ignored=ignored_entries(records(settings.ledger))
        actual=[e for e in entries(documents) if e['id'] not in ignored]
        check=amount_check(rule,effective['amount'],actual,documents[settings.ledger/'data/accounts.json']['accounts'])
        if check and not body.get('amount_anyway'): raise AmountCheck(check)
    from transactions import safe_name
    proposal,estimated=wire_proposal(rule,day,[safe_name(f['original_name'].rsplit('.',1)[0]) for f in draft['files']],settings.ledger,effective)
    if check: proposal['doubts'].append(amount_doubt(check))
    try:
        Transaction(settings,drafts,draft).prepare(proposal)
    except BalanceRefusal as exc:
        if draft.get('saved_at') is None: drafts.save_time(draft,body.get('client_time'),body.get('client_zone'))
        draft.update(state='failed',refusal=exc.refusal,error=str(exc),last_proposal=proposal,proposal=None)
        draft['check_errors'].append(str(exc))
        from outcomes import queue_outcome
        queue_outcome(draft,'failed')
        drafts.put(draft)
        raise
    period=rule['bill']['occurrence'] if rule['bill'] else period_for(rule,day)
    ignored=ignored_entries(records(settings.ledger))
    already=[e for e in entries(documents) if e['id'] not in ignored and e['rule']==dict(id=rule['id'],occurrence=period)]
    # Also recognise old unlinked payments in this bucket, so stale rules cannot double-book.
    already += [e for e in entries(documents) if e['id'] not in ignored and not e.get('rule') and same_payment(e,rule,documents[settings.ledger/'data/accounts.json']['accounts']) and period_for(rule,e['date'])==period]
    if already and not body.get('another_anyway'): raise ValueError('This period already has a payment; choose Add another anyway')
    from agent import now
    if draft.get('saved_at') is None: drafts.save_time(draft,body.get('client_time'),body.get('client_zone'))
    draft.update(usual_payment=dict(rule_id=rule['id'],occurrence=period,request_hash=request_hash,estimated=estimated,update_rule=body.get('update_rule',False),another_anyway=body.get('another_anyway',False),remember_account=body.get('remember_account',False)),
                 usual_rule_hash=version,state='queued',queued_at=draft.get('queued_at') or now(),proposal=proposal,error=None,waiting=None)
    drafts.put(draft)
    return draft


def queue_bill(settings, drafts, draft, body):
    """Freeze an unpaid bill in the existing durable FIFO, without an agent."""
    allowed={'rule_id','amount','date','pay_by','amount_anyway','client_time','client_zone'}
    if not isinstance(body,dict) or set(body)-allowed: raise ValueError('Unknown bill field')
    prior=draft.get('bill_request')
    if prior:
        if prior['body']!=body: raise ValueError('This bill item already has a different request')
        if draft['state']=='failed':
            draft.update(state='queued',error=None,waiting=None);drafts.put(draft)
        return draft
    if draft['state']!='editing' or draft.get('claim') or draft.get('correction'): raise ValueError('This item cannot save a bill now')
    if 'amount_anyway' in body and type(body['amount_anyway']) is not bool: raise ValueError('amount_anyway must be boolean')
    from draft_time import client_context
    context=client_context(body.get('client_time'),body.get('client_zone'))
    policy(settings);root=settings.ledger;documents=load(root)
    rule=next((r for r in documents[root/'data/forecast.json'] if r['id']==body.get('rule_id')),None)
    if not rule or not active(rule) or rule['amount']!='varies' or rule['bill']: raise ValueError('Choose an active varying rule without a saved bill')
    version=digest(rule)
    value=body.get('amount');value=decimal_text(value) if isinstance(value,str) and re.fullmatch(r'(0|[1-9]\d*)(\.\d+)?',value) else value
    bill={'amount':value,'received':body.get('date') or context['client_time'][:10],'pay-by':body.get('pay_by') or None,'occurrence':rule['date'],'evidence':[]}
    rule['bill']=bill;validate(documents,root)
    ignored=ignored_entries(records(root))
    actual=[e for e in entries(documents) if e['id'] not in ignored]
    check=amount_check(rule,bill['amount'],actual,documents[root/'data/accounts.json']['accounts'])
    if check and not body.get('amount_anyway'): raise AmountCheck(check)
    if draft.get('saved_at') is None: drafts.save_time(draft,body.get('client_time'),body.get('client_zone'))
    operation=dict(action='bill-saved',rule_id=rule['id'],version=version,draft_id=draft['id'],bill=bill,
                   **context,**({'amount_anyway':body['amount_anyway']} if 'amount_anyway' in body else {}))
    from agent import now
    draft.update(bill_request=dict(body=deepcopy(body),operation=operation),state='queued',queued_at=draft.get('queued_at') or now(),error=None,waiting=None)
    drafts.put(draft)
    return draft


class RuleRecords(SettingsRecords):
    def __init__(self, root):
        super().__init__(root)
        self.root=root/'drafts/rules'
        if self.root.is_symlink(): raise ValueError('Rule records must stay inside drafts')
        self.root.mkdir(parents=True,exist_ok=True)


def snapshot(settings, clock=None):
    root=settings.ledger; documents=load(root); lines=records(root); accounts=documents[root/'data/accounts.json']['accounts']
    ignored=ignored_entries(lines)
    actual=[e for e in entries(documents) if e['id'] not in ignored]
    rules=documents[root/'data/forecast.json']; stale={r['id'] for r,e in stale_rules(actual,rules,accounts)}
    proposals={}; review_states={}
    for line in lines:
        if line['kind']=='rule-review':
            proposals[line['rule_id']]=line
            review_states[line['rule_id']]=line['rule_verdict']
        if line['kind']=='rule' and line['rule_action'] in ('removed','kept'):
            for change in line['rule_changes']: proposals.pop(change['id'],None)
            proposals.pop(line.get('rule_id'),None)
            for identity in {line.get('rule_id'),*[c['id'] for c in line['rule_changes']]} - {None}:
                review_states[identity] = 'owner-kept' if line['rule_action']=='kept' else 'owner-removed'
    _,suggestions=detect(actual,rules,accounts,documents.get(root/'data/recurring-settings.json',{}),today(clock))
    public=[]
    for rule in rules:
        paid=payment_history(rule,actual,accounts) if rule['amount']=='varies' else [e for e in actual if (e['rule'] or {}).get('id')==rule['id'] or same_payment(e,rule,accounts)]
        last=max(paid,key=lambda e:e['date']) if paid else None
        life=rule['lifecycle']
        status=life if life!='active' else 'bill-received' if rule['bill'] else 'stale' if rule['id'] in stale else 'overdue' if rule['date']<today(clock) else 'due' if rule['date']==today(clock) else 'on-track'
        public.append(dict(rule, version=digest(rule),last_payment=last,next_due=(rule['bill'] or {}).get('pay-by') or rule['date'],payments=[dict(id=e['id'],date=e['date'],**({'rule':e['rule']} if e['rule'] else {})) for e in paid],rule_status=status,review=proposals.get(rule['id']),review_state=review_states.get(rule['id'],'waiting'),**varying_details(rule,actual,accounts)))
    return dict(rules=public,suggestions=suggestions,history=[line for line in lines if line['kind'] in ('rule','rule-review')])


def match(settings,text,day=None,currency=None):
    documents=load(settings.ledger);manifest=read_manifest(settings.ledger)
    return match_text(documents[settings.ledger/'data/forecast.json'],text,documents[settings.ledger/'data/accounts.json']['accounts'],day or today(),currency or manifest['defaultCurrency'],manifest.get('locale'))


def from_entry(settings,identity):
    documents=load(settings.ledger); entry=next((e for e in entries(documents) if e['id']==identity),None)
    if not entry: raise ValueError('Unknown entry')
    used={e['id'] for v in documents.values() if isinstance(v,list) for e in v}
    rule=new_rule(**{k:deepcopy(entry[k]) for k in ('date','status','description','postings','notes')})
    accounts=documents[settings.ledger/'data/accounts.json']['accounts']
    money=rule_postings(rule,accounts)
    if len(money) != len(rule['postings']) and (not money or not paying(entry,accounts)):
        raise ValueError('Only payments in money can become recurring rules')
    rule['postings']=money
    rule.update(id=new_id(used),origin={'kind':'owner','words':None,'entry-ids':[identity],'came-back':False},created=today(),
                schedule={'every':'month','from':entry['date'],'interval':1,'anchor-day':None})
    for p in rule['postings']: p['assert']=None
    return dict(rule=advance(rule,entry['date']),entry_id=identity)


def operate(settings, op_id, body, *, clock=None):
    if not re.fullmatch(r'[0-9a-f]{32}',op_id): raise ValueError('Use a unique 32-hex operation id')
    if not isinstance(body,dict): raise ValueError('Supply an operation object')
    allowed={'action','rule_id','rule','version','entry_id','client_time','client_zone','bill','text','draft_id','amount_anyway'}
    if set(body)-allowed: raise ValueError('Unknown rule operation field')
    if 'amount_anyway' in body and type(body['amount_anyway']) is not bool: raise ValueError('amount_anyway must be boolean')
    from draft_time import client_context
    context = client_context(body.get('client_time'),body.get('client_zone')) if 'client_time' in body or 'client_zone' in body else {}
    action_day = context['client_time'][:10] if context else today(clock)
    root=settings.ledger; store=RuleRecords(root); request_hash=digest(body)
    with ledger_lock(root) as fd:
        policy(settings)
        for folder in store.root.iterdir():
            if (folder/'transaction.json').exists():
                draft=json.loads((folder/'record.json').read_text());tx=Transaction(settings,store,draft);tx.marker=draft.get('marker','Rule operation: '+draft['id']);tx.recover()
        lines=records(root); prior=next((r for r in lines if r.get('request_id')==op_id),None)
        if prior:
            if prior['request_hash']!=request_hash: raise ValueError('This operation id was used for a different payload')
            result=dict(audit_id=prior['id'],replayed=True)
            if prior['rule_action']=='bill-saved':
                after=next(c['after'] for c in prior['rule_changes'] if c['id']==prior['rule_id'])
                bill=format1.bill(prior['bill'])
                accounts=load(root)[root/'data/accounts.json']['accounts']
                result.update(outcome='bill',rule_id=prior['rule_id'],description=after['description'],bill=bill,sources=bill['evidence'],total=f"{bill['amount']} {main_postings(after, accounts)[0]['commodity']}")
            return result
        receipt_path = store.folder(op_id) / 'record.json'
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        if receipt.get('request_hash') and receipt['request_hash'] != request_hash:
            raise ValueError('This operation id was used for a different payload')
        if receipt.get('committed_result'):
            return {**receipt['committed_result'], 'replayed': True}
        if not clean(root): raise LedgerBusy('Waiting for a clean ledger before changing rules')
        documents=load(root); rules=documents[root/'data/forecast.json']; before=deepcopy(rules)
        ignored=ignored_entries(lines)
        actual=[e for e in entries(documents) if e['id'] not in ignored]
        accounts=documents[root/'data/accounts.json']['accounts']; action=body.get('action');rule=next((r for r in rules if r['id']==body.get('rule_id')),None)
        if body.get('version') and (not rule or digest(rule)!=body['version']): raise ValueError('The rule changed; reload it')
        bill_draft=None;copies=[];check=None;bill_record=None
        if action=='made':
            proposed=deepcopy(body.get('rule')) if body.get('rule') else from_entry(settings,body['entry_id'])['rule'] if body.get('entry_id') else None
            if not isinstance(proposed,dict): raise ValueError('Supply a complete rule')
            used={e['id'] for v in documents.values() if isinstance(v,list) for e in v}
            if not proposed.get('id'): proposed['id']=new_id(used)
            elif proposed['id'] in used: raise ValueError('Rule id already exists')
            proposed=complete_rule(proposed,action_day)
            rules.append(proposed)
            recompute_estimate(proposed,actual,accounts)
        elif action in ('backfill','catch-up'):
            if action=='backfill':
                made,_=detect(actual,rules,accounts,documents.get(root/'data/recurring-settings.json',{}),today(clock))
                for made_rule in made: made_rule['origin']['kind']='history-pass'
                rules.extend(made)
            else:
                if body.get('rule_id') and not rule: raise ValueError('Unknown rule')
                for old,last in stale_rules(actual,rules,accounts):
                    if not body.get('rule_id') or old['id']==body['rule_id']:
                        paid_occurrence = last['rule']['occurrence'] if (last['rule'] or {}).get('id') == old['id'] else last['date']
                        rules[rules.index(old)]=advance(old,max(last['date'],paid_occurrence))
            if rules==before:
                result = dict(changed=0,changes=[])
                store.put(dict(id=op_id,request_hash=request_hash,committed_result=result))
                return result
        else:
            if action=='removed' and not rule and 'rule' in body and not body.get('rule_id'):
                # Dismiss a suggestion by retaining its evidence as a suppression rule.
                proposed=body['rule']
                if not isinstance(proposed,dict) or not isinstance(proposed.get('origin'),dict) or not isinstance(proposed['origin'].get('entry-ids'),list):
                    raise ValueError('Supply a current suggestion with its evidence')
                evidence=proposed['origin']['entry-ids']
                if not evidence or not any(e['id']==evidence[-1] for e in actual): raise ValueError('The suggestion evidence changed; reload rules')
                _,suggestions=detect(actual,rules,accounts,documents.get(root/'data/recurring-settings.json',{}),today(clock))
                suggestion=next((s['rule'] for s in suggestions if (s['rule']['origin'] or {}).get('entry-ids')==evidence),None)
                if not suggestion: raise ValueError('The suggestion changed; reload rules')
                # Trust the current detector, not caller-supplied postings.
                rule=deepcopy(suggestion)
                rules.append(rule)
            if not rule: raise ValueError('Unknown rule')
            life=rule['lifecycle']
            if action in ('bill-saved','bill-removed'):
                if rule['amount']!='varies': raise ValueError('Bills need a varying rule')
                if action=='bill-removed':
                    if not rule['bill']: raise ValueError('There is no saved bill')
                    bill_record=rule['bill'];rule['bill']=None
                else:
                    if rule['bill']: raise ValueError('Remove the saved bill before replacing it')
                    bill=deepcopy(body.get('bill'))
                    if not isinstance(bill,dict): raise ValueError('Supply a bill')
                    # Validate the entire candidate before arithmetic or filing.
                    rule['bill']=bill;validate(documents,root)
                    check=amount_check(rule,bill['amount'],actual,accounts)
                    if check and body.get('amount_anyway') is not True: raise AmountCheck(check)
                    if body.get('draft_id'):
                        from drafts import Drafts
                        drafts=Drafts(root)
                        with drafts.mutex:
                            bill_draft=drafts.get(body['draft_id'])
                            if bill_draft['state'] not in ('queued','working') or bill_draft.get('bill_request',{}).get('operation')!=body or bill_draft.get('claim') or bill_draft.get('correction'): raise ValueError('This item cannot save a bill now')
                            if bill.get('evidence'): raise ValueError('Uploaded bills use their filed evidence')
                            if bill_draft.get('saved_at') is None: drafts.save_time(bill_draft,body.get('client_time'),body.get('client_zone'))
                            from transactions import evidence_paths
                            answer={'entry':{'date':bill['received']},'attachment-names':[f['original_name'].rsplit('.',1)[0] for f in bill_draft['files']]}
                            targets=evidence_paths(answer,bill_draft,root)
                            copies=[(drafts.folder(bill_draft['id'])/'files'/f['name'],target) for f,target in zip(bill_draft['files'],targets)]
                            bill['evidence']=[str(p.relative_to(root)) for p in targets]
                    rule['bill']=bill;bill_record=bill
            elif action=='edited':
                patch=deepcopy(body.get('rule'))
                if not isinstance(patch,dict) or set(patch)-{'description','postings','schedule','date','status','amount','notes'}:
                    raise ValueError('Edit description, postings, schedule, date, status, amount or notes')
                if patch.get('amount','varies') in (None,'fixed'):
                    if rule['bill']: raise ValueError('Remove the saved bill before making the amount fixed')
                    patch['amount']='fixed'
                if 'schedule' in patch and isinstance(patch['schedule'],dict):
                    patch['schedule']={'every':patch['schedule'].get('every'),'from':patch['schedule'].get('from'),
                                       'interval':patch['schedule'].get('interval') or 1,'anchor-day':patch['schedule'].get('anchor-day')}
                if 'postings' in patch and isinstance(patch['postings'],list):
                    patch['postings']=[complete_posting(p) for p in patch['postings']]
                rule.update(patch)
                if 'schedule' in patch: rule['date']=rule['schedule']['from']
                elif 'date' in patch: rule['schedule']['from']=rule['date']
                recompute_estimate(rule,actual,accounts)
            elif action=='removed':
                evidence = {e['id'] for e in actual if same_payment(e,rule,accounts)} | set((rule['origin'] or {}).get('entry-ids',[]))
                rule.update(lifecycle='removed',removal={'date':action_day,'entry-ids':sorted(evidence)})
            elif action=='kept': pass
            else:
                transitions={'paused':({'active'},'paused'),'resumed':({'paused'},'active'),'ended':({'active','paused'},'ended'),'restored':({'ended','removed'},'active')}
                if action not in transitions: raise ValueError('Unknown rule operation')
                allowed_lives,target=transitions[action]
                if life not in allowed_lives: raise ValueError('This operation does not apply to the rule lifecycle')
                rule['lifecycle']=target
        validate(documents,root)
        changes=rule_changes(before,rules)
        record=event('rule',rule_action=action,request_id=op_id,request_hash=request_hash,rule_changes=changes,
                     **context,
                     **({'rule_id':rule['id']} if rule else {}),
                     )
        if bill_record is not None:
            record.update(bill=deepcopy(bill_record),text=bill_draft['text'] if bill_draft else body.get('text',''),
                          files=[dict(original_name=f['original_name'],filed_path=source) for f,source in zip(bill_draft['files'],bill_record['evidence'])] if bill_draft else [dict(original_name=source,filed_path=source) for source in bill_record['evidence']],
                          doubts=[amount_doubt(check)] if check else [])
            if check: record['amount_check']=check
        draft=dict(id=op_id,request_hash=request_hash,marker=('Draft: ' if action=='bill-saved' else 'Rule operation: ')+op_id);store.put(draft);tx=Transaction(settings,store,draft);tx.marker=draft['marker']
        journal=tx.snapshot([*documents,root/'expense_ledger.ledger',audit_path(root,record),*[target for source,target in copies]])
        try:
            from transactions import compress
            for source,target in copies:
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,target);compress(target)
            for path,value in documents.items(): dump(path,value)
            append(root,record)
            result=dict(audit_id=record['id'],action=action,changes=changes,changed=len(changes))
            if action=='bill-saved':
                result.update(outcome='bill',rule_id=rule['id'],description=rule['description'],bill=deepcopy(bill_record),sources=bill_record['evidence'],total=f"{bill_record['amount']} {main_postings(rule, accounts)[0]['commodity']}")
            journal['result']=result;tx.save_journal(journal)
            message=f'Rules: {action}\n\n{tx.marker}\nAudit: {record["id"]}\nAdded-by: user (rule operation)'
            if action=='bill-saved':
                from draft_time import saved_local, client_context
                local=bill_draft or {**(context or client_context()),'created_at':record['time']}
                message='\n'.join([f"Record {rule['description']} bill via dashboard",'',
                    'Typed: '+json.dumps(record['text'],ensure_ascii=False),'Saved: '+saved_local(local),
                    f"Bill: {result['total']}, pay by {bill_record['pay-by'] or 'none'}",
                    'Evidence: '+(', '.join(bill_record['evidence']) or 'none'),
                    'Agent doubts: '+('; '.join(record['doubts']) or 'none'),f'Draft: {op_id}',
                    f"Added-by: user (bill, rule {rule['id']})"])
            result['commit_id']=tx.verified_commit(fd,message)
            draft['committed_result']=result;store.put(draft);tx.finish_commit()
        except BaseException:
            tx.rollback(journal);raise
    return refresh(settings,result)
