"""Balance-style Apply using the existing lock, rollback journal and verified commit."""
from copy import deepcopy
from decimal import Decimal
import os
import shutil
import subprocess
from audit import event, append, audit_path
from store import entries, dump, new_id, store_paths, validate
from statement_readers import check_statement, signed, money_text, card_debt
from reconcile_statement import reconciliation_path, card_amount
from ledger_lock import LOCK_FD_ENV
from transactions import jj


def apply_bank(transaction, documents, lock_fd):
    from statements import statement_opening
    t, root, session = transaction, transaction.root, transaction.draft
    statement, checklist = session['statement'], session['plan']['items']
    account = statement['account']
    metadata = documents[root/'data/accounts.json']['accounts'][account]
    currency, end = metadata['currency'], statement['periodEnd']
    if statement.get('currency') != currency:
        raise ValueError('Statement currency changed; rebuild the checklist')
    if any(i['needs_user'] and not i['decision'] for i in checklist):
        raise ValueError('There are undecided statement rows')
    if all(i['rule'] == 'pre_start' for i in checklist):
        raise ValueError('This statement is entirely covered by opening balances. No entries should be applied.')
    target = root/'Bills'/end/session['original_name']
    if not target.resolve().is_relative_to((root/'Bills').resolve()) or not (root/'Bills').resolve().is_relative_to(root.resolve()):
        raise ValueError('Evidence directory escapes Bills')
    if target.exists() or target.is_symlink():
        raise ValueError('A statement file with this name is already filed')
    reconciliation = reconciliation_path(root, end, account, metadata)
    if reconciliation.exists():
        raise ValueError('This reconciliation file already exists')
    source = str(target.relative_to(root))
    record = event('statement', statement={**statement,'source':source}, counts=session['plan']['counts'], decisions=session['decisions'], entry_ids=[], extraction=session['extraction'], runs=session['runs'])
    paths = {root/'data/expenses'/f"{i['row']['date'][:4]}.json" for i in checklist}
    paths.add(root/'data/expenses'/f'{end[:4]}.json')
    journal = t.snapshot([*store_paths(root), root/'expense_ledger.ledger', target, reconciliation, audit_path(root,record), *paths])
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(t.drafts.folder(session['id'])/('read'+session['file_suffix']), target)
        opening = statement_opening(documents, root, statement)
        if opening:
            opening['sources'] = [source]
        actual = {e['id']:e for e in entries(documents)}
        used_ids = {e['id'] for rows in documents.values() if isinstance(rows,list) for e in rows if 'id' in e}
        members, added, matched, used, uncertain = [], [], [], set(), []
        def add(entry):
            entry['id'] = new_id(used_ids)
            documents.setdefault(root/'data/expenses'/f"{entry['date'][:4]}.json", []).append(entry)
            actual[entry['id']] = entry
            added.append(entry['id'])
            return entry['id']
        for item in checklist:
            row, choice = item['row'], item['decision']
            member = {k:row[k] for k in ('date','serial','description','amount','direction','citation','reference','balance') if k in row}
            if item['rule'] == 'pre_start':
                member['disposition'] = 'pre-start'
                members.append(member)
                continue
            identity = choice.get('entry_id') if choice and choice['action'] == 'match' else item['entry_id'] if not choice or choice['action']=='conversion' else None
            if item['rule'] == 'overlap' and choice and choice['action'] == 'pending':
                # Retain the unresolved source row in Review without booking a
                # second financial movement from an overlapping download.
                member.update(disposition='overlap-pending', **{'review-candidates':[c['entryId'] for c in item['candidates']]})
                if not member['review-candidates']:
                    raise ValueError('An overlapping row needs an existing member')
                members.append(member)
                continue
            if identity:
                if identity in used:
                    raise ValueError('An entry was assigned more than once')
                entry = actual[identity]
                if item['rule']=='foreign_currency' and choice and choice['action']=='conversion':
                    from statement_rules import convert
                    convert(entry,row,account)
                if card_amount(entry, account) != signed(row):
                    raise ValueError('Matched account amount changed since review')
                used.add(identity)
                matched.append(identity)
                member.update(disposition='already-in-ledger' if item['rule'] in ('already_in_ledger','overlap') else 'recorded', **{'entry-id':identity})
                # Evidence may be added; financial postings, dates and descriptions
                # on an existing entry are never rewritten by balance-style Apply.
                entry.setdefault('sources', [])
                if source not in entry['sources']:
                    entry['sources'].append(source)
            else:
                if (not choice and not item.get('postings')) or choice and choice['action'] not in ('account','pending'):
                    raise ValueError('New row has no approved account')
                total = -signed(row)
                posting = dict(account=choice['account'] if choice else item['postings'][0]['account'], amount=money_text(total), commodity=currency)
                if row.get('foreignAmount'):
                    original = Decimal(row['foreignAmount']) * (1 if total >= 0 else -1)
                    posting.update(amount=money_text(original), commodity=row['foreignCurrency'],cost=dict(amount=row['amount'], commodity=currency, per='total'))
                entry = dict(date=row['date'], status='pending' if choice and choice['action']=='pending' else 'confirmed', description=row['description'], sources=[source],
                             postings=[posting,dict(account=account,amount=money_text(signed(row)),commodity=currency)] if choice else deepcopy(item['postings']))
                if row.get('reference'):
                    entry['reference'] = row['reference']
                if row.get('citation',{}).get('row'):
                    entry['source-row'] = row['citation']['row']
                elif row.get('page'):
                    entry['source-page'] = row['page']
                if row.get('foreignAmount'):
                    entry['original-amount'] = dict(amount=row['foreignAmount'],commodity=row['foreignCurrency'])
                identity = add(entry)
                member.update(disposition='recorded', **{'entry-id':identity})
                if choice and choice['action']=='pending' and item.get('candidates'):
                    uncertain.append((identity,item))
            members.append(member)
        declarations = documents[root/'data/accounts.json']
        if (not metadata.get('statement') or session.get('learn_mapping')) and statement.get('readerConfig'):
            metadata['statement'] = deepcopy(statement['readerConfig'])
        if any(p['account']=='Expenses:Uncategorized' for e in actual.values() for p in e['postings']):
            declarations['accounts'].setdefault('Expenses:Uncategorized', {})
        for code in {p['commodity'] for e in actual.values() for p in e['postings']}:
            if code not in declarations['commodities']:
                declarations['commodities'].append(code)
        if uncertain:
            from review import link_pair
            for identity, item in uncertain:
                reason = item.get('duplicate_reason') or ('several-candidates' if len(item['candidates'])>1 else 'amount-date-gap' if item['candidates'][0]['dayDistance']>3 else 'dissimilar-name')
                for candidate in item['candidates']:
                    link_pair(actual,identity,candidate['entryId'],reason)
        assertion = dict(id=new_id(used_ids),date=end,status='confirmed',description='Statement closing balance',sources=[source],postings=[dict(account=account,amount='0.00',commodity=currency,**{'assert':dict(amount=statement['closingBalance'],commodity=currency)})])
        # Billed debt is a membership total, not the account balance on that date:
        # unbilled purchases may already exist, or arrive on the next statement.
        if not card_debt(statement):
            documents.setdefault(root/'data/expenses'/f'{end[:4]}.json',[]).append(assertion)
        summary = dict(id=new_id(used_ids),kind='bank',account=account,start=statement['periodStart'],end=end,source=source,reader=statement['reader'],rows=len(members),members=members,
                       **{'opening-balance':statement['openingBalance'],'closing-balance':statement['closingBalance'],'debits':statement['debits'],'credits':statement['credits'],'assertion-id':assertion['id']})
        if card_debt(statement):
            summary.pop('assertion-id')
            summary['source-balance-style'] = 'debt'
        documents[root/'data/card-statements.json'].append(summary)
        documents[root/'data/card-statements.json'].sort(key=lambda s:s['end'])
        for path, rows in documents.items():
            if path.parent.name=='expenses':
                rows.sort(key=lambda e:e['date'])
        validate(documents, root)
        for path, document in documents.items():
            dump(path,document)
        dump(reconciliation,{**statement,'source':source})
        record['entry_ids'] = matched + added + ([] if card_debt(statement) else [assertion['id']]) + ([opening['id']] if opening else [])
        append(root,record)
        result = dict(entry_ids=record['entry_ids'],changed=len(matched),added=len(added),converted=sum(i['rule']=='foreign_currency' and (i['decision'] or {}).get('action')=='conversion' for i in checklist),source=source,end=end,audit_id=record['id'],skipped=sum(m['disposition'] in ('already-in-ledger','pre-start','overlap-pending') for m in members))
        journal['result'] = result
        t.save_journal(journal)
        label = metadata.get('label') or account.split(':')[-1]
        message = f"Reconcile {label} statement ending {end}\n\nMatched {len(matched)} entries; added {len(added)}; skipped {result['skipped']}.\nSource: {source}\n{t.marker}\nAdded-by: dashboard statements ({statement['reader']})"
        completed = subprocess.run(['make','commit'],cwd=t.settings.application,env={**os.environ,'LEDGER_DIR':str(root),LOCK_FD_ENV:str(lock_fd),'EXPENSES_COMMIT_MESSAGE':message},pass_fds=(lock_fd,),capture_output=True,text=True)
        if completed.returncode:
            raise RuntimeError('The checked statement change failed; nothing was applied. '+(completed.stdout+completed.stderr)[-4000:])
        result['commit_id'] = jj(root,'log','-r','@-','--no-graph','-T','commit_id')
        session['committed_result'] = result
        t.drafts.put(session)
        t.journal.unlink()
        return result
    except BaseException:
        t.rollback(journal)
        raise
