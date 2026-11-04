"""Human review writes use disposable ledgers; never agents or browser state."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import threading
from unittest.mock import patch

from support import Fixture
from store import dump, load, read_json, entries
from audit import records, event, append
from ledger_lock import ledger_lock
from review import snapshot, apply, import_preview, link_pair, merge_order
from transactions import clean

AT = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


class ReviewTests(Fixture):
    def seed(self, *, pair=False, statement=False):
        rows = [dict(id=f'{i:016x}', date=f'2026-10-{20+i:02d}', status='pending', description=f'Willow Workshop {i}',
                     sources=[f'Bills/receipt-{i}.md'], notes=[f'Invented note {i}'],
                     postings=[dict(account='Expenses:Food', amount='12.50', commodity='INR'), dict(account='Assets:Cash', amount='-12.50', commodity='INR')]) for i in range(1, 4)]
        for row in rows:
            (self.ledger / row['sources'][0]).write_text('Fictional evidence')
        if pair:
            link_pair({e['id']:e for e in rows}, rows[0]['id'], rows[1]['id'], 'amount-date-gap')
        if statement:
            # A real membership statement on the second entry, separate card and
            # zero opening; its financial postings must survive merge unchanged.
            declarations = read_json(self.ledger/'data/accounts.json')
            declarations['accounts']['Liabilities:Card'] = dict(kind='card', currency='INR', label='Mosaic Card')
            dump(self.ledger/'data/accounts.json', declarations)
            rows[1]['postings'][1]['account'] = 'Liabilities:Card'
            rows[1].update({'statement-end':'2026-10-28','serial-number':'RW-1','statement-page':1})
            rows[1]['sources'].append('Bills/statement.pdf')
            (self.ledger/'Bills/statement.pdf').write_bytes(b'%PDF-fictional offline membership fixture')
            rows.insert(0,dict(id='0000000000000004',date='2026-10-01',status='confirmed',description='Card opening',postings=[dict(account='Liabilities:Card',amount='0.00',commodity='INR'),dict(account='Equity:Opening Balances',amount='0.00',commodity='INR')]))
            declarations['accounts']['Equity:Opening Balances']={};dump(self.ledger/'data/accounts.json',declarations)
            dump(self.ledger/'data/card-statements.json',[dict(id='0000000000000005',account='Liabilities:Card',start='2026-10-01',end='2026-10-28',source='Bills/statement.pdf',**{'previous-balance':'0.00','purchases':'12.50','credits':'0.00','total-due':'12.50','rows':1})])
        dump(self.ledger/'data/expenses/2026.json',rows)
        self.jj('commit','-m','Fictional review baseline')
        return rows

    def body(self, action, *ids, **extra):
        state = snapshot(self.ledger,clock=lambda:AT)
        return dict(action=action, request_id=__import__('secrets').token_hex(16),
                    **({'entry_id':ids[0]} if ids else {}), **({'other_id':ids[1]} if len(ids)>1 else {}),
                    versions={i:state['entries'][i]['version'] for i in ids}, **extra)

    def decide(self, status='resolved', account='Expenses:Education'):
        return dict(status=status, account=account, label='Willow class', notes='Checked receipt\nConfirmed category',updatedAt=AT.isoformat())

    def check_commit(self, before, expected):
        self.assertTrue(clean(self.ledger))
        self.assertNotEqual(before,self.jj('log','-r','@-','--no-graph','-T','commit_id'))
        self.assertEqual(records(self.ledger)[-1]['action'],expected)
        self.assertIn('Review: '+expected,self.jj('log','-r','@-','--no-graph','-T','description'))
        subprocess.run(['make','verify'],cwd=self.application,env={**__import__('os').environ,'LEDGER_DIR':str(self.ledger)},check=True,capture_output=True)

    def test_resolve_followup_idempotency_versions_and_audit(self):
        rows=self.seed();identity=rows[0]['id'];unchanged=deepcopy(rows[0]);before=self.jj('log','-r','@-','--no-graph','-T','commit_id')
        body=self.body('followup',identity,decision=self.decide('followup'))
        with patch('review.subprocess.run', wraps=subprocess.run) as run:
            result=apply(self.settings,body)
        self.check_commit(before,'followup')
        self.assertEqual(read_json(self.ledger/'data/expenses/2026.json')[0],unchanged)
        self.assertEqual(snapshot(self.ledger)['count'],3)
        calls=[c for c in run.call_args_list if c.args[0]==['make','commit']]
        self.assertEqual(len(calls),1)
        fd=calls[0].kwargs['pass_fds'][0]
        self.assertEqual(calls[0].kwargs['env']['EXPENSES_LEDGER_LOCK_FD'] if 'EXPENSES_LEDGER_LOCK_FD' in calls[0].kwargs['env'] else calls[0].kwargs['env'][__import__('ledger_lock').LOCK_FD_ENV],str(fd))
        parent=self.jj('log','-r','@-','--no-graph','-T','commit_id')
        self.assertTrue(apply(self.settings,body)['replayed']);self.assertEqual(parent,self.jj('log','-r','@-','--no-graph','-T','commit_id'))
        with self.assertRaisesRegex(ValueError,'different action'):apply(self.settings,{**body,'action':'resolve'})
        with self.assertRaisesRegex(ValueError,'changed since'):apply(self.settings,{**body,'request_id':'1'*32})
        before=self.jj('log','-r','@-','--no-graph','-T','commit_id')
        apply(self.settings,self.body('resolve',identity,decision=self.decide()))
        updated=read_json(self.ledger/'data/expenses/2026.json')[0]
        self.assertEqual(updated['status'],'confirmed');self.assertEqual(updated['description'],'Willow class')
        self.assertEqual(updated['postings'][0],{**unchanged['postings'][0],'account':'Expenses:Education'})
        self.assertEqual(updated['postings'][1:],unchanged['postings'][1:]);self.assertEqual(updated['sources'],unchanged['sources'])
        self.assertEqual(updated['notes'],['Invented note 1','Checked receipt','Confirmed category'])
        self.check_commit(before,'resolve');self.assertEqual(snapshot(self.ledger)['count'],2)

    def test_separate_history_and_removed_reverse_action(self):
        rows=self.seed(pair=True);one,two=rows[0]['id'],rows[1]['id']
        result=apply(self.settings,self.body('separate',one,two))
        self.assertEqual(snapshot(self.ledger)['important'],0)
        with self.assertRaises(ValueError): apply(self.settings,self.body('undo-separate',one,two,undoes=result['audit_id']))

    def test_merge_keeps_statement_postings_and_moves_receipts_notes(self):
        rows=self.seed(pair=True,statement=True);one='0000000000000001';two='0000000000000002'
        prior=deepcopy(next(e for e in rows if e['id']==two))
        result=apply(self.settings,self.body('merge',one,two));state=snapshot(self.ledger)
        self.assertNotIn(one,state['entries']);kept=next(e for e in entries(load(self.ledger)) if e['id']==two)
        self.assertEqual(kept['postings'],prior['postings']);self.assertEqual(kept['status'],prior['status'])
        self.assertEqual(kept['notes'],['Invented note 2','Invented note 1']);self.assertIn('Bills/receipt-1.md',kept['sources'])
        self.assertEqual(result['changes'][0]['kept_id'],two);self.assertTrue(clean(self.ledger))
        self.assertEqual(snapshot(self.ledger)['important'],0)

    def test_merge_uses_first_addition_not_date_or_id_and_retargets_other_edges(self):
        rows=self.seed(pair=True);one,two,third=[r['id'] for r in rows]
        # Both were imported in the same baseline; lower ID is only the tie break.
        self.assertEqual(merge_order(rows[0],rows[1],[],self.ledger)[0]['id'],one)
        lines=[dict(kind='draft',outcome='added',entry_id=one,time='2026-10-30T00:00:00Z'),dict(kind='draft',outcome='added',entry_id=two,time='2026-10-29T00:00:00Z')]
        self.assertEqual(merge_order(rows[0],rows[1],lines)[0]['id'],two)
        link_pair({e['id']:e for e in rows},two,third,'several-candidates');dump(self.ledger/'data/expenses/2026.json',rows);self.jj('commit','-m','Another uncertain pair')
        before=deepcopy(rows[0]['postings']);apply(self.settings,self.body('merge',one,two))
        current=entries(load(self.ledger));self.assertEqual(current[0]['postings'],before)
        self.assertEqual(current[0]['possible-duplicate-of'],[dict(id=third,reason='several-candidates')])

    def test_merge_keeps_evidence_paths_when_the_kept_entry_date_is_unchanged(self):
        rows=self.seed(pair=True);one,two=[r['id'] for r in rows[:2]]
        for entry in rows[:2]:
            old=self.ledger/entry['sources'][0];target=self.ledger/'Bills'/entry['date']/old.name
            target.parent.mkdir(parents=True,exist_ok=True);old.rename(target);entry['sources']=[str(target.relative_to(self.ledger))]
        dump(self.ledger/'data/expenses/2026.json',rows);self.jj('commit','-m','Dated duplicate evidence')
        expected=deepcopy(rows[1]['sources']);apply(self.settings,self.body('merge',one,two))
        kept=next(e for e in entries(load(self.ledger)) if e['id']==one)
        self.assertTrue(all(source in kept['sources'] and (self.ledger/source).is_file() for source in expected))

    def test_recent_doubt_boundary_reviewed_and_large_backlog(self):
        rows=self.seed();rows[0]['status']='confirmed';rows[0]['postings'][0]['account']='Assets:Wallet'
        declarations=read_json(self.ledger/'data/accounts.json');declarations['accounts']['Assets:Wallet']=dict(kind='wallet',currency='INR');dump(self.ledger/'data/accounts.json',declarations)
        dump(self.ledger/'data/expenses/2026.json',rows)
        self.assertEqual(snapshot(self.ledger,clock=lambda:AT)['important'],0)
        for i,offset in enumerate([timedelta(days=7),timedelta(days=7,microseconds=1),timedelta(days=6)]):
            record=event('draft',draft_id=str(i),outcome='added',text='Fictional',saved_at=None,pieces=None,files=[],questions=[],runs=[],proposal=None,doubts=['Unclear category'],check_errors=[],error=None,entry_id=rows[i]['id'])
            record['time']=(AT-offset).isoformat();append(self.ledger,record)
        self.jj('commit','-m','Fictional doubtful additions')
        state=snapshot(self.ledger,clock=lambda:AT);self.assertEqual(state['count'],3);self.assertEqual(state['important'],2)
        self.assertEqual([i['kind'] for i in state['items']],['doubt','doubt','pending'])
        audit_file=self.ledger/'data/additions/2026.jsonl';before_audit=audit_file.read_bytes()
        apply(self.settings,self.body('acknowledge',rows[0]['id']),clock=lambda:AT)
        self.assertEqual(snapshot(self.ledger,clock=lambda:AT)['important'],1)
        self.assertEqual(snapshot(self.ledger,clock=lambda:AT)['count'],2,'Acknowledging the confirmed entry settles its doubt without changing postings')
        exported=snapshot(self.ledger,clock=lambda:AT)
        self.assertEqual(import_preview(self.ledger,dict(reviewVersion=2,transactions=[dict(**exported['export_entries'][rows[0]['id']],decision=exported['decisions'][rows[0]['id']])]),clock=lambda:AT)['already'],1)
        portable=dict(reviewVersion=2,transactions=[dict(**exported['export_entries'][rows[0]['id']],decision=exported['decisions'][rows[0]['id']])])
        for path in (self.ledger/'data/additions').glob('*.jsonl'):path.unlink()
        audit_file.write_bytes(before_audit);self.jj('commit','-m','Fictional original doubt audit for portable import')
        preview=import_preview(self.ledger,portable,clock=lambda:AT);self.assertEqual(preview['valid'],1)
        apply(self.settings,self.body('import',payload=portable,preview_token=preview['token']),clock=lambda:AT)
        self.assertEqual(snapshot(self.ledger,clock=lambda:AT)['important'],1)
        self.assertEqual(entries(load(self.ledger))[0]['postings'],rows[0]['postings'])


    def test_import_preview_old_ids_changed_unknown_one_commit_and_replay(self):
        rows=self.seed();state=snapshot(self.ledger)
        one={**state['entries'][rows[0]['id']],'decision':self.decide()}
        two={**state['entries'][rows[1]['id']],'id':'legacy-derived-id','decision':self.decide('followup')};two.pop('version')
        unknown=dict(id='unknown',decision=self.decide())
        changed={**state['entries'][rows[2]['id']],'description':'Changed description','decision':self.decide()}
        payload=dict(reviewVersion=2,transactions=[one,two,unknown,changed],drafts={},unmatched={})
        preview=import_preview(self.ledger,payload);self.assertEqual([preview[k] for k in ('valid','unknown','changed')],[2,1,1])
        recovery=dict(reviewVersion=2,transactions=[],drafts={rows[0]['id']:dict(account='',label='Unsent label',notes='Not yet decided')})
        recovered=import_preview(self.ledger,recovery);self.assertEqual((recovered['valid'],recovered['drafts']),(0,1))
        recovery['drafts'][rows[0]['id']]['notes']=[]
        with self.assertRaisesRegex(ValueError,'invalid unsent edits'):import_preview(self.ledger,recovery)

        body=self.body('import',payload=payload,preview_token=preview['token'])
        result=apply(self.settings,body);self.assertEqual(len(records(self.ledger)),1);self.assertEqual(len(result['changes']),2)
        self.assertEqual(snapshot(self.ledger)['count'],2);self.assertTrue(apply(self.settings,body)['replayed'])
        self.assertEqual(import_preview(self.ledger,payload)['already'],2)

    def test_import_stale_atomicity_and_verification_rollback(self):
        rows=self.seed();state=snapshot(self.ledger)
        payload=dict(reviewVersion=2,transactions=[{**e,'decision':self.decide()} for e in state['entries'].values()])
        preview=import_preview(self.ledger,payload);before=self.bytes()
        real_run=subprocess.run
        def fail(args,*a,**kw):
            if args==['make','commit']:return subprocess.CompletedProcess(args,1,'','Injected failure')
            return real_run(args,*a,**kw)
        with patch('review.subprocess.run',side_effect=fail),self.assertRaisesRegex(RuntimeError,'failed verification'):
            apply(self.settings,self.body('import',payload=payload,preview_token=preview['token']))
        self.assertEqual(self.bytes(),before);self.assertTrue(clean(self.ledger));self.assertEqual(records(self.ledger),[])
        apply(self.settings,self.body('followup',rows[0]['id'],decision=self.decide('followup')))
        with self.assertRaisesRegex(ValueError,'preview changed'):apply(self.settings,self.body('import',payload=payload,preview_token=preview['token']))

    def test_access_rules_and_dirty_ledger_refusal(self):
        rows=self.seed()
        with self.client(worker=False) as client:
            body=self.body('resolve',rows[0]['id'],decision=self.decide())
            self.assertEqual(client.get('/api/review').status_code,200)
            self.assertEqual(client.post('/api/review/actions',json=body,headers={'origin':'https://foreign.invalid'}).status_code,403)
            (self.ledger/'unfinished').write_text('Human work')
            response=client.post('/api/review/actions',json=body);self.assertEqual(response.status_code,409,response.text)
            self.assertIn('uncommitted',response.text)
        self.assertEqual(records(self.ledger),[])

    def test_statement_uncertain_candidates_survive_pending_apply(self):
        from statement_rules import plan
        from statement_fixtures import statement_pdf
        from statements import StatementTransaction, fingerprint
        rows=self.seed(statement=True)
        docs=load(self.ledger)
        card='Liabilities:Card'
        # Unbilled same-amount charge four days before the printed date.
        candidate=next(e for e in entries(docs) if e['id']=='0000000000000003')
        candidate['date']='2026-11-02';candidate['postings'][1]['account']=card
        dump(self.ledger/'data/expenses/2026.json',sorted(entries(docs),key=lambda e:e['date']))
        self.jj('commit','-m','Fictional uncertain statement charge')
        statement=dict(account=card,accountAssigned=True,accountLabel='Mosaic Card',currency='INR',card_last4=None,periodStart='2026-10-29',periodEnd='2026-11-28',previousBalance='12.50',purchasesCharges='12.50',paymentsCredits='0.00',cashAdvances='0.00',totalDue='25.00',rows=[dict(date='2026-11-06',serial='RW-2',description=candidate['description'],amount='12.50',direction='Debit',page=1)])
        planned=plan(load(self.ledger),statement,self.ledger)
        item=planned['items'][0];self.assertTrue(item['needs_user']);self.assertEqual(item['candidates'][0]['dayDistance'],4)
        item['decision']=dict(action='pending',account='Expenses:Uncategorized')
        from statements import Statements
        sessions=Statements(self.settings)
        session=dict(id='a'*32,state='applying',original_name='fictional-next-statement.pdf',statement=statement,plan=planned,decisions=[dict(serial='RW-2',action='pending')],extraction='parser',runs=[],fingerprint=fingerprint(self.ledger))
        sessions.put(session);(sessions.folder(session['id'])/'read.pdf').write_bytes(statement_pdf('Fictional statement'))
        with ledger_lock(self.ledger) as fd:
            result=StatementTransaction(self.settings,sessions,session).apply_statement(fd)
        actual=entries(load(self.ledger));pairs=[(e,l) for e in actual for l in e.get('possible-duplicate-of',[])]
        self.assertEqual(len(pairs),1);self.assertEqual(pairs[0][1]['reason'],'amount-date-gap')
        self.assertEqual({pairs[0][0]['id'],pairs[0][1]['id']},{candidate['id'],result['entry_ids'][0]})
        self.assertTrue(clean(self.ledger))

    def test_resolved_export_retains_predecision_identity_for_portability(self):
        rows=self.seed();original=deepcopy(rows)
        apply(self.settings,self.body('followup',rows[0]['id'],decision=self.decide('followup')))
        apply(self.settings,self.body('resolve',rows[0]['id'],decision=self.decide()))
        state=snapshot(self.ledger)
        exported=state['export_entries'][rows[0]['id']]
        self.assertEqual(exported['description'],original[0]['description'])
        self.assertEqual(exported['currentAccount'],'Expenses:Food')
        self.assertEqual(state['entries'][rows[0]['id']]['description'],'Willow class')
        payload=dict(reviewVersion=2,transactions=[dict(**exported,decision=state['decisions'][rows[0]['id']])])
        # A separate device's copy of the original ledger can import the decision.
        for path in (self.ledger/'data/additions').glob('*.jsonl'):path.unlink()
        dump(self.ledger/'data/expenses/2026.json',original)
        self.jj('commit','-m','Fictional original ledger for portable import')
        self.assertEqual(import_preview(self.ledger,payload)['valid'],1)

    def test_simultaneous_identical_requests_and_interrupted_commit_receipt(self):
        from concurrent.futures import ThreadPoolExecutor
        rows=self.seed();body=self.body('followup',rows[0]['id'],decision=self.decide('followup'))
        start=threading.Barrier(2)
        def submit():
            start.wait(timeout=10)
            return apply(self.settings,body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:submit(),range(2)))
        self.assertEqual(len(records(self.ledger)),1)
        self.assertEqual(sum(r.get('replayed',False) for r in results),1)
        receipt=next(r for r in results if not r.get('replayed'))
        # An interrupted process can leave its durable transaction journal after
        # the commit. Recovery discovers the exact Review marker before replay.
        folder=self.ledger/'drafts/review'/body['request_id']
        (folder/'transaction.json').write_text(json.dumps(dict(result=receipt)))
        parent=self.jj('log','-r','@-','--no-graph','-T','commit_id')
        self.assertTrue(apply(self.settings,body)['replayed'])
        self.assertFalse((folder/'transaction.json').exists());self.assertEqual(len(records(self.ledger)),1)
        self.assertEqual(parent,self.jj('log','-r','@-','--no-graph','-T','commit_id'))

    def test_review_amounts_retain_separate_currencies_and_refund_kind(self):
        rows=self.seed();declarations=read_json(self.ledger/'data/accounts.json')
        declarations['accounts']['Assets:Travel']=dict(kind='bank',currency='USD')
        dump(self.ledger/'data/accounts.json',declarations)
        rows[0]['postings'] += [dict(account='Expenses:Food',amount='7.00',commodity='USD'),dict(account='Assets:Travel',amount='-7.00',commodity='USD')]
        rows[1]['postings'][0]['amount']='-12.50';rows[1]['postings'][1]['amount']='12.50'
        dump(self.ledger/'data/expenses/2026.json',rows)
        state=snapshot(self.ledger)
        self.assertEqual(state['entries'][rows[0]['id']]['amounts'],dict(INR='12.50',USD='7.00'))
        self.assertEqual(state['entries'][rows[1]['id']]['kind'],'Refund')
        exported=[]
        for code,amount in state['entries'][rows[0]['id']]['amounts'].items():
            exported.append(dict(**{**state['entries'][rows[0]['id']],'id':rows[0]['id']+'-'+code,'storeId':rows[0]['id'],'amount':float(amount),'currency':code},decision=self.decide('followup')))
        payload=dict(reviewVersion=2,transactions=exported)
        self.assertEqual(import_preview(self.ledger,payload)['valid'],1)
        conflicting=deepcopy(payload);conflicting['transactions'][1]['decision']['account']='Expenses:Different'
        with self.assertRaisesRegex(ValueError,'Conflicting decisions'):import_preview(self.ledger,conflicting)

        # Legacy version-2 exports compacted whitespace and named absent evidence.
        rows[2].pop('sources');rows[2]['description']='Willow  Workshop 3'
        dump(self.ledger/'data/expenses/2026.json',rows)
        legacy=dict(id='old-derived-id',description='Willow Workshop 3',originalDate=rows[2]['date'],source='Derived ledger entry',sourceReference='',currentAccount='Expenses:Food',amount=12.5,decision=self.decide())
        self.assertEqual(import_preview(self.ledger,dict(reviewVersion=2,transactions=[legacy]))['valid'],1)


    def test_merge_keeps_earlier_import_even_when_its_id_and_date_are_later(self):
        rows=self.seed();older=rows[1]
        newer=deepcopy(rows[0]);newer['id']='0000000000000000';newer['date']='2026-10-20';newer['description']='Later imported receipt'
        rows.insert(0,newer);link_pair({e['id']:e for e in rows},newer['id'],older['id'],'amount-date-gap')
        dump(self.ledger/'data/expenses/2026.json',rows);self.jj('commit','-m','Import a later receipt with an earlier calendar date')
        result=apply(self.settings,self.body('merge',newer['id'],older['id']))
        self.assertEqual(result['changes'][0]['kept_id'],older['id'])
        self.assertEqual(snapshot(self.ledger)['entries'][older['id']]['postings'],older['postings'])
