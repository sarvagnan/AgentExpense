"""Append-only audit schema, integrity checks and reviewer CLI."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from jsonschema import ValidationError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from audit import append, audit_path, event, main, missing_entries, records, review, stamp
from ledger_lock import LOCK_FD_ENV, ledger_lock


def draft(**changes):
    return event('draft',draft_id='a'*32,outcome='added',text='Words\nwith a newline',saved_at=None,pieces=None,
                 files=[{'original_name':'receipt.png','filed_path':'Bills/2026-10-30/receipt.png'}],questions=[],runs=[],proposal=None,
                 doubts=['No bank reference'],check_errors=[],error=None,entry_id='b'*16,**changes)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='expense-audit-')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        from store import dump
        dump(self.root/'ledger.json', dict(format=1, id='00000000-0000-4000-8000-000000000001', name='Offline fixture', defaultCurrency='INR'))

    def add(self,line):
        with ledger_lock(self.root): append(self.root,line)

    def test_append_preserves_prior_bytes_and_idempotence(self):
        one=draft();self.add(one);path=audit_path(self.root,one);first=path.read_bytes()
        two=event('review',line_id=one['id'],verdict='ok',note='Checked',reviewer='Offline reviewer')
        self.add(two);self.assertTrue(path.read_bytes().startswith(first));self.assertEqual(len(path.read_bytes().splitlines()),2)
        self.add(one);self.assertEqual(len(records(self.root)),2)
        changed={**one,'text':'Changed'}
        with self.assertRaisesRegex(ValueError,'different record'): self.add(changed)
        self.assertEqual(missing_entries(self.root,set()),[(one['id'],'b'*16)])
        self.assertEqual(missing_entries(self.root,{'b'*16}),[])

    def test_local_save_context_is_optional_and_legacy_lines_are_preserved(self):
        legacy = draft(); self.add(legacy)
        path = audit_path(self.root, legacy); before = path.read_bytes()
        context = dict(client_time='2000-01-01T23:30:00-05:00', client_zone='America/Chicago')
        current = draft(**context); self.add(current)
        self.assertTrue(path.read_bytes().startswith(before))
        self.assertEqual(records(self.root), [legacy, current])
        for changes in ({'client_time':'2000-01-01T00:00:00+14:01'}, {'client_zone':'UTC\nForged: metadata'}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.add(draft(**{**context, **changes}))
        with self.assertRaises(ValidationError):
            self.add(draft(client_zone='UTC'))

    def test_review_since_calendar_date_uses_server_local_midnight(self):
        import subprocess
        for zone, expected in [('America/Chicago','2026-10-30T00:00:00-05:00'),
                               ('Europe/Paris','2026-10-30T00:00:00+01:00')]:
            code = 'from audit import since_time; print(since_time("2026-10-30").isoformat())'
            result = subprocess.check_output([sys.executable, '-c', code],
                env={**os.environ, 'TZ':zone, 'PYTHONPATH':str(Path(__file__).resolve().parents[1])}, text=True)
            self.assertEqual(result.strip(), expected)

    def test_schema_required_types_unique_ids_year_and_duplicate_keys(self):
        base=draft()
        for change in ({'version':2},{'kind':'unknown'},{'time':'yesterday'},{'id':'bad'},{'unexpected':True},{'outcome':'maybe'}):
            with self.subTest(change=change), self.assertRaises(ValidationError): self.add({**base,**change})
        for key in ('draft_id','outcome','text','runs','entry_id'):
            value=deepcopy(base);value.pop(key)
            with self.subTest(key=key),self.assertRaises(ValidationError): self.add(value)
        self.add(base);path=audit_path(self.root,base)
        valid=path.read_bytes()
        for bad,pattern in [(valid+valid,'Duplicate audit ID'),(valid.replace(b'"version":1',b'"version":1,"version":1'),'Duplicate JSON key')]:
            path.write_bytes(bad)
            with self.assertRaisesRegex(ValueError,pattern): records(self.root)
        path.write_bytes(valid);path.rename(path.with_name('2000.jsonl'))
        with self.assertRaisesRegex(ValueError,'wrong year'): records(self.root)

    def test_list_period_readable_and_json(self):
        one=draft();self.add(one)
        old={**draft(),'time':'2000-01-01T00:00:00+00:00'};self.add(old)
        out=io.StringIO()
        with redirect_stdout(out): main(['--root',str(self.root),'list','--since','7d','--json'])
        self.assertEqual(json.loads(out.getvalue()),[one])
        out=io.StringIO()
        with redirect_stdout(out): main(['--root',str(self.root),'list','--since','7d'])
        self.assertIn(one['id'],out.getvalue());self.assertIn('Words',out.getvalue());self.assertNotIn(old['id'],out.getvalue())

    def test_unreviewed_lists_lines_without_a_verdict_of_any_age(self):
        old={**draft(),'time':'2000-01-01T00:00:00+00:00'};self.add(old)
        one=draft();self.add(one);done=draft();self.add(done)
        self.add(event('review',line_id=done['id'],verdict='ok',note='Fine',reviewer='Offline reviewer'))
        out=io.StringIO()
        with redirect_stdout(out): main(['--root',str(self.root),'list','--unreviewed','--json'])
        self.assertEqual([r['id'] for r in json.loads(out.getvalue())],[old['id'],one['id']])

    def test_a_missing_ledger_is_an_error_not_an_empty_log(self):
        for command in (['list','--unreviewed'],['review','--verdicts',str(self.root/'v.json'),'--reviewer','Offline reviewer']):
            err=io.StringIO()
            with self.subTest(command=command[0]),self.assertRaises(SystemExit),redirect_stdout(io.StringIO()),redirect_stderr(err):
                main(['--root',str(self.root/'application-worktree/ledger'),*command])
            self.assertIn('No ledger at',err.getvalue())

    def test_review_appends_every_verdict_and_commits_once_under_the_lock(self):
        one=draft();two=draft();self.add(one);self.add(two)
        before=audit_path(self.root,one).read_bytes();messages=[]
        # Under make commit (the server tests) a stale inherited lock descriptor could match ours.
        env=mock.patch.dict(os.environ);env.start();self.addCleanup(env.stop);os.environ.pop(LOCK_FD_ENV,None)
        def commit(root,message,lock_fd):
            with self.assertRaises(BlockingIOError), ledger_lock(root,blocking=False): pass
            messages.append(message);return 'c'*40
        commit_id,ids=review(self.root,[{'line':one['id'],'verdict':'ok','note':'Fine'},{'line':two['id'],'verdict':'issue','note':'Check account'}],
                             'Offline reviewer','Review the week',clean=lambda root:True,commit=commit)
        self.assertEqual((commit_id,messages),('c'*40,['Review the week']))
        self.assertTrue(audit_path(self.root,one).read_bytes().startswith(before))
        reviews=[r for r in records(self.root) if r['kind']=='review']
        self.assertEqual([(r['id'],r['line_id'],r['verdict'],r['note'],r['reviewer']) for r in reviews],
                         [(ids[0],one['id'],'ok','Fine','Offline reviewer'),(ids[1],two['id'],'issue','Check account','Offline reviewer')])

    def test_review_that_fails_to_commit_leaves_the_log_exactly_as_it_was(self):
        old={**draft(),'time':'2000-01-01T00:00:00+00:00'};self.add(old)
        before=audit_path(self.root,old).read_bytes()
        def commit(root,message,lock_fd): raise RuntimeError('verify failed')
        with self.assertRaisesRegex(RuntimeError,'verify failed'):
            review(self.root,[{'line':old['id'],'verdict':'ok','note':'Fine'}],'Offline reviewer','Review',clean=lambda root:True,commit=commit)
        self.assertEqual(audit_path(self.root,old).read_bytes(),before)
        self.assertFalse(audit_path(self.root,{'time':stamp()}).exists())
        one=draft();self.add(one);before=audit_path(self.root,one).read_bytes()
        with self.assertRaisesRegex(RuntimeError,'verify failed'):
            review(self.root,[{'line':one['id'],'verdict':'ok','note':'Fine'}],'Offline reviewer','Review',clean=lambda root:True,commit=commit)
        self.assertEqual(audit_path(self.root,one).read_bytes(),before)

    def test_review_refuses_bad_batches_and_a_dirty_ledger_without_writing(self):
        one=draft();self.add(one)
        earlier=event('review',line_id=one['id'],verdict='issue',note='Check account',reviewer='Offline reviewer');self.add(earlier)
        before=audit_path(self.root,one).read_bytes()
        def never(root,message,lock_fd): self.fail('committed')
        ok={'line':one['id'],'verdict':'ok','note':'Resolved'}
        for batch,pattern in [([],'No verdicts'),([{**ok,'line':'f'*32}],'must identify'),([{**ok,'line':earlier['id']}],'must identify'),
                              ([{**ok,'line':['x']}],'must identify'),([ok,ok],'more than once'),([{**ok,'verdict':'maybe'}],'ok or issue'),
                              ([{**ok,'note':' '}],'needs a note'),([{**ok,'extra':1}],'exactly line, verdict and note')]:
            with self.subTest(pattern=pattern),self.assertRaisesRegex(ValueError,pattern):
                review(self.root,batch,'Offline reviewer','Review',clean=lambda root:True,commit=never)
        with self.assertRaisesRegex(ValueError,'named'):
            review(self.root,[ok],' ','Review',clean=lambda root:True,commit=never)
        with self.assertRaisesRegex(ValueError,'uncommitted'):
            review(self.root,[ok],'Offline reviewer','Review',clean=lambda root:False,commit=never)
        self.assertEqual(audit_path(self.root,one).read_bytes(),before)
        verdicts=self.root/'verdicts.json'
        for content,message in [(json.dumps([{**ok,'line':earlier['id']}]),'must identify a draft or statement audit line'),
                                (json.dumps(ok),'must hold a JSON list'),('[{"line":"a","line":"b"}]','Duplicate JSON key')]:
            verdicts.write_text(content);err=io.StringIO()
            with self.subTest(message=message),self.assertRaises(SystemExit),redirect_stdout(io.StringIO()),redirect_stderr(err):
                main(['--root',str(self.root),'review','--verdicts',str(verdicts),'--reviewer','Offline reviewer'])
            self.assertIn(message,err.getvalue())
        self.assertEqual(audit_path(self.root,one).read_bytes(),before)

    def test_incomplete_log_not_repaired_or_rewritten(self):
        one=draft();self.add(one);path=audit_path(self.root,one);value=path.read_bytes().rstrip(b'\n');path.write_bytes(value)
        with self.assertRaisesRegex(ValueError,'incomplete'): self.add(draft())
        self.assertEqual(path.read_bytes(),value)

    def test_check_store_rejects_bad_log_but_warns_about_missing_entry(self):
        from store import dump
        from check_store import check
        dump(self.root/'data/accounts.json',{'commodities':['INR'],'accounts':{}})
        dump(self.root/'data/expenses/2026.json',[])
        dump(self.root/'data/forecast.json',[])
        dump(self.root/'data/card-statements.json',[])
        (self.root/'data/prices').mkdir();(self.root/'data/prices/mutual-funds.prices').write_text('; fixture prices\n')
        self.add(draft())
        out=io.StringIO()
        with redirect_stdout(out): check(self.root)
        self.assertIn('WARNING: audit',out.getvalue());self.assertIn('b'*16,out.getvalue())
        path=next((self.root/'data/additions').glob('*.jsonl'));path.write_text('{"version":2}\n')
        with self.assertRaisesRegex(ValueError,'data/additions'):
            check(self.root)

    def test_unicode_line_separators_round_trip_on_one_physical_line(self):
        line=draft();line['text']='Tamil: தமிழ்; separators: \u0085 \u2028 \u2029; newline: \n'
        self.add(line)
        data=audit_path(self.root,line).read_text()
        self.assertEqual(len(data.splitlines()),1)
        self.assertEqual(records(self.root),[line])

    def test_mixed_rule_verdicts_are_append_only_locked_and_do_not_mutate_rules(self):
        from store import dump
        rule={'id':'f000000000000001','description':'Willow club'}
        dump(self.root/'data/forecast.json',[rule])
        one=draft();self.add(one)
        operation=event('rule',rule_action='made',request_id='1'*32,request_hash='2'*64,rule_changes=[dict(id=rule['id'],before=None,after=rule)])
        self.add(operation)
        path=audit_path(self.root,one);before=path.read_bytes();financial=(self.root/'data/forecast.json').read_bytes()
        def commit(root,message,fd):
            self.assertEqual((root/'data/forecast.json').read_bytes(),financial)
            return '3'*40
        result,ids=review(self.root,[dict(line=one['id'],verdict='ok',note='Checked complete payment'),dict(line=operation['id'],verdict='ok',note='Checked automatic origin'),dict(rule=rule['id'],verdict='propose-removal',reason='Covered by another active rule')],'Offline reviewer','Review rules',clean=lambda root:True,commit=commit)
        self.assertEqual(len(ids),3);self.assertEqual(result,'3'*40);self.assertTrue(path.read_bytes().startswith(before))
        lines=records(self.root);self.assertEqual(lines[-1]['kind'],'rule-review');self.assertEqual(lines[-1]['rule_verdict'],'propose-removal')
        self.assertEqual((self.root/'data/forecast.json').read_bytes(),financial)
        from audit import unreviewed
        self.assertEqual(unreviewed(lines),[])
        committed=path.read_bytes()
        for batch in ([dict(rule=rule['id'],verdict='remove',reason='Never mutate')],[dict(rule=rule['id'],verdict='confirm',reason=' ')],[dict(rule='f000000000000099',verdict='confirm',reason='Unknown')],[dict(rule=rule['id'],verdict='confirm',reason='First'),dict(rule=rule['id'],verdict='confirm',reason='Twice')]):
            with self.assertRaises(ValueError):review(self.root,batch,'Offline reviewer','Bad review',clean=lambda root:True,commit=commit)
            self.assertEqual(path.read_bytes(),committed)
        def fail(*args):raise RuntimeError('Check failed')
        with self.assertRaises(RuntimeError):review(self.root,[dict(rule=rule['id'],verdict='confirm',reason='Checked balanced rule')],'Offline reviewer','Review rollback',clean=lambda root:True,commit=fail)
        self.assertEqual(path.read_bytes(),committed)
