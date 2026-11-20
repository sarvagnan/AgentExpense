"""Exercise the browser host's actual seeding helper without launching a browser."""
from dataclasses import replace
import os
from pathlib import Path
import runpy
import sys
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient
from support import APPLICATION, Fixture

sys.path.insert(0, str(APPLICATION / 'review-dashboard/tests/browser'))
from fixture_seed import save_fixture_settings


class BrowserSeedTests(Fixture):
    def test_workflow_reset_waits_for_running_agent_exit(self):
        from adapters.base import Result
        from run_events import RunEvents
        from settings import Settings

        fake = self.fake.rename(self.base / 'fake-agent')
        settings = replace(self.settings, codex=str(fake))
        fixture = APPLICATION / 'review-dashboard/tests/browser/serve.py'
        with patch.object(Settings, 'from_env', return_value=settings), patch('uvicorn.run') as serve:
            runpy.run_path(str(fixture))
        app = serve.call_args.args[0]
        entered, release, exited = Event(), Event(), Event()

        def run(adapter, task):
            entered.set()
            assert release.wait(10), 'The reset never reached the held agent'
            return Result(dict(status='question', question='Which account did you pay from?', entry=None,
                               doubts=[], explanation=None, **{'new-accounts': [], 'attachment-names': []}))

        write = RunEvents.write
        def event(events, kind, **fields):
            write(events, kind, **fields)
            if kind == 'exit':
                exited.set()

        class ResetCalls(list):
            def append(self, job):
                super().append(job)
                release.set()

        rmdir = os.rmdir
        with patch('adapters.codex.Codex.run', run), patch.object(RunEvents, 'write', event), TestClient(app) as client:
            identity = self.create(client, upload=False)
            folder = app.state.drafts.folder(identity)
            def remove(path, **kwargs):
                if Path(path).name == identity and not exited.is_set():
                    # Before the fix, recreate events.jsonl after rmtree has unlinked its contents.
                    release.set()
                    self.assertTrue(exited.wait(10))
                    self.assertIn('"type":"exit"', (folder / 'events.jsonl').read_text())
                return rmdir(path, **kwargs)
            try:
                self.save(client, identity)
                self.assertTrue(entered.wait(10))
                with patch.object(app.state.worker, 'calls', ResetCalls()), patch.object(os, 'rmdir', remove):
                    self.assertEqual(client.post('/__test/reset-workflows').status_code, 200)
                self.assertTrue(exited.is_set())
                self.assertFalse(folder.exists())
            finally:
                release.set()

    def test_extra_file_is_committed_with_its_change_line(self):
        from audit import records, event
        from store import read_json
        path = self.ledger / 'Bills/Fictional-seed-note.txt'
        manifest = read_json(self.ledger / 'ledger.json')
        manifest['name'] = 'Fictional seeded ledger'
        verdict = event('review', line_id='0' * 32, verdict='ok', note='Fictional fixture verdict', reviewer='Offline fixture')
        result = save_fixture_settings(self.settings, lambda: {self.ledger / 'ledger.json': manifest}, {},
                                       'Seed fictional browser evidence', files={path: b'Fictional fixture evidence\n'}, events=[verdict])
        line = next(row for row in records(self.ledger) if row['kind'] == 'change')
        self.assertEqual(line['actor'], 'agent')
        self.assertIn({'path': 'Bills/Fictional-seed-note.txt', 'status': 'added'}, line['paths'])
        self.assertEqual({row['path'] for row in line['paths']}, {'Bills/Fictional-seed-note.txt', 'ledger.json'})
        self.assertEqual(line['change_id'], self.jj('log', '-r', 'main', '--no-graph', '-T', 'change_id'))
        self.assertEqual(result['commit_id'], self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'))
        self.assertIn(verdict, records(self.ledger))
        self.assertEqual(path.read_bytes(), b'Fictional fixture evidence\n')
        self.assertEqual(self.jj('diff', '--summary'), '')

    def test_refused_seed_restores_all_owned_files_and_audit(self):
        from audit import records
        from store import read_json
        path = self.ledger / 'Bills/Fictional-seed-note.txt'
        path.parent.mkdir(exist_ok=True); path.write_bytes(b'Original fixture evidence\n')
        self.checkpoint('Fictional pre-existing evidence')
        before = self.bytes()
        manifest = read_json(self.ledger / 'ledger.json'); manifest['name'] = 'Must roll back'
        with patch('commit.verify', side_effect=ValueError('Injected seed failure')):
            with self.assertRaisesRegex(RuntimeError, 'Injected seed failure'):
                save_fixture_settings(self.settings, lambda: {self.ledger / 'ledger.json': manifest}, {},
                                      'Refused fictional seed', files={path: b'Replacement fixture evidence\n'})
        self.assertEqual(self.bytes(), before)
        self.assertEqual(records(self.ledger), [])

    def test_production_settings_refuses_and_names_undeclared_evidence(self):
        from currency_settings import update_settings
        from store import read_json
        path = self.ledger / 'Bills/Fictional-undeclared-note.txt'
        main = self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
        manifest = read_json(self.ledger / 'ledger.json'); manifest['name'] = 'Must refuse'
        def install():
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'Keep this undeclared file\n')
            return {self.ledger / 'ledger.json': manifest}
        with self.assertRaisesRegex(RuntimeError, 'unexpected or changed: Bills/Fictional-undeclared-note.txt'):
            update_settings(self.settings, install, {}, 'Refused undeclared fixture path')
        self.assertEqual(path.read_bytes(), b'Keep this undeclared file\n')
        self.assertEqual(self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'), main)
        self.assertNotEqual(read_json(self.ledger / 'ledger.json')['name'], 'Must refuse')

    def test_seed_records_replacement_and_removal(self):
        from audit import records
        path = self.ledger / 'Bills/Fictional-seed-note.txt'
        path.parent.mkdir(exist_ok=True); path.write_bytes(b'Original fixture evidence\n')
        self.checkpoint('Fictional pre-existing evidence')
        for content, status in ((b'Replacement fixture evidence\n', 'modified'), (None, 'removed')):
            with self.subTest(status=status):
                save_fixture_settings(self.settings, lambda: {}, {}, 'Change fictional seed evidence', files={path: content})
                self.assertEqual(records(self.ledger)[-1]['paths'], [{'path': 'Bills/Fictional-seed-note.txt', 'status': status}])
                self.assertEqual(self.jj('diff', '--summary'), '')
        self.assertFalse(path.exists())

    def test_settings_keeps_a_file_saved_during_verification(self):
        import commit
        from currency_settings import update_name
        from store import read_json
        path = self.ledger / 'Bills/Fictional-concurrent-note.txt'
        before = read_json(self.ledger / 'ledger.json')
        main = self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
        verify = commit.verify
        def saved(root, **kwargs):
            result = verify(root, **kwargs)
            path.parent.mkdir(exist_ok=True); path.write_bytes(b'Keep the concurrent file\n')
            return result
        with patch('commit.verify', side_effect=saved):
            with self.assertRaisesRegex(RuntimeError, 'Bills/Fictional-concurrent-note.txt'):
                update_name(self.settings, 'Must refuse')
        self.assertTrue(path.exists(), 'Settings rollback deleted the concurrent file')
        self.assertEqual(path.read_bytes(), b'Keep the concurrent file\n')
        self.assertEqual(read_json(self.ledger / 'ledger.json'), before)
        self.assertEqual(self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'), main)

    def test_failed_addition_keeps_a_file_outside_its_before_images(self):
        import commit
        from audit import records
        path = self.ledger / 'Bills/Fictional-concurrent-note.txt'
        main = self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
        verify = commit.verify
        def saved(root, **kwargs):
            verify(root, **kwargs)
            path.parent.mkdir(exist_ok=True); path.write_bytes(b'Keep the concurrent file\n')
            raise ValueError('Injected verification failure')
        with self.client() as client:
            identity = self.create(client)
            with patch('commit.verify', side_effect=saved):
                self.save(client, identity)
                self.wait(client, identity, 'failed')
        self.assertTrue(path.exists(), 'Addition rollback deleted the concurrent file')
        self.assertEqual(path.read_bytes(), b'Keep the concurrent file\n')
        self.assertEqual(records(self.ledger), [])
        self.assertEqual(self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'), main)

    def test_receipt_failure_keeps_a_file_saved_after_the_commit(self):
        from currency_settings import SettingsRecords
        path = self.ledger / 'Bills/Fictional-late-note.txt'
        main = self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id')
        original = SettingsRecords.put
        def fail(records, draft):
            if draft.get('committed_result'):
                path.parent.mkdir(exist_ok=True); path.write_bytes(b'Keep the late file\n')
                raise OSError('Injected success receipt failure')
            return original(records, draft)
        with self.client(worker=False) as client, patch.object(SettingsRecords, 'put', fail):
            with self.assertRaisesRegex(OSError, 'success receipt failure'):
                client.post('/api/settings/name', json={'name': 'Must roll back'})
        self.assertEqual(path.read_bytes(), b'Keep the late file\n')
        self.assertEqual(self.jj('log', '-r', 'main', '--no-graph', '-T', 'commit_id'), main)
