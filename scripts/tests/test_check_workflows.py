"""The offline workflow check passes this repository and catches each kind of mistake."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

APPLICATION = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APPLICATION / 'scripts'))
try:
    import yaml  # noqa: F401  The server environment provides it; make puts it first on PATH.
    import check_workflows
except ImportError:  # pragma: no cover - reported as a skip with its reason
    check_workflows = None


@unittest.skipIf(check_workflows is None, 'PyYAML comes with the server environment: uv sync --locked --directory review-dashboard/server')
class WorkflowCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP'))
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(APPLICATION / '.github', self.root / '.github')

    def edit(self, name, old, new):
        path = self.root / '.github' / name
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1))

    def test_this_repository_passes(self):
        self.assertEqual(check_workflows.check(APPLICATION), [])

    def test_a_missing_split_workflow_is_reported(self):
        (self.root / '.github/workflows/browsers.yml').unlink()
        self.assertIn('browsers.yml: missing; it runs the browser matrix and make test-update', check_workflows.check(self.root))

    def test_mistakes_are_reported(self):
        cases = [
            ('workflows/ci.yml', '    timeout-minutes: 15\n', '', 'job privacy: missing timeout-minutes'),
            ('workflows/ci.yml', 'actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065', 'actions/setup-python', 'is not pinned to a commit SHA'),
            ('workflows/ci.yml', 'actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065', 'actions/setup-python@v5', 'is not pinned to a commit SHA'),
            ('workflows/release.yml', '          fi\n          echo "VERSION_NUMBER', '          echo "VERSION_NUMBER', 'bash -n'),
            ('workflows/ci.yml', 'name: Offline suites and build (${{ matrix.persona }})', 'name: Offline (${{ matrix.person }})', 'matrix.person is not in the job matrix'),
            ('workflows/ci.yml', 'make test-server SERVER_TESTS=${{ matrix.persona', 'make test-server SERVER_TESTS=${{ matrix.engine', 'matrix.engine is not in the job matrix'),
            ('workflows/ci.yml', '      - uses: ./.github/actions/setup\n', '      - uses: ./.github/actions/missing\n', 'local action ./.github/actions/missing has no action.yml'),
            ('workflows/ci.yml', ': > "$list"', 'cp "$HOME/private.tsv" "$list"', 'must use an empty list made in the job'),
            ('workflows/browsers.yml', 'PLAYWRIGHT_VERSION: "1.64.1"', 'PLAYWRIGHT_VERSION: "1.50.0"', 'Playwright must be pinned to 1.64.1'),
            ('workflows/browsers.yml', '    timeout-minutes: 180\n', '    timeout-minutes: 600\n', 'timeout-minutes must be a whole number of minutes from 1 to 360'),
            ('workflows/ci.yml', 'npm --prefix review-dashboard test', 'npm audit && npm --prefix review-dashboard test', 'runs npm audit'),
            ('actions/setup/action.yml', '      shell: bash\n      env:', '      env:', 'composite run steps need a shell'),
            ('workflows/agent-upgrade.yml', '    runs-on: ubuntu-24.04\n', '    runs-on: ubuntu-24.04\n    needs: [nothing]\n', 'needs unknown job nothing'),
        ]
        cases += [
            # The trigger split (owner, 2026-11-07).
            ('workflows/ci.yml', '  push:\n  pull_request:\n', '  pull_request:\n', 'ci.yml: must run on push and pull_request only'),
            ('workflows/ci.yml', '  push:\n  pull_request:\n', '  push:\n    branches: [main]\n  pull_request:\n', 'ci.yml: push must not be filtered'),
            ('workflows/ci.yml', '  push:\n  pull_request:\n', '  push:\n  pull_request:\n  schedule:\n    - cron: "0 0 * * 0"\n', 'ci.yml: must run on push and pull_request only'),
            ('workflows/ci.yml', '        persona: [asha, empty, jordan, sam]', '        persona: [asha, jordan]', 'offline suites must run for every persona'),
            ('workflows/ci.yml', '        run: make test-scanner\n', '        run: make test-scanner && make test-update\n', 'ci.yml: the update test (make test-update) belongs in browsers.yml'),
            ('workflows/ci.yml', '        run: npm --prefix review-dashboard test\n', '        run: npm --prefix review-dashboard test && make browser-tests\n', 'ci.yml: the browser matrix (make browser-tests) belongs in browsers.yml'),
            ('workflows/ci.yml', '        run: make test-scanner\n', '        run: echo skipped\n', 'ci.yml: the scanner and tooling tests (make test-scanner) must run on every push'),
            ('workflows/browsers.yml', '  push:\n    tags:\n      - "v*"\n', '  push:\n', 'browsers.yml: push must be limited to v* tags'),
            ('workflows/browsers.yml', '  push:\n    tags:\n      - "v*"\n', '  push:\n    tags:\n      - "v*"\n  pull_request:\n', 'browsers.yml: must run on v* tags, workflow_dispatch and a schedule only'),
            ('workflows/browsers.yml', '  workflow_dispatch:\n', '', 'browsers.yml: must run on v* tags, workflow_dispatch and a schedule only'),
            ('workflows/browsers.yml', '    - cron: "41 3 * * 0"\n', '    - cron: "weekly"\n', 'browsers.yml: schedule needs a five-field cron'),
            ('workflows/browsers.yml', 'run: ENGINES=${{ matrix.engine }} THEMES=light CAPTURE=0 make browser-tests', 'run: ENGINES=${{ matrix.engine }} CAPTURE=0 make browser-tests', 'CI browser runs use THEMES=light'),
            ('workflows/browsers.yml', '        engine: [chromium, firefox, webkit]', '        engine: [chromium, firefox]', 'browser engine webkit is missing'),
            ('workflows/browsers.yml', '        run: make test-update\n', '        run: echo skipped\n', 'browsers.yml: make test-update is missing'),
            ('workflows/browsers.yml', 'run: BROWSER_PROBE_REQUIRED=1 SCANNER_TESTS', 'run: SCANNER_TESTS', 'the browser cleanup probe must run'),
        ]
        for name, old, new, expected in cases:
            with self.subTest(expected=expected):
                shutil.rmtree(self.root / '.github')
                shutil.copytree(APPLICATION / '.github', self.root / '.github')
                self.edit(name, old, new)
                problems = check_workflows.check(self.root)
                self.assertTrue(any(expected in problem for problem in problems), problems)


if __name__ == '__main__':
    unittest.main()
