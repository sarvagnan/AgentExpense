"""Checked entry replacements shared by corrections and duplicate merges."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import os
import shutil
import subprocess

from transactions import ProposalError
from store import entries, extensions_of, validate
from confirmed_balances import correction_locks, enforce_locks, enforce_order, raise_failure


def entry_hash(entry):
    from proposal_consent import proposal_hash
    return proposal_hash(entry)


def require_version(entry, expected):
    if not entry or entry_hash(entry) != expected:
        raise ValueError('This entry changed since the form opened. Reopen it and check the saved entry.')


def statement_locked(documents, root, entry):
    return bool((entry['statement'] or {}).get('end') or any(s.get('assertion-id') == entry['id'] or any(m.get('entry-id') == entry['id'] for m in s.get('members', [])) for s in documents[root/'data/card-statements.json']))


def enforce_statement(before, after, documents, root):
    if not statement_locked(documents, root, before):
        return
    old, new = before['postings'], after['postings']
    accounts = documents[root/'data/accounts.json']['accounts']
    if len(old) != len(new) or any(any(a.get(k) != b.get(k) for k in ('amount','commodity','cost','assert')) or accounts.get(a['account'],{}).get('kind') and a['account'] != b['account'] for a,b in zip(old,new)):
        raise ValueError('This entry belongs to a filed statement. Amounts, currencies, costs, posting count and paying accounts are locked.')


def replace_entry(documents, root, before, after, *, expected, remove=None, removed_expected=None):
    """Pure replacement, retaining identity and checking both sides of a merge."""
    current = next((e for e in entries(documents) if e['id'] == before['id']), None)
    require_version(current, expected)
    if remove:
        require_version(next((e for e in entries(documents) if e['id']==remove['id']),None), removed_expected)
    after = deepcopy(after)
    after['id'] = before['id']
    locks = enforce_locks(before, after, documents, root)
    enforce_statement(before, after, documents, root)
    old_rows=documents[root/'data/expenses'/f"{before['date'][:4]}.json"]
    old_index=next(i for i,e in enumerate(old_rows) if e['id']==before['id'])
    old_prefix=old_rows[:old_index]
    for path, rows in documents.items():
        if path.parent.name == 'expenses':
            rows[:] = [e for e in rows if e['id'] not in {before['id'], (remove or {}).get('id')}]
    path = root/'data/expenses'/f"{after['date'][:4]}.json"
    rows = documents.setdefault(path, [])
    # Preserve same-day order for statement assertions.
    index = next((i for i,e in enumerate(rows) if e['date'] > after['date']),len(rows))
    if before['date']==after['date']:
        index=sum(e['id'] not in {before['id'],(remove or {}).get('id')} for e in old_prefix)
    rows.insert(index,after)
    enforce_order(before, after, documents, root, locks)
    return after


def check_documents(documents, root, candidate=None):
    from generate_ledger import render_entry
    validate(documents, root)
    declarations=documents[root/'data/accounts.json']
    lines=[*("commodity "+c for c in declarations['commodities']),*("account "+a for a in declarations['accounts']), '']
    extensions=extensions_of(root)
    for entry in entries(documents): lines.extend(render_entry(entry, extensions))
    hledger=os.environ.get('HLEDGER_BIN') or shutil.which('hledger') or '/opt/homebrew/bin/hledger'
    checked=subprocess.run([hledger,'-f','-','check','-s','ordereddates'],input='\n'.join(lines)+'\n',capture_output=True,text=True)
    if checked.returncode:
        raise_failure(documents, root, candidate=candidate, detail=checked.stderr)
        raise ProposalError('Proposed correction fails strict hledger validation:\n'+checked.stderr)


def safe_evidence(root, relative):
    """Refuse every symlink component, traversal and non-evidence path."""
    path=Path(relative)
    if path.is_absolute() or not path.parts or path.parts[0]!='Bills' or '..' in path.parts:
        raise ValueError('Receipt files must be inside the ledger evidence folder')
    target=root
    for part in path.parts:
        target=target/part
        if target.is_symlink(): raise ValueError('Evidence symlinks are not allowed')
    if not target.resolve().is_relative_to((root/'Bills').resolve()) or not target.is_file():
        raise ValueError('Evidence file is missing or outside the ledger evidence folder')
    return target


def cited(value, source):
    if isinstance(value,str): return value==source
    if isinstance(value,list): return any(cited(v,source) for v in value)
    if isinstance(value,dict): return any(cited(v,source) for v in value.values())
    return False


def move_evidence(documents, root, before, after):
    """Plan exclusive old-date evidence moves; publication journals both ends."""
    moves=[];used=set()
    if before['date']==after['date']: return moves
    old_folder=Path('Bills')/before['date']
    for source in list(after['evidence']):
        if source not in before['evidence'] or Path(source).parent!=old_folder: continue
        old=safe_evidence(root,source)
        others=[e for e in entries(documents) if e['id']!=after['id']]
        others += [v for path,v in documents.items() if path.parent.name!='expenses']
        if any(cited(v,source) for v in others): continue
        folder=root/'Bills'/after['date'];target=folder/old.name;number=2
        if folder.is_symlink() or not folder.resolve().is_relative_to((root/'Bills').resolve()): raise ValueError('Evidence folder escapes the ledger')
        while target.exists() or target.is_symlink() or target in used:
            target=folder/f'{old.stem}-{number}{old.suffix}';number+=1
        used.add(target);moves.append((old,target));after['evidence'][after['evidence'].index(source)]=str(target.relative_to(root))
    return moves


def correction_rules(documents, root, before, after):
    from recurring import same_payment, advance, period_for, simple_payment, main_postings, recompute_estimate
    rules=documents[root/'data/forecast.json'];accounts=documents[root/'data/accounts.json']['accounts']
    link=before.get('rule')
    if not link: return dict(status='none',description=None,next_due=None)
    rule=next(r for r in rules if r['id']==link['id'])
    # Match the complete payment, including costs and every allocation leg.
    def signature(entry):
        return sorted((p['account'],p['commodity'],Decimal(p['amount']), (p['cost']['commodity'],Decimal(p['cost']['amount']),p['cost']['per']) if p.get('cost') else ('',Decimal(0),'')) for p in entry['postings'])
    varying=rule.get('amount')=='varies'
    bill_payments=set();lines=[]
    if varying:
        from audit import records
        lines=records(root)
        bill_payments={r.get('entry_id') for r in lines if r['kind']=='rule' and r['rule_action']=='bill-paid'}
    kept=same_payment(after,rule,accounts) and (simple_payment(after,accounts) and main_postings(after)[0]['commodity']==main_postings(rule)[0]['commodity'] if varying else signature(after)==signature(rule))
    if kept: after['rule']=dict(id=rule['id'],occurrence=link['occurrence'] if before['id'] in bill_payments else period_for(rule,after['date']))
    else: after['rule']=None
    actual=[e for e in entries(documents) if (e['rule'] or {}).get('id')==rule['id'] and e['id']!=after['id']]
    def paid_through(entry):
        return entry['rule']['occurrence'] if entry['id'] in bill_payments else max(entry['date'],entry['rule']['occurrence'])
    latest_before=max([before,*actual],key=lambda e:(paid_through(e) if varying else e['date'],e['id']))
    # An older correction never shifts the current schedule.
    if latest_before['id']==before['id']:
        if kept: actual.append(after)
        if actual:
            latest=max(actual,key=lambda e:(paid_through(e) if varying else e['date'],e['id']))
            start=deepcopy(rule);start['date']=start['schedule']['from']=latest['rule']['occurrence']
            rules[rules.index(rule)]=rule=advance(start,paid_through(latest))
    if varying:
        from rules_service import ignored_entries
        ignored=ignored_entries(lines)
        recompute_estimate(rule,[e for e in entries(documents) if e['id']!=after['id'] and e['id'] not in ignored]+[after],accounts)
    return dict(status='kept' if kept else 'dropped',description=rule['description'],next_due=rule['date'])
