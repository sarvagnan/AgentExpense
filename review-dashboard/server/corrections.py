"""Human entry corrections: one idempotent, locked replacement transaction."""
from copy import deepcopy
import json
import re

from transactions import Transaction, LedgerBusy, clean, refresh, evidence_paths
from replacements import (entry_hash, require_version, replace_entry, statement_locked,
                          correction_locks, check_documents, move_evidence, safe_evidence, correction_rules)
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
    """The hand form (Edit entry) for a stored entry: the form model's fields, every extension included."""
    from answers import form_extensions
    from decimal import Decimal
    a={key:deepcopy(entry[key]) for key in ('date','status','description','references','classification','original-amount')}
    extensions=form_extensions(settings.ledger,entry['extensions'])
    if extensions is not None: a['extensions']=extensions
    a['notes']=deepcopy(entry['notes']);a['postings']=[]
    for p in entry['postings']:
        cost=p['cost']
        if cost: cost=dict(amount=format(abs(Decimal(cost['amount'])*(Decimal(p['amount']) if cost['per']=='unit' else 1)),'f'),commodity=cost['commodity'])
        a['postings'].append(dict(account=p['account'],amount=p['amount'],commodity=p['commodity'],cost=cost))
    return {'status':'ready','question':None,'entry':a,'new-accounts':[],'doubts':[],'attachment-names':[],'explanation':None}


def open_form(settings, drafts, entry_id):
    with ledger_lock(settings.ledger):
        documents=load(settings.ledger)
        entry=next((e for e in entries(documents) if e['id']==entry_id),None)
        if not entry: raise ValueError('Unknown entry')
        locks=correction_locks(documents,settings.ledger,entry)
        card_member=bool((entry['statement'] or {}).get('end') or any(documents[settings.ledger/'data/accounts.json']['accounts'].get(s['account'],{}).get('kind')=='card' and (s.get('assertion-id')==entry_id or any(m.get('entry-id')==entry_id for m in s.get('members',[]))) for s in documents[settings.ledger/'data/card-statements.json']))
        draft=drafts.create('')
        draft.update(state='question',correction=dict(entry_id=entry_id,version=entry_hash(entry),before=deepcopy(entry),sources=entry['evidence'],locked=card_member,balance_locks=locks),hand_form=wire_entry(settings,entry),hand_reason='correction')
        drafts.put(draft)
        return draft


def prepare_replacement(settings, draft, editable, declarations):
    root=settings.ledger; documents=load(root);info=draft['correction']
    before=next((e for e in entries(documents) if e['id']==info['entry_id']),None)
    require_version(before,info['version'])
    # Preserve fields not offered by the form, including membership and assertions.
    after=deepcopy(before)
    for key in ('date','status','description','references','classification','original-amount','extensions','notes','postings'):
        after[key]=deepcopy(editable[key])
    original_wire=wire_entry(settings,before)['entry']['postings']
    for index,p in enumerate(after['postings']):
        if index<len(before['postings']):
            old=before['postings'][index]
            if p['cost'] and draft['hand_form']['entry']['postings'][index].get('cost')==original_wire[index].get('cost') and p['amount']==old['amount']:
                p['cost']=deepcopy(old['cost'])
            p['assert']=deepcopy(old['assert'])
            p['notes']=deepcopy(old['notes'])
    sources=info.get('sources',before['evidence'])
    if not isinstance(sources,list) or any(s not in before['evidence'] for s in sources) or len(set(sources))!=len(sources):
        raise ValueError('Choose sources already attached to this entry, or upload more attachments')
    for source in sources:
        if source.startswith('Bills/'): safe_evidence(root,source)
    required_sources={s['source'] for s in documents[root/'data/card-statements.json'] if ((before['statement'] or {}).get('end')==s['end'] and any(p['account']==s['account'] for p in before['postings'])) or any(m.get('entry-id')==before['id'] for m in s.get('members',[]))}
    if required_sources-set(sources): raise ValueError('Keep the filed statement source attached to this entry')
    after['evidence']=list(sources)
    documents[root/'data/accounts.json']=declarations
    after=replace_entry(documents,root,before,after,expected=info['version'])
    rule_result=correction_rules(documents,root,before,after)
    check_documents(documents,root,candidate=after)
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
        draft=dict(id=op_id,files=[],hand_form=body.get('proposal'),correction=dict(entry_id=entry_id,version=body['version'],sources=body.get('sources',before['evidence'])))
        if body.get('draft_id'):
            if drafts is None: raise ValueError('Attachments require the draft service')
            uploaded=drafts.get(body['draft_id'])
            if uploaded.get('correction',{}).get('entry_id')!=entry_id or uploaded['state']!='question': raise ValueError('This correction draft cannot be used')
            draft['files']=deepcopy(uploaded['files'])
        draft['finished_by']=dict(actor='user',reason='correction')
        tx=Transaction(settings,store,draft)
        prepared=tx.prepare(body['proposal'],person=True)
        if set(body.get('approved_accounts',[]))!=set(prepared['new_accounts']): raise ValueError('Tick every new account before correcting')
        docs,before,after,rule_result=prepare_replacement(settings,draft,prepared['entry'],prepared['declarations'])
        targets=evidence_paths(body['proposal'],draft,root)
        after['evidence'].extend(str(t.relative_to(root)) for t in targets)
        moves=move_evidence(docs,root,before,after)
        changed=[key for key in sorted(before.keys()|after.keys()) if before.get(key)!=after.get(key)]
        # Receipt answers may audit a no-change choice; ordinary untouched forms stay neutral.
        if not changed and not body.get('receipt'): raise ValueError('No changes yet')
        before_rules=documents[root/'data/forecast.json']
        result=dict(entry_id=entry_id,description=after['description'],sources=after['evidence'],rule=rule_result,changed=changed)
        record=event('correction',entry_id=entry_id,operation_id=op_id,request_hash=request_hash,before=before,after=after,changed_fields=changed,reason=body.get('reason',''),actor=actor,result=deepcopy(result),rule_changes=rule_changes(before_rules,docs[root/'data/forecast.json']),**({'receipt':body['receipt']} if body.get('receipt') else {}))
        if prepared['new_accounts']:
            record['proposal']=deepcopy(body['proposal'])
            record['account_consent']=dict(proposal_hash=prepared['proposal_hash'],accounts=prepared['new_accounts'],method='hand-ticks')
        result['audit_id']=record['id']
        def one(value): return json.dumps(value,ensure_ascii=False,separators=(',',':'))
        rule_text='none' if rule_result['status']=='none' else f"{rule_result['status']}: {rule_result['description']}; next due {rule_result['next_due']}"
        evidence=[str(t.relative_to(root)) for t in targets]+[str(b.relative_to(root)) for a,b in moves]
        message='\n'.join([f"Edit {after['description']} via dashboard",'',f'Entry: {entry_id}','Changed: '+'; '.join(f'{key}: {one(before.get(key))} → {one(after.get(key))}' for key in changed),'Reason: '+one(body.get('reason','')),'Rule: '+rule_text,'Evidence: '+(', '.join(evidence) or 'none'),f'Draft: {op_id}',trailer or 'Added-by: user (correction)'])
        store.put(draft)
        copies=[(drafts.folder(body['draft_id'])/'files'/file['name'],target) for file,target in zip(draft['files'],targets)]
        result=tx.publish(docs,record,result,fd,message,copies=copies,moves=moves)
    if drafts:
        from transactions import payment
        with drafts.mutex:
            for saved in drafts.all():
                if (saved.get('result') or {}).get('entry_id')==entry_id:
                    saved['result'].update(description=after['description'],sources=after['evidence'],**payment(after,docs[root/'data/accounts.json']['accounts']))
                    drafts.put(saved)
    if body.get('draft_id') and drafts:
        uploaded=drafts.get(body['draft_id']);drafts.completed(uploaded,dict(outcome='ready',**result))
    return refresh(settings,result)
