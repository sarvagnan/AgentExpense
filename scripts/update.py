"""Update AgentExpense to a newer release tag, upgrade the ledger, and roll back on failure.

`make update` (or `make update ARGS="--yes"`). The steps, as docs/RELEASING.md describes:

1. `make doctor`; stop on blocking problems.
2. Refuse when the application checkout has local changes or commits that the release
   doesn't contain. Customisations belong in the ledger or the ignored local.json.
3. Fetch tags from the configured remote, pick the newest release above VERSION
   (pre-releases only with --pre) and show the changelog sections in between.
4. Ask for confirmation unless --yes.
5. Check out the release, detached at its tag (`jj new <tag>` in a jj-colocated clone,
   `git switch --detach <tag>` in a git clone), then reinstall dependencies that differ
   from the release's lockfiles (`make install`).
6. `make doctor` for the release, then `make upgrade-ledger` for the selected ledger.
7. `make update-checks`: versions, ledger verification, the dashboard data and build,
   and the ledger, migration and server tests.
8. Restart the server when local.json names a service, and wait until it stays up.
9. On any failure after checkout: return to the previous checkout, reinstall its
   dependencies, restore the ledger to its checkpoint (op restore or reset --keep),
   rebuild the journal and dashboard, restart a configured service, and say exactly
   what failed and what state everything is in.

This module never contacts anything but the configured git remote, which `git fetch`
reads; dependency installs use the package managers' own sources.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

APPLICATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APPLICATION / 'scripts'))
import local_settings  # noqa: E402
import releases  # noqa: E402

CUSTOMISATIONS = ('Customisations belong in your ledger (its AGENTS.md, rules and settings) or in the '
                  "application's ignored local.json, not in application files.")
JJ = ('jj', '--config', 'snapshot.max-new-file-size=0')


class UpdateError(Exception):
    """A failure to report; nothing after checkout has happened yet unless rollback says so."""


class Refusal(UpdateError):
    """make update refuses before changing anything."""


def output(command, cwd, environ=None, check=True):
    result = subprocess.run(command, cwd=cwd, env=environ, capture_output=True, text=True)
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise UpdateError(f'{" ".join(command[:3])} failed' + (f': {detail[-1]}' if detail else '.'))
    # Keep leading spaces: git status --porcelain columns start with one.
    return result.stdout.rstrip() if check else result


class Checkout:
    """The application clone: plain git, or jj colocated with git."""

    def __init__(self, root):
        self.root = Path(root)
        if not (self.root / '.git').exists():
            raise Refusal('make update needs a git clone or a jj clone colocated with git; '
                          f'{self.root} has no .git.')
        self.jj = (self.root / '.jj').is_dir()

    def git(self, *args, check=True):
        return output(('git', *args), self.root, check=check)

    def jj_run(self, *args):
        return output((*JJ, *args), self.root)

    def local_changes(self):
        if self.jj:
            return self.jj_run('diff', '-r', '@', '--name-only').splitlines()
        return [line[3:] for line in self.git('status', '--porcelain', '--untracked-files=normal').splitlines()]

    def current(self):
        if self.jj:
            parents = self.jj_run('log', '-r', '@-', '--no-graph', '-T', 'commit_id ++ "\\n"').split()
            if len(parents) != 1:
                raise Refusal('The working-copy commit has more than one parent; make update needs a single checkout.')
            return parents[0]
        return self.git('rev-parse', 'HEAD')

    def state(self):
        """Where to return to: the commit, and the git branch when one is checked out."""
        branch = None if self.jj else (self.git('symbolic-ref', '-q', '--short', 'HEAD', check=False).stdout.strip() or None)
        return {'commit': self.current(), 'branch': branch}

    def fetch(self, remote):
        if self.git('remote', 'get-url', remote, check=False).returncode:
            raise Refusal(f'There is no git remote named {remote}. Set updates.remote in local.json or pass --remote.')
        self.git('fetch', '--tags', '--quiet', remote)

    def tags(self):
        return self.git('tag', '--list', 'v*').split()

    def commit_of(self, tag):
        return self.git('rev-parse', '--verify', '--quiet', f'refs/tags/{tag}^{{commit}}')

    def show(self, revision, path):
        result = self.git('show', f'{revision}:{path}', check=False)
        return result.stdout if result.returncode == 0 else None

    def contains(self, ancestor, descendant):
        return self.git('merge-base', '--is-ancestor', ancestor, descendant, check=False).returncode == 0

    def checkout(self, commit):
        if self.jj:
            self.jj_run('new', commit)
        else:
            self.git('-c', 'advice.detachedHead=false', 'switch', '--quiet', '--discard-changes', '--detach', commit)

    def restore(self, state):
        if self.jj:
            self.jj_run('new', state['commit'])
        elif state['branch']:
            self.git('switch', '--quiet', '--discard-changes', state['branch'])
            if self.git('rev-parse', 'HEAD') != state['commit']:
                raise UpdateError(f'branch {state["branch"]} no longer points at {state["commit"][:12]}')
        else:
            self.git('-c', 'advice.detachedHead=false', 'switch', '--quiet', '--discard-changes', '--detach', state['commit'])


class Ledger:
    """The selected ledger repository's checkpoint, recorded before its upgrade."""

    def __init__(self, root):
        self.root = Path(root)
        self.jj = (self.root / '.jj').is_dir()
        if not self.jj and not (self.root / '.git').exists():
            raise Refusal(f'The ledger at {self.root} is not a jj or git repository; '
                          'initialise it (jj git init --colocate) so its upgrade can be rolled back.')

    def run(self, *args):
        return output((*JJ, *args) if self.jj else ('git', *args), self.root)

    def changes(self):
        if self.jj:
            names = self.run('diff', '-r', '@', '--name-only').splitlines()
        else:
            names = [line[3:] for line in self.run('status', '--porcelain', '--untracked-files=all').splitlines()]
        return [name for name in names if name != '.lock']

    def commit(self):
        return self.run('log', '-r', '@-', '--no-graph', '-T', 'commit_id') if self.jj else self.run('rev-parse', 'HEAD')

    def checkpoint(self):
        operation = self.run('op', 'log', '--limit', '1', '--no-graph', '-T', 'id') if self.jj else None
        return {'commit': self.commit(), 'operation': operation}

    def format(self):
        try:
            return json.loads((self.root / 'ledger.json').read_text()).get('format')
        except (OSError, ValueError, AttributeError):
            return 0 if not (self.root / 'ledger.json').exists() else None

    def restore(self, checkpoint):
        if self.jj:
            self.run('op', 'restore', checkpoint['operation'])
        else:
            self.run('reset', '--quiet', '--keep', checkpoint['commit'])


class Service:
    """Restart a configured systemd user unit or launchd agent and wait until it stays up."""

    timeout = 30.0
    interval = 1.0
    stable = 3

    def __init__(self, kind, name, *, run=subprocess.run, clock=time.monotonic, sleep=time.sleep, uid=None):
        self.kind, self.name, self.run, self.clock, self.sleep = kind, name, run, clock, sleep
        self.uid = os.getuid() if uid is None else uid

    def command(self, action):
        if self.kind == 'systemd':
            return ['systemctl', '--user', 'restart' if action == 'restart' else 'is-active', self.name]
        target = f'gui/{self.uid}/{self.name}'
        return ['launchctl', 'kickstart', '-k', target] if action == 'restart' else ['launchctl', 'print', target]

    def status(self):
        """'up', 'starting' or 'failed'."""
        result = self.run(self.command('status'), capture_output=True, text=True)
        if self.kind == 'systemd':
            state = result.stdout.strip()
            return 'up' if state == 'active' else 'starting' if state in ('activating', 'reloading') else 'failed'
        if result.returncode:
            return 'failed'
        return 'up' if any(line.strip() == 'state = running' for line in result.stdout.splitlines()) else 'starting'

    def restart(self):
        result = self.run(self.command('restart'), capture_output=True, text=True)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise UpdateError(f'{self.describe()} did not restart' + (f': {detail[-1]}' if detail else '.'))
        deadline, stable = self.clock() + self.timeout, 0
        while True:
            state = self.status()
            if state == 'failed':
                raise UpdateError(f'{self.describe()} stopped after its restart.')
            stable = stable + 1 if state == 'up' else 0
            if stable >= self.stable:
                return
            if self.clock() >= deadline:
                raise UpdateError(f'{self.describe()} was not running steadily {int(self.timeout)} seconds after its restart.')
            self.sleep(self.interval)

    def describe(self):
        return f'The systemd user unit {self.name}' if self.kind == 'systemd' else f'The launchd agent {self.name}'


class Updater:
    def __init__(self, args, *, application=APPLICATION, environ=None, stdin=None, out=None, service_factory=Service):
        self.args, self.application = args, Path(application)
        self.environ = dict(os.environ if environ is None else environ)
        # Each make below is a run of its own; never pass on make update's own ARGS.
        for name in ('MAKEFLAGS', 'MFLAGS', 'MAKEOVERRIDES', 'MAKELEVEL', 'ARGS'):
            self.environ.pop(name, None)
        self.stdin, self.out = stdin or sys.stdin, out or sys.stdout
        self.service_factory = service_factory

    def say(self, text=''):
        print(text, file=self.out, flush=True)

    def make(self, *targets, step):
        self.say(f'\n==> {step}: make {" ".join(targets)}')
        result = subprocess.run(['make', '--no-print-directory', *targets], cwd=self.application, env=self.environ)
        if result.returncode:
            raise UpdateError(f'{step} failed (make {" ".join(targets)} exited with status {result.returncode}).')

    def ledger_root(self):
        configured = self.environ.get('LEDGER_DIR')
        default = self.application / 'ledger'
        root = Path(configured).expanduser().resolve() if configured else default
        if root.resolve() == default.resolve() and not default.exists():
            return None
        if not root.is_dir():
            raise Refusal(f'LEDGER_DIR {root} is not a folder. Select your ledger, or unset LEDGER_DIR.')
        return root

    def confirm(self, current, target):
        if self.args.yes:
            return True
        self.say(f'\nUpdate AgentExpense {current} to {target}? [y/N] ')
        answer = self.stdin.readline()
        return answer.strip().lower() in ('y', 'yes')

    def choose(self, app, current):
        tags = app.tags()
        if self.args.to:
            target = releases.parse(self.args.to)
            if not target:
                raise Refusal(f'{self.args.to} is not a release version (vMAJOR.MINOR.PATCH).')
            if target.tag not in tags:
                raise Refusal(f'There is no release tag {target.tag} on the remote.')
            if target < current:
                raise Refusal(f'{target.tag} is older than the installed {current}; make update never downgrades. '
                              'To go back, follow "Going back" in docs/RELEASING.md.')
            if target == current:
                raise Refusal(f'AgentExpense {current} is already installed.')
            return target
        found = releases.newer(tags, current, pre=self.args.pre)
        return found[0] if found else None

    def run(self):
        self.say('==> Checking prerequisites: make doctor')
        if subprocess.run(['make', '--no-print-directory', 'doctor'], cwd=self.application, env=self.environ).returncode:
            raise Refusal('make doctor found blocking problems. Fix the rows marked FAIL, then run make update again.')
        app = Checkout(self.application)
        changes = app.local_changes()
        if changes:
            listed = ', '.join(changes[:5]) + (f' and {len(changes) - 5} more' if len(changes) > 5 else '')
            raise Refusal(f'The application checkout has local changes ({listed}). make update needs a clean checkout. '
                          + CUSTOMISATIONS + ' Commit them elsewhere or discard them, then run make update again.')
        local = local_settings.read_local(self.application)
        remote = self.args.remote or local_settings.update_settings(local)['remote']
        service = local_settings.service_settings(local)
        current = releases.Version((self.application / 'VERSION').read_text())
        self.say(f'Installed: AgentExpense {current}. Fetching release tags from {remote}.')
        app.fetch(remote)
        target = self.choose(app, current)
        if not target:
            latest = releases.releases(app.tags(), pre=self.args.pre)
            self.say(f'AgentExpense {current} is up to date' + (f' (latest release {latest[0].tag}).' if latest else '.'))
            return 0
        commit = app.commit_of(target.tag)
        base = app.current()
        if not app.contains(base, commit):
            raise Refusal(f'This checkout ({base[:12]}) has commits that {target.tag} does not contain, so updating would drop them. '
                          + CUSTOMISATIONS)
        tagged = (app.show(commit, 'VERSION') or '').strip()
        if tagged != str(target):
            raise Refusal(f'{target.tag} contains VERSION {tagged or "(none)"}, not {target}; the release is malformed.')
        self.check_node(app, commit, target)
        root = self.ledger_root()
        ledger = Ledger(root) if root else None
        if ledger and ledger.changes():
            raise Refusal(f'The ledger at {root} has uncommitted changes. Commit or discard them (or wait for the '
                          'server to finish saving), then run make update again.')
        notes = releases.changelog_sections(app.show(commit, 'CHANGELOG.md') or '', current, target)
        self.say(f'\nChanges from {current} to {target}:\n\n' + (notes or '(The changelog has no sections for these versions.)'))
        if not self.confirm(current, target):
            self.say('Update cancelled; nothing changed.')
            return 1
        previous = app.state()
        checkpoint = ledger.checkpoint() if ledger else None
        if ledger:
            self.say(f'Ledger checkpoint: {checkpoint["commit"]}')
        self.say(f'\n==> Checking out {target.tag}')
        app.checkout(commit)
        progress = {'step': f'Checking out {target.tag}', 'upgraded': None}
        try:
            progress['step'] = 'Installing dependencies'
            self.make('install', step=progress['step'])
            progress['step'] = f'Checking prerequisites for {target}'
            self.make('doctor', step=progress['step'])
            if ledger:
                progress['step'] = 'Upgrading the ledger'
                self.make('upgrade-ledger', step=progress['step'])
                progress['upgraded'] = ledger.commit()
            progress['step'] = 'Verifying, rebuilding and testing'
            self.make('update-checks', step=progress['step'])
            if service:
                progress['step'] = 'Restarting the server'
                self.say(f'\n==> {progress["step"]}')
                self.service_factory(*service).restart()
        except BaseException as exc:  # Including Ctrl-C: never leave a half-updated installation.
            reason = str(exc) if isinstance(exc, UpdateError) else ('Interrupted.' if isinstance(exc, KeyboardInterrupt) else f'{progress["step"]} failed: {exc}')
            self.rollback(app, previous, ledger, checkpoint, progress, service, current, target, reason)
            return 1
        format_line = f' Ledger format {ledger.format()}.' if ledger else ''
        self.say(f'\nAgentExpense updated from {current} to {target}.{format_line}'
                 + (' The server was restarted.' if service else ''))
        return 0

    def check_node(self, app, commit, target):
        pin = (app.show(commit, '.node-version') or '').strip()
        fnm = shutil.which('fnm', path=self.environ.get('PATH'))
        if not pin or not fnm:
            return
        listed = subprocess.run([fnm, 'list'], env=self.environ, capture_output=True, text=True).stdout
        if f'v{pin}' not in listed.split():
            raise Refusal(f'{target.tag} needs Node {pin}. Run fnm install {pin}, then make update again.')

    def rollback(self, app, previous, ledger, checkpoint, progress, service, current, target, reason):
        self.say(f'\n==> Update to {target} failed during "{progress["step"]}". Rolling back.')
        done, problems = [], []

        def attempt(description, action):
            try:
                action()
                done.append(description)
            except BaseException as exc:  # Keep going: report every part that could not be restored.
                problems.append(f'{description}: {exc}')

        def restore_ledger():
            from ledger.ledger_lock import ledger_lock
            now = ledger.commit()
            if now == checkpoint['commit']:
                return
            if now != progress['upgraded'] or ledger.changes():
                raise UpdateError(f'the ledger moved on to {now[:12]} after its upgrade, so it was left there; '
                                  f'its checkpoint is {checkpoint["commit"][:12]}')
            with ledger_lock(ledger.root):
                ledger.restore(checkpoint)

        attempt(f'application returned to {current} ({previous["commit"][:12]}'
                + (f', branch {previous["branch"]})' if previous['branch'] else ')'), lambda: app.restore(previous))
        if not problems:
            attempt(f'dependencies reinstalled for {current} where they differed', lambda: self.make('install', step='Reinstalling dependencies'))
            if ledger:
                attempt(f'ledger restored to its checkpoint {checkpoint["commit"][:12]}', restore_ledger)
                attempt('journal and dashboard rebuilt', lambda: self.make('ledger', 'build-dashboard', step='Rebuilding'))
            if service:
                attempt('server restarted on the previous release', lambda: self.service_factory(*service).restart())
        self.say(f'\nUpdate failed: {reason}')
        self.say(f'Nothing from {target} remains installed.' if not problems else
                 'Rollback was incomplete; see below and docs/RELEASING.md ("Going back").')
        for item in done:
            self.say(f'  restored: {item}')
        for item in problems:
            self.say(f'  NOT restored: {item}')
        if ledger and ledger.format() is not None:
            self.say(f'  ledger: format {ledger.format()} at {ledger.commit()[:12]}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Update AgentExpense to the newest release; see docs/RELEASING.md.')
    parser.add_argument('--yes', action='store_true', help='skip the confirmation prompt')
    parser.add_argument('--pre', action='store_true', help='include pre-release tags such as v0.3.0-rc.1')
    parser.add_argument('--remote', help='git remote to read tags from (default: updates.remote in local.json, else origin)')
    parser.add_argument('--to', help='update to this release tag instead of the newest one (never older)')
    args = parser.parse_args(argv)
    try:
        return Updater(args).run()
    except KeyboardInterrupt:
        print('\nmake update: cancelled before anything changed.', file=sys.stderr, flush=True)
        return 1
    except (Refusal, UpdateError, ValueError) as exc:
        print(f'make update: {exc}', file=sys.stderr, flush=True)
        return 2 if isinstance(exc, Refusal) else 1


if __name__ == '__main__':
    sys.exit(main())
