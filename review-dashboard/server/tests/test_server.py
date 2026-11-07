from datetime import datetime, timedelta, timezone
import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch

from PIL import Image

from support import Fixture, ready, png, SERVER, metadata
from agent import command_for, prompt_for, prepare, policy_text
from transactions import ledger_lock, Transaction, clean, jj


class LifecycleTests(Fixture):
    def test_add_compress_commit_refresh_and_purge(self):
        with self.client() as client:
            identity = self.create(client, 'lunch "100"; $(touch nope)')
            self.save(client, identity)
            draft = self.wait(client, identity, 'added')
            result = draft['result']
            self.assertFalse(result['data_stale'], result)
            entries = json.loads((self.ledger / 'data/expenses/2026.json').read_text())
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['id'], result['entry_id'])
            self.assertEqual(entries[0]['postings'][0]['amount'], '100.00')
            filed = self.ledger / result['sources'][0]
            self.assertEqual(filed.name, 'Cafe-Receipt.png')
            self.assertLess(filed.stat().st_size, len(png()))
            with Image.open(filed) as a, Image.open(io.BytesIO(png())) as b:
                self.assertEqual(a.convert('RGBA').tobytes(), b.convert('RGBA').tobytes())
            self.assertIn('Expenses:Food:Cafe', json.loads((self.ledger / 'data/accounts.json').read_text())['accounts'])
            message = self.jj('log', '-r', '@-', '--no-graph', '-T', 'description')
            self.assertTrue(message.startswith('Add Cafe — lunch — cash via dashboard\n\nTyped: '))
            for line in ['Saved:', draft['client_zone'], 'Total: 100.00 INR, Assets:Cash', 'Evidence: Bills/2026-10-29/Cafe-Receipt.png',
                         'Agent doubts: No bank reference supplied', 'New accounts: Expenses:Food:Cafe',
                         f'Draft: {identity}', 'Added-by: dashboard agent (gpt-6.1-terra, xhigh)']:
                self.assertIn(line, message)
            self.assertNotIn('Asked:', message)
            self.assertEqual(self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'), result['commit_id'])
            from audit import records
            audit_line = records(self.ledger)[0]
            self.assertEqual((audit_line['outcome'], audit_line['entry_id']), ('added', result['entry_id']))
            self.assertEqual(audit_line['text'], 'lunch "100"; $(touch nope)')
            self.assertIn('data/additions/'+audit_line['time'][:4]+'.jsonl', self.jj('diff','-r','@-','--name-only').splitlines())
            self.assertTrue(clean(self.ledger))
            self.assertFalse((self.ledger / 'drafts' / identity / 'files').exists())
            self.assertEqual(client.get('/api/drafts').json(), [])
            self.assertEqual(len(client.get('/api/drafts?include_added=true').json()), 1)
            dashboard = gzip.decompress((self.application / 'review-dashboard/public/data/dashboard.json.gz').read_bytes())
            self.assertIn(result['entry_id'].encode(), dashboard)
            check = subprocess.run(['make', 'verify'], cwd=self.application,
                                   env={**os.environ, 'LEDGER_DIR': str(self.ledger)}, capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            self.assertEqual(draft['runs'][0]['usage']['input_tokens'], 123)
            self.assertEqual(draft['runs'][0]['lookups'][0]['tool'], 'entries_near')
            app_drafts = client.app.state.drafts
            app_drafts.purge(datetime.now(timezone.utc) + timedelta(hours=23))
            self.assertTrue(app_drafts.folder(identity).exists())
            app_drafts.purge(datetime.now(timezone.utc) + timedelta(hours=25))
            self.assertEqual(client.get(f'/api/drafts/{identity}').status_code, 404)

    def test_question_answer_then_added(self):
        question = ready()
        question.update(status='question', question='Which account paid?', entry=None)
        self.set_answers(question, ready())
        with self.client() as client:
            identity = self.create(client)
            self.save(client, identity)
            self.wait(client, identity, 'question')
            response = client.post(f'/api/drafts/{identity}/answer', json={'answer': 'Cash'})
            self.assertEqual(response.status_code, 200)
            draft = self.wait(client, identity, 'added')
            self.assertEqual(len(draft['runs']), 2)
            self.assertEqual(draft['questions'][0]['answer'], 'Cash')
            prompt = self.calls()[1]['argv'][-1]
            self.assertIn('Which account paid?', prompt)
            self.assertIn('Cash', prompt)
            self.assertIn('Asked: "Which account paid?" Answered: "Cash"',
                          self.jj('log', '-r', '@-', '--no-graph', '-T', 'description'))

    def test_not_expense_record_only_commit_changes_only_audit(self):
        answer = ready()
        answer.update(status='not_expense', entry=None, explanation='A card statement; no single expense')
        self.set_answers(answer)
        before = self.jj('log', '-r', '@-', '--no-graph', '-T', 'commit_id')
        with self.client() as client:
            identity = self.create(client)
            self.save(client, identity)
            draft = self.wait(client, identity, 'added')
            self.assertEqual(draft['result']['outcome'], 'not_expense')
            self.wait_condition(client, identity, lambda d: not d.get('audit_pending'))
            self.assertFalse(client.app.state.drafts.get(identity).get('audit_pending'))
            self.assertNotEqual(self.jj('log', '-r', '@-', '--no-graph', '-T', 'commit_id'), before)
            from audit import records
            line = records(self.ledger)[0]
            self.assertEqual((line['kind'],line['outcome'],line['draft_id']), ('draft','not_expense',identity))
            self.assertEqual(self.jj('diff', '-r', '@-', '--name-only').splitlines(), ['data/additions/'+line['time'][:4]+'.jsonl'])
            self.assertEqual(json.loads((self.ledger / 'data/expenses/2026.json').read_text()), [])

    def test_failure_modes_retry_once_then_explicit_retry(self):
        for kind in ('exit', 'noanswer', 'invalid'):
            with self.subTest(kind=kind):
                self.set_answers({'kind': kind})
                # Gate/login/probe use the ordinary generous fixture deadline.
                # The agent timeout itself is triggered only after the fake process
                # has recorded its invocation and reached its explicit sync point.
                def synchronized_communicate(process, timeout):
                    if kind != 'timeout' or json.loads(self.config.read_text())['steps'][0].get('kind') != 'timeout':
                        return process.communicate()
                    while True:
                        line = process.stdout.readline()
                        if line.strip() == 'FAKE_TIMEOUT_READY':
                            raise subprocess.TimeoutExpired(process.args, timeout)
                        if not line:
                            self.fail('Fake agent exited before its timeout synchronization point')
                with patch('agent.communicate', side_effect=synchronized_communicate), self.client(timeout=20) as client:
                    identity = self.create(client)
                    self.save(client, identity)
                    draft = self.wait(client, identity, 'failed')
                    self.assertEqual(len(draft['runs']), 2)
                    self.assertEqual(len(self.calls()), 2)
                    self.assertTrue(all(r['end'] and r['error'] for r in draft['runs']))
                    self.set_answers(ready())
                    self.assertEqual(client.post(f'/api/drafts/{identity}/retry').status_code, 200)
                    self.wait(client, identity, 'added')
                    self.assertEqual(client.delete(f'/api/drafts/{identity}').status_code, 409)
                    self.assertFalse(client.app.state.drafts.get(identity).get('trashed_at'))

    def test_server_checks_correct_once_then_fail(self):
        for defect in ('invisible', 'unbalanced', 'text_control', 'missing_names'):
            with self.subTest(defect=defect):
                answer = ready()
                if defect == 'invisible':
                    answer['entry']['postings'][0]['account'] += '\u200b'
                if defect == 'inr_original':
                    answer['entry']['original-amount'] = dict(amount='100.00', commodity='INR')
                if defect == 'unknown_parent':
                    answer['new-accounts'] = ['Unknown:Merchant']
                    answer['entry']['postings'][0]['account'] = 'Unknown:Merchant'
                if defect == 'unbalanced':
                    answer['entry']['postings'][0]['amount'] = '99.00'
                if defect == 'text_control':
                    answer['entry']['notes'] = ['control\x01text']
                if defect == 'missing_names':
                    answer['attachment-names'] = []
                self.set_answers(answer)
                before = self.bytes()
                with self.client() as client:
                    identity = self.create(client)
                    self.save(client, identity)
                    draft = self.wait(client, identity, 'failed')
                    self.assertEqual(len(draft['check_errors']), 2)
                    self.assertEqual(len(draft['runs']), 2)
                    self.assertIn(json.dumps(draft['check_errors'][0], ensure_ascii=False), self.calls()[1]['argv'][-1])
                    self.wait_condition(client, identity, lambda d: not d.get('audit_pending'))
                    self.assertFalse(client.app.state.drafts.get(identity).get('audit_pending'))
                    after=self.bytes()
                    accounting=lambda snapshot:{k:v for k,v in snapshot.items() if not k.startswith('data/additions/') and k!='expense_ledger.ledger'}
                    self.assertEqual(accounting(after),accounting(before))
                    from audit import records
                    line=next(r for r in records(self.ledger) if r.get('draft_id')==identity)
                    self.assertEqual(line['outcome'],'failed');self.assertEqual(len(line['check_errors']),2)
                    self.assertEqual(line['check_errors'],draft['check_errors'])
                    self.assertEqual(self.jj('diff','-r','@-','--name-only').splitlines(),['data/additions/'+line['time'][:4]+'.jsonl'])

    def test_check_failure_can_be_corrected(self):
        bad = ready()
        bad['entry']['postings'][0]['amount'] = '99.00'
        self.set_answers(bad, ready())
        with self.client() as client:
            identity = self.create(client)
            self.save(client, identity)
            draft = self.wait(client, identity, 'added')
            self.assertEqual(len(draft['check_errors']), 1)

    def test_uploadless_typed_expense(self):
        answer = ready()
        answer['attachment-names'] = []
        self.set_answers(answer)
        with self.client() as client:
            identity = self.create(client, upload=False)
            self.save(client, identity)
            self.wait(client, identity, 'added')


class SafetyTests(Fixture):
    def test_dirty_ledger_waits_and_fifo_blocks_later_drafts(self):
        dirty = self.ledger / 'human.txt'
        dirty.write_bytes(b'Someone is editing\n' * 100000)  # exceeds jj's default new-file limit
        with self.client() as client:
            first, second = self.create(client), self.create(client)
            self.save(client, first)
            self.save(client, second)
            draft = self.wait_condition(client, first, lambda d: bool(d['waiting']))
            self.assertIn('clean ledger', draft['waiting'])
            self.assertEqual(client.get(f'/api/drafts/{second}').json()['state'], 'queued')
            self.assertEqual(client.delete(f'/api/drafts/{first}').status_code, 409)
            self.assertEqual(json.loads((self.ledger / 'data/expenses/2026.json').read_text()), [])
            dirty.unlink()
            self.wait(client, first, 'added')
            self.wait(client, second, 'added')
            descriptions = self.jj('log', '-r', 'ancestors(@, 3)', '--no-graph', '-T', 'description')
            self.assertLess(descriptions.index(second), descriptions.index(first))

    def test_failure_after_changes_restores_exact_bytes_and_history(self):
        before = self.bytes()
        parent = self.jj('log', '-r', '@-', '--no-graph', '-T', 'commit_id')
        import transactions
        original_add = transactions.add_entry
        def fail_after_check(*args, **kwargs):
            if kwargs.get('dry_run'):
                return original_add(*args, **kwargs)
            raise RuntimeError('injected failure after filing/accounts')
        rollback=Transaction.rollback
        def assert_exact_rollback(transaction,journal):
            rollback(transaction,journal)
            self.assertEqual(self.bytes(),before)
            self.assertEqual(self.jj('log','-r','@-','--no-graph','-T','commit_id'),parent)
        with patch('transactions.Transaction.rollback',new=assert_exact_rollback), patch('transactions.add_entry', side_effect=fail_after_check):
            with self.client() as client:
                identity = self.create(client)
                self.save(client, identity)
                draft = self.wait(client, identity, 'failed')
                self.assertIn('injected failure', draft['error'])
                self.wait_condition(client, identity, lambda d: not d.get('audit_pending'))
                self.assertFalse(client.app.state.drafts.get(identity).get('audit_pending'))
        accounting=lambda snapshot:{k:v for k,v in snapshot.items() if not k.startswith('data/additions/') and k!='expense_ledger.ledger'}
        self.assertEqual(accounting(self.bytes()),accounting(before))
        self.assertTrue(clean(self.ledger))
        self.assertEqual(self.jj('log','-r','@--','--no-graph','-T','commit_id'),parent)
        from audit import records
        line=records(self.ledger)[0]
        self.assertEqual((line['outcome'],line['draft_id']),('failed',identity))
        self.assertIn('injected failure',line['error'])
        self.assertEqual(self.jj('diff','-r','@-','--name-only').splitlines(),['data/additions/'+line['time'][:4]+'.jsonl'])

    def test_commit_failure_restores_files(self):
        (self.application / 'Makefile').write_text('commit:\n\t@echo injected failure >&2\n\t@exit 1\n')
        before = self.bytes()
        with self.client() as client:
            identity = self.create(client)
            self.save(client, identity)
            draft = self.wait(client, identity, 'failed')
            self.assertIn('Ledger commit failed', draft['error'])
        self.assertEqual(self.bytes(), before)
        self.assertTrue(clean(self.ledger))

    def test_shared_lock_blocks_make_commit_and_worker(self):
        env = {**os.environ, 'LEDGER_DIR': str(self.ledger), 'EXPENSES_COMMIT_MESSAGE': 'Human commit'}
        with ledger_lock(self.ledger) as fd:
            process = subprocess.Popen(['make', 'commit'], cwd=self.application, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            with self.client() as client:
                identity = self.create(client)
                self.save(client, identity)
                self.wait(client, identity, 'working')
                self.assertIsNone(process.poll())
                self.assertEqual(client.get(f'/api/drafts/{identity}').json()['state'], 'working')
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
                self.wait(client, identity, 'added')
        out, err = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0, out + err)

    def test_refresh_failure_keeps_commit(self):
        package_path = self.application / 'review-dashboard/package.json'
        package = json.loads(package_path.read_text())
        package['scripts']['data'] = 'node -e "process.exit(9)"'
        package_path.write_text(json.dumps(package))
        with self.client() as client:
            identity = self.create(client)
            self.save(client, identity)
            draft = self.wait(client, identity, 'added')
            self.assertTrue(draft['result']['data_stale'])
            self.assertTrue(draft['result']['refresh_error'])
            self.assertEqual(len(json.loads((self.ledger / 'data/expenses/2026.json').read_text())), 1)

    def test_bookmark_failure_undoes_commit_and_files(self):
        helper = self.application / 'scripts/ledger/commit.py'
        helper.write_text(helper.read_text().replace("            jj(root, 'bookmark', 'set', 'main', '-r', '@-')",
                                                      "            raise RuntimeError('injected bookmark failure')"))
        before = self.bytes()
        parent = self.jj('log', '-r', '@-', '--no-graph', '-T', 'commit_id')
        with self.client() as client:
            identity = self.create(client)
            self.save(client, identity)
            draft = self.wait(client, identity, 'failed')
            self.assertIn('bookmark failure', draft['error'])
        self.assertEqual(self.bytes(), before)
        self.assertTrue(clean(self.ledger))
        self.assertEqual(self.jj('log', '-r', '@-', '--no-graph', '-T', 'commit_id'), parent)

    def test_restart_resumes_queued_and_working(self):
        with self.client(worker=False) as client:
            first, second = self.create(client), self.create(client)
            self.save(client, first)
            self.save(client, second)
            drafts = client.app.state.drafts
            draft = drafts.get(first)
            draft['state'] = 'working'
            draft['runs'] = [dict(start='2026-10-29T00:00:00+00:00',end=None,model=self.settings.model,effort=self.settings.effort,outcome=None,question=None,doubts=[],error=None,duration=None,usage={},lookups=[])]
            drafts.put(draft)
        with self.client() as client:
            added = self.wait(client, first, 'added')
            self.wait(client, second, 'added')
            self.assertEqual(added['runs'][0]['outcome'], 'interrupted')

    def test_journal_recovers_uncommitted_changes(self):
        with self.client(worker=False) as client:
            identity = self.create(client)
            self.save(client, identity)
            draft = client.app.state.drafts.get(identity)
            transaction = Transaction(self.settings, client.app.state.drafts, draft)
            before = (self.ledger / 'data/accounts.json').read_bytes()
            with ledger_lock(self.ledger):
                clean(self.ledger)
                transaction.snapshot([self.ledger / 'data/accounts.json'])
                (self.ledger / 'data/accounts.json').write_text('{}')
                self.assertIsNone(transaction.recover())
            self.assertEqual((self.ledger / 'data/accounts.json').read_bytes(), before)

    def test_interrupted_journal_preserves_later_human_work(self):
        with self.client(worker=False) as client:
            identity = self.create(client)
            self.save(client, identity)
            draft = client.app.state.drafts.get(identity)
            transaction = Transaction(self.settings, client.app.state.drafts, draft)
            with ledger_lock(self.ledger):
                clean(self.ledger)
                transaction.snapshot([self.ledger / 'data/accounts.json'])
                human = self.ledger / 'human.txt'
                human.write_text('Keep this work')
                with self.assertRaisesRegex(RuntimeError, 'later human changes'):
                    transaction.recover()
                self.assertEqual(human.read_text(), 'Keep this work')
                self.assertTrue(transaction.journal.exists())
                self.jj('commit', '-m', 'Human change after server interruption')
                with self.assertRaisesRegex(RuntimeError, 'later human changes'):
                    transaction.recover()
                self.assertIn('Human change', self.jj('log', '-r', '@-', '--no-graph', '-T', 'description'))

    def test_committed_result_restart_does_not_duplicate(self):
        with self.client() as client:
            identity = self.create(client)
            self.save(client, identity)
            self.wait(client, identity, 'added')
            drafts = client.app.state.drafts
            draft = drafts.get(identity)
            draft['state'] = 'working'
            drafts.put(draft)
        with self.client() as client:
            self.wait(client, identity, 'added')
            self.assertEqual(len(json.loads((self.ledger / 'data/expenses/2026.json').read_text())), 1)
            self.assertEqual(len(self.calls()), 1)


class ApiTests(Fixture):
    def test_upload_types_names_remove_and_limits(self):
        with self.client(worker=False) as client:
            identity = self.create(client)
            draft = client.get(f'/api/drafts/{identity}').json()
            file = draft['files'][0]
            self.assertEqual(file['original_name'], '../../outside.exe')
            self.assertTrue(file['name'].endswith('.png'))
            self.assertNotIn('..', file['name'])
            path = f"/api/drafts/{identity}/files/{file['id']}"
            self.assertEqual(client.get(path).content, png())
            self.assertEqual(client.delete(path).status_code, 200)
            self.assertEqual(client.get(path).status_code, 404)
            for fmt in ('JPEG', 'WEBP'):
                out = io.BytesIO()
                Image.new('RGB', (16, 16), 'blue').save(out, format=fmt)
                response = client.post(f'/api/drafts/{identity}/files', files={'files': ('x.txt', out.getvalue())})
                self.assertEqual(response.status_code, 201)
            response = client.post(f'/api/drafts/{identity}/files', files={'files': ('image.png', b'not an image')})
            self.assertEqual(response.status_code, 415)
            response = client.post(f'/api/drafts/{identity}/files', files={'files': ('large.png', b'x' * (25 * 1024 * 1024 + 1))})
            self.assertEqual(response.status_code, 413)
            response = client.post(f'/api/drafts/{identity}/files', files=[('files', ('f.png', png()))] * 9)
            self.assertEqual(response.status_code, 413)
            self.assertEqual(len(client.get(f'/api/drafts/{identity}').json()['files']), 2)
            response = client.post(f'/api/drafts/{identity}/files', files=[('files', ('f.png', png()))] * 8)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(len(response.json()['files']), 10)
            self.assertEqual(client.post(f'/api/drafts/{identity}/files', files={'files': ('f.png', png())}).status_code, 413)

    def test_access_identity_origin_and_host(self):
        with self.client(worker=False, dev=False, allowed_logins=('allowed@example.com',), allowed_hosts=('tail.test',)) as client:
            self.assertEqual(client.post('/api/drafts').status_code, 403)
            headers = {'Tailscale-User-Login': 'allowed@example.com'}
            self.assertEqual(client.post('/api/drafts', headers=headers).status_code, 201)
            self.assertEqual(client.post('/api/drafts', headers={**headers, 'Origin': 'https://evil.test'}).status_code, 403)
            self.assertEqual(client.post('/api/drafts', headers={**headers, 'Origin': 'https://tail.test'}).status_code, 201)
            self.assertEqual(client.post('/api/drafts', headers={**headers, 'Origin': 'https://tail.test@evil.test'}).status_code, 403)
            self.assertEqual(client.get('/api/drafts').status_code, 200)
            self.assertEqual(client.get('/api/health', headers={'Host': 'evil.test'}).status_code, 400)
        with self.client(worker=False) as client:
            self.assertEqual(client.post('/api/drafts').status_code, 201)
            self.assertEqual(client.post('/api/drafts', headers={'Origin': 'null'}).status_code, 403)

    def test_states_edit_discard_and_startup_purge(self):
        with self.client(worker=False) as client:
            identity = self.create(client)
            self.assertEqual(client.patch(f'/api/drafts/{identity}', json={'text': 'changed'}).status_code, 200)
            self.assertEqual(client.get(f'/api/drafts/{identity}').json()['pieces'], {'category': 'food'})
            self.save(client, identity)
            self.assertEqual(client.patch(f'/api/drafts/{identity}', json={'text': 'late'}).status_code, 409)
            self.assertEqual(client.post(f'/api/drafts/{identity}/save').status_code, 409)
            self.assertEqual(client.delete(f'/api/drafts/{identity}').status_code, 204)
            self.assertTrue(client.get(f'/api/drafts/{identity}').json()['trashed_at'])
            identity = self.create(client)
            drafts = client.app.state.drafts
            draft = drafts.get(identity)
            draft.update(state='added', completed_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat())
            drafts.put(draft)
        with self.client(worker=False) as client:
            self.assertEqual(client.get(f'/api/drafts/{identity}').status_code, 404)

    def test_static_fresh_data_symlinks_base_and_traversal(self):
        dash = self.application / 'review-dashboard'
        dist = dash / 'dist'
        for view in ('', 'explorer', 'atlas', 'journal', 'forecast', 'review', 'account-summary', 'add', 'drafts', 'statements'):
            (dist / view).mkdir(parents=True, exist_ok=True)
            (dist / view / 'index.html').write_text('<html>view</html>')
        (dash / 'public/data').mkdir(parents=True)
        (dist / 'data').mkdir()
        (dist / 'data/dashboard.json.gz').write_bytes(b'old')
        fresh = dash / 'public/data/dashboard.json.gz'
        fresh.write_bytes(b'fresh')
        (self.ledger / 'Bills/receipt.png').write_bytes(png())
        (dist / 'bills').symlink_to(os.path.relpath(self.ledger / 'Bills', dist))
        (self.ledger / 'Bank.xlsx').write_bytes(b'workbook')
        (dist / 'statements').mkdir(exist_ok=True)
        (dist / 'statements/Bank.xlsx').symlink_to(os.path.relpath(self.ledger / 'Bank.xlsx', dist / 'statements'))
        secret = self.base / 'secret'
        secret.write_text('secret')
        (dist / 'leak').symlink_to(secret)
        (self.ledger / 'Bills/leak').symlink_to(secret)
        (dash / 'public/data/leak').symlink_to(secret)
        with self.client(worker=False, base_path='/expenses/') as client:
            self.assertEqual(client.get('/expenses/api/health').status_code, 200)
            for view in ('', 'explorer', 'atlas', 'journal', 'forecast', 'review', 'account-summary', 'add', 'drafts', 'statements'):
                self.assertEqual(client.get('/' + view).text, '<html>view</html>')
                self.assertEqual(client.get('/expenses/' + view + ('/' if view else '')).text, '<html>view</html>')
            self.assertEqual(client.get('/expenses/data/dashboard.json.gz').content, b'fresh')
            fresh.write_bytes(b'newer')
            self.assertEqual(client.get('/data/dashboard.json.gz').content, b'newer')
            self.assertNotIn('content-encoding', client.get('/data/dashboard.json.gz').headers)
            self.assertEqual(client.get('/bills/receipt.png').content, png())
            self.assertEqual(client.get('/statements/Bank.xlsx').content, b'workbook')
            for path in ('/leak', '/bills/leak', '/data/leak', '/statements/../secret', '/%2e%2e/secret',
                         '/bills/%252e%252e/AGENTS.md', '/bills/%5c..%5csecret', '/.git/config'):
                self.assertIn(client.get(path).status_code, (400, 404), path)

    def test_default_base_path_is_the_builds_and_accepts_stripped_paths(self):
        from settings import Settings
        with patch.dict(os.environ, {'EXPENSES_BASE_PATH': ''}):
            os.environ.pop('EXPENSES_BASE_PATH')
            self.assertEqual((Settings().base_path, Settings.from_env().base_path), ('/expenses/', '/expenses/'))
        with patch.dict(os.environ, {'EXPENSES_BASE_PATH': '/'}):
            self.assertEqual(Settings.from_env().base_path, '/')
        with self.client(worker=False, base_path=Settings().base_path) as client:
            for path in ('/expenses/api/health', '/api/health'):
                self.assertEqual(client.get(path).status_code, 200, path)
            self.assertEqual(client.get('/expenses/api/health').json()['base_path'], '/expenses/')

class ManifestIntegrationTests(Fixture):
    def test_format_checked_at_startup_and_again_per_request(self):
        from store import dump
        path = self.ledger / 'ledger.json'
        original = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'This ledger uses format 0; run make upgrade-ledger.'):
            from app import create_app
            create_app(self.settings, start_worker=False)
        path.write_bytes(original)
        with self.client(worker=False) as client:
            from ledger_manifest import CURRENT_FORMAT
            dump(path, {'format': CURRENT_FORMAT + 1})
            response = client.get('/api/statements')
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['detail'], f'This app reads format {CURRENT_FORMAT}; this ledger uses format {CURRENT_FORMAT + 1}; a newer AgentExpense is needed.')
        path.write_bytes(original)

    def test_proposed_balance_sheet_account_gets_metadata_without_reordering(self):
        from store import dump, load
        from transactions import to_store
        path = self.ledger / 'ledger.json'
        manifest = json.loads(path.read_text());manifest['defaultCurrency'] = 'USD';dump(path, manifest)
        accounts_path = self.ledger / 'data/accounts.json'
        document = load(self.ledger)[accounts_path]
        document['accounts']['Assets:Bank:Example Checking'] = metadata('Assets', kind='bank', currency='INR')
        document['accounts']['Dev Sen:Accounts Payable:Opening'] = metadata('Assets', kind='person', currency='INR')
        document['accounts']['Dev Sen:Balances:Accounts Receivable:Opening'] = metadata('Assets', kind='person', currency='INR')
        dump(accounts_path, document)
        answer = ready();answer['new-accounts'] = ['Assets:Bank:Example Savings', 'Expenses:Food:Cafe',
                                                 'Dev Sen:Accounts Payable:Asha', 'Dev Sen:Balances:Accounts Receivable:Asha']
        _, result, new = to_store(answer, self.ledger, 1)
        self.assertEqual(list(result['accounts'])[:len(document['accounts'])], list(document['accounts']))
        self.assertEqual(result['accounts']['Assets:Bank:Example Savings'], metadata('Assets', kind='bank', currency='USD'))
        self.assertEqual(result['accounts']['Expenses:Food:Cafe'], {'category': None})
        for name in answer['new-accounts'][2:]:
            self.assertEqual(result['accounts'][name], metadata('Assets', kind='person', currency='USD'))
        answer['new-accounts'] = ['Dev Sen:Other:Asha']
        from transactions import AccountError
        with self.assertRaisesRegex(AccountError, 'Cannot infer kind'):
            to_store(answer, self.ledger, 1)
