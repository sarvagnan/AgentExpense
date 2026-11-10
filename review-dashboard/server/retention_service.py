"""Queue/Trash lifecycle through the shared checked transaction and FIFO."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import secrets
import time

from audit import event, records
from currency_settings import SettingsRecords
from draft_time import client_context, saved_local
from ledger_lock import ledger_lock
from ledger_manifest import read_manifest
from replacements import entry_hash, require_version, safe_evidence, cited, statement_locked
import format1
from rules_service import policy, digest, rule_changes, ignored_entries
from store import load, entries
from transactions import Transaction, LedgerBusy, clean, refresh
# Server and ledger modules share a name, so load the small common contract explicitly.
from importlib.util import spec_from_file_location, module_from_spec
from settings import APPLICATION
_spec = spec_from_file_location('ledger_retention', APPLICATION / 'scripts/ledger/retention.py')
_shared = module_from_spec(_spec); _spec.loader.exec_module(_shared)
limits, AdditionTimes, history_for = _shared.limits, _shared.AdditionTimes, _shared.history_for


def utc(clock=None):
    return clock() if clock else datetime.now(timezone.utc)


def coverage(documents, root, entry, *, restoring=False):
    accounts = documents[root/'data/accounts.json']['accounts']
    if statement_locked(documents, root, entry):
        return 'This entry is included in a filed statement.'
    payers = {p['account'] for p in entry['postings'] if accounts.get(p['account'], {}).get('kind') in ('bank','card','cash','wallet') and Decimal(p['amount']) < 0}
    if not payers:
        payers = {p['account'] for p in entry['postings'] if accounts.get(p['account'], {}).get('kind') in ('bank','card')}
    for row in entries(documents):
        if row['date'] >= entry['date'] and any(p['account'] in payers and accounts.get(p['account'],{}).get('kind')=='bank' and p['assert'] for p in row['postings']):
            return 'A statement-confirmed balance covers this date for the paying account.'
    if restoring:
        for s in documents[root/'data/card-statements.json']:
            if s['account'] not in payers:
                continue
            start = s['start']
            if accounts.get(s['account'],{}).get('kind') == 'card':
                # A card statement also covers the purchases it billed from
                # before its period, so it covers back to its earliest member.
                members = [m['date'] for m in s.get('members', [])] + [e['date'] for e in entries(documents) if (e['statement'] or {}).get('end') == s['end'] and any(p['account'] == s['account'] for p in e['postings'])]
                start = min([start, *members])
            if start <= entry['date'] <= s['end']:
                return 'A statement filed since removal covers this date without this entry.'
    return None


def file_plan(documents, root, entry, reserved=()):
    owned, kept = [], []
    for source in entry['evidence']:
        if not source.startswith('Bills/'):
            continue
        safe_evidence(root, source)
        others = sum(source in e['evidence'] for e in entries(documents) if e['id'] != entry['id'])
        others += sum(source in (r['bill'] or {}).get('evidence',[]) for r in documents[root/'data/forecast.json'])
        others += sum(source in i['entry']['evidence'] for i in documents.get(root/'data/trash.json',[]))
        others += source in reserved
        if others:
            kept.append(dict(path=source, citations=others))
        else:
            owned.append(source)
    return owned, kept


class RetentionRecords(SettingsRecords):
    def __init__(self, root):
        super().__init__(root)
        self.root = root/'drafts/retention'
        if self.root.is_symlink(): raise ValueError('Retention records must stay inside the ledger')
        self.root.mkdir(parents=True, exist_ok=True)


class Retention:
    def __init__(self, settings, drafts, clock=None):
        self.settings, self.drafts, self.clock = settings, drafts, clock
        self.history = history_for(settings.ledger)
        self.history.refresh()
        self.next_sweep = 0
        self.statements = None

    def eligibility(self, entry_id):
        root=self.settings.ledger
        with ledger_lock(root):
            docs=load(root); entry=next((e for e in entries(docs) if e['id']==entry_id),None)
            if not entry: raise ValueError('Unknown entry')
            return self.entry_status(docs, entry, records(root))

    def entry_status(self, docs, entry, lines):
        self.history.refresh()
        root=self.settings.ledger; settings=limits(read_manifest(root)); added=self.history.get(entry['id'], lines)
        reason=coverage(docs, root, entry)
        until=(datetime.fromisoformat(added)+timedelta(days=settings['delete_days'])).isoformat() if added else None
        if not reason and not added: reason='The addition time is unknown. Entries from the first ledger commit cannot be deleted.'
        if not reason and utc(self.clock)>=datetime.fromisoformat(until): reason='The Delete window has passed.'
        reserved=[]
        link=entry.get('rule')
        rule=next((r for r in docs[root/'data/forecast.json'] if r['id']==(link or {}).get('id')),None)
        if rule and not any(e['id']!=entry['id'] and e.get('rule')==link for e in entries(docs)):
            bill=format1.bill(next((r['bill'] for r in reversed(lines) if r['kind']=='rule' and r.get('rule_action')=='bill-paid' and r.get('entry_id')==entry['id']),None))
            if bill: reserved=[s for s in bill['evidence'] if s not in (rule['bill'] or {}).get('evidence',[])]
        owned, kept=file_plan(docs, root, entry,reserved)
        return dict(entry_id=entry['id'],description=entry['description'],version=entry_hash(entry),offered=reason is None,reason=reason,lock='statement' if coverage(docs,root,entry) else 'clock',added_at=added,delete_until=until,restore_until=(utc(self.clock)+timedelta(days=settings['trash_days'])).isoformat(),limits=settings,files=owned,kept=kept,rule=entry.get('rule'))

    def listing(self):
        root=self.settings.ledger; settings=limits(read_manifest(root))
        docs=load(root)
        rows=[dict(id=item['id'],kind='entry',description=item['entry']['description'],removed_at=item['removed-at'],reason=item['reason'],owned_files=[f['original'] for f in item['files']],kept_files=[f['path'] for f in item['kept']],files=[f['original'] for f in item['files']]+[f['path'] for f in item['kept']],entry_id=item['entry']['id']) for item in docs.get(root/'data/trash.json', [])]
        rows += [dict(id=d['id'],kind='queue',description=d['text'] or (d.get('correction') or {}).get('before',{}).get('description') or (d['files'][0]['original_name'] if d['files'] else 'Files only'),removed_at=d['trashed_at'],reason=d.get('trash_reason','Discarded'),files=[f['original_name'] for f in d['files']]) for d in self.drafts.all(include_discarded=True) if d.get('trashed_at')]
        if self.statements:
            rows += [dict(id=s['id'],kind='statement',description=s['original_name'],removed_at=s['trashed_at'],reason=s.get('trash_reason','Discarded'),files=[s['original_name']]) for s in self.statements.all() if s.get('trashed_at')]
        for row in rows: row['expires_at']=(datetime.fromisoformat(row['removed_at'])+timedelta(days=settings['trash_days'])).isoformat()
        return dict(items=sorted(rows,key=lambda x:(x['removed_at'],x['id']),reverse=True),limits=settings)

    def preview(self, values):
        self.validate_limits(values)
        root=self.settings.ledger; before=limits(read_manifest(root)); now=utc(self.clock); docs=load(root); lines=records(root)
        trash=[item for item in self.listing()['items'] if datetime.fromisoformat(item['removed_at'])+timedelta(days=values['trash_days'])<=now]
        saved=self.expired_saved(values['saved_hours'],now)
        reopened=[]
        if values['delete_days']>before['delete_days']:
            self.history.refresh()
            for e in entries(docs):
                added=self.history.get(e['id'],lines)
                if added and not coverage(docs,root,e) and datetime.fromisoformat(added)+timedelta(days=before['delete_days'])<=now<datetime.fromisoformat(added)+timedelta(days=values['delete_days']): reopened.append(dict(id=e['id'],description=e['description']))
        return dict(before=before,after=values,remove_now=trash,move_to_trash=saved,deletable_again=reopened)

    def expired_saved(self, hours, now):
        saved=[(d,d['text']) for d in self.drafts.all() if d['state']=='editing' and (d['text'].strip() or d['files'])]
        if self.statements:
            saved += [(s,s['original_name']) for s in self.statements.all() if s['state']=='saved' and not s.get('trashed_at')]
        return [dict(id=item['id'],description=description) for item,description in saved if datetime.fromisoformat(item.get('updated_at',item['created_at']))+timedelta(hours=hours)<=now]

    @staticmethod
    def validate_limits(values):
        if not isinstance(values,dict) or set(values)!={'delete_days','trash_days','saved_hours'} or any(type(v) is not int or v not in ([12,24,72,168] if k=='saved_hours' else [1,3,7,14,30]) for k,v in values.items()):
            raise ValueError('Choose one of the offered time limits')

    def recover(self, store):
        for folder in store.root.iterdir():
            path=folder/'record.json'
            if not path.exists(): continue
            record=json.loads(path.read_text()); tx=Transaction(self.settings,store,record)
            if tx.journal.exists(): tx.recover()
            if record.get('committed_result') and record.get('runtime_pending'):
                self.runtime(record['runtime_pending']);record.pop('runtime_pending');store.put(record)

    def runtime(self, changes):
        with self.drafts.mutex:
            for change in changes:
                if change.get('kind')=='statement':
                    if self.statements:
                        import shutil
                        if change.get('remove'): shutil.rmtree(self.statements.folder(change['id']),ignore_errors=True)
                        else:
                            with self.statements.mutex:
                                saved=self.statements.get(change['id'],include_discarded=True);saved.update(change['values']);self.statements.put(saved,force=True)
                    continue
                try: draft=self.drafts.get(change['id'],include_discarded=True)
                except FileNotFoundError: continue
                if change.get('remove'):
                    import shutil
                    shutil.rmtree(self.drafts.folder(draft['id']))
                else:
                    for key in change.get('remove_keys',[]): draft.pop(key,None)
                    draft.update(change['values']);self.drafts.put(draft)

    def operate(self, op_id, body):
        if not re.fullmatch('[a-f0-9]{32}',op_id): raise ValueError('Use a unique operation id')
        if not isinstance(body,dict) or set(body)-{'action','entry_id','version','item_id','items','reason','limits','client_time','client_zone'}: raise ValueError('Unknown retention field')
        if not isinstance(body.get('reason',''),str) or len(body.get('reason',''))>10000: raise ValueError('Enter a short reason')
        root=self.settings.ledger; store=RetentionRecords(root); hashed=digest(body); action=body.get('action')
        context=client_context(body.get('client_time'),body.get('client_zone')); now=utc(self.clock); reason=body.get('reason',''); runtime=[]; moves=[]; removals=[]
        with ledger_lock(root) as fd:
            policy(self.settings); self.recover(store)
            lines=records(root); prior=next((r for r in lines if r['kind']=='retention' and r['operation_id']==op_id),None)
            if prior:
                if prior['request_hash']!=hashed: raise ValueError('This operation id was used for a different action')
                return {**prior['result'],'replayed':True}
            if not clean(root): raise LedgerBusy('Waiting for a clean ledger before changing retention')
            docs=load(root); before_rules=deepcopy(docs[root/'data/forecast.json']); trash=docs.setdefault(root/'data/trash.json',[]); selected=[]; details={}; result={}; title=''; event_action=action
            if action=='delete':
                entry=next((e for e in entries(docs) if e['id']==body.get('entry_id')),None);require_version(entry,body.get('version'))
                status=self.entry_status(docs,entry,lines)
                if not status['offered']: raise ValueError(status['reason'])
                selected=[entry['id']]; index=docs[root/f"data/expenses/{entry['date'][:4]}.json"].index(entry)
                docs[root/f"data/expenses/{entry['date'][:4]}.json"].remove(entry)
                link=entry.get('rule');rule=next((r for r in docs[root/'data/forecast.json'] if r['id']==(link or {}).get('id')),None)
                if rule:
                    others=[e for e in entries(docs) if e.get('rule')==link]
                    if not others:
                        rule['date']=rule['schedule']['from']=min(rule['date'],link['occurrence'])
                        bill=format1.bill(next((r['bill'] for r in reversed(lines) if r['kind']=='rule' and r.get('rule_action')=='bill-paid' and r.get('entry_id')==entry['id']),None))
                        if bill:
                            if rule['bill'] and rule['bill']!=bill: raise ValueError('A newer bill is saved; remove it before deleting this payment')
                            rule['bill']=deepcopy(bill);rule['date']=rule['schedule']['from']=bill['occurrence']
                    self.estimate(docs,rule,lines)
                # A restored bill can retain evidence formerly cited by its payment alone.
                owned,kept=file_plan(docs,root,entry)
                files=[dict(original=source,stored=f'data/trash-files/{op_id}/{i}-{Path(source).name}') for i,source in enumerate(owned)]
                moves=[(root/f['original'],root/f['stored']) for f in files]
                links={}
                for other in entries(docs):
                    if any(p['id']==entry['id'] for p in other['possible-duplicate-of']):
                        links[other['id']]=deepcopy(other['possible-duplicate-of']);other['possible-duplicate-of']=[p for p in other['possible-duplicate-of'] if p['id']!=entry['id']]
                item={'id':op_id,'entry':deepcopy(entry),'removed-at':now.isoformat(),'added-at':status['added_at'],'reason':reason,'index':index,'files':files,'kept':kept,
                      'rule-changes':rule_changes(before_rules,docs[root/'data/forecast.json']),'duplicate-links':links}
                trash.append(item);details=deepcopy(item);event_action='deleted';title=f"Delete {entry['description']} via dashboard"
                for d in self.drafts.all(include_discarded=True):
                    if d.get('receipt_reading',{}).get('entry_id')==entry['id']:
                        runtime.append(dict(id=d['id'],values=dict(receipt_reading={**d['receipt_reading'],'state':'cancelled','message':'Entry moved to Trash.'})))
                result=dict(item_id=op_id,entry_id=entry['id'],description=entry['description'])
            elif action=='restore':
                item=next((i for i in trash if i['id']==body.get('item_id')),None)
                if not item: raise ValueError('This entry is no longer in Trash')
                selected=[item['id']];entry=deepcopy(item['entry']);refused=coverage(docs,root,entry,restoring=True)
                if refused:
                    event_action='restore-refused';details=dict(reason=refused,entry=entry);result=dict(refused=True,reason=refused,item_id=item['id']);title=f"Refuse restore {entry['description']} via dashboard"
                else:
                    if any(e['id']==entry['id'] for e in entries(docs)): raise ValueError('This entry already exists')
                    for f in item['files']:
                        old=self.trash_file(f['stored'])
                        target=root/f['original']
                        if target.exists() or target.is_symlink(): raise ValueError('The original evidence path is occupied; restore cannot replace it')
                        self.safe_target(target)
                        moves.append((old,target))
                    for f in item['kept']: safe_evidence(root,f['path'])
                    rows=docs.setdefault(root/f"data/expenses/{entry['date'][:4]}.json",[])
                    # Reinsert at its original same-day position, never after a closing assertion.
                    first=next((i for i,e in enumerate(rows) if e['date']>=entry['date']),len(rows));last=next((i for i,e in enumerate(rows) if e['date']>entry['date']),len(rows))
                    rows.insert(max(first,min(item['index'],last)),entry)
                    for identity,links in item['duplicate-links'].items():
                        other=next((e for e in entries(docs) if e['id']==identity),None)
                        if other: other['possible-duplicate-of']=sorted({p['id']:p for p in [*other['possible-duplicate-of'],*links] if any(e['id']==p['id'] for e in entries(docs))}.values(),key=lambda p:p['id'])
                    if entry['rule']:
                        from recurring import advance
                        rule=next(r for r in docs[root/'data/forecast.json'] if r['id']==entry['rule']['id'])
                        paid_bill=format1.bill(next((r['bill'] for r in reversed(lines) if r['kind']=='rule' and r.get('rule_action')=='bill-paid' and r.get('entry_id')==entry['id']),None))
                        if paid_bill and rule['bill']==paid_bill: rule['bill']=None
                        through=max([entry['rule']['occurrence'],*[e['rule']['occurrence'] for e in entries(docs) if (e['rule'] or {}).get('id')==rule['id']]])
                        # An intervening rule edit or newer bill may already put
                        # its next occurrence beyond this restored payment.
                        if rule['date']<=through: rule.update(advance(rule,through))
                        self.estimate(docs,rule,lines)
                    trash.remove(item);details=dict(entry=entry,files_restored=[f['original'] for f in item['files']],rule_changes=rule_changes(before_rules,docs[root/'data/forecast.json']));event_action='restored';title=f"Restore {entry['description']} via dashboard";result=dict(entry_id=entry['id'],description=entry['description'],item_id=item['id'])
            elif action in ('queue-discard','queue-restore'):
                with self.drafts.mutex:
                    try: d=self.drafts.get(body.get('item_id',''),include_discarded=True);kind='queue'
                    except FileNotFoundError:
                        if not self.statements: raise ValueError('Unknown Queue item')
                        try: d=self.statements.get(body.get('item_id',''),include_discarded=True);kind='statement'
                        except FileNotFoundError: raise ValueError('This Queue item is no longer available') from None
                    if d['state'] in ('applied','already_filed') or d['state']=='added' and not d.get('statement_file') or d.get('intake_complete'): raise ValueError('Added entries leave the Queue; use the entry inspector')
                    if kind=='statement' and (d.get('apply_started') or d['state']=='applying'): raise ValueError('The checked apply has started; wait for it to finish')
                    if kind=='queue' and action=='queue-discard' and d.get('correction_save'):raise ValueError('Save is in progress; check its result before discarding this item')
                    if kind=='queue' and (d['state']=='working' or d.get('claim')): raise ValueError('Release the form or wait for the reading to finish')
                    restore=action=='queue-restore'
                    if restore != bool(d.get('trashed_at')): raise ValueError('The item is not in the expected location')
                    values=dict(state='editing',updated_at=now.isoformat(),trashed_at=None,discarded_until=None,trash_reason=None,claim=None,proposal=None,usual_payment=None,bill_request=None,hand_form=None,correction=None,refusal=None,waiting=None,error=None,result=None) if restore else dict(trashed_at=now.isoformat(),discarded_until=(now+timedelta(days=limits(read_manifest(root))['trash_days'])).isoformat(),trash_reason=reason or 'Discarded')
                    if restore and kind=='queue' and d.get('correction'):
                        # Restore is the exact unsaved edit/proposal, never unsent Add words.
                        values=dict(state='question',updated_at=now.isoformat(),trashed_at=None,discarded_until=None,trash_reason=None,claim=None,error=None,waiting=None)
                    if restore and kind=='queue' and d.get('ledger_fix',{}).get('state') in ('queued','working','failed'):
                        values.update(state='failed')
                        if d['ledger_fix']['state'] in ('queued','working'):
                            values['ledger_fix']={**d['ledger_fix'],'state':'failed','message':'This proposal was stopped. Retry to ask the agent again, or discard it.'}
                    if kind=='statement':
                        values.update(state='saved' if restore else d['state'],cancelled=True,generation=d.get('generation',0)+1)
                    runtime=[dict(id=d['id'],kind=kind,values=values)]
                    if kind=='queue': runtime[0]['remove_keys']=['correction_save']
                    if restore and kind=='queue' and not d.get('correction'):
                        # A restored item is unsent words, not a replay of its
                        # former checked/hand-finished payment or correction.
                        runtime[0]['remove_keys']+=['usual_payment','usual_rule_hash','usual_request','bill_request','finished_by','account_consent','intake_result','hand_reason','correction','refusal']
                        for key in runtime[0]['remove_keys']: values.pop(key,None)
                    if restore and kind=='queue' and d.get('correction'):
                        runtime[0]['remove_keys']+=['claim','hand_checks','account_consent']
                        values.pop('claim',None)
                    selected=[d['id']];details=dict(text=d.get('text',''),files=[f['original_name'] for f in d['files']] if kind=='queue' else [d['original_name']],reason=reason);event_action='queue-restored' if restore else 'queue-trashed';title=('Restore' if restore else 'Discard')+' Queue item via dashboard';result=dict(item_id=d['id'])
            elif action=='remove':
                selected=body.get('items')
                if not isinstance(selected,list) or not selected or len(set(selected))!=len(selected): raise ValueError('Choose unique Trash items')
                listed={i['id']:i for i in self.listing()['items']}
                if any(i not in listed for i in selected): raise ValueError('A selected item is no longer in Trash')
                details=dict(items=[listed[i] for i in selected])
                for item in list(trash):
                    if item['id'] not in selected: continue
                    removals.extend(self.trash_file(f['stored']) for f in item['files']);trash.remove(item)
                    for rule in docs[root/'data/forecast.json']:
                        for field in ('origin','removal'):
                            if rule[field]: rule[field]['entry-ids']=[i for i in rule[field]['entry-ids'] if i!=item['entry']['id']]
                # A shared file may have stayed in Bills for another trashed entry.
                # Remove it only when no live or remaining Trash record cites it.
                removed_sources={source for item in details['items'] if item['kind']=='entry' for source in item['files'] if source.startswith('Bills/')}
                for source in removed_sources:
                    if (root/source).is_file() and not any(cited(value,source) for value in docs.values()): removals.append(safe_evidence(root,source))
                runtime=[dict(id=i,kind=listed[i]['kind'],remove=True) for i in selected if listed[i]['kind']!='entry'];event_action='removed-for-good';title=f'Remove {len(selected)} entries for good via dashboard';result=dict(removed=selected)
            elif action=='settings':
                self.validate_limits(body.get('limits'));preview=self.preview(body['limits']);manifest=__import__('store').read_json(root/'ledger.json');manifest['retention']=body['limits'];docs[root/'ledger.json']=manifest
                selected=[];details=preview;event_action='settings-changed';title='Change time limits via dashboard';result=dict(limits=body['limits'])
            else: raise ValueError('Unknown retention action')
            details.setdefault('reason',reason)
            record=event('retention',retention_action=event_action,operation_id=op_id,request_hash=hashed,selected=selected,details=details,result=deepcopy(result),**context)
            record['time']=now.isoformat()
            # No empty trash file is introduced by record-only outcomes/settings.
            if not trash and not (root/'data/trash.json').exists(): docs.pop(root/'data/trash.json')
            local=dict(created_at=now.isoformat(),**context)
            msg=[title,'','Saved: '+saved_local(local),'Entry: '+(result.get('entry_id') or ', '.join(selected) or 'none'),'Reason: '+json.dumps(reason,ensure_ascii=False)]
            if action in ('delete','restore'):
                rule=next((r for r in docs[root/'data/forecast.json'] if r['id']==(entry.get('rule') or {}).get('id')),None)
                msg+=['Rule: '+(rule['date'] if rule else 'none')]
            if action=='delete': msg+=['Files to Trash: '+(', '.join(f['original'] for f in details['files']) or 'none'),'Files kept: '+(', '.join(f['path'] for f in details['kept']) or 'none')]
            if action=='restore': msg+=['Files restored: '+(', '.join(details.get('files_restored',[])) or 'none')]
            msg += [f'Draft: {op_id}','Added-by: user (retention)']
            operation=dict(id=op_id,runtime_pending=runtime);store.put(operation);tx=Transaction(self.settings,store,operation)
            result=tx.publish(docs,record,result,fd,'\n'.join(msg),moves=moves,removals=removals)
            self.runtime(runtime);operation.pop('runtime_pending',None);store.put(operation)
        result=refresh(self.settings,result)
        if action=='settings': self.sweep(force=True)
        return result

    def safe_target(self, target):
        root=self.settings.ledger
        if not target.is_relative_to(root): raise ValueError('File escapes ledger')
        for p in [target,*target.parents]:
            if p==root: break
            if p.is_symlink(): raise ValueError('Evidence symlinks are not allowed')

    def trash_file(self, relative):
        if not re.fullmatch(r'data/trash-files/[a-f0-9]{32}/[^/]+',relative) or Path(relative).name in ('.','..'): raise ValueError('Invalid Trash file')
        path=self.settings.ledger/relative;self.safe_target(path)
        if not path.is_file(): raise ValueError('Trash evidence is missing')
        return path

    def estimate(self, docs, rule, lines):
        from recurring import recompute_estimate
        ignored=ignored_entries(lines)
        recompute_estimate(rule,[e for e in entries(docs) if e['id'] not in ignored],docs[self.settings.ledger/'data/accounts.json']['accounts'])

    def sweep(self, force=False):
        if not force and time.monotonic()<self.next_sweep: return
        self.next_sweep=time.monotonic()+30
        root=self.settings.ledger
        try:
            with ledger_lock(root,blocking=False):
                self.recover(RetentionRecords(root))
                if not clean(root): return
            settings=limits(read_manifest(root));now=utc(self.clock)
            for item in self.expired_saved(settings['saved_hours'],now):
                self.operate(secrets.token_hex(16),dict(action='queue-discard',item_id=item['id'],reason='Saved for later expired'))
            expired=[i['id'] for i in self.listing()['items'] if datetime.fromisoformat(i['expires_at'])<=now]
            if expired: self.operate(secrets.token_hex(16),dict(action='remove',items=expired,reason='Trash limit expired'))
        except (BlockingIOError,LedgerBusy): return
