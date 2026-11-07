"""Try AgentExpense on a fictional ledger without setting anything up.

`make demo [PERSONA=asha|jordan|sam|empty] [PORT=8790]` copies the application and
a fixture ledger to a new folder under /tmp, builds the dashboard there and starts
the server on 127.0.0.1 with an offline fake agent: no model runs and nothing
contacts the network. Ctrl-C stops the server and removes the folder. Your own
ledger, dashboard build, local.json and agent sign-ins are never read or changed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

APPLICATION = Path(__file__).resolve().parents[2]
FIXTURES = APPLICATION / 'fixtures/ledgers'
APPLICATION_PARTS = ('scripts', 'schema', 'extensions', 'migrations', 'policy', 'readers', 'templates', 'Makefile', '.node-version', 'VERSION')
DASHBOARD_PARTS = ('app', 'public', 'scripts', 'server', 'index.html', 'package.json', 'package-lock.json',
                   'tsconfig.json', 'vite.config.ts')
SKIPPED = {'.git', '.jj', '.venv', 'node_modules', 'dist', '__pycache__', '.source-ledger.json', 'drafts', '.lock',
           'expense_ledger.ledger'}
POLL = 0.2               # seconds between health requests while waiting
START_SECONDS = 60       # the server answers its health check within this
AGENT_CHECK_SECONDS = 60  # the background agent check settles within this
BASE_PATH = '/expenses/'  # the dashboard build loads its files from here (`base` in vite.config.ts)
DEMO_QUESTION = ('This is the demo: no agent model runs here, so nothing reads your words or files and this item '
                 'stays unfinished. Discard it from the Queue when you are done looking. With your own ledger, the '
                 'agent you choose during setup reads items like this one.')


def personas():
    return sorted(path.name for path in FIXTURES.iterdir() if (path / 'ledger.json').is_file())


def ignore(folder, names):
    skipped = {name for name in names if name in SKIPPED}
    # Never copy this installation's own generated dashboard data into the demo.
    if Path(folder).name in ('app', 'public') and Path(folder).parent.name == 'review-dashboard':
        skipped |= {name for name in names if name == 'data'}
    return skipped


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class Demo:
    def __init__(self, persona, port, out=sys.stdout):
        self.persona, self.out = persona, out
        # Tests choose their own run folder; people always get a new folder in /tmp.
        parent = os.environ.get('AGENTEXPENSE_DEMO_PARENT') or '/tmp'
        self.root = Path(tempfile.mkdtemp(prefix='agentexpense-demo-', dir=parent)).resolve()
        self.app = self.root / 'application'
        self.dashboard = self.app / 'review-dashboard'
        self.ledger = self.root / 'ledger'
        self.port = port or free_port()
        self.server = None

    def say(self, text):
        print(text, file=self.out, flush=True)

    def environment(self):
        inherited = {key: value for key, value in os.environ.items()
                     if not key.startswith(('EXPENSES_', 'DASHBOARD_', 'AGENTEXPENSE_'))
                     and key not in ('LEDGER_DIR', 'LEDGER', 'ADAPTER', 'MAKEFLAGS', 'MFLAGS', 'MAKEOVERRIDES', 'MAKELEVEL')}
        jj_config = self.root / 'jj.toml'
        return {**inherited, 'LEDGER_DIR': str(self.ledger), 'DASHBOARD_DATA_DIR': str(self.dashboard),
                'EXPENSES_STATE_DIR': str(self.root / 'state'), 'EXPENSES_CODEX_HOME': str(self.root / 'agent-home'),
                'EXPENSES_AGENT': 'codex', 'EXPENSES_CODEX': str(self.root / 'fake-agent'),
                'EXPENSES_FAKE_CONFIG': str(self.root / 'fake.json'), 'EXPENSES_DEV': '1',
                'EXPENSES_BASE_PATH': BASE_PATH, 'EXPENSES_PORT': str(self.port), 'EXPENSES_AGENT_TIMEOUT': '30',
                'JJ_CONFIG': str(jj_config), 'PYTHONDONTWRITEBYTECODE': '1', 'UV_OFFLINE': '1',
                'npm_config_update_notifier': 'false', 'npm_config_cache': str(self.root / 'npm-cache'),
                'NODE_COMPILE_CACHE': str(self.root / 'node-cache'), 'TMPDIR': str(self.root / 'tmp')}

    def run(self, command, log, cwd=None):
        with open(self.root / log, 'w') as handle:
            result = subprocess.run(command, cwd=cwd or self.app, env=self.env, stdout=handle, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL)
        if result.returncode:
            tail = (self.root / log).read_text()[-3000:]
            raise RuntimeError(f'{" ".join(map(str, command[:4]))} failed:\n{tail}')

    def stage(self):
        sys.path.insert(0, str(APPLICATION / 'scripts'))
        from dependencies import npm_differences
        if npm_differences(APPLICATION):
            raise RuntimeError('The dashboard packages differ from package-lock.json; run make install first.')
        if npm_differences(APPLICATION) is None:
            raise RuntimeError('The dashboard packages are not installed; run make install first.')
        for name in APPLICATION_PARTS:
            source = APPLICATION / name
            (shutil.copytree(source, self.app / name, ignore=ignore) if source.is_dir()
             else shutil.copy2(source, self.app / name))
        for name in DASHBOARD_PARTS:
            source = APPLICATION / 'review-dashboard' / name
            (shutil.copytree(source, self.dashboard / name, ignore=ignore) if source.is_dir()
             else shutil.copy2(source, self.dashboard / name))
        # Installed packages are linked read-only; nothing is installed or downloaded.
        modules = self.dashboard / 'node_modules'
        modules.mkdir()
        for entry in (APPLICATION / 'review-dashboard/node_modules').iterdir():
            if not entry.name.startswith('.vite'):
                (modules / entry.name).symlink_to(entry)
        shutil.copytree(FIXTURES / self.persona, self.ledger, ignore=ignore)
        (self.ledger / '.gitignore').write_text((APPLICATION / 'templates/ledger/.gitignore').read_text())
        (self.root / 'tmp').mkdir()
        (self.root / 'jj.toml').write_text('[user]\nname = "AgentExpense demo"\nemail = "demo@example.invalid"\n')
        source = (APPLICATION / 'review-dashboard/server/tests/fake_agent.py').read_text().split('\n', 1)[1]
        fake = self.root / 'fake-agent'
        fake.write_text(f'#!{sys.executable}\n' + source)
        fake.chmod(0o755)
        answer = {'status': 'question', 'question': DEMO_QUESTION, 'entry': None, 'new-accounts': [], 'doubts': [],
                  'attachment-names': [], 'explanation': None}
        (self.root / 'fake.json').write_text(json.dumps({'steps': [{'answer': answer}]}))
        self.env = self.environment()

    def build(self):
        if not shutil.which('jj', path=self.env.get('PATH')):
            raise RuntimeError('make demo needs Jujutsu (jj) for its disposable ledger repository; run make doctor.')
        self.run(['jj', 'git', 'init', '--colocate', str(self.ledger)], 'jj.log', cwd=self.root)
        self.run(['jj', '-R', str(self.ledger), 'commit', '-m', 'Fictional demo ledger'], 'jj.log', cwd=self.root)
        self.run(['jj', '-R', str(self.ledger), 'bookmark', 'set', 'main', '-r', '@-'], 'jj.log', cwd=self.root)
        self.run([sys.executable, str(self.app / 'scripts/ledger/generate_ledger.py')], 'journal.log')
        self.run(['npm', '--prefix', str(self.dashboard), 'run', 'data'], 'data.log')
        self.run(['npm', '--prefix', str(self.dashboard), 'run', 'build'], 'build.log')

    def start(self, timeout=START_SECONDS):
        log = open(self.root / 'server.log', 'w')
        self.server = subprocess.Popen([sys.executable, str(self.dashboard / 'server/app.py')],
                                       cwd=self.dashboard / 'server', env=self.env, stdout=log,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        log.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                break
            try:
                with urllib.request.urlopen(self.url + 'api/health', timeout=2) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(POLL)
        raise RuntimeError('The demo server did not start:\n' + (self.root / 'server.log').read_text()[-3000:])

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}{BASE_PATH}'

    def stop(self):
        if self.server and self.server.poll() is None:
            try:
                os.killpg(self.server.pid, signal.SIGTERM)
                self.server.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(self.server.pid, signal.SIGKILL)
                self.server.wait()
            except ProcessLookupError:
                pass
        shutil.rmtree(self.root, ignore_errors=True)


def smoke(demo):
    """Fetch the page and the health check, as `make demo SMOKE=1` does."""
    with urllib.request.urlopen(demo.url, timeout=10) as response:
        page = response.read().decode()
    if response.status != 200 or 'id="root"' not in page:
        raise RuntimeError('The demo page did not load.')
    # The page stays blank unless its scripts and styles load too.
    assets = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', page)
    if not assets:
        raise RuntimeError('The demo page names no scripts or styles.')
    for asset in assets:
        try:
            with urllib.request.urlopen(urllib.parse.urljoin(demo.url, asset), timeout=10) as response:
                if response.status != 200:
                    raise OSError(f'status {response.status}')
        except OSError as exc:
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
            raise RuntimeError(f'The demo page could not load {asset} ({exc}).') from exc
    # The agent check runs in the background after start; wait for its result.
    deadline = time.monotonic() + AGENT_CHECK_SECONDS
    while True:
        with urllib.request.urlopen(demo.url + 'api/health', timeout=10) as response:
            health = json.loads(response.read())
        if health.get('ok') is not True:
            raise RuntimeError('The demo health check failed.')
        if health['agent']['state'] != 'checking' or time.monotonic() > deadline:
            break
        time.sleep(POLL)
    if health['agent']['state'] != 'ready':
        raise RuntimeError(f'The demo agent check did not pass: {health["agent"]}.')
    demo.say(f'Smoke check passed at {demo.url}: page loaded, server healthy, agent {health["agent"]["state"]}.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--persona', default='asha', choices=personas())
    parser.add_argument('--port', type=int)
    parser.add_argument('--smoke', action='store_true', help='start, fetch the page, stop and clean up')
    args = parser.parse_args(argv)
    demo = Demo(args.persona, args.port)
    stopping = threading.Event()
    def request_stop(*_):
        stopping.set()
    for name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
        signal.signal(getattr(signal, name), request_stop)
    try:
        demo.say(f'Preparing the {args.persona} demo in {demo.root} (fictional data; no model, no network)…')
        demo.stage()
        demo.build()
        if stopping.is_set():
            return 0
        demo.start()
        if args.smoke:
            smoke(demo)
            return 0
        demo.say(f'\nAgentExpense demo: {demo.url}\nEvery person, account and amount is invented. Add uses an offline '
                 'fake agent: it reads nothing and only explains that it is a demo.\nPress Ctrl-C to stop and remove the demo.')
        while not stopping.wait(0.5):
            if demo.server.poll() is not None:
                demo.say('The demo server stopped:\n' + (demo.root / 'server.log').read_text()[-3000:])
                return 1
        return 0
    except RuntimeError as exc:
        demo.say(str(exc))
        return 1
    finally:
        demo.stop()
        demo.say(f'Demo stopped; removed {demo.root}.')


if __name__ == '__main__':
    sys.exit(main())
