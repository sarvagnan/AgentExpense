"""Byte guard for the source checkout; fixture applications remain disposable."""
import hashlib
from pathlib import Path
import subprocess


def snapshot(application):
    result = {}

    def visit(path):
        if path.is_symlink():
            result[str(path.relative_to(application))] = 'link:' + str(path.readlink())
        elif path.is_dir():
            for child in sorted(path.iterdir()):
                visit(child)
        elif path.is_file():
            result[str(path.relative_to(application))] = hashlib.sha256(path.read_bytes()).hexdigest()

    for folder in ('review-dashboard/app/data', 'review-dashboard/public/data',
                   'review-dashboard/dist', 'ledger/data/expenses'):
        visit(application / folder)
    return result


def assert_unchanged(before, application):
    after = snapshot(application)
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    expenses = [name for name in changed if name.startswith('ledger/data/expenses/')
                and len(after.get(name, '')) == 64 and all(c in '0123456789abcdef' for c in after[name])]
    ledger = application / 'ledger'

    def git(*args):
        return subprocess.check_output(['git', '--no-optional-locks', '-C', str(ledger), *args],
                                       stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)

    commit = None
    if expenses:
        try:
            # Reject the enclosing application repository; pin main before reading blobs.
            if Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve() == ledger.resolve():
                commit = git('rev-parse', '--verify', 'refs/heads/main^{commit}').decode().strip()
        except (OSError, subprocess.SubprocessError, ValueError):
            pass  # Missing git, repository or main leaves the byte guard strict.
    unexpected = []
    for name in changed:
        if commit and name in expenses:
            try:
                content = git('show', f'{commit}:{name[len("ledger/"):]}')
                if hashlib.sha256(content).hexdigest() == after[name]:
                    print(f'{name} changed to ledger main {commit[:7]} during the run; not a test write')
                    continue
            except (OSError, subprocess.SubprocessError, ValueError):
                pass  # Unreadable or absent committed files remain failures.
        unexpected.append(name)
    if unexpected:
        raise AssertionError('Test changed protected application/ledger files:\n' + '\n'.join(unexpected))
