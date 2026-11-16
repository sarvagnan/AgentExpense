"""Historical audit warnings and supported repository layouts."""
from argparse import Namespace
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit
import audit_changes
import commit
from store import dump, read_json
from test_ledger import Fixture, entry
import test_hand_changes as hand_cases


class HistoricalAuditTests(unittest.TestCase):
    def setUp(self):
        self.case=hand_cases.HandChangeTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.case.set_start()


    def test_baseline_schema_is_closed_and_application_actor_is_exclusive(self):
        from jsonschema import Draft202012Validator,ValidationError
        validator=Draft202012Validator(read_json(audit.SCHEMA))
        line=audit.event('baseline',actor='application',message='Initialize boundary',baseline=dict(
            from_format=2,to_format=2,checkpoint='a'*40,change_id='k'*32,paths=[dict(path='ledger.json',status='modified')]))
        validator.validate(line)
        values=[{k:v for k,v in line.items() if k!='actor'},{**line,'actor':'user'},
                *({**line,key:value} for key,value in [('outcome','added'),('verdict','ok'),('reviewer','Offline')])]
        values.append(audit.event('review',actor='application',line_id='a'*32,verdict='ok',note='Checked',reviewer='Offline'))
        for value in values:
            with self.subTest(value=value),self.assertRaises(ValidationError):validator.validate(value)
        validator.validate(audit.event('change',actor='user',message='Earlier shape',change_id='k'*32,commit='b'*40,
            entries=[dict(id='0000000000000001',action='added',entry={'id':'0000000000000001'})],rules=[],paths=[dict(path='data/expenses/2026.json',status='modified')],note='Historical'))


    def test_tracked_generated_journal_has_a_clear_refusal(self):
        root=self.case.root
        (root/'.gitignore').write_text('/.lock\n/test-jj.toml\n')
        (root/'expense_ledger.ledger').write_text('; Generated placeholder\n')
        self.case.raw_commit('Unsupported tracked journal')
        dump(self.case.year,[entry(description='Changed')])
        with self.assertRaisesRegex(ValueError,'expense_ledger.ledger.*(untrack|ignored)|generated journal'):
            self.case.save()


class GitBranchTests(Fixture):
    def test_upgrade_after_plain_new(self):
        from upgrade_ledger import upgrade
        case = hand_cases.HandChangeTests(); case.setUp(); self.addCleanup(case.doCleanups)
        case.jj('new'); parent = audit_changes.identity(case.root, '@-')['commit']
        run = subprocess.run(['make', 'upgrade-ledger'], cwd=Path(commit.__file__).resolve().parents[2],
            env={**os.environ, 'LEDGER_DIR':str(case.root), 'LEDGER_PYTHON':sys.executable}, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        self.assertEqual(read_json(case.root/'ledger.json')['auditStart'], {'after':parent})
        self.assertEqual(audit_changes.unrecorded(case.root, audit.records(case.root)), [])
        before = audit_changes.identity(case.root, 'main')['commit']
        with redirect_stdout(io.StringIO()): upgrade(case.root, Namespace(dry_run=False))
        self.assertEqual(audit_changes.identity(case.root, 'main')['commit'], before)

    def test_plain_git_upgrade_on_master_and_another_branch(self):
        from upgrade_ledger import upgrade,Repository
        for branch in ('master','household'):
            with self.subTest(branch=branch):
                case=Fixture();case.setUp()
                try:
                    env={**os.environ,'GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','GIT_AUTHOR_NAME':'Offline test','GIT_COMMITTER_NAME':'Offline test',
                         'GIT_AUTHOR_EMAIL':'test@example.invalid','GIT_COMMITTER_EMAIL':'test@example.invalid'}
                    def git(*args):return subprocess.check_output(['git',*args],cwd=case.root,env=env,text=True,stderr=subprocess.PIPE).strip()
                    (case.root/'.gitignore').write_text('/.lock\n/expense_ledger.ledger\n')
                    git('init','-b',branch);git('add','-A');git('commit','-m','Fictional baseline');before=git('rev-parse','HEAD')
                    if branch == 'household':
                        run = subprocess.run(['make','upgrade-ledger'],cwd=Path(commit.__file__).resolve().parents[2],
                            env={**env,'LEDGER_DIR':str(case.root),'LEDGER_PYTHON':sys.executable},capture_output=True,text=True)
                        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
                    else:
                        with patch.dict(os.environ,env),redirect_stdout(io.StringIO()):upgrade(case.root,Namespace(dry_run=False))
                    self.assertEqual(git('branch','--show-current'),branch)
                    self.assertEqual(read_json(case.root/'ledger.json')['auditStart'],{'after':before})
                    with patch.dict(os.environ,env):self.assertEqual(Repository(case.root).audit_start(),{'after':before})
                finally:case.doCleanups()

    def test_upgrade_outside_repository_is_clear(self):
        from upgrade_ledger import Repository
        with self.assertRaisesRegex(ValueError,'own repository|Initialize.*repository'):Repository(self.root)
