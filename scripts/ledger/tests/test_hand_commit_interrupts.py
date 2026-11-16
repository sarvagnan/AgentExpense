"""Interrupted hand/audit writes, their refusal command and committed audit history."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_hand_changes as hand_cases
from test_ledger import entry
from store import dump, read_json
import audit
import audit_changes
import commit


def crash(case, stage='append', writer='hand', target=''):
    if writer == 'hand': dump(case.year, [entry(description='Intended interrupted edit')])
    script = """import os,sys
from pathlib import Path
import audit,commit
from extension_update import update
root=Path(sys.argv[1]);writer,stage,target=sys.argv[2:]
original_append=audit.append;original_verify=commit.verify;original_jj=commit.jj
def append(*a,**k):
    value=original_append(*a,**k)
    if stage=='append':os._exit(73)
    return value
def verify(*a,**k):
    if stage=='verify':os._exit(73)
    return original_verify(*a,**k)
def jj(root,*a):
    value=original_jj(root,*a)
    if (stage=='commit' and a[0]=='commit') or (stage=='bookmark' and a[:2]==('bookmark','set')):os._exit(73)
    return value
audit.append=append;commit.verify=verify;commit.jj=jj
if writer=='record':audit.record(root,[dict(commit=target,actor='user')])
elif writer=='review':audit.review(root,[dict(line=target,verdict='ok',note='Checked')],'Offline reviewer','Review')
elif writer=='extension':update(root,'work',actor='agent')
else:commit.commit(root,'Interrupted fictional write',actor='user',emit=lambda *a:None)
"""
    run = subprocess.run([sys.executable, '-c', script, str(case.root), writer, stage, target],
                         env={**os.environ, 'PYTHONPATH': str(Path(commit.__file__).parent)}, capture_output=True, text=True)
    assert run.returncode == 73, run.stderr


def restore_audit(case):
    import shlex
    try: case.save()
    except ValueError as error:
        text = str(error)
        assert 'audit log' in text, text
        command = text.split('with: ', 1)[1].split('. This leaves', 1)[0]
        args = shlex.split(command)
        assert args[:2] == ['jj', '-R'] and args[3:6] == ['restore', '--from', '@-'], args
        assert any(name.startswith('data/additions/') for name in args), args
        subprocess.run(args, cwd=case.root, check=True, capture_output=True)
    else: raise AssertionError('An uncommitted audit line did not refuse')


class AuditCommitTests(unittest.TestCase):
    def setUp(self):
        self.fixture = hand_cases.HandChangeTests('test_missing_actor_is_refused_before_a_commit')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        for name in ('root', 'year', 'jj', 'save', 'raw_commit', 'set_start'):
            setattr(self, name, getattr(self.fixture, name))

    def test_two_crashes_restore_pending_commit_audit(self):
        self.set_start()
        crash(self, 'commit')
        pending = audit.records(self.root)[0]
        dump(self.year, [entry(description='Second intended edit')]); saved = self.year.read_bytes()
        crash(self, 'append', 'other')
        restore_audit(self)
        self.assertEqual(audit.records(self.root), [pending])
        self.assertEqual(self.year.read_bytes(), saved)
        self.save()
        self.assertEqual(len(audit.records(self.root)), 2)
        self.assertEqual(audit_changes.unrecorded(self.root, audit.records(self.root)), [])
        self.assertEqual(self.jj('diff', '--summary'), '')

    def test_ctrl_c_restores_the_append_and_keeps_hand_edits(self):
        dump(self.year, [entry(description='Keep the intended hand edit')]); intended = self.year.read_bytes()
        parent = self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
        with patch.object(commit, 'verify', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.save()
        self.assertEqual(self.year.read_bytes(), intended)
        self.assertEqual(audit.records(self.root), [])
        self.assertEqual(self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'), parent)
        self.save()

    def test_terminal_declaration_environment_is_ignored(self):
        dump(self.year, [entry(description='Hand change')])
        audit.append(self.root, audit.event('review', line_id='a'*32, verdict='ok', note='Fake claim', reviewer='Offline'))
        run = subprocess.run([sys.executable, str(Path(commit.__file__))], text=True, capture_output=True,
            env={**os.environ, 'LEDGER_DIR': str(self.root), 'EXPENSES_COMMIT_MESSAGE':'Hand commit',
                 'EXPENSES_COMMIT_CARRIES_AUDIT':'1'})
        self.assertNotEqual(run.returncode, 0, run.stdout)
        self.assertIn('audit log', run.stderr)


    def test_odd_old_commit_warns_without_breaking_json(self):
        self.set_start()
        line=audit.event('review',line_id='a'*32,verdict='ok',note='Before',reviewer='Offline')
        audit.append(self.root,line); self.raw_commit('Original line')
        path=audit.audit_path(self.root,line); before=path.read_bytes()
        path.write_text('Broken old JSON\n')
        dump(self.year,[entry(description='Also changed')]); odd=self.raw_commit('Accidentally malformed old log')
        path.write_bytes(before); self.raw_commit('Repair old log')
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):audit.main(['--root',str(self.root),'list','--unreviewed','--json'])
        self.assertIsInstance(json.loads(out.getvalue()),list)
        self.assertIn(odd,err.getvalue())
        self.assertIn('WARNING',err.getvalue())

    def test_declared_save_uses_receipt_without_history_or_snapshot(self):
        from ledger_lock import ledger_lock
        with ledger_lock(self.root) as fd:
            operation=self.jj('op','log','--limit','1','--no-graph','-T','id')
            # The transaction owns this validated append, not a caller-supplied id.
            audit.reset_appends(self.root,operation)
            dump(self.year,[entry(description='Server save')])
            audit.append(self.root,audit.event('review',line_id='a'*32,verdict='ok',note='New',reviewer='Offline'))
            real=commit.jj
            calls=[]
            def writer(root,*args):
                calls.append(args)
                self.assertNotEqual(args[0],'status')
                return real(root,*args)
            with patch.object(commit,'jj',side_effect=writer),patch.object(audit_changes,'read_jj',side_effect=AssertionError('history read')):
                commit.commit(self.root,'Server save',lock_fd=fd,rollback_operation=operation,
                              carries_audit=True,audit_receipts=audit.take_appends(self.root),emit=lambda *args:None)
            self.assertEqual(len(calls),3)

    def test_history_check_is_batched_for_thirty_commits(self):
        self.set_start()
        for number in range(30):
            dump(self.year,[entry(description=f'Fictional change {number}')]);self.raw_commit(f'Hand change {number}')
        real=audit_changes.read_jj
        with patch.object(audit_changes,'read_jj',wraps=real) as calls:
            self.assertEqual(len(audit_changes.unrecorded(self.root,[])),30)
        self.assertEqual(calls.call_count,1)

    def test_kills_leave_files_and_allow_the_documented_retry(self):
        import test_audit as audit_cases
        import test_extensions as extension_cases
        from ledger_lock import ledger_lock
        for writer in ('hand', 'record', 'review', 'extension'):
            for stage in ('append', 'verify', 'commit', 'bookmark'):
                with self.subTest(writer=writer, stage=stage):
                    case = hand_cases.HandChangeTests(); case.setUp(); self.addCleanup(case.doCleanups)
                    note = case.root / 'Bills/owner-note.txt'; note.parent.mkdir(exist_ok=True); note.write_bytes(b'Keep the owner file\n')
                    case.raw_commit('Fictional owner note baseline')
                    target = ''
                    if writer == 'record':
                        dump(case.year, [entry(description='Earlier hand change')]); target = case.raw_commit('Earlier hand change')
                    elif writer == 'review':
                        line = audit_cases.draft(); line['entry_id'] = entry()['id']
                        audit.append(case.root, line); case.raw_commit('Existing outcome'); target = line['id']
                    elif writer == 'extension':
                        (case.root / 'extensions').mkdir(); (case.root / 'extensions/work.json').write_text(json.dumps(extension_cases.WORK))
                        manifest = read_json(case.root / 'ledger.json'); manifest['extensions'] = {'work': {'ask': True}}
                        dump(case.root / 'ledger.json', manifest)
                    prior = case.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
                    crash(case, stage, writer, target); saved = case.year.read_bytes()
                    with ledger_lock(case.root): pass
                    self.assertEqual(case.year.read_bytes(), saved)
                    self.assertEqual(note.read_bytes(), b'Keep the owner file\n')
                    self.assertFalse((case.root / '.jj/agentexpense-commit.json').exists())
                    if stage in ('append', 'verify'):
                        self.assertEqual(case.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'), prior)
                        if writer != 'hand':
                            with self.assertRaisesRegex(ValueError, 'restore --from @-'):
                                if writer == 'record': audit.record(case.root, [dict(commit=target, actor='user')])
                                elif writer == 'review': audit.review(case.root, [dict(line=target, verdict='ok', note='Checked')], 'Offline reviewer', 'Review')
                                else: extension_cases.update(case.root, 'work', actor='agent')
                        restore_audit(case)
                        self.assertEqual(case.year.read_bytes(), saved)
                        if writer == 'record': audit.record(case.root, [dict(commit=target, actor='user')])
                        elif writer == 'review': audit.review(case.root, [dict(line=target, verdict='ok', note='Checked')], 'Offline reviewer', 'Review')
                        else: case.save(actor='agent' if writer == 'extension' else 'user')
                    else:
                        self.assertEqual(case.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id') == prior, stage == 'commit')
                        case.save()  # Publishes a verified parent, or saves normally after publication.
                    self.assertEqual(case.year.read_bytes(), saved)
                    self.assertEqual(note.read_bytes(), b'Keep the owner file\n')
                    self.assertEqual(case.jj('diff', '--summary'), '')


    def test_new_path_during_verification_is_rolled_back(self):
        dump(self.year,[entry(description='Intended')]);before=self.year.read_bytes()
        parent=self.jj('log','-r','main','--no-graph','-T','commit_id')
        original=commit.verify
        def edited(root,**kwargs):
            result=original(root,**kwargs)
            (root/'Bills').mkdir();(root/'Bills/late.txt').write_text('Late editor change')
            return result
        with patch.object(commit,'verify',side_effect=edited),self.assertRaisesRegex(ValueError,'paths changed'):
            self.save()
        self.assertEqual(self.year.read_bytes(),before)
        self.assertEqual(audit.records(self.root),[])
        self.assertEqual(self.jj('log','-r','main','--no-graph','-T','commit_id'),parent)


    def test_record_only_writers_refuse_late_data_paths(self):
        import test_audit as audit_cases
        original = commit.verify
        for writer in ('record', 'review'):
            with self.subTest(writer=writer):
                case = hand_cases.HandChangeTests(); case.setUp()
                try:
                    if writer == 'record':
                        dump(case.year, [entry(description='Earlier hand change')])
                        target = case.raw_commit('Earlier hand change')
                    else:
                        line = audit_cases.draft(); line['entry_id'] = entry()['id']
                        audit.append(case.root, line); case.raw_commit('Earlier outcome')
                        target = line['id']
                    prior = case.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
                    before = {p.name:p.read_bytes() for p in (case.root/'data/additions').glob('*.jsonl')}
                    late = case.root/'Bills/late.txt'
                    def edited(root, **kwargs):
                        result = original(root, **kwargs)
                        late.parent.mkdir(exist_ok=True); late.write_text('Late editor change')
                        return result
                    with patch.object(commit, 'verify', side_effect=edited), self.assertRaisesRegex(ValueError, 'paths changed'):
                        if writer == 'record': audit.record(case.root, [dict(commit=target, actor='user')])
                        else: audit.review(case.root, [dict(line=target, verdict='ok', note='Checked')], 'Offline reviewer', 'Review')
                    self.assertEqual(case.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'), prior)
                    self.assertEqual({p.name:p.read_bytes() for p in (case.root/'data/additions').glob('*.jsonl')}, before)
                    self.assertEqual(late.read_text(), 'Late editor change')
                finally:
                    case.doCleanups()

    def test_current_format_dry_run_does_not_snapshot_dirty_edits(self):
        from argparse import Namespace
        from upgrade_ledger import upgrade
        dump(self.year,[entry(description='Unsnapshotted edit')])
        before=audit_changes.read_jj(self.root,'op','log','--limit','1','--no-graph','-T','id')
        with redirect_stdout(io.StringIO()):upgrade(self.root,Namespace(dry_run=True))
        self.assertEqual(audit_changes.read_jj(self.root,'op','log','--limit','1','--no-graph','-T','id'),before)
        self.assertNotIn('auditStart',read_json(self.root/'ledger.json'))


    def test_schema_disallows_fields_from_other_kinds(self):
        from jsonschema import ValidationError
        dump(self.year,[entry(description='Audited')]);self.save()
        line=audit.records(self.root)[0]
        for key,value in [('outcome','added'),('verdict','ok'),('draft_id','a'*32),('baseline',{})]:
            with self.subTest(key=key),self.assertRaises(ValidationError):audit.append(self.root,{**line,key:value})
        other=audit.event('review',line_id='a'*32,verdict='ok',note='Checked',reviewer='Offline')
        with self.assertRaises(ValidationError):audit.append(self.root,{**other,'change_id':'k'*32})

    def test_extension_requires_actor_and_refuses_unrelated_covered_edits(self):
        import test_extensions as extension_cases
        from extension_update import update
        (self.root/'extensions').mkdir()
        (self.root/'extensions/work.json').write_text(json.dumps(extension_cases.WORK))
        value=read_json(self.root/'ledger.json');value['extensions']={'work':{'ask':True}}
        dump(self.root/'ledger.json',value)
        original=self.year.read_bytes()
        with self.assertRaisesRegex(ValueError,'by=user.*by=agent'):update(self.root,'work')
        self.assertEqual(self.year.read_bytes(),original)
        dump(self.year,[entry(description='Unrelated manual edit',extensions={'work':None})])
        with self.assertRaisesRegex(ValueError,'only commits its own changes'):update(self.root,'work',actor='agent')
        self.assertEqual(read_json(self.year)[0]['description'],'Unrelated manual edit')
