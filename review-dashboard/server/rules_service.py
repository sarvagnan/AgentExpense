"""Recurring-rule services; all financial writes use Transaction and the ledger lock."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
import secrets

from transactions import Transaction, clean, jj, refresh
from currency_settings import SettingsRecords
from store import load, entries, dump, validate, store_paths, new_id
from recurring import (active, advance, candidate, detect, identity, main_postings,
                       match_text, paying, payee_match, period_for, same_payment, stale_rules)
from audit import records, append, event, audit_path
from ledger_lock import ledger_lock
from ledger_manifest import read_manifest


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def today(clock=None):
    return (clock() if clock else datetime.now().astimezone()).date().isoformat()


def current_time(clock=None):
    return clock() if clock else datetime.now(timezone.utc)


def policy(settings):
    (settings.ledger/'AGENTS.md').read_text()
    general = settings.application/'policy/BOOKKEEPING.md'
    if general.is_file(): general.read_text()


def ignored_entries(lines):
    undone = {r.get('undoes') for r in lines if r['kind']=='rule' and r['rule_action']=='undone'}
    originals = {r['entry_id'] for r in lines if r['id'] in undone and r.get('entry_id')}
    return originals | {r['reversal_id'] for r in lines if r['kind']=='rule' and r.get('reversal_id')}


def rule_changes(before, after):
    old, new = {r['id']:r for r in before}, {r['id']:r for r in after}
    return [dict(id=i, before=deepcopy(old.get(i)), after=deepcopy(new.get(i))) for i in sorted(old.keys()|new.keys()) if old.get(i)!=new.get(i)]


def addition_effects(root, entry_id, draft):
    """Called inside Transaction.apply before its single audit/verified commit."""
    documents=load(root); rules=documents[root/'data/forecast.json']; before=deepcopy(rules)
    lines=records(root); ignored=ignored_entries(lines)
    actual=[e for e in entries(documents) if e['id'] not in ignored]
    entry=next(e for e in actual if e['id']==entry_id)
    accounts=documents[root/'data/accounts.json']['accounts']
    link=None; resumed=[]; usual=draft.get('usual_payment')
    declined=set(draft.get('declined_rules', []))
    if usual:
        rule=next((r for r in rules if r['id']==usual['rule_id']),None)
        if not rule or not active(rule) or rule['id'] in declined: raise ValueError('This rule is no longer available for this draft')
        # The proposal is frozen at queue time; concurrent edits must be reviewed.
        if digest(rule)!=draft['usual_rule_hash']: raise ValueError('The rule changed; choose Add as usual again')
        period=usual['occurrence']
        duplicates=[e for e in actual if e['id']!=entry_id and (e.get('rule')==dict(id=rule['id'],occurrence=period) or
                    not e.get('rule') and same_payment(e,rule,accounts) and period_for(rule,e['date'])==period)]
        if duplicates and not usual['another_anyway']: raise ValueError('This period already has a payment; choose Add another anyway')
        link=rule
        if usual['update_rule']:
            updated = deepcopy(entry['postings'])
            if not usual['remember_account']:
                original_payer = paying(rule,accounts) or next((p for p in rule['postings'] if Decimal(p['amount'])<0),None)
                selected = paying(entry,accounts)
                if original_payer and selected:
                    next(p for p in updated if p==selected)['account'] = original_payer['account']
            rule['postings']=updated
        elif usual['remember_account']:
            selected=paying(entry,accounts)
            old=paying(rule,accounts)
            placeholder=old or next((p for p in rule['postings'] if Decimal(p['amount'])<0),None)
            if not placeholder or not selected: raise ValueError('Choose a paying account')
            if placeholder['commodity']!=selected['commodity']: raise ValueError('The saved paying account needs a conversion; edit the rule postings first')
            placeholder['account']=selected['account']
    else:
        matches=[r for r in rules if r['id'] not in declined and active(r) and same_payment(entry,r,accounts)]
        if len(matches)==1: link=matches[0]
    if link:
        occurrence=usual['occurrence'] if usual else period_for(link,entry['date'])
        entry['rule']=dict(id=link['id'],occurrence=occurrence)
        # The explicitly paid occurrence is consumed even when paid a little early.
        latest=max((e['date'] for e in actual if e['id']!=entry_id and (e.get('rule',{}).get('id')==link['id'] or same_payment(e,link,accounts))),default='')
        if entry['date']>=latest and max(entry['date'],occurrence)>=link['date']:
            rules[rules.index(link)]=advance(link,max(entry['date'],occurrence))
    else:
        resumed=[dict(id=r['id'],description=r['description']) for r in rules if r['id'] not in declined and r.get('lifecycle')=='paused' and same_payment(entry,r,accounts)]
    settings=documents.get(root/'data/recurring-settings.json',{'never-automatic':[]})
    covered=any(r['id'] in declined and same_payment(entry,r,accounts) for r in rules)
    found=candidate(entry,actual,rules,accounts,settings,draft['client_time'][:10],draft['text']) if not link and not covered else None
    made=[]
    if found and found['automatic']:
        made=[found['rule']]; rules.extend(made)
        entry['rule']=dict(id=made[0]['id'],occurrence=entry['date'])
    validate(documents,root)
    for path,value in documents.items(): dump(path,value)
    return dict(rule_changes=rule_changes(before,rules), made_rules=[r['id'] for r in made],
                rule=entry.get('rule'), next_due=next((r['date'] for r in rules if entry.get('rule',{}).get('id')==r['id']),None), resume_rules=resumed)


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
        original=sum((Decimal(p['amount']) for p in main_postings(rule)),Decimal(0))
        currencies={p['commodity'] for p in main_postings(rule)}
        if not original or len(currencies)!=1: raise ValueError('Edit the postings for this complex rule')
        scale=Decimal(value)/original
        minor=json.loads((__import__('settings').APPLICATION/'schema/currency-minor-units.json').read_text())
        from decimal import ROUND_HALF_UP
        for p in postings:
            precision=int(minor.get(p['commodity'],2))
            p['amount']=format((Decimal(p['amount'])*scale).quantize(Decimal(10)**-precision,rounding=ROUND_HALF_UP),'f')
            if p.get('cost') and p['cost']['per']=='total':
                p['cost']['amount']=format((Decimal(p['cost']['amount'])*scale).quantize(Decimal(10)**-int(minor.get(p['cost']['commodity'],2)),rounding=ROUND_HALF_UP),'f')
    estimated=any(p.get('cost') and p['commodity']!=p['cost']['commodity'] for p in postings)
    notes=list(rule.get('notes',[]))
    if estimated: notes.append('Currency estimate: planned conversion; statement reconciliation will correct it.')
    wire=[]
    for p in postings:
        cost=p.get('cost')
        if cost:
            value=abs(Decimal(cost['amount'])*(Decimal(p['amount']) if cost['per']=='unit' else 1))
            cost=dict(amount=format(value,'f'),commodity=cost['commodity'])
        wire.append(dict(account=p['account'],amount=p['amount'],commodity=p['commodity'],cost=cost))
    return dict(status='ready',question=None,explanation=None,attachment_names=filenames,doubts=[],new_accounts=[],
                entry=dict(date=day,status='confirmed',description=rule['description'],order_id=None,reference=None,classification=None,original_amount=None,
                           **({'fuel_filled_litres':None,'trip_km':None} if __import__('ledger_manifest').module_settings(read_manifest(root))['fuel'] else {}),notes=notes,postings=wire)),estimated


def queue_usual(settings, drafts, draft, body):
    if draft.get('correction'): raise ValueError('A correction form cannot add another usual payment')
    allowed={'rule_id','date','amount','update_rule','account','remember_account','another_anyway','client_time','client_zone'}
    if not isinstance(body,dict) or set(body)-allowed: raise ValueError('Unknown usual payment field')
    for key in ('update_rule','remember_account','another_anyway'):
        if key in body and type(body[key]) is not bool: raise ValueError(key+' must be boolean')
    request_hash=digest(body)
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
    from draft_time import client_context
    context = client_context(body.get('client_time'),body.get('client_zone')) if 'client_time' in body or 'client_zone' in body else None
    day=body.get('date') or (draft.get('pieces') or {}).get('fields',{}).get('date',{}).get('value') or (context or draft)['client_time'][:10]
    if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day: raise ValueError('Choose a calendar payment date')
    proposal,estimated=wire_proposal(rule,day,[f['original_name'].rsplit('.',1)[0] for f in draft['files']],settings.ledger,body)
    Transaction(settings,drafts,draft).prepare(proposal)
    period=period_for(rule,day)
    ignored=ignored_entries(records(settings.ledger))
    already=[e for e in entries(documents) if e['id'] not in ignored and e.get('rule')==dict(id=rule['id'],occurrence=period)]
    # Also recognise old unlinked payments in this bucket, so stale rules cannot double-book.
    already += [e for e in entries(documents) if e['id'] not in ignored and not e.get('rule') and same_payment(e,rule,documents[settings.ledger/'data/accounts.json']['accounts']) and period_for(rule,e['date'])==period]
    if already and not body.get('another_anyway'): raise ValueError('This period already has a payment; choose Add another anyway')
    from agent import now
    if draft.get('saved_at') is None: drafts.save_time(draft,body.get('client_time'),body.get('client_zone'))
    draft.update(usual_payment=dict(rule_id=rule['id'],occurrence=period,request_hash=request_hash,estimated=estimated,update_rule=body.get('update_rule',False),another_anyway=body.get('another_anyway',False),remember_account=body.get('remember_account',False)),
                 usual_rule_hash=digest(rule),state='queued',queued_at=draft.get('queued_at') or now(),proposal=proposal,error=None,waiting=None)
    drafts.put(draft)
    return draft


class RuleRecords(SettingsRecords):
    def __init__(self, root):
        super().__init__(root)
        self.root=root/'drafts/rules'
        if self.root.is_symlink(): raise ValueError('Rule records must stay inside drafts')
        self.root.mkdir(parents=True,exist_ok=True)


def undo_eligibility(settings, line, clock=None):
    if line.get('kind')!='draft' or line.get('outcome')!='added' or not line.get('entry_id') or 'rule_changes' not in line and not line.get('usual_payment'): return False
    age=current_time(clock)-datetime.fromisoformat(line['time'])
    if not timedelta(0)<=age<=timedelta(minutes=30) or not clean(settings.ledger): return False
    description=jj(settings.ledger,'log','-r','@-','--no-graph','-T','description')
    return ('Draft: '+line['draft_id']) in description.splitlines()


def snapshot(settings, clock=None):
    root=settings.ledger; documents=load(root); lines=records(root); accounts=documents[root/'data/accounts.json']['accounts']
    actual=[e for e in entries(documents) if e['id'] not in ignored_entries(lines)]
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
        paid=[e for e in actual if e.get('rule',{}).get('id')==rule['id'] or same_payment(e,rule,accounts)]
        last=max(paid,key=lambda e:e['date']) if paid else None
        life=rule.get('lifecycle','active')
        status=life if life!='active' else 'stale' if rule['id'] in stale else 'overdue' if rule['date']<today(clock) else 'due' if rule['date']==today(clock) else 'on-track'
        public.append(dict(rule, lifecycle=life,version=digest(rule),last_payment=last,next_due=rule['date'],payments=[dict(id=e['id'],date=e['date'],**({'rule':e['rule']} if e.get('rule') else {})) for e in paid],rule_status=status,review=proposals.get(rule['id']),review_state=review_states.get(rule['id'],'waiting')))
    return dict(rules=public,suggestions=suggestions,history=[line for line in lines if line['kind'] in ('rule','rule-review')],
                undo=[dict(audit_id=line['id'],entry_id=line['entry_id'],eligible=undo_eligibility(settings,line,clock)) for line in lines if line['kind']=='draft' and line.get('entry_id') and (line.get('usual_payment') or 'rule_changes' in line)])


def match(settings,text,day=None,currency=None):
    documents=load(settings.ledger);manifest=read_manifest(settings.ledger)
    return match_text(documents[settings.ledger/'data/forecast.json'],text,documents[settings.ledger/'data/accounts.json']['accounts'],day or today(),currency or manifest['defaultCurrency'],manifest.get('locale'))


def from_entry(settings,identity):
    documents=load(settings.ledger); entry=next((e for e in entries(documents) if e['id']==identity),None)
    if not entry: raise ValueError('Unknown entry')
    used={e['id'] for v in documents.values() if isinstance(v,list) for e in v}
    rule={k:deepcopy(entry[k]) for k in ('date','status','description','postings','notes') if k in entry}
    rule.update(id=new_id(used),lifecycle='active',origin=dict(kind='owner',evidence=[identity]),created=today(),schedule=dict(every='month',**{'from':entry['date']}))
    for p in rule['postings']: p.pop('assert',None)
    return dict(rule=advance(rule,entry['date']),entry_id=identity)


def operate(settings, op_id, body, *, clock=None):
    if not re.fullmatch(r'[0-9a-f]{32}',op_id): raise ValueError('Use a unique 32-hex operation id')
    if not isinstance(body,dict): raise ValueError('Supply an operation object')
    allowed={'action','rule_id','rule','version','undoes','entry_id','client_time','client_zone'}
    if set(body)-allowed: raise ValueError('Unknown rule operation field')
    from draft_time import client_context
    context = client_context(body.get('client_time'),body.get('client_zone')) if 'client_time' in body or 'client_zone' in body else {}
    action_day = context['client_time'][:10] if context else today(clock)
    root=settings.ledger; store=RuleRecords(root); request_hash=digest(body)
    with ledger_lock(root) as fd:
        policy(settings)
        for folder in store.root.iterdir():
            if (folder/'transaction.json').exists():
                draft=json.loads((folder/'record.json').read_text());tx=Transaction(settings,store,draft);tx.marker='Rule operation: '+draft['id'];tx.recover()
        lines=records(root); prior=next((r for r in lines if r.get('request_id')==op_id),None)
        if prior:
            if prior['request_hash']!=request_hash: raise ValueError('This operation id was used for a different payload')
            return dict(audit_id=prior['id'],replayed=True)
        receipt_path = store.folder(op_id) / 'record.json'
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        if receipt.get('request_hash') and receipt['request_hash'] != request_hash:
            raise ValueError('This operation id was used for a different payload')
        if receipt.get('committed_result'):
            return {**receipt['committed_result'], 'replayed': True}
        if not clean(root): raise ValueError('The ledger has uncommitted changes; finish them before changing rules')
        documents=load(root); rules=documents[root/'data/forecast.json']; before=deepcopy(rules)
        actual=[e for e in entries(documents) if e['id'] not in ignored_entries(lines)]
        accounts=documents[root/'data/accounts.json']['accounts']; action=body.get('action');rule=next((r for r in rules if r['id']==body.get('rule_id')),None)
        if body.get('version') and (not rule or digest(rule)!=body['version']): raise ValueError('The rule changed; reload it')
        reversal=None;undone=None
        if action=='made':
            proposed=deepcopy(body.get('rule')) if body.get('rule') else from_entry(settings,body['entry_id'])['rule'] if body.get('entry_id') else None
            if not isinstance(proposed,dict): raise ValueError('Supply a complete rule')
            used={e['id'] for v in documents.values() if isinstance(v,list) for e in v}
            if 'id' not in proposed: proposed['id']=new_id(used)
            elif proposed['id'] in used: raise ValueError('Rule id already exists')
            proposed.setdefault('lifecycle','active');proposed.setdefault('created',action_day);proposed.setdefault('origin',dict(kind='owner'))
            rules.append(proposed)
        elif action in ('backfill','catch-up'):
            if action=='backfill':
                made,_=detect(actual,rules,accounts,documents.get(root/'data/recurring-settings.json',{}),today(clock))
                for made_rule in made: made_rule['origin']['kind']='history-pass'
                rules.extend(made)
            else:
                if body.get('rule_id') and not rule: raise ValueError('Unknown rule')
                for old,last in stale_rules(actual,rules,accounts):
                    if not body.get('rule_id') or old['id']==body['rule_id']:
                        paid_occurrence = last.get('rule',{}).get('occurrence',last['date']) if last.get('rule',{}).get('id') == old['id'] else last['date']
                        rules[rules.index(old)]=advance(old,max(last['date'],paid_occurrence))
            if rules==before:
                result = dict(changed=0,changes=[])
                store.put(dict(id=op_id,request_hash=request_hash,committed_result=result))
                return result
        elif action=='undone':
            undone=next((r for r in lines if r['id']==body.get('undoes')),None)
            if not undone or not undo_eligibility(settings,undone,clock): raise ValueError('Undo is no longer available')
            original=next((e for e in actual if e['id']==undone['entry_id']),None)
            if not original: raise ValueError('The payment changed')
            reversal=deepcopy(original);reversal.pop('rule',None);reversal.pop('possible-duplicate-of',None)
            for key in ('statement-end','serial-number','statement-page','source-row','source-page'): reversal.pop(key,None)
            reversal['id']=new_id({e['id'] for v in documents.values() if isinstance(v,list) for e in v})
            reversal['description']='Undo '+original['description']
            for p in reversal['postings']:
                p['amount']=format(-Decimal(p['amount']),'f');p.pop('assert',None)
            rows=documents.setdefault(root/'data/expenses'/f"{reversal['date'][:4]}.json",[]);rows.append(reversal);rows.sort(key=lambda e:e['date'])
            for change in undone.get('rule_changes',[]):
                now_rule=next((r for r in rules if r['id']==change['id']),None)
                if now_rule!=change['after']: raise ValueError('The rule changed; Undo is no longer available')
                if change['before'] is None:
                    now_rule.update(lifecycle='removed',removal=dict(date=action_day,evidence=now_rule.get('origin',{}).get('evidence',[])))
                else: rules[rules.index(now_rule)]=deepcopy(change['before'])
        else:
            if action=='removed' and not rule and 'rule' in body and not body.get('rule_id'):
                # Dismiss a suggestion by retaining its evidence as a suppression rule.
                proposed=body['rule']
                if not isinstance(proposed,dict) or not isinstance(proposed.get('origin'),dict) or not isinstance(proposed['origin'].get('evidence'),list):
                    raise ValueError('Supply a current suggestion with its evidence')
                evidence=proposed['origin']['evidence']
                if not evidence or not any(e['id']==evidence[-1] for e in actual): raise ValueError('The suggestion evidence changed; reload rules')
                _,suggestions=detect(actual,rules,accounts,documents.get(root/'data/recurring-settings.json',{}),today(clock))
                suggestion=next((s['rule'] for s in suggestions if s['rule'].get('origin',{}).get('evidence')==evidence),None)
                if not suggestion: raise ValueError('The suggestion changed; reload rules')
                # Trust the current detector, not caller-supplied postings.
                rule=deepcopy(suggestion)
                rules.append(rule)
            if not rule: raise ValueError('Unknown rule')
            life=rule.get('lifecycle','active')
            if action=='edited':
                patch=deepcopy(body.get('rule'))
                if not isinstance(patch,dict) or set(patch)-{'description','postings','schedule','date','status'}: raise ValueError('Edit description, postings, schedule, date or status')
                rule.update(patch)
                if 'schedule' in patch: rule['date']=rule['schedule']['from']
                elif 'date' in patch: rule['schedule']['from']=rule['date']
            elif action=='removed':
                evidence = {e['id'] for e in actual if same_payment(e,rule,accounts)} | set(rule.get('origin',{}).get('evidence',[]))
                rule.update(lifecycle='removed',removal=dict(date=action_day,evidence=sorted(evidence)))
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
                     **({'undoes':undone['id'],'reversal_id':reversal['id']} if undone else {}))
        draft=dict(id=op_id,request_hash=request_hash);store.put(draft);tx=Transaction(settings,store,draft);tx.marker='Rule operation: '+op_id
        journal=tx.snapshot([*documents,root/'expense_ledger.ledger',audit_path(root,record)])
        try:
            for path,value in documents.items(): dump(path,value)
            append(root,record)
            result=dict(audit_id=record['id'],action=action,changes=changes,changed=len(changes),**({'reversal_id':reversal['id']} if reversal else {}))
            journal['result']=result;tx.save_journal(journal)
            result['commit_id']=tx.verified_commit(fd,f'Rules: {action}\n\n{tx.marker}\nAudit: {record["id"]}\nAdded-by: user (rule operation)')
            draft['committed_result']=result;store.put(draft);tx.journal.unlink()
        except BaseException:
            tx.rollback(journal);raise
    return refresh(settings,result)
