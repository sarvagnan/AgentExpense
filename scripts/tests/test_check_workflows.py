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

    def test_mistakes_are_reported(self):
        cases = [
            ('workflows/ci.yml', '    timeout-minutes: 15\n', '', 'job privacy: missing timeout-minutes'),
            ('workflows/ci.yml', 'actions/setup-python@v5', 'actions/setup-python', 'is not pinned to a ref'),
            ('workflows/release.yml', '          fi\n          echo "VERSION_NUMBER', '          echo "VERSION_NUMBER', 'bash -n'),
            ('workflows/ci.yml', 'name: Offline suites and build (${{ matrix.persona }})', 'name: Offline (${{ matrix.person }})', 'matrix.person is not in the job matrix'),
            ('workflows/ci.yml', 'make test-server SERVER_TESTS=${{ matrix.persona', 'make test-server SERVER_TESTS=${{ matrix.engine', 'matrix.engine is not in the job matrix'),
            ('workflows/ci.yml', '      - uses: ./.github/actions/setup\n', '      - uses: ./.github/actions/missing\n', 'local action ./.github/actions/missing has no action.yml'),
            ('workflows/ci.yml', ': > "$list"', 'cp "$HOME/private.tsv" "$list"', 'must use an empty list made in the job'),
            ('workflows/ci.yml', 'PLAYWRIGHT_VERSION: "1.64.1"', 'PLAYWRIGHT_VERSION: "1.50.0"', 'Playwright must be pinned to 1.64.1'),
            ('workflows/ci.yml', 'npm --prefix review-dashboard test', 'npm audit && npm --prefix review-dashboard test', 'runs npm audit'),
            ('actions/setup/action.yml', '      shell: bash\n      env:', '      env:', 'composite run steps need a shell'),
            ('workflows/agent-upgrade.yml', '    runs-on: ubuntu-24.04\n', '    runs-on: ubuntu-24.04\n    needs: [nothing]\n', 'needs unknown job nothing'),
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
