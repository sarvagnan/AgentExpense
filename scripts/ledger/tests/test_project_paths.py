"""Exercise the application against separate stores without a ledger-owned schema."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from test_ledger import Fixture
from project_paths import APPLICATION_ROOT, SCHEMA_PATH
from store import load


class SeparateLedgerTests(Fixture):
    def setUp(self):
        super().setUp()
        from store import dump, read_json
        manifest = read_json(self.root / 'ledger.json')
        manifest['extensions'] = {'fuel': {'ask': True}}
        dump(self.root / 'ledger.json', manifest)
        dump(self.year, [{**item, 'extensions': {'fuel': None}} for item in read_json(self.year)])
        prices = self.root / 'data/prices/mutual-funds.prices'
        prices.parent.mkdir(parents=True)
        prices.write_text('; no funds in this fixture\n')
        (self.root / 'data/fuel-log.json').write_text('{"meta":{},"entries":[]}\n')

    def test_schema_is_owned_by_application(self):
        self.assertEqual(SCHEMA_PATH, APPLICATION_ROOT / 'schema/store.schema.json')
        self.assertFalse((self.root / 'data/schema').exists())
        load(self.root)
        fake = self.root / 'data/schema/store.schema.json'
        fake.parent.mkdir()
        fake.write_text('{"not":{}}')
        load(self.root)  # The ledger cannot substitute a different schema.

    def test_environment_and_explicit_root_generate_and_check(self):
        env = {**os.environ, 'LEDGER_DIR': str(self.root)}
        def cli(script, *args, override=None):
            return subprocess.run([sys.executable, str(APPLICATION_ROOT / 'scripts/ledger' / script), *args],
                                  cwd=self.root, env=override or env, check=True, capture_output=True, text=True)
        cli('generate_ledger.py')
        journal = (self.root / 'expense_ledger.ledger').read_bytes()
        self.assertIn(b'2026-01-02 * Purchase', journal)
        cli('check_store.py')
        cli('generate_ledger.py', '--root', str(self.root), override={**env, 'LEDGER_DIR': str(self.root / 'absent')})
        self.assertEqual((self.root / 'expense_ledger.ledger').read_bytes(), journal)
        cli('check_store.py', '--root', str(self.root), override={**env, 'LEDGER_DIR': str(self.root / 'absent')})

    def test_dashboard_builders_read_separate_ledger(self):
        # Isolate generated outputs from both the real dashboard and parallel Node tests.
        with tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp'), prefix='expense-app-fixture-') as temp:
            application = Path(temp)
            (application / 'scripts').mkdir()
            shutil.copytree(APPLICATION_ROOT / 'scripts', application / 'scripts', dirs_exist_ok=True)
            shutil.copytree(APPLICATION_ROOT / 'schema', application / 'schema')
            shutil.copytree(APPLICATION_ROOT / 'extensions', application / 'extensions')
            scripts = application / 'review-dashboard/scripts'
            shutil.copytree(APPLICATION_ROOT / 'review-dashboard/scripts', scripts)
            env = {**os.environ, 'LEDGER_DIR': str(self.root), 'DASHBOARD_DATA_DIR': str(application / 'review-dashboard')}
            for name in ['copy-fuel-log', 'build-dashboard-data', 'build-review-data', 'build-add-patterns', 'compress-dashboard-data']:
                subprocess.run(['node', str(scripts / (name + '.mjs'))], cwd=self.root, env=env,
                               check=True, capture_output=True, text=True)
            data = application / 'review-dashboard/app/data'
            dashboard = json.loads((data / 'dashboard.json').read_text())
            self.assertEqual([row['id'] for row in dashboard['transactions']], ['0000000000000001'])
            self.assertEqual((data / 'fuel-log.json').read_bytes(), (self.root / 'data/fuel-log.json').read_bytes())
            self.assertTrue((application / 'review-dashboard/public/data/dashboard.json.gz').is_file())
            self.assertEqual(json.loads((data / 'add-patterns.json').read_text())['patterns'], {})
            self.assertTrue((application / 'review-dashboard/public/data/add-patterns.json.gz').is_file())

    def test_python_and_node_resolve_relative_environment_from_caller(self):
        env = {**os.environ, 'LEDGER_DIR': '.'}
        python_root = subprocess.check_output([sys.executable, str(APPLICATION_ROOT / 'scripts/project_paths.py')],
                                              cwd=self.root, env=env, text=True).strip()
        module = (APPLICATION_ROOT / 'scripts/project-paths.mjs').as_uri()
        node_root = subprocess.check_output(['node', '--input-type=module', '-e',
                                            f'import {{ ledgerRoot }} from {json.dumps(module)}; console.log(ledgerRoot());'],
                                           cwd=self.root, env=env, text=True).strip()
        self.assertEqual(python_root, str(self.root.resolve()))
        self.assertEqual(node_root, python_root)

    def test_make_exports_absolute_ledger_before_npm_changes_directory(self):
        result = subprocess.run(['make', '--no-print-directory', '-f', str(APPLICATION_ROOT / 'Makefile'),
                                 '-f', '-', 'print-selected-ledger'],
                                input='print-selected-ledger:\n\t@printenv LEDGER_DIR\n',
                                cwd=self.root, env={**os.environ, 'LEDGER_DIR': '.'},
                                text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), str(self.root.resolve()))
