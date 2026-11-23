"""Hand changes are audited in disposable, offline jj ledgers."""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

from test_ledger import Fixture, entry
from store import dump
import commit as commits


class HandChangeTests(Fixture):
    def setUp(self):
        super().setUp()
        self.config = self.root / 'test-jj.toml'
        self.config.write_text('[user]\nname="Offline audit test"\nemail="test@example.invalid"\n')
        self.env = patch.dict(os.environ, {'JJ_CONFIG': str(self.config), 'GIT_CONFIG_GLOBAL': '/dev/null',
                                          'GIT_CONFIG_NOSYSTEM': '1'})
        self.env.start(); self.addCleanup(self.env.stop)
        (self.root / '.gitignore').write_text('/.lock\n/expense_ledger.ledger\n/test-jj.toml\n')
        subprocess.run(['jj', 'git', 'init', '--colocate', str(self.root)], cwd=self.root, check=True, capture_output=True)
        self.jj('commit', '-m', 'Baseline')
        self.jj('bookmark', 'set', 'main', '-r', '@-')

    def jj(self, *args):
        return subprocess.check_output(['jj', '-R', str(self.root), *args], cwd=self.root,
                                       text=True, stderr=subprocess.PIPE).strip()

    def test_missing_actor_is_refused_before_a_commit(self):
        dump(self.year, [entry(description='Changed by hand')])
        before = self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
        with self.assertRaisesRegex(ValueError, 'by=user.*by=agent'):
            commits.commit(self.root, 'Hand edit', emit=lambda *args: None)
        self.assertEqual(self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'), before)

    def test_hand_audit_log_edit_is_refused(self):
        from audit import append, event
        append(self.root, event('review', line_id='a'*32, verdict='ok', note='Checked', reviewer='Offline test'))
        with self.assertRaisesRegex(ValueError, 'audit log'):
            commits.commit(self.root, 'Hand log edit', emit=lambda *args: None)

    def save(self, message='Hand edit', actor='user', **kwargs):
        return commits.commit(self.root, message, actor=actor, emit=lambda *args: None, **kwargs)

    def raw_commit(self, message):
        self.jj('commit', '-m', message)
        self.jj('bookmark', 'set', 'main', '-r', '@-')
        return self.jj('log', '-r', '@-', '--no-graph', '-T', 'commit_id')

    def set_start(self, value=None):
        from store import read_json
        manifest = read_json(self.root/'ledger.json'); manifest['auditStart'] = {'after': value or self.jj('log','-r','main','--no-graph','-T','commit_id')}
        dump(self.root/'ledger.json', manifest)
        self.raw_commit('Set history boundary')

    def test_exact_entries_rules_and_evidence_paths_validate(self):
        from test_ledger import rule
        from audit import records
        from audit_changes import identity
        from copy import deepcopy
        before = [entry(), entry(identity='0000000000000002', description='Remove me')]
        rules = [rule(identity='0000000000000011'), rule(identity='0000000000000012')]
        dump(self.year, before); dump(self.root/'data/forecast.json', rules)
        evidence = self.root/'Bills/original.txt'; evidence.parent.mkdir(); evidence.write_text('Fictional receipt')
        self.raw_commit('Before the hand change')
        after = [entry(description='Edited purchase', notes=['Reason']), entry(identity='0000000000000003')]
        after_rules = [rule(identity='0000000000000011', description='Edited rule'), rule(identity='0000000000000013')]
        dump(self.year, after); dump(self.root/'data/forecast.json', after_rules)
        evidence.rename(evidence.with_name('renamed.txt'))
        (self.root/'Bills/new.txt').write_text('Another fictional receipt')
        (self.root/'notes.txt').write_text('Uncovered, but included in the diff')
        self.jj('status'); info = identity(self.root, '@')
        result = self.save('Explain the complete change\n\nEvidence: receipts', actor='agent')
        line = records(self.root)[0]
        self.assertEqual({k:line[k] for k in ('kind','actor','message','change_id','commit')},
                         dict(kind='change', actor='agent', message='Explain the complete change\n\nEvidence: receipts', change_id=info['change_id'],commit=None))
        self.assertEqual(line['entries'], [dict(id=before[0]['id'], action='changed',changed_fields={
            'description':dict(before='Purchase',after='Edited purchase'),'notes':dict(before=[],after=['Reason'])}),
            dict(id=before[1]['id'],action='removed',entry=before[1]),dict(id=after[1]['id'],action='added',entry=after[1])])
        self.assertEqual(line['rules'],[dict(id=rules[0]['id'],action='edited',changed_fields={'description':dict(before='Purchase',after='Edited rule')}),
            dict(id=rules[1]['id'],action='removed',entry=rules[1]),dict(id=after_rules[1]['id'],action='made',entry=after_rules[1])])
        self.assertEqual(line['paths'], [dict(path='Bills/new.txt',status='added'),
            dict(path='Bills/renamed.txt',status='renamed',source='Bills/original.txt'),
            dict(path='data/expenses/2026.json',status='modified'),dict(path='data/forecast.json',status='modified'),
            dict(path='notes.txt',status='added')])
        self.assertEqual(identity(self.root, result)['change_id'], info['change_id'])
        self.assertFalse(self.jj('diff','--summary'))

    def test_bad_actor_and_declared_missing_line_are_refused(self):
        dump(self.year, [entry(description='Changed')])
        for kwargs, pattern in [({'actor':'somebody'},'by=user.*by=agent'),
                                ({'carries_audit':True},'without adding a new audit line')]:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError,pattern):
                commits.commit(self.root,'Invalid',emit=lambda *args:None,**kwargs)
        from audit import append, event
        append(self.root,event('review',line_id='a'*32,verdict='ok',note='Previous line',reviewer='Offline test'))
        self.raw_commit('Prior audit line')
        dump(self.year,[entry(description='Changed again')])
        with self.assertRaisesRegex(ValueError,'without adding a new audit line'):
            self.save(carries_audit=True)

    def test_declared_save_reads_only_audit_blobs(self):
        from audit import append, event, records, reset_appends, take_appends
        import audit_changes
        self.set_start()
        operation = self.jj('op','log','--limit','1','--no-graph','-T','id')
        reset_appends(self.root,operation)
        dump(self.year, [entry(description='Server addition')])
        append(self.root,event('review',line_id='a'*32,verdict='ok',note='Own line',reviewer='Offline test'))
        original = audit_changes.file_text
        def audit_only(root, revision, path):
            self.assertTrue(path.startswith('data/additions/'))
            return original(root,revision,path)
        with patch.object(audit_changes,'file_text',side_effect=audit_only):
            self.save(carries_audit=True,rollback_operation=operation,audit_receipts=take_appends(self.root))
        from audit_changes import unrecorded
        self.assertEqual(unrecorded(self.root, records(self.root)), [])
        self.assertEqual([r['kind'] for r in records(self.root)],['review'])

    def test_uncovered_prices_need_no_actor_or_line(self):
        from audit import records
        path=self.root/'data/prices/test.prices';path.parent.mkdir();path.write_text('; Offline prices\n')
        self.save(actor=None)
        self.assertEqual(records(self.root),[])

    def test_no_own_repository_never_falls_through(self):
        from audit_changes import identity
        import tempfile
        with tempfile.TemporaryDirectory(dir=self.root) as folder:
            nested = Path(folder)
            with self.assertRaisesRegex(ValueError,'No own jj repository'):
                identity(nested,'@')
            with self.assertRaisesRegex(ValueError,'No own jj repository'):
                commits.commit(nested,'Must not snapshot the outer ledger')

    def test_verification_failure_restores_bytes_and_no_commit(self):
        from audit import append, audit_path, event, records
        initial=event('review',line_id='a'*32,verdict='ok',note='Existing',reviewer='Offline test')
        append(self.root,initial); self.raw_commit('Existing audit')
        journal=self.root/'expense_ledger.ledger';journal.write_bytes(b'Original ignored journal\n')
        dump(self.year,[entry(description='Uncommitted intended edit')])
        financial=self.year.read_bytes(); prior=audit_path(self.root,initial).read_bytes()
        head=self.jj('log','-r','main','--no-graph','-T','commit_id')
        def fail(root,**kwargs):
            self.assertEqual(len(records(root)),2)
            journal.write_bytes(b'Verification wrote a new journal')
            raise ValueError('forced verification failure')
        with patch.object(commits,'verify',side_effect=fail),self.assertRaisesRegex(ValueError,'forced verification failure'):
            self.save()
        self.assertEqual(self.year.read_bytes(),financial)
        self.assertEqual(audit_path(self.root,initial).read_bytes(),prior)
        self.assertEqual(journal.read_bytes(),b'Original ignored journal\n')
        self.assertEqual(self.jj('log','-r','main','--no-graph','-T','commit_id'),head)
        self.assertEqual(self.jj('diff','--name-only'),'data/expenses/2026.json')

    def test_backfill_reorders_commits_and_is_record_only(self):
        from audit import record, records
        from audit_changes import unrecorded
        self.set_start()
        dump(self.year,[entry(description='First old change')]); first=self.raw_commit('First hand change\n\nIts evidence')
        dump(self.year,[entry(description='Second old change')]); second=self.raw_commit('Second hand change')
        financial=self.year.read_bytes()
        commit_id, ids = record(self.root,[dict(commit=second,actor='agent',note='Owner identified the actor'),dict(commit=first,actor='user')])
        lines=records(self.root)
        self.assertEqual([r['commit'] for r in lines],[first,second])
        self.assertEqual([r['message'] for r in lines],['First hand change\n\nIts evidence','Second hand change'])
        self.assertEqual(lines[1]['note'],'Owner identified the actor')
        self.assertEqual(self.year.read_bytes(),financial)
        self.assertEqual(unrecorded(self.root,lines),[])
        self.assertEqual(self.jj('diff','-r',commit_id,'--name-only'),f"data/additions/{lines[0]['time'][:4]}.jsonl")

    def test_backfill_refusals_are_all_or_nothing(self):
        from audit import record, records
        self.set_start()
        dump(self.year,[entry(description='Old change')]); old=self.raw_commit('Old hand change')
        (self.root/'notes.txt').write_text('Uncovered'); uncovered=self.raw_commit('No financial change')
        self.jj('new','-r','main'); (self.root/'Bills').mkdir();(self.root/'Bills/side.txt').write_text('Branch evidence')
        self.jj('commit','-m','Off-main change'); side=self.jj('log','-r','@-','--no-graph','-T','commit_id');self.jj('new','main')
        valid=dict(commit=old,actor='user')
        for batch,pattern in [([], 'nonempty'), ([{**valid,'extra':True}],'Each change'),
            ([{**valid,'actor':'bad'}],'Each change'), ([{**valid,'commit':[]}],'Each change'),
            ([{**valid,'note':1}],'Each change'),([valid,valid],'already recorded'),
            ([valid,dict(commit=uncovered,actor='user')],'no covered'),([dict(commit=side,actor='user')],'not on main')]:
            with self.subTest(pattern=pattern),self.assertRaisesRegex(ValueError,pattern):record(self.root,batch)
            self.assertEqual(records(self.root),[])
        def fail(*args):raise RuntimeError('record commit failure')
        with self.assertRaisesRegex(RuntimeError,'record commit failure'):record(self.root,[valid],commit=fail)
        self.assertEqual(records(self.root),[])
        self.assertFalse(self.jj('diff','--summary'))
        (self.root/'notes.txt').write_text('Dirty')
        with self.assertRaisesRegex(ValueError,'uncommitted'):record(self.root,[valid])
        self.jj('restore','notes.txt')
        record(self.root,[valid])
        with self.assertRaisesRegex(ValueError,'already recorded'):record(self.root,[valid])
        dump(self.year,[entry(description='Audited hand edit')]); own=self.save()
        with self.assertRaisesRegex(ValueError,'already recorded'):record(self.root,[dict(commit=own,actor='user')])
        from audit import append, event
        dump(self.year,[entry(description='Server change with its own line')])
        append(self.root,event('review',line_id='a'*32,verdict='ok',note='Own event',reviewer='Offline test'))
        server=self.raw_commit('Server-owned audit line')
        with self.assertRaisesRegex(ValueError,'own audit line'):record(self.root,[dict(commit=server,actor='user')])

    def test_duplicate_change_ids_and_commit_ids_are_refused_on_read(self):
        from audit import records, audit_path
        import json
        dump(self.year,[entry(description='Changed')]); self.save()
        line=records(self.root)[0];path=audit_path(self.root,line);original=path.read_bytes()
        for fields in ({'change_id':line['change_id']},{'change_id':'z'*32,'commit':'a'*40}):
            first={**line,'commit':'a'*40};second={**line,'id':'f'*32,**fields}
            path.write_text(json.dumps(first)+'\n'+json.dumps(second)+'\n')
            with self.assertRaisesRegex(ValueError,'Duplicate recorded change'):records(self.root)
        path.write_bytes(original)

    def test_unreviewed_json_warnings_and_start_boundary(self):
        from audit import main, records
        from contextlib import redirect_stderr
        from audit_changes import identity
        import json
        dump(self.year,[entry(description='Before cutoff')]); earlier=self.raw_commit('Historical hand change')
        self.set_start()
        dump(self.year,[entry(description='After cutoff')]); later=self.raw_commit('Unrecorded hand change')
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):main(['--root',str(self.root),'list','--unreviewed','--json'])
        self.assertEqual(json.loads(out.getvalue()),[])
        from audit_changes import main_history
        from store import read_json
        self.assertIn(later,err.getvalue(), (read_json(self.root/'ledger.json')['auditStart'], main_history(self.root)))
        self.assertNotIn(earlier,err.getvalue());self.assertIn('record --commit',err.getvalue())

    def test_change_reviews_accept_only_ok_and_issue(self):
        from audit import review, records, unreviewed
        dump(self.year,[entry(description='Changed')]);self.save()
        line=records(self.root)[0]
        for verdict in ('confirm','propose-removal'):
            with self.assertRaisesRegex(ValueError,'ok or issue'):
                review(self.root,[dict(line=line['id'],verdict=verdict,note='Reviewed')],'Offline test','Review')
        review(self.root,[dict(line=line['id'],verdict='ok',note='Checked full change')],'Offline test','Review')
        with self.assertRaisesRegex(ValueError,'already has a verdict'):
            review(self.root,[dict(line=line['id'],verdict='issue',note='Checked full change')],'Offline test','Review')
        dump(self.year,[entry(description='Changed again')]);self.save()
        next_line, = unreviewed(records(self.root))
        review(self.root,[dict(line=next_line['id'],verdict='issue',note='Checked another change')],'Offline test','Review')
        self.assertEqual(unreviewed(records(self.root)),[])

    def test_schema_snapshot_and_field_variants(self):
        from audit import append, records
        from copy import deepcopy
        from jsonschema import ValidationError
        dump(self.year,[entry(description='Changed')]);self.save()
        line=records(self.root)[0]
        for fields in ({'actor':'bad'}, {'commit':'x'*40},{'change_id':'a'*32}, {'entries':[dict(id='x',action='added')]},
            {'entries':[dict(id='x',action='changed',changed_fields={})]},
            {'entries':[dict(id='x',action='changed',entry={'id':'x'},changed_fields={'x':dict(before=1,after=2)})]},
            {'rules':[dict(id='x',action='made',entry={})]},
            {'paths':[dict(path='Bills/b',status='renamed')]},
            {'paths':[dict(path='Bills/b',status='added',source='Bills/a')]}):
            with self.subTest(fields=fields),self.assertRaises(ValidationError):append(self.root,{**line,**fields})


    def test_existing_format_two_upgrade_seeds_once_without_warning(self):
        from argparse import Namespace
        from audit import records
        from audit_changes import unrecorded
        from ledger_manifest import read_manifest
        from upgrade_ledger import upgrade
        dump(self.year,[entry(description='Before upgrading audit support')])
        self.raw_commit('Historical owner change')
        with redirect_stdout(io.StringIO()):
            upgrade(self.root, Namespace(dry_run=True))
        self.assertNotIn('auditStart',read_manifest(self.root))
        with redirect_stdout(io.StringIO()):
            upgrade(self.root, Namespace(dry_run=False))
        self.assertIn('auditStart',read_manifest(self.root))
        self.assertEqual(unrecorded(self.root, records(self.root)), [])
        start=read_manifest(self.root)['auditStart']
        with redirect_stdout(io.StringIO()):upgrade(self.root, Namespace(dry_run=False))
        self.assertEqual(start,read_manifest(self.root)['auditStart'])

    def test_record_cli_inputs_and_missing_history_warning(self):
        import json
        from audit import main
        from contextlib import redirect_stderr
        self.set_start()
        dump(self.year,[entry(description='Old CLI change')]);old=self.raw_commit('CLI backfill')
        manifest=self.root/'ledger.json'
        cases=[(['record','--commit',old],'requires --actor'),
               (['record','--changes',str(self.root/'missing.json')],'No such file'),
               (['record','--commit',old,'--changes','x'],'not allowed')]
        for args,pattern in cases:
            out=io.StringIO()
            with self.subTest(args=args),self.assertRaises(SystemExit),redirect_stderr(out):
                main(['--root',str(self.root),*args])
            self.assertIn(pattern,out.getvalue())
        # Batch input lives outside the ledger, keeping the record-only gate clean.
        batch=self.root.parent/(self.root.name+'-changes.json')
        self.addCleanup(batch.unlink)
        for payload in ('{}','[{"commit":"a","commit":"b"}]','not JSON'):
            batch.write_text(payload)
            with self.assertRaises(SystemExit),redirect_stderr(io.StringIO()):
                main(['--root',str(self.root),'record','--changes',str(batch)])
        batch.write_text(json.dumps([dict(commit=old,actor='agent',note='Owner identified')]))
        with redirect_stdout(io.StringIO()):main(['--root',str(self.root),'record','--changes',str(batch)])
        # A nested folder has no own history even inside this jj ledger.
        nested=self.root/'nested';(nested/'data').mkdir(parents=True)
        from store import read_json
        dump(nested/'ledger.json',read_json(manifest))
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):main(['--root',str(nested),'list','--unreviewed','--json'])
        self.assertEqual(json.loads(out.getvalue()),[])
        self.assertIn("couldn't check",err.getvalue());self.assertIn('No own jj repository',err.getvalue())

    def test_reading_diff_never_snapshots_uncommitted_edits(self):
        from audit_changes import read_jj, change_line
        operation = read_jj(self.root,'op','log','--limit','1','--no-graph','-T','id')
        dump(self.year,[entry(description='Unsnapshotted edit')])
        with self.assertRaisesRegex(ValueError,'no covered'):
            change_line(self.root,'@','user')
        self.assertEqual(read_jj(self.root,'op','log','--limit','1','--no-graph','-T','id'),operation)
        self.assertIn(b'Unsnapshotted edit',self.year.read_bytes())

    def test_copied_and_renamed_paths_cover_both_endpoints(self):
        from audit_changes import covered, touches, change_line
        for status in ('copied','renamed'):
            for source,target in (('Bills/source','notes/target'),('notes/source','Bills/target'),
                                  ('data/expenses/2026.json','notes/target')):
                self.assertTrue(touches(dict(path=target,source=source,status=status),covered))
        # A copy of an expense file outside data leaves its original entries intact.
        (self.root/'copy.txt').write_bytes(self.year.read_bytes())
        self.jj('status')
        row=dict(path='copy.txt',source='data/expenses/2026.json',status='copied')
        with patch('audit_changes.paths',return_value=[row]):
            line=change_line(self.root,'@','user')
        self.assertEqual(line['entries'],[])
        self.assertEqual(line['paths'],[row])
        from jsonschema import Draft202012Validator
        from audit import SCHEMA
        import json
        Draft202012Validator(json.loads(SCHEMA.read_text())).validate(line)

    def test_failed_bookmark_restores_hand_edits_and_the_log(self):
        from audit import records
        dump(self.year,[entry(description='Hand edits remain after failed bookmark')])
        before=self.year.read_bytes()
        parent=self.jj('log','-r','main','--no-graph','-T','commit_id')
        original=commits.jj
        def fail(root,*args):
            if args[:2]==('bookmark','set'):raise RuntimeError('forced bookmark failure')
            return original(root,*args)
        with patch.object(commits,'jj',side_effect=fail),self.assertRaisesRegex(RuntimeError,'forced bookmark failure'):
            self.save()
        self.assertEqual(records(self.root),[])
        self.assertEqual(self.year.read_bytes(),before)
        self.assertEqual(self.jj('log','-r','main','--no-graph','-T','commit_id'),parent)

    def test_backfill_shared_verification_failure_restores_operation_and_bytes(self):
        from audit import record, records
        from audit_changes import read_jj
        dump(self.year,[entry(description='Historical hand edit')]);old=self.raw_commit('Old hand edit')
        original=self.year.read_bytes()
        parent=self.jj('log','-r','main','--no-graph','-T','commit_id')
        with patch.object(commits,'verify',side_effect=ValueError('forced record verification failure')):
            with self.assertRaisesRegex(ValueError,'forced record verification failure'):
                record(self.root,[dict(commit=old,actor='user')])
        self.assertEqual(records(self.root),[])
        self.assertEqual(self.year.read_bytes(),original)
        self.assertEqual(read_jj(self.root,'diff','--name-only').strip(),'')
        self.assertEqual(self.jj('log','-r','main','--no-graph','-T','commit_id'),parent)
