"""Human entry corrections: one idempotent, locked replacement transaction."""
from copy import deepcopy
import json
import re

from transactions import Transaction, LedgerBusy, clean, refresh, evidence_paths
from replacements import (entry_hash, require_version, replace_entry, statement_locked,
                          check_documents, move_evidence, safe_evidence, correction_rules)
from currency_settings import SettingsRecords
from rules_service import policy, digest, rule_changes
from ledger_lock import ledger_lock
from store import load, entries
from audit import event, records


class CorrectionRecords(SettingsRecords):
    def __init__(self, root):
        super().__init__(root)
        self.root=root/'drafts/corrections'
        if self.root.is_symlink(): raise ValueError('Correction records must stay inside drafts')
        self.root.mkdir(parents=True,exist_ok=True)


def wire_entry(settings, entry):
    from agent import schema_for
    from decimal import Decimal
    a={key:deepcopy(entry.get(field)) for key,field in [('order_id','order-id'),('reference','reference'),('classification','classification'),('original_amount','original-amount'),('fuel_filled_litres','fuel-filled-litres'),('trip_km','trip-km')] if key in schema_for(settings)['properties']['entry']['properties']}
    a.update({key:deepcopy(entry[key]) for key in ('date','status','description')})
    a['notes']=deepcopy(entry.get('notes',[]));a['postings']=[]
    for p in entry['postings']:
        cost=p.get('cost')
        if cost: cost=dict(amount=format(abs(Decimal(cost['amount'])*(Decimal(p['amount']) if cost['per']=='unit' else 1)),'f'),commodity=cost['commodity'])
        a['postings'].append(dict(account=p['account'],amount=p['amount'],commodity=p['commodity'],cost=cost))
    return dict(status='ready',question=None,explanation=None,entry=a,new_accounts=[],doubts=[],attachment_names=[])


def open_form(settings, drafts, entry_id):
    with ledger_lock(settings.ledger):
        documents=load(settings.ledger)
        entry=next((e for e in entries(documents) if e['id']==entry_id),None)
        if not entry: raise ValueError('Unknown entry')
        draft=drafts.create('')
        draft.update(state='question',correction=dict(entry_id=entry_id,version=entry_hash(entry),before=deepcopy(entry),sources=entry.get('sources',[]),locked=statement_locked(documents,settings.ledger,entry)),hand_form=wire_entry(settings,entry),hand_reason='correction')
        drafts.put(draft)
        return draft


def prepare_replacement(settings, draft, editable, declarations):
    root=settings.ledger; documents=load(root);info=draft['correction']
    before=next((e for e in entries(documents) if e['id']==info['entry_id']),None)
    require_version(before,info['version'])
    # Preserve fields not offered by the form, including membership and assertions.
    after=deepcopy(before)
    editable_fields={'date','status','description','postings','notes','order-id','reference','classification','original-amount','fuel-filled-litres','trip-km'}
    for key in editable_fields:
        if key in ('fuel-filled-litres','trip-km') and key.replace('-','_') not in draft['hand_form']['entry']: continue
        after.pop(key,None)
    after.update(deepcopy(editable))
    original_wire=wire_entry(settings,before)['entry']['postings']
    for index,p in enumerate(after['postings']):
        if index<len(before['postings']):
            old=before['postings'][index]
            if p.get('cost') and draft['hand_form']['entry']['postings'][index].get('cost')==original_wire[index].get('cost') and p['amount']==old['amount']:
                p['cost']=deepcopy(old['cost'])
            if 'assert' in old: p['assert']=deepcopy(old['assert'])
    sources=info.get('sources',before.get('sources',[]))
    if not isinstance(sources,list) or any(s not in before.get('sources',[]) for s in sources) or len(set(sources))!=len(sources):
        raise ValueError('Choose sources already attached to this entry, or upload more attachments')
    for source in sources:
        if source.startswith('Bills/'): safe_evidence(root,source)
    required_sources={s['source'] for s in documents[root/'data/card-statements.json'] if (before.get('statement-end')==s['end'] and any(p['account']==s['account'] for p in before['postings'])) or any(m.get('entry-id')==before['id'] for m in s.get('members',[]))}
    if required_sources-set(sources): raise ValueError('Keep the filed statement source attached to this entry')
    if sources: after['sources']=list(sources)
    else: after.pop('sources',None)
    documents[root/'data/accounts.json']=declarations
    after=replace_entry(documents,root,before,after,expected=info['version'])
    rule_result=correction_rules(documents,root,before,after)
    check_documents(documents,root)
    return documents,before,after,rule_result


def apply(settings, entry_id, op_id, body, *, drafts=None, actor='user', trailer=None):
    if not re.fullmatch('[0-9a-f]{32}',op_id) or not re.fullmatch('[0-9a-f]{16}',entry_id): raise ValueError('Invalid correction identity')
    allowed={'version','proposal','sources','reason','approved_accounts','draft_id','receipt'}
    if not isinstance(body,dict) or set(body)-allowed: raise ValueError('Unknown correction field')
    if not isinstance(body.get('reason',''),str) or len(body.get('reason',''))>10000: raise ValueError('Enter a short correction reason')
    request_hash=digest(dict(entry_id=entry_id,body=body));root=settings.ledger;store=CorrectionRecords(root)
    with ledger_lock(root) as fd:
        policy(settings)
        for folder in store.root.iterdir():
            if (folder/'transaction.json').exists():
                record=json.loads((folder/'record.json').read_text());tx=Transaction(settings,store,record);tx.recover()
        prior=next((r for r in records(root) if r['kind']=='correction' and r['operation_id']==op_id),None)
        if prior:
            if prior['request_hash']!=request_hash: raise ValueError('This operation id was used for a different correction')
            return {**prior['result'],'audit_id':prior['id'],'replayed':True}
        if not clean(root): raise LedgerBusy('Waiting for a clean ledger before correcting this entry')
        documents=load(root);before=next((e for e in entries(documents) if e['id']==entry_id),None)
        require_version(before,body.get('version'))
        draft=dict(id=op_id,files=[],hand_form=body.get('proposal'),correction=dict(entry_id=entry_id,version=body['version'],sources=body.get('sources',before.get('sources',[]))))
        if body.get('draft_id'):
            if drafts is None: raise ValueError('Attachments require the draft service')
            uploaded=drafts.get(body['draft_id'])
            if uploaded.get('correction',{}).get('entry_id')!=entry_id or uploaded['state']!='question': raise ValueError('This correction draft cannot be used')
            draft['files']=deepcopy(uploaded['files'])
        draft['finished_by']=dict(actor='user',reason='correction')
        tx=Transaction(settings,store,draft)
        prepared=tx.prepare(body['proposal'])
        if set(body.get('approved_accounts',[]))!=set(prepared['new_accounts']): raise ValueError('Tick every new account before correcting')
        docs,before,after,rule_result=prepare_replacement(settings,draft,prepared['entry'],prepared['declarations'])
        targets=evidence_paths(body['proposal'],draft,root)
        after.setdefault('sources',[]).extend(str(t.relative_to(root)) for t in targets)
        if not after['sources']: after.pop('sources')
        moves=move_evidence(docs,root,before,after)
        changed=[key for key in sorted(before.keys()|after.keys()) if before.get(key)!=after.get(key)]
        # Receipt answers may audit a no-change choice; ordinary untouched forms stay neutral.
        if not changed and not body.get('receipt'): raise ValueError('No changes yet')
        before_rules=documents[root/'data/forecast.json']
        result=dict(entry_id=entry_id,description=after['description'],sources=after.get('sources',[]),rule=rule_result,changed=changed)
        record=event('correction',entry_id=entry_id,operation_id=op_id,request_hash=request_hash,before=before,after=after,changed_fields=changed,reason=body.get('reason',''),actor=actor,result=deepcopy(result),rule_changes=rule_changes(before_rules,docs[root/'data/forecast.json']),**({'receipt':body['receipt']} if body.get('receipt') else {}))
        if prepared['new_accounts']:
            record['proposal']=deepcopy(body['proposal'])
            record['account_consent']=dict(proposal_hash=prepared['proposal_hash'],accounts=prepared['new_accounts'],method='hand-ticks')
        result['audit_id']=record['id']
        def one(value): return json.dumps(value,ensure_ascii=False,separators=(',',':'))
        rule_text='none' if rule_result['status']=='none' else f"{rule_result['status']}: {rule_result['description']}; next due {rule_result['next_due']}"
        evidence=[str(t.relative_to(root)) for t in targets]+[str(b.relative_to(root)) for a,b in moves]
        message='\n'.join([f"Correct {after['description']} via dashboard",'',f'Entry: {entry_id}','Changed: '+'; '.join(f'{key}: {one(before.get(key))} → {one(after.get(key))}' for key in changed),'Reason: '+one(body.get('reason','')),'Rule: '+rule_text,'Evidence: '+(', '.join(evidence) or 'none'),f'Draft: {op_id}',trailer or 'Added-by: user (correction)'])
        store.put(draft)
        copies=[(drafts.folder(body['draft_id'])/'files'/file['name'],target) for file,target in zip(draft['files'],targets)]
        result=tx.publish(docs,record,result,fd,message,copies=copies,moves=moves)
    if drafts:
        from transactions import payment
        with drafts.mutex:
            for saved in drafts.all():
                if (saved.get('result') or {}).get('entry_id')==entry_id:
                    saved['result'].update(description=after['description'],sources=after.get('sources',[]),**payment(after,docs[root/'data/accounts.json']['accounts']))
                    drafts.put(saved)
    if body.get('draft_id') and drafts:
        uploaded=drafts.get(body['draft_id']);drafts.completed(uploaded,dict(outcome='ready',**result))
    return refresh(settings,result)
