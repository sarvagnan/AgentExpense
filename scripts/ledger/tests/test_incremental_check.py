"""Incremental/full agreement on the existing mutation battery, not mirrored rules."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
import random
from decimal import Decimal

from test_strict_check import Base, MUTATIONS, CONTROLS, STORE_CHECKS, BANK_CHECKS
from incremental_check import LedgerIndex
from store import load, load_parsed, _model_memos, ModelMemo
from check_store import _check
from unittest.mock import patch


class IncrementalAgreementTests(Base):
    @staticmethod
    def result(check):
        try: check();return None
        except ValueError as error:return type(error).__name__,str(error)

    def test_date_account_commodity_moves_and_posting_deletion_agree(self):
        from store import canonical_text, entries
        import strict_check
        for kind in ('date','account','delete-posting','commodity'):
            with self.subTest(change=kind):
                ledger = self.copy('move-'+kind)
                documents = load(ledger.root)
                index = LedgerIndex(deepcopy(documents),ledger.root)
                row = next(row for row in entries(documents) if not row['statement'] and not row['rule']
                           and len(row['postings']) == 2 and not any(p['assert'] or p['cost'] for p in row['postings']))
                if kind == 'date':
                    path = next(path for path,rows in documents.items() if path.parent.name == 'expenses' and row in rows)
                    documents[path].remove(row);row['date'] = '2027-01-02'
                    documents.setdefault(ledger.root/'data/expenses/2027.json',[]).append(row)
                elif kind == 'account':
                    accounts = documents[ledger.root/'data/accounts.json']['accounts']
                    row['postings'][0]['account'] = next(name for name in accounts if name != row['postings'][0]['account'])
                elif kind == 'delete-posting':row['postings'].pop()
                else:row['postings'][0]['commodity'] = 'USD' if row['postings'][0]['commodity'] != 'USD' else 'INR'
                self.assertEqual(self.result(lambda:index.check(documents,ledger.root)),
                                 self.result(lambda:strict_check.check(documents,ledger.root)))
                for path,rows in documents.items():
                    path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_text(canonical_text(rows))  # deliberately invalid candidates must reach the gate
                self.assertEqual(self.verdict(ledger.root,index),self.verdict(ledger.root))

    def test_seeded_statement_heavy_mutations_keep_exact_strict_and_membership_verdicts(self):
        from store import entries, validate
        from check_store import check_card_statements
        import strict_check
        ledger = self.copy('seeded-statement-heavy')
        baseline = load(ledger.root)
        statements = baseline[ledger.root/'data/card-statements.json']
        self.assertGreaterEqual(len(statements),36)
        totals = dict(cases=0,strict_refusals=0,membership_refusals=0,advances=0,statements=len(statements))
        for seed in (7,19):
            rng = random.Random(seed)
            current = deepcopy(baseline)
            index = LedgerIndex(deepcopy(current),ledger.root)
            for number in range(120):
                candidate = deepcopy(current)
                path = rng.choice([p for p in candidate if p.parent.name == 'expenses' and candidate[p]])
                rows = candidate[path];row = rng.choice(rows)
                kind = 'statement' if number % 3 == 0 else rng.choice(['date','account','commodity','delete-posting','delete','amount','assert','description'])
                if kind == 'statement':
                    statement = rng.choice(candidate[ledger.root/'data/card-statements.json'])
                    statement['purchases'] = format(Decimal(statement['purchases'])+Decimal('.01'),'.2f')
                elif kind == 'date':
                    rows.remove(row)
                    row['date'] = rng.choice(entries(candidate))['date']
                    target = candidate.setdefault(ledger.root/f"data/expenses/{row['date'][:4]}.json",[])
                    target.insert(next((i for i,e in enumerate(target) if e['date']>row['date']),len(target)),row)
                elif kind == 'account':row['postings'][0]['account'] = rng.choice(list(candidate[ledger.root/'data/accounts.json']['accounts']))
                elif kind == 'commodity':row['postings'][0]['commodity'] = rng.choice(['INR','USD'])
                elif kind == 'delete-posting':row['postings'].pop()
                elif kind == 'delete':rows.remove(row)
                elif kind == 'amount':row['postings'][0]['amount'] = str(Decimal(row['postings'][0]['amount'])+Decimal('.01'))
                elif kind == 'assert':row['postings'][0]['assert'] = dict(amount='1.00',commodity=row['postings'][0]['commodity'])
                else:row['description'] += ' checked'
                with self.subTest(seed=seed,case=number,change=kind):
                    full = self.result(lambda:strict_check.check(candidate,ledger.root))
                    member = self.result(lambda:check_card_statements(candidate,ledger.root))
                    self.assertEqual(self.result(lambda:index.check(candidate,ledger.root)),full)
                    self.assertEqual(self.result(lambda:index.check_membership(candidate,ledger.root)),member)
                    totals['cases'] += 1
                    totals['strict_refusals'] += full is not None
                    totals['membership_refusals'] += member is not None
                    if full is None and member is None and self.result(lambda:validate(candidate,ledger.root)) is None:
                        index = LedgerIndex(candidate,ledger.root,previous=index);current = candidate
                        totals['advances'] += 1
        self.assertEqual(totals['cases'],240)
        self.assertGreaterEqual(totals['membership_refusals'],80)
        self.assertGreater(totals['strict_refusals'],20)
        self.assertGreater(totals['advances'],5)
        print('FUZZ_AGREEMENT',json.dumps(totals,sort_keys=True))

    def test_repository_operation_head_changes_invalidate_the_source_stamp(self):
        from incremental_check import fingerprint
        ledger = self.copy('operation-heads')
        heads = ledger.root/'.jj/repo/op_heads/heads'
        heads.mkdir(parents=True,exist_ok=True)
        previous = fingerprint(ledger.root)
        head = heads/'fictional-operation-head'
        head.write_text('first')
        self.assertNotEqual(fingerprint(ledger.root),previous)
        previous = fingerprint(ledger.root)
        head.write_text('replacement')
        self.assertNotEqual(fingerprint(ledger.root),previous)
        previous = fingerprint(ledger.root)
        head.unlink()
        self.assertNotEqual(fingerprint(ledger.root),previous)

    def test_a_detected_runtime_disagreement_refuses_the_save(self):
        ledger = self.copy('disagreement')
        documents = load(ledger.root)
        index = LedgerIndex(deepcopy(documents), ledger.root)
        row = deepcopy(ledger.plain())
        row.update(id='a1'*8, date='2026-12-31', evidence=[], statement=None, rule=None, **{'possible-duplicate-of':[]})
        documents[ledger.root/'data/expenses/2026.json'].append(row)
        with patch('incremental_check.running', side_effect=ValueError('injected index defect')):
            with self.assertRaisesRegex(RuntimeError, 'disagree; save refused'):
                index.check(documents, ledger.root)
    def verdict(self, root, index=None):
        try:
            with redirect_stdout(io.StringIO()):
                loaded = load_parsed(root)
                _check(root, loaded=loaded, strict_checker=index.check if index else None,
                       membership_checker=index.check_membership if index else None, emit=lambda *args: None)
            return None
        except ValueError as error:
            return type(error).__name__, str(error)

    def test_existing_strict_and_store_mutations_have_the_same_exact_reason(self):
        cases = [(name, case[-1]) for name, *case in MUTATIONS]
        cases += [(name, mutate) for name, mutate, message in STORE_CHECKS]
        cases += CONTROLS
        for number, (name, mutate) in enumerate(cases):
            with self.subTest(mutation=name):
                ledger = self.copy(f'incremental-{number}')
                baseline = LedgerIndex(load(ledger.root), ledger.root)
                _model_memos[ledger.root.resolve()] = ModelMemo()
                try:
                    mutate(ledger); ledger.write()
                    incremental = self.verdict(ledger.root, baseline)
                    _model_memos.pop(ledger.root.resolve())
                    self.assertEqual(incremental, self.verdict(ledger.root))
                finally:
                    _model_memos.pop(ledger.root.resolve(), None)

    def test_assertions_recheck_only_the_changed_suffix_and_keep_same_day_order(self):
        ledger = self.copy('prefix')
        before = load(ledger.root)
        index = LedgerIndex(deepcopy(before), ledger.root)
        # An unchanged baseline scans no decimal suffixes.
        index.check(before, ledger.root)
        self.assertEqual(index.checked_postings, 0)
        self.assertEqual(index.checked_entries, 0)
        candidate = deepcopy(before)
        row = next(row for path, rows in candidate.items() if path.parent.name == 'expenses' for row in reversed(rows) if not row['statement'])
        row['description'] += ' checked'
        index.check(candidate, ledger.root)
        self.assertEqual(index.checked_entries, 1)
        self.assertEqual(index.checked_postings, 0)

    def test_existing_balance_statement_mutations_agree(self):
        from test_bank_statements import BankMembershipTests
        from store import read_json, canonical_text
        for name, change, message in BANK_CHECKS:
            with self.subTest(mutation=name):
                fixture = BankMembershipTests('test_membership_totals_and_assertion')
                fixture.setUp()
                try:
                    (fixture.root/'Bills').mkdir()
                    (fixture.root/'Bills/bank.csv').write_text('Fictional statement\n')
                    documents = fixture.documents()
                    index = LedgerIndex(documents, fixture.root)
                    summary, rows = read_json(fixture.root/'data/card-statements.json')[0], read_json(fixture.year)
                    change(summary, rows)
                    (fixture.root/'data/card-statements.json').write_text(canonical_text([summary]))
                    fixture.year.write_text(canonical_text(rows))
                    self.assertEqual(self.verdict(fixture.root,index), self.verdict(fixture.root))
                finally:
                    fixture.doCleanups()

    def test_manifest_start_date_change_retains_the_full_membership_refusal(self):
        from test_bank_statements import BankMembershipTests
        from store import read_json, dump
        from check_store import check_card_statements
        fixture = BankMembershipTests('test_membership_totals_and_assertion')
        fixture.setUp()
        try:
            documents = fixture.documents()
            index = LedgerIndex(deepcopy(documents), fixture.root)
            manifest = read_json(fixture.root/'ledger.json')
            dump(fixture.root/'ledger.json', {**manifest,'startDate':'2026-01-03'})
            def verdict(check):
                try:check(documents, fixture.root);return None
                except ValueError as error:return str(error)
            full = verdict(check_card_statements)
            self.assertIn('Pre-start rows cannot be applied',full)
            self.assertEqual(verdict(index.check_membership),full)
        finally:
            fixture.doCleanups()
