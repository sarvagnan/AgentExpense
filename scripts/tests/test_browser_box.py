"""Offline browser-box boundary, lifecycle and scheduler tests; never invoke Incus."""
import argparse
import ast
from contextlib import redirect_stderr
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tarfile
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[2] / 'tools/browser-box/browser_box.py'
SPEC = importlib.util.spec_from_file_location('browser_box', MODULE_PATH)
box = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = box
SPEC.loader.exec_module(box)


class BrowserBoxTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(os.environ.get('EXPENSES_TEST_TMP', '/tmp/agentexpense-browser-box'))
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment=patch.dict(os.environ,{'BROWSER_BOX_CLIENT_REGISTRY':str(self.root/'clients')})
        environment.start();self.addCleanup(environment.stop)
        box.CANCELLED.clear()
        box.STOP_DEADLINE=None
        self.addCleanup(box.CANCELLED.clear)

    def write(self, name, text='source'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def image_checks(self):
        source = (box.TOOLS/'build.sh').read_text().split(" <<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
        tree = ast.parse(source)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        names = {node.name for node in functions}
        self.assertTrue({'required_commands', 'check_project_node', 'check_timezones'} <= names,
                        'Image version step must check commands, the actual Node resolver and timezone aliases')
        namespace = {}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<image checks>', 'exec'), namespace)
        for name in names:
            self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
                                for node in ast.walk(ast.Module(body=[n for n in tree.body if not isinstance(n, ast.FunctionDef)], type_ignores=[]))),
                            f'{name} must run in the image version step')
        return namespace

    def test_image_required_commands_reject_each_missing_build_or_runtime_command(self):
        check = self.image_checks()['required_commands']
        for missing in ('python3', 'make', 'cc', 'git'):
            with self.subTest(missing=missing), patch('shutil.which', side_effect=lambda name: None if name == missing else '/bin/'+name):
                with self.assertRaisesRegex(RuntimeError, missing):
                    check()
        with patch('shutil.which', side_effect=lambda name: '/bin/'+name):
            self.assertEqual(set(check()), {'python3', 'make', 'cc', 'git'})

    def test_image_node_check_executes_actual_makefile_resolver_and_selected_node(self):
        check = self.image_checks()['check_project_node']
        with patch('subprocess.check_output', side_effect=['/fnm/node/bin\n', 'v26.13.0\n']) as command:
            result = check(self.root, '26.13.0')
        self.assertEqual(command.call_args_list[0].args[0], ['python3', str(self.root/'scripts/node_toolchain.py')])
        self.assertEqual(command.call_args_list[1].args[0], ['/fnm/node/bin/node', '--version'])
        self.assertEqual(result['version'], 'v26.13.0')
        with patch('subprocess.check_output', side_effect=subprocess.CalledProcessError(1, 'node_toolchain.py')):
            with self.assertRaises(subprocess.CalledProcessError):
                check(self.root, '26.13.0')
        with patch('subprocess.check_output', side_effect=['/wrong/bin\n', 'v1.0.0\n']):
            with self.assertRaisesRegex(RuntimeError, 'Node resolver'):
                check(self.root, '26.13.0')

    def test_image_timezone_check_rejects_missing_browser_alias(self):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        check = self.image_checks()['check_timezones']
        def lookup(name):
            if name == 'Asia/Calcutta':
                raise ZoneInfoNotFoundError(name)
            return ZoneInfo(name)
        with patch('zoneinfo.ZoneInfo', side_effect=lookup):
            with self.assertRaisesRegex(RuntimeError, 'Asia/Calcutta'):
                check()

    def test_missing_timezone_alias_reproduces_actual_draft_validation_error(self):
        from zoneinfo import TZPATH
        database = self.root/'zoneinfo'
        (database/'Asia').mkdir(parents=True)
        canonical = next(Path(folder)/'Asia/Kolkata' for folder in TZPATH if (Path(folder)/'Asia/Kolkata').is_file())
        shutil.copyfile(canonical, database/'Asia/Kolkata')
        application = box.TOOLS.parents[1]
        program = ('import sys; sys.path.insert(0, sys.argv[1]); '
                   'from datetime import datetime, timezone; from draft_time import client_context; '
                   'print(client_context("2026-11-11T15:41:01+05:30", "Asia/Calcutta", '
                   'at=datetime(2026,11,11,12,tzinfo=timezone.utc)))')
        command = [sys.executable, '-S', '-c', program, str(application/'review-dashboard/server')]
        environment = {**os.environ, 'PYTHONTZPATH': str(database), 'PYTHONDONTWRITEBYTECODE': '1'}
        red = subprocess.run(command, env=environment, text=True, capture_output=True)
        self.assertNotEqual(red.returncode, 0)
        self.assertIn('client_zone must be a known IANA zone name.', red.stderr)
        shutil.copyfile(canonical, database/'Asia/Calcutta')
        green = subprocess.run(command, env=environment, text=True, capture_output=True)
        self.assertEqual(green.returncode, 0, green.stderr)
        self.assertIn("'client_zone': 'Asia/Calcutta'", green.stdout)

    def listing(self, names):
        return patch.object(box.subprocess, 'check_output', side_effect=[str(self.root)+'\n',
                            b'\0'.join(os.fsencode(name) for name in names) + b'\0'])

    def test_tracked_and_untracked_working_files_deleted_files_and_git_command(self):
        self.write('scripts/ledger/store.py', 'uncommitted edit')
        self.write('new.py', 'untracked')
        names = ['scripts/ledger/store.py', 'new.py', 'deleted.py', 'new.py']
        with self.listing(names) as command:
            files = box.pack_source(self.root, self.root/'source.tar.gz')
        self.assertEqual(files, ['new.py', 'scripts/ledger/store.py'])
        self.assertEqual(command.call_args.args[0][-5:], ['ls-files', '-co', '--exclude-standard', '-t', '-z'])
        with tarfile.open(self.root/'source.tar.gz') as archive:
            self.assertEqual(archive.extractfile('scripts/ledger/store.py').read(), b'uncommitted edit')

    def test_private_paths_fail_closed(self):
        names = ['ledger/data/expenses/a.json', 'tmp/a', 'local.json', 'auth.json',
                 'nested/auth.json', '.codex/config.toml', 'nested/.codex/x', '.git/config',
                 '.npmrc', '.netrc', '.pypirc', '.ssh/identity', '.aws/config', 'nested/AUTH.JSON',
                 '.env', '.env.production', 'secrets/private.key', 'cert.pem', 'credentials.json',
                 '/absolute', '../outside', 'nested/../../escape', 'nested\\auth.json',
                 'personal/ledger.json', 'backup/data/accounts.json', 'private/books.ledger',
                 'data/accounts.json',
                 'backup/data/expenses/2026.json']
        for name in names:
            with self.subTest(name=name), self.assertRaises(box.BoxError):
                box.check_path(name)

    def test_nested_ledger_fragments_and_untracked_documents_are_refused(self):
        for name in ['copy/Bills/2026/receipt.pdf','copy/drafts/d1/upload.jpg','copy/data/additions/2026.jsonl',
                     'copy/data/card-statements.json','copy/data/prices/fund.json','copy/data/forecast.json',
                     'copy/data/recurring-settings.json','copy/data/fuel-log.json','copy/data/trash.json',
                     'copy/data/trash-files/id/file.dat','deep/copy/forecast.json','deep/copy/export.prices',
                     'deep/copy/LEDGER.JSON','Ledger/evidence.dat','LOCAL.JSON','scratch/nested/receipt.pdf',
                     'scratch/nested/photo.png','scratch/nested/book.xlsx','scratch/nested/statement.csv']:
            with self.subTest(name=name),self.listing([name]):
                self.write(name)
                with self.assertRaises(box.BoxError):box.pack_source(self.root,self.root/'refused.tar.gz')

    def test_public_tracked_documents_and_known_asset_fixture_folders_are_allowed(self):
        names=['docs/sample.pdf','review-dashboard/public/icon.png','fixtures/statements/demo/statement.xlsx',
               'fixtures/ledgers/demo/Bills/receipt.jpg']
        for name in names:self.write(name)
        raw=b'\0'.join((('H ' if name=='docs/sample.pdf' else '? ')+name).encode() for name in names)+b'\0'
        with patch.object(box.subprocess,'check_output',side_effect=[str(self.root)+'\n',raw]):
            self.assertEqual(box.source_files(self.root),sorted(names))

    def test_pinned_download_checks_reject_wrong_bytes_before_extraction(self):
        import hashlib
        script=(box.TOOLS/'build.sh').read_text()
        helper=script.split('verify_download() {',1)[1].split('\n}',1)[0]
        archive=self.write('download.bin','test archive')
        expected=hashlib.sha256(archive.read_bytes()).hexdigest()
        command=['bash','-c','verify_download() {'+helper+'\n}\nverify_download "$1" "$2"','check',str(archive)]
        self.assertEqual(subprocess.run([*command,expected],capture_output=True).returncode,0)
        self.assertNotEqual(subprocess.run([*command,'0'*64],capture_output=True).returncode,0)
        for name,extract in [('fnm','unzip fnm.zip'),('uv','tar -xzf uv.tar.gz'),('jj','tar -xzf jj.tar.gz')]:
            archive_name='fnm.zip' if name=='fnm' else name+'.tar.gz'
            self.assertRegex(box.PINS[name+'_sha256'],r'^[0-9a-f]{64}$')
            self.assertLess(script.index('verify_download '+archive_name),script.index(extract))

    def test_refuses_entire_bundle_before_exporting_private_path(self):
        self.write('fine.py')
        self.write('ledger/private.json')
        with self.listing(['fine.py', 'ledger/private.json']), self.assertRaises(box.BoxError):
            box.pack_source(self.root, self.root/'source.tar.gz')
        self.assertFalse((self.root/'source.tar.gz').exists())

    def test_ignored_files_are_not_enumerated_or_read(self):
        self.write('fine.py')
        self.write('ledger/auth.json', 'do not read')
        with self.listing(['fine.py']), patch.object(Path, 'read_text', side_effect=AssertionError('private read')):
            self.assertEqual(box.source_files(self.root), ['fine.py'])

    def test_agent_home_source_is_refused_before_git_runs(self):
        with patch.object(box.subprocess, 'check_output') as command:
            with self.assertRaisesRegex(box.BoxError, 'credential directory'):
                box.source_files(self.root/'.codex')
        command.assert_not_called()

    def test_rejects_symlink_and_nonregular_file(self):
        target = self.write('target.py')
        (self.root/'linked.py').symlink_to(target)
        with self.listing(['linked.py']), self.assertRaisesRegex(box.BoxError, 'symlink'):
            box.source_files(self.root)
        (self.root/'folder').mkdir()
        with self.listing(['folder']), self.assertRaisesRegex(box.BoxError, 'non-file'):
            box.source_files(self.root)

    def test_rejects_linked_parent_and_subdirectory_checkout(self):
        (self.root/'real').mkdir()
        self.write('real/a.py')
        (self.root/'link').symlink_to(self.root/'real', target_is_directory=True)
        with self.listing(['link/a.py']), self.assertRaisesRegex(box.BoxError, 'symlink'):
            box.source_files(self.root)
        with patch.object(box.subprocess, 'check_output', return_value=str(self.root.parent)+'\n'):
            with self.assertRaisesRegex(box.BoxError, 'checkout root'):
                box.source_files(self.root)

    def test_fixtures_and_unfilled_template_are_allowed(self):
        names = ['fixtures/ledgers/asha/ledger.json', 'scripts/ledger/store.py',
                 'templates/ledger/ledger.json', 'templates/ledger/data/accounts.json']
        for name in names:
            value = '{{id}}' if name == 'templates/ledger/ledger.json' else '{}'
            if name == 'templates/ledger/data/accounts.json':
                value = '{"commodities":["{{currency}}"],"accounts":{}}'
            self.write(name, value)
        with self.listing(names):
            self.assertEqual(box.source_files(self.root), sorted(names))
        self.write('templates/ledger/ledger.json', '{"id":"real-ledger"}')
        with self.listing(names), self.assertRaisesRegex(box.BoxError, 'populated'):
            box.source_files(self.root)

    def test_image_hash_changes_for_each_lock_and_pins_but_not_application_edit(self):
        for name in box.IMAGE_HASH_INPUTS:
            self.write(name, name)
        original = box.image_hash(self.root)
        self.write('application.py', 'new source')
        self.assertEqual(original, box.image_hash(self.root))
        for name in box.IMAGE_HASH_INPUTS:
            self.write(name, 'changed')
            self.assertNotEqual(original, box.image_hash(self.root))
            self.write(name, name)
        with patch.dict(box.PINS, {'uv': 'different'}):
            self.assertNotEqual(original, box.image_hash(self.root))

    def test_build_inputs_use_the_packaged_locks_after_checkout_changes(self):
        for name in box.BUILD_INPUTS:
            self.write(name, name)
        with self.listing(list(box.BUILD_INPUTS)):
            box.pack_source(self.root, self.root/'source.tar.gz')
        self.write('.node-version', 'changed after packaging')
        inputs = box.snapshot_build_inputs(self.root/'source.tar.gz', self.root/'snapshot')
        self.assertEqual((inputs/'.node-version').read_text(), '.node-version')
        self.assertNotEqual(box.image_hash(inputs), box.image_hash(self.root))

    def test_populated_or_extended_templates_are_refused(self):
        for name, content in [('templates/ledger/data/accounts.json', '{"accounts":{"Personal":{}},"commodities":["{{currency}}"]}'),
                              ('templates/ledger/data/forecast.json', '[{"private":true}]'),
                              ('templates/ledger/Bills/private.txt', 'private')]:
            self.write(name, content)
            with self.listing([name]), self.assertRaises(box.BoxError):
                box.source_files(self.root)

    def test_case_parsing(self):
        for persona in ('asha', 'sam', 'jordan', 'empty'):
            for engine in ('chromium', 'firefox', 'webkit'):
                for theme in ('light', 'dark'):
                    for width in (1440, 412):
                        case = box.parse_case(f'{persona}:{engine}:{theme}:{width}')
                        self.assertEqual(case, box.Case(persona, engine, theme, width))
        for text in ('asha', 'asha:chrome:light:1440', 'asha:webkit:light:320',
                     'owner:chromium:dark:412', 'asha:chromium:blue:412', 'asha:chromium:light:1440;id'):
            with self.subTest(text=text), self.assertRaises(argparse.ArgumentTypeError):
                box.parse_case(text)

    def test_concurrency_is_bounded_and_retry_keeps_slot_and_case(self):
        for invalid in ('0', '3', '-1', 'unlimited'):
            with self.assertRaises(argparse.ArgumentTypeError):
                box.jobs_value(invalid)
        self.assertEqual(box.jobs_value('2'), 2)
        lock, arrived, release = threading.Lock(), threading.Event(), threading.Event()
        active = peak = 0
        calls = []

        def attempt(case, number):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                calls.append((case, number))
                if active == 2:
                    arrived.set()
            self.assertTrue(release.wait(5))
            with lock:
                active -= 1
            return {'exit': int(number == 1)}

        cases = [box.Case(p, 'chromium', 'light', 1440) for p in ('asha', 'sam', 'jordan', 'empty')]
        with ThreadPoolExecutor(max_workers=1) as controller:
            pending = controller.submit(box.run_cases, cases, 2, attempt)
            try:
                self.assertTrue(arrived.wait(5))
                self.assertEqual(peak, 2)
            finally:
                release.set()
            records = pending.result(timeout=5)
        self.assertEqual(peak, 2)
        self.assertEqual([len(attempts) for attempts in records], [2]*4)
        self.assertCountEqual(calls, [(case, n) for case in cases for n in (1, 2)])

    def test_passes_are_not_retried_and_cancelled_work_does_not_start(self):
        case = box.parse_case('asha:chromium:light:1440')
        with patch.object(box, 'run_attempt') as attempt:
            attempt.return_value = {'exit': 0}
            self.assertEqual(len(box.run_cases([case], 1, attempt)[0]), 1)
            attempt.assert_called_once_with(case, 1)
        box.CANCELLED.set()
        with patch.object(box, 'run_attempt') as attempt:
            self.assertEqual(box.run_cases([case], 1, attempt)[0][0]['exit'], 130)
            attempt.assert_not_called()

    def test_incus_commands_scope_project_before_exec_separator(self):
        incus = box.Incus('configured', 'agentexpense-ci')
        child = SimpleNamespace(communicate=lambda timeout: ('ok', ''), returncode=0, poll=lambda: 0)
        with patch.object(box.subprocess, 'Popen', return_value=child) as spawn:
            incus.exec('owned', 'printf', 'hello')
        wrapped=spawn.call_args.args[0]
        self.assertEqual(wrapped[wrapped.index('--')+1:], ['incus', '--project', 'agentexpense-ci',
                                                  'exec', 'configured:owned', '--', 'printf', 'hello'])

    def test_client_wait_interval_and_cancellation_terminate_child(self):
        incus = box.Incus('configured', 'agentexpense-ci')
        from unittest.mock import Mock
        child = Mock()
        child.communicate.side_effect = [subprocess.TimeoutExpired('incus', 1), ('ok', '')]
        child.returncode = 0
        child.poll.return_value = 0
        with patch.object(box.subprocess, 'Popen', return_value=child), patch.object(box.time, 'monotonic', return_value=0):
            incus.exec('owned', 'true')
        self.assertEqual([call.kwargs['timeout'] for call in child.communicate.call_args_list], [1, 1])

        def cancel(**kwargs):
            box.CANCELLED.set()
            raise subprocess.TimeoutExpired('incus', 1)
        child.reset_mock()
        child.communicate.side_effect = cancel
        child.poll.return_value = None
        with patch.object(box.subprocess, 'Popen', return_value=child):
            with self.assertRaisesRegex(box.BoxError, 'Cancelled'):
                incus.exec('owned', 'long-command')
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=1)

    def test_run_launch_is_ephemeral_profileless_and_has_no_nic(self):
        incus = box.Incus('configured', 'agentexpense-ci')
        with patch.object(incus, 'project_instances', return_value=[]), patch.object(incus, 'call', return_value=SimpleNamespace(returncode=0)) as call, patch.object(incus, 'owned_instance', return_value={'expanded_devices': {'root': {'type': 'disk'}}}):
            incus.launch('configured:browser-box-hash', 'owned')
        command = call.call_args.args
        self.assertIn('--no-profiles', command)
        self.assertIn('--ephemeral', command)
        config = json.loads(call.call_args.kwargs['input_text'])
        self.assertEqual(config['config']['limits.cpu'], '2')
        self.assertEqual(config['config']['limits.memory'], '3GiB')
        self.assertEqual(set(config['devices']), {'root'})
        with patch.object(incus, 'project_instances', return_value=[]), patch.object(incus, 'call', return_value=SimpleNamespace(returncode=0)), patch.object(incus, 'owned_instance', return_value={'expanded_devices': {'eth0': {'type': 'nic'}}}):
            with self.assertRaisesRegex(box.BoxError, 'no network'):
                incus.launch('configured:image', 'owned')

    def test_builder_has_only_explicit_nic_and_is_not_ephemeral_before_publish(self):
        incus = box.Incus('configured', 'agentexpense-ci')
        with patch.object(incus, 'project_instances', return_value=[]), patch.object(incus, 'call', return_value=SimpleNamespace(returncode=0)) as call:
            incus.launch('images:debian/13/amd64', 'owned', build=True)
        command = call.call_args.args
        self.assertIn('--no-profiles', command)
        self.assertNotIn('--ephemeral', command)
        config = json.loads(call.call_args.kwargs['input_text'])
        self.assertEqual(config['devices']['eth0']['type'], 'nic')

    def test_cached_image_reuses_provenance_without_launch_or_download(self):
        for name in box.IMAGE_HASH_INPUTS:
            self.write(name, name)
        incus = box.Incus('configured', 'agentexpense-ci')
        digest = box.image_hash(self.root)
        image = {'aliases': [{'name': 'browser-box-'+digest}], 'size': 123,
                 'properties': {'user.browser-box.hash': digest, 'user.browser-box.versions': '{"node":"pinned"}'}}
        with patch.object(incus, 'call', return_value=SimpleNamespace(stdout=json.dumps([image]))) as call, patch.object(incus, 'launch') as launch:
            self.assertEqual(incus.ensure_image(self.root, self.root, 60), 'browser-box-'+digest)
        call.assert_called_once()
        launch.assert_not_called()
        self.assertTrue(json.loads((self.root/'image.json').read_text())['cached'])
        self.assertEqual(json.loads((self.root/'versions.json').read_text()), {'node': 'pinned'})

    def test_cleanup_lists_exact_owned_instance_before_stop_and_checks_removal(self):
        incus = box.Incus('configured', 'agentexpense-ci')
        owned = {'name': 'owned', 'status': 'Running', 'config': {'user.browser-box.owner': incus.owner}}
        results = [SimpleNamespace(stdout=json.dumps([owned])), SimpleNamespace(stdout=''),
                   SimpleNamespace(stdout='[]'), SimpleNamespace(stdout='[]')]
        with patch.object(incus, 'call', side_effect=results) as call:
            incus.cleanup('owned', ephemeral=True)
        self.assertEqual([c.args[0] for c in call.call_args_list], ['list', 'stop', 'list', 'list'])
        self.assertEqual(call.call_args_list[0].args[1:3], ('configured:', '^owned$'))
        self.assertEqual(call.call_args_list[1].args[1], 'configured:owned')
        owned['config'] = {}
        with patch.object(incus, 'call', return_value=SimpleNamespace(stdout=json.dumps([owned]))) as call:
            with self.assertRaisesRegex(box.BoxError, 'not created'):
                incus.cleanup('owned', ephemeral=True)
        self.assertEqual(call.call_count, 1)

    def test_unreachable_remote_is_not_treated_as_missing_project(self):
        incus = box.Incus('configured', 'agentexpense-ci')
        with patch.object(incus, 'call', return_value=SimpleNamespace(returncode=1, stderr='connection refused')) as call:
            with self.assertRaisesRegex(box.BoxError, 'unreachable'):
                incus.ensure_project()
        self.assertEqual(call.call_count, 1)

    def test_dns_failures_retry_only_after_a_successful_remote_response(self):
        errors=['Error: lookup box.invalid: no such host','Error: lookup box.invalid: temporary failure in name resolution',
                'Error: lookup box.invalid: server misbehaving','Error: lookup box.invalid: i/o timeout']
        for error in errors:
            with self.subTest(error=error):
                incus=box.Incus('configured','agentexpense-ci')
                okay=SimpleNamespace(returncode=0,stdout='{}',stderr='')
                failed=SimpleNamespace(returncode=1,stdout='',stderr=error)
                with patch.object(incus,'_call_once',side_effect=[okay,failed,failed,okay]) as client, \
                     patch.object(box.CANCELLED,'wait',return_value=False):
                    incus.call('list','configured:')
                    self.assertEqual(incus.exec('owned','status').returncode,0)
                self.assertEqual(client.call_count,4)

    def test_startup_dns_does_not_retry_a_wrong_remote(self):
        for error in ['Error: lookup box.invalid: no such host','Error: lookup box.invalid: i/o timeout']:
            with self.subTest(error=error):
                incus=box.Incus('configured','agentexpense-ci');incus.reconnect_timeout=.001
                with patch.object(incus,'_call_once',return_value=SimpleNamespace(returncode=1,stdout='',stderr=error)) as client, \
                     patch.object(box.CANCELLED,'wait',return_value=False):
                    with self.assertRaises(box.BoxError):incus.call('project','show','configured:agentexpense-ci')
                self.assertEqual(client.call_count,1)

    def test_lost_exec_and_file_operations_are_retried(self):
        for command in [('exec','configured:owned','--','status'),('file','pull','configured:owned/file','destination')]:
            with self.subTest(command=command):
                incus=box.Incus('configured','agentexpense-ci')
                replies=[SimpleNamespace(returncode=1,stdout='{"state":"running"}',stderr='Error: Operation not found'),
                         SimpleNamespace(returncode=0,stdout='{"state":"running"}',stderr='')]
                with patch.object(incus,'_call_once',side_effect=replies) as client,patch.object(box.CANCELLED,'wait',return_value=False):
                    self.assertEqual(incus.call(*command).returncode,0)
                self.assertEqual(client.call_count,2)

    def test_new_project_has_independent_images_profiles_and_total_limits(self):
        incus = box.Incus('configured', 'agentexpense-ci')
        with patch.object(incus, 'call', side_effect=[SimpleNamespace(returncode=1, stderr='Project not found'), SimpleNamespace(returncode=0)]) as call:
            incus.ensure_project()
        command = call.call_args.args
        for value in ('features.images=true', 'features.profiles=true', 'limits.cpu=4', 'limits.memory=6GiB', 'limits.containers=2'):
            self.assertIn(value, command)

    def test_existing_project_with_wrong_limits_is_not_modified(self):
        incus = box.Incus('configured', 'agentexpense-ci')
        with patch.object(incus, 'call', side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(stdout='false')]) as call:
            with self.assertRaisesRegex(box.BoxError, 'Refusing to change'):
                incus.ensure_project()
        self.assertFalse(any(c.args[1] == 'set' for c in call.call_args_list))

    def test_generated_case_script_is_offline_and_pins_context_without_host_environment(self):
        script = box.case_script(box.parse_case('sam:webkit:dark:412'), 'browser-capture', 'rules', 'rules-')
        for text in ('TZ=Asia/Kolkata', 'LC_ALL=en_US.UTF-8', 'UV_OFFLINE=1', 'UV_NO_SYNC=1',
                     'npm_config_offline=true', 'TRACE_SCREENSHOTS=0', 'BROWSER_CASE=dark-412',
                     'BROWSER_FOCUS=rules', 'fnm exec --using=.node-version -- make browser-capture'):
            self.assertIn(text, script)
        self.assertNotIn('os.environ', script)
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)

    def test_result_extraction_refuses_paths_and_links(self):
        for name, kind in (('../escape', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE), ('link', tarfile.SYMTYPE)):
            archive_path = self.root/'results.tar.gz'
            with tarfile.open(archive_path, 'w:gz') as archive:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = '/outside'
                archive.addfile(member)
            with self.assertRaises(box.BoxError):
                box.unpack_results(archive_path, self.root/'unpacked')
        with tarfile.open(archive_path, 'w:gz') as archive:
            member = tarfile.TarInfo('run.log')
            member.size = 2
            archive.addfile(member, io.BytesIO(b'ok'))
        box.unpack_results(archive_path, self.root/'unpacked')
        self.assertEqual((self.root/'unpacked/run.log').read_text(), 'ok')

    def test_unconfigured_remote_fails_before_source_or_incus_access(self):
        with patch.dict(os.environ, {'BROWSER_BOX_REMOTE': ''}), patch.object(box, 'pack_source') as source, patch.object(box.Incus, 'call') as incus, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exit_status:
                box.main(['asha:chromium:light:1440'])
        self.assertEqual(exit_status.exception.code, 2)
        source.assert_not_called()
        incus.assert_not_called()


if __name__ == '__main__':
    unittest.main()
