"""Run the existing headless matrix in isolated, disposable Incus containers.

Only the standard library is needed on the operator's machine. No client settings,
host dependencies, application assertions or financial documents are changed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import signal
import stat
import subprocess
import sys
import tarfile
import threading
import time
import uuid

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from browser_box_lifecycle import (ControllerState, host_identity, process_identity,
                                   owner_record, owner_status, spawn_controller)
import client_watch
MAX_JOBS = 2
MAX_UNEXPLAINED_STATUS_ERRORS = 3
COLLECTION_GRACE_SECONDS = 600
CANCELLED = threading.Event()
CONTROL_FOLDER = None
STOP_DEADLINE = None


def is_stopping():
    global STOP_DEADLINE
    if CONTROL_FOLDER and (CONTROL_FOLDER/'stop.requested').exists():
        CANCELLED.set()
    if CANCELLED.is_set() and STOP_DEADLINE is None:
        STOP_DEADLINE = time.monotonic()+45
    return CANCELLED.is_set()
PINS = {"fnm": "1.39.0", "uv": "0.13.10", "python": "3.15.2",
        "hledger": "1.52.4", "jj": "0.47.1", "playwright": "1.64.1",
        "fnm_sha256":"7807664f39d39fc518da1c35ba0181e4b3267603c4b1dedeb4b5fc6ae440a224",  # pragma: allowlist secret (public release checksum)
        "uv_sha256":"bf65ac1a0af10f92c522d91ac096c7c0a39657647b155577e7836c2cbbb4526c",  # pragma: allowlist secret (public release checksum)
        "jj_sha256":"d4b657f622ee50929c8916466dfa3d6a6c17a0a32b50e7b2c793707ec9ebcad7"}  # pragma: allowlist secret (public release checksum)
LOCKS = (".node-version", "review-dashboard/package-lock.json", "review-dashboard/server/.python-version",
         "review-dashboard/server/uv.lock")
IMAGE_HASH_INPUTS = (*LOCKS, "scripts/node_toolchain.py", "scripts/project_paths.py")
BUILD_INPUTS = (*IMAGE_HASH_INPUTS, "review-dashboard/package.json", "review-dashboard/server/pyproject.toml")
COLLECT_SCRIPT = """from pathlib import Path
import fcntl
import io
import json
import os
import shutil
import tarfile
import time
root = Path('/tmp/browser-box')
root.mkdir(parents=True, exist_ok=True)
lock=(root/'collect.lock').open('a')
fcntl.flock(lock,fcntl.LOCK_EX)
destination=Path('/opt/browser-box/results.tar.gz')
if destination.exists():
    raise SystemExit(0)
shutil.copyfile('/opt/browser-box/versions.json', root/'versions.json')
if not (root/'exit').exists():
    (root/'exit').write_text('125\\n')
budget=os.environ.get('BROWSER_BOX_COLLECTION_SECONDS')
deadline=(time.monotonic()+float(budget)) if budget else float((root/'collection.deadline').read_text())
stop_deadline=None
files=[]
names=('run.log','exit','versions.json','results.json','server.log','build.log','data.log','status.json','supervisor.log')
for folder, directories, entries in os.walk(root,followlinks=False):
    directories[:]=[name for name in directories if name not in ('application','node_modules','.git','.jj','oracles','npm-cache','node-cache','uv-cache') and not (Path(folder)/name).is_symlink()]
    for name in entries:
        path=Path(folder)/name
        if path.is_symlink() or not path.is_file():continue
        relative=path.relative_to(root)
        if name in names:priority=0
        elif name.startswith('failure-') and path.suffix=='.zip':priority=1
        elif 'traces' in relative.parts:priority=2
        elif relative.parts[0]=='shots':priority=3
        else:continue
        files.append((priority,str(relative),path))
temporary=destination.with_suffix('.next')
skipped=[]
with tarfile.open(temporary,'w:gz',compresslevel=1) as archive:
    for _,relative,path in sorted(files):
        if (root/'stop.requested').exists() and stop_deadline is None:stop_deadline=time.monotonic()+10
        if time.monotonic()>=min(deadline,stop_deadline or deadline):
            skipped.append(relative);continue
        try:archive.add(path,arcname=relative,recursive=False)
        except FileNotFoundError:pass
    payload=(json.dumps({'skipped':skipped,'partial':bool(skipped)})+'\\n').encode()
    metadata=tarfile.TarInfo('collection.json');metadata.size=len(payload)
    archive.addfile(metadata,io.BytesIO(payload))
temporary.replace(destination)
"""


class BoxError(RuntimeError):
    pass


class Stopped(BoxError):
    pass


class Infrastructure(BoxError):
    pass


class ConnectionLost(Infrastructure):
    pass


class PermanentError(Infrastructure):
    pass


class StatusUnavailable(Infrastructure):
    pass


def permanent_error(detail):
    return bool(re.search(r'not authori[sz]ed|unauthori[sz]ed|forbidden|permission denied|not trusted|'
                          r'certificate[^\n]*(?:revoked|invalid|expired)|trust[^\n]*revoked|'
                          r'project[^\n]*(?:not found|does not exist)|HTTP (?:401|403)\b',detail,re.I))


def dns_error(detail):
    return bool(re.search(r'no such host|temporary failure in name resolution|server misbehaving|'
                          r'\blookup\s+\S+[^\n]*(?:timeout|timed out|deadline exceeded)',detail,re.I))


def safe_error(detail,remote=''):
    if remote:detail=detail.replace(remote+':','<remote>:')
    detail=re.sub(r'(?i)(lookup\s+|connect to:\s+|dial tcp\s+|read tcp\s+)\S+',r'\1<endpoint>',detail)
    return re.sub(r'https?://[^\s/\"\)]+','<endpoint>',detail)


def transport_kind(detail):
    if dns_error(detail):return 'dns'
    if re.search(r'operation not found',detail,re.I):return 'lost-operation'
    if re.search(r'independent local client deadline|Incus client request timed out',detail,re.I):return 'client-deadline'
    if re.search(r'context cancel+ed',detail,re.I):return 'context-cancelled'
    if re.search(r'lost connection to the event listener',detail,re.I):return 'lost-event-listener'
    if 'i/o timeout' in detail.lower():return 'io-timeout'
    return 'transport'


def transient_error(detail, *, connected=False, operation=None):
    # A wrong remote must fail fast; a lookup failure after a verified response
    # is a dropped connection, not evidence that the guest disappeared.
    if permanent_error(detail):return False
    if dns_error(detail):return connected
    if operation in ('exec','file') and re.search(r'\bOperation not found\b',detail,re.I):return True
    return bool(re.search(r'i/o timeout|context deadline exceeded|context cancel+ed|connection timed out|connection (?:refused|reset|closed)|lost connection to the event listener|'
                          r'network is unreachable|no route to host|TLS handshake timeout|unexpected EOF|'
                          r'HTTP (?:502|503|504)\b', detail, re.I))


@dataclass(frozen=True)
class Case:
    persona: str
    engine: str
    theme: str
    width: int

    @property
    def name(self):
        return f"{self.persona}-{self.engine}-{self.theme}-{self.width}"


def parse_case(value):
    parts = value.split(":")
    if (len(parts) != 4 or parts[0] not in ("asha", "sam", "jordan", "empty")
            or parts[1] not in ("chromium", "firefox", "webkit")
            or parts[2] not in ("light", "dark") or parts[3] not in ("1440", "412")):
        raise argparse.ArgumentTypeError("case must be persona:engine:light|dark:1440|412")
    return Case(*parts[:3], int(parts[3]))


def jobs_value(value):
    try:
        jobs = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("jobs must be 1 or 2") from None
    if not 1 <= jobs <= MAX_JOBS:
        raise argparse.ArgumentTypeError("jobs must be 1 or 2 (project maximum)")
    return jobs


def check_path(name, *, tracked=False):
    path = PurePosixPath(name)
    parts = path.parts
    if not parts or path.is_absolute() or ".." in parts or "\\" in name:
        raise BoxError(f"Refusing unsafe source path: {name!r}")
    if (parts[0].lower() in ("ledger", "tmp", "local.json")
            or any(p.lower() in (".git", ".jj", ".codex", ".claude", "auth.json", ".ssh", ".aws",
                                  ".npmrc", ".pypirc", ".netrc", ".git-credentials", ".config") for p in parts)
            or any(p == ".env" or p.startswith(".env.") for p in parts)
            or path.suffix.lower() in (".key", ".pem", ".p12", ".pfx")
            or path.name.lower() in ("id_rsa", "id_ed25519", "credentials.json", "token.json")):
        raise BoxError(f"Refusing private source path: {name!r}")
    fixture = len(parts) >= 4 and parts[:2] == ("fixtures", "ledgers")
    template = parts[:2] == ("templates", "ledger")
    lower=tuple(part.lower() for part in parts)
    ledger_stores={'accounts.json','card-statements.json','forecast.json','recurring-settings.json','fuel-log.json',
                   'bank-statements.json','balances.json','prices.json','trash.json'}
    private_fragments=(any(part in ('bills','drafts') for part in lower)
                       or any(lower[index]=='data' and index+1<len(lower) and
                              lower[index+1] in ('additions','expenses','prices','trash-files') for index in range(len(lower)))
                       or path.name.lower() in ledger_stores)
    if not fixture and not template and (private_fragments or
            path.name.lower() == "ledger.json" or path.suffix.lower() in (".ledger", ".journal", ".prices")
            or "data/expenses/" in name or ("/" + name).endswith("/data/accounts.json")):
        raise BoxError(f"Refusing a ledger outside fixtures/ledgers/: {name!r}")
    attachments={'.pdf','.png','.jpg','.jpeg','.gif','.webp','.avif','.heic','.tif','.tiff','.bmp','.svg',
                 '.xls','.xlsx','.xlsm','.xlsb','.ods','.csv','.tsv'}
    asset=parts[:2] in (('review-dashboard','public'),('fixtures','statements')) or fixture
    if not tracked and not asset and path.suffix.lower() in attachments:
        raise BoxError(f'Refusing untracked document/image outside application assets and fixtures: {name!r}')


def source_files(root):
    """Fail closed before packaging; never dereference a checkout's symlinks."""
    root = root.resolve()
    if any(part.lower() in (".codex", ".claude", ".ssh", ".aws") for part in root.parts):
        raise BoxError("Refusing an agent or credential directory as a source checkout")
    top = subprocess.check_output(["git", "-C", str(root), "rev-parse", "--show-toplevel"], text=True).strip()
    if Path(top).resolve() != root:
        raise BoxError("--checkout must be the Git checkout root")
    raw = subprocess.check_output(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-t", "-z"])
    listed={}
    for item in raw.split(b'\0'):
        if not item:continue
        tagged=len(item)>2 and item[1:2]==b' ' and item[:1] in (b'H',b'?')
        name=os.fsdecode(item[2:] if tagged else item)
        listed[name]=listed.get(name,False) or (tagged and item[:1]==b'H')
    names=sorted(listed)
    files = []
    for name in names:
        check_path(name,tracked=listed[name])
        path = root / name
        # Check parents too: an untracked directory link must not export its target.
        for parent in (path, *path.parents):
            if parent == root:
                break
            if parent.is_symlink():
                raise BoxError(f"Refusing symlink in source: {name!r}")
        if not path.exists():  # tracked deletion in a dirty working copy
            continue
        if not stat.S_ISREG(path.stat().st_mode):
            raise BoxError(f"Refusing non-file source: {name!r}")
        if name.startswith("templates/ledger/"):
            # Only the maintained inert skeleton is an exception to the ledger ban.
            template_names = {".gitignore", "AGENTS.md", "README.md", "checks/README.md",
                              "data/accounts.json", "data/card-statements.json", "data/forecast.json",
                              "extensions/README.md", "ledger.json", "readers/README.md"}
            if name.removeprefix("templates/ledger/") not in template_names:
                raise BoxError("Refusing additional files in the ledger template")
            if name.endswith("/ledger.json") and "{{id}}" not in path.read_text():
                raise BoxError("Refusing a populated ledger template")
            if name.endswith("/data/accounts.json"):
                document = json.loads(path.read_text())
                if document.get("accounts") or document.get("commodities") != ["{{currency}}"]:
                    raise BoxError("Refusing populated template accounts")
            if name.endswith(("/data/card-statements.json", "/data/forecast.json")) and json.loads(path.read_text()) != []:
                raise BoxError("Refusing populated template data")
        files.append(name)
    return files


def pack_source(root, destination):
    root = root.resolve()
    names = source_files(root)
    with tarfile.open(destination, "w:gz", dereference=False) as archive:
        for name in names:
            # Recheck at the point of reading; links are never valid source inputs.
            path = root / name
            if path.is_symlink() or path.resolve() != root / name:
                raise BoxError(f"Source changed while packaging: {name!r}")
            archive.add(path, arcname=name, recursive=False)
    return names


def image_hash(root):
    digest = hashlib.sha256()
    for name in IMAGE_HASH_INPUTS:
        digest.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    # Installer and pins are inputs too; an installer fix must not reuse a stale image.
    digest.update(json.dumps(PINS, sort_keys=True).encode())
    installer = root / "browser-box-build.sh"
    digest.update((installer if installer.exists() else TOOLS / "build.sh").read_bytes())
    return digest.hexdigest()[:24]


def snapshot_build_inputs(bundle, destination):
    """Image and runs use the same immutable lock bytes even if the checkout changes."""
    destination.mkdir()
    with tarfile.open(bundle) as archive:
        for name in BUILD_INPUTS:
            source = archive.extractfile(name)
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
    (destination / "browser-box-build.sh").write_bytes((TOOLS / "build.sh").read_bytes())
    return destination


def json_write(path, value):
    temporary = path.with_suffix(path.suffix + ".next")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


class Incus:
    def __init__(self, remote, project):
        for label, value in (("remote", remote), ("project", project)):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
                raise BoxError(f"Invalid {label} name")
        self.remote, self.project = remote, project
        self.owner = uuid.uuid4().hex
        self.controller = owner_record(self.owner)
        self.state = None
        self.context = threading.local()
        self.reconnect_timeout = 120
        self.pending_cleanup = set()
        self.deferred_cleanup = set()
        self.pending_lock = threading.Lock()
        self.had_launch = False
        self.remote_answered=threading.Event()
        self.client_registry=Path(os.environ.get('BROWSER_BOX_CLIENT_REGISTRY','/tmp/agentexpense-browser-box/client-registry'))

    @contextmanager
    def budget(self, deadline, *, reconnect=False,phase=None):
        previous=getattr(self.context,'deadline',None)
        previous_reconnect=getattr(self.context,'reconnect_until_deadline',False)
        previous_phase=getattr(self.context,'phase',None)
        self.context.deadline=deadline
        self.context.reconnect_until_deadline=reconnect
        self.context.phase=phase
        try:yield
        finally:
            self.context.deadline=previous
            self.context.reconnect_until_deadline=previous_reconnect
            self.context.phase=previous_phase

    def ref(self, name=""):
        return f"{self.remote}:{name}"

    def call(self, *args, check=True, log=None, timeout=None, allow_cancel=True, input_text=None, retry=True):
        backoff=1
        reconnect_end=None
        last_error=None
        # Every healthy operation, including transfers, image use and supervisor
        # start, may use its enclosing phase budget. No guessed RPC duration.
        end=getattr(self.context,'deadline',None)
        if timeout is not None:
            end=min(end or float('inf'),time.monotonic()+timeout)
        if end is None:end=time.monotonic()+7200
        while True:
            if allow_cancel and is_stopping():raise Stopped('Cancelled: stopped by operator')
            if not allow_cancel and is_stopping():end=min(end or STOP_DEADLINE,STOP_DEADLINE)
            remaining=end-time.monotonic()
            if reconnect_end is not None and time.monotonic()>=reconnect_end:
                raise ConnectionLost('Reconnect budget exhausted'+(': '+last_error if last_error else ''))
            if remaining<=0:raise Infrastructure('Incus operation exceeded its time budget')
            try:
                # The reconnect window limits starting another retry. Once a
                # retry connects, a healthy transfer can outlast that window.
                result=self._call_once(*args,check=False,log=log,timeout=remaining,
                                       allow_cancel=allow_cancel,input_text=input_text)
                detail='\n'.join(x for x in (result.stdout,result.stderr) if x)
                if result.returncode==0:self.remote_answered.set()
                if result.returncode and dns_error(detail) and not self.remote_answered.is_set():
                    raise PermanentError('Configured remote DNS lookup failed before the first successful response; no changes made: '+safe_error(detail,self.remote))
                if result.returncode and transient_error(detail,connected=self.remote_answered.is_set(),operation=args[0]):
                    raise ConnectionLost(safe_error(detail,self.remote))
                if check and result.returncode:
                    error=PermanentError if permanent_error(detail) else Infrastructure
                    raise error('Incus API operation failed: '+safe_error(detail,self.remote))
                return result
            except ConnectionLost as exc:
                if not retry:raise
                last_error=safe_error(str(exc),self.remote)
                if reconnect_end is None:
                    reconnect_end=end if getattr(self.context,'reconnect_until_deadline',False) else time.monotonic()+self.reconnect_timeout
                until=min(reconnect_end,end or reconnect_end)
                delay=min(backoff,max(0,until-time.monotonic()))
                if delay<=0:raise ConnectionLost('Reconnect budget exhausted: '+last_error) from exc
                print('RECONNECT '+json.dumps({'delay':round(delay,3),'operation':str(args[0]),
                    'kind':transport_kind(str(exc)),'error':last_error[:1000],'phase':getattr(self.context,'phase',None),
                    'remainingSeconds':round(max(0,end-time.monotonic()),3),
                    'windowRemainingSeconds':round(max(0,until-time.monotonic()),3)}),flush=True)
                if log:
                    with Path(log).open('a') as handle:handle.write('RECONNECT '+last_error+'\n')
                if allow_cancel:
                    if CANCELLED.wait(delay):raise Stopped('Cancelled during reconnect')
                else:threading.Event().wait(delay)
                backoff=min(backoff*2,8)

    def _call_once(self, *args, check=True, log=None, timeout=120, allow_cancel=True, input_text=None):
        # Global flags must precede exec's '--', otherwise they reach the guest.
        command = ["incus", "--project", self.project, *map(str, args)]
        output = None
        pull_target=Path(args[-1]) if args[:2]==('file','pull') else None
        incoming=pull_target.with_name(pull_target.name+'.incoming') if pull_target else None
        completed=False
        def retain_pull():
            if incoming is None or not incoming.exists():return
            if completed or not pull_target.exists() or incoming.stat().st_size>=pull_target.stat().st_size:
                incoming.replace(pull_target)
            else:
                incoming.unlink()
        try:
            if allow_cancel and is_stopping():
                raise Stopped("Cancelled: stopped by operator")
            if not allow_cancel and is_stopping():
                timeout = min(timeout,max(.1,STOP_DEADLINE-time.monotonic()))
            if incoming is not None:
                # A restarted CLI truncates its destination. Keep the largest
                # prefix separately until a complete transfer replaces it.
                retain_pull()
                command[-1]=str(incoming)
            if log is not None:
                output = Path(log).open("a")
                output.write("$ " + safe_error(shlex.join(command),self.remote) + "\n")
                output.flush()
                log_start=output.tell()
            self.client_registry=client_watch.secure_registry(self.client_registry)
            watch=[sys.executable,'-B',str(TOOLS/'client_watch.py'),'--record',
                   str(self.client_registry/(uuid.uuid4().hex+'.json')),'--parent',json.dumps(self.controller),
                   '--deadline',str(time.monotonic()+timeout),'--',*command]
            child = subprocess.Popen(watch, text=True, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                     stdout=output or subprocess.PIPE,
                                     stderr=subprocess.STDOUT if output else subprocess.PIPE)
            deadline = time.monotonic() + timeout
            first_input = input_text
            try:
                while True:
                    if allow_cancel and is_stopping():
                        raise Stopped("Cancelled: stopped by operator")
                    if not allow_cancel and is_stopping():
                        deadline=min(deadline,STOP_DEADLINE)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ConnectionLost(f"Incus client request timed out after {timeout}s: {shlex.join(command)}")
                    try:
                        options = {"timeout": min(1, remaining)}
                        if first_input is not None:
                            options["input"] = first_input
                        stdout, stderr = child.communicate(**options)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                    finally:
                        first_input = None
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
            result = subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
            completed=child.returncode==0
            if log:
                output.flush()
                # Classify this invocation only; an older transport error in
                # the same log must not hide a new auth/config/missing error.
                with Path(log).open('rb') as handle:
                    handle.seek(max(log_start,handle.seek(0,2)-32768))
                    result.stdout=handle.read().decode(errors='replace')
        except OSError as exc:
            raise BoxError(f"Incus command failed: {shlex.join(command)}: {exc}") from exc
        finally:
            if output:
                output.close()
            retain_pull()
        if check and result.returncode:
            raise BoxError(f"Incus command exited {result.returncode}: {shlex.join(command)}\n"
                           + (f"See {log}" if log else result.stderr))
        return result

    def ensure_project(self):
        result = self.call("project", "show", self.ref(self.project), check=False)
        limits = {"features.images": "true", "features.profiles": "true",
                  "limits.cpu": "4", "limits.memory": "6GiB", "limits.containers": "2"}
        if result.returncode:
            if not re.search(r"(not found|does not exist)", result.stderr, re.I):
                raise BoxError("Remote/project unreachable; no changes made.\n" + result.stderr)
            self.call("project", "create", self.ref(self.project),
                      *[arg for key, value in limits.items() for arg in ("-c", f"{key}={value}")])
        else:
            # Read individual values without enumerating any other project.
            for key, value in limits.items():
                existing = self.call("project", "get", self.ref(self.project), key).stdout.strip()
                if existing != value:
                    raise BoxError(f"Existing project must have {key}={value}; found {existing!r}. "
                                   "Refusing to change an existing project's limits or features.")

    def instances(self, name, *, allow_cancel=True):
        records = json.loads(self.call("list", self.ref(), "^" + re.escape(name) + "$", "--format=json",
                                       allow_cancel=allow_cancel).stdout)
        return [record for record in records if record["name"] == name]

    def project_instances(self):
        return json.loads(self.call('list', self.ref(), '--format=json').stdout)

    def reap_stale(self):
        report = []
        for instance in self.project_instances():
            config = instance.get('config', {})
            token = config.get('user.browser-box.owner')
            raw = config.get('user.browser-box.controller')
            try:
                record = json.loads(raw) if raw else {}
                status = owner_status(record) if token and record.get('token') == token else 'unknown'
            except (TypeError, ValueError, AttributeError):
                status = 'unknown'
            item = {'instance':instance['name'], 'ownerStatus':status, 'removed':False}
            print('INSTANCE_OWNER '+json.dumps(item), flush=True)
            if status == 'dead':
                self.remove_stale(instance['name'], token, raw)
                item['removed'] = True
            report.append(item)
        return report

    def remove_stale(self, name, token, controller):
        def checked():
            rows = self.instances(name)
            if not rows:
                return None
            config = rows[0].get('config', {})
            if (config.get('user.browser-box.owner') != token
                    or config.get('user.browser-box.controller') != controller
                    or owner_status(json.loads(controller)) != 'dead'):
                raise BoxError('Stale owner changed or is uncertain; preserving '+name)
            return rows[0]
        instance = checked()
        if not instance:
            return
        if instance['status'].lower() != 'stopped':
            self.call('stop', self.ref(name), '--force', allow_cancel=False,check=False)
        if checked():
            self.call('delete', self.ref(name), allow_cancel=False,check=False)
        if self.instances(name):
            raise BoxError('Stale instance still exists: '+name)

    def prune_images(self, current):
        if self.project_instances():
            raise BoxError('Image prune requires an idle project; instances were preserved')
        images = json.loads(self.call('image','list',self.ref(),'browser-box-','--format=json').stdout)
        report = []
        for info in images:
            aliases = [item['name'] for item in info.get('aliases', [])]
            provenance = info.get('properties', {}).get('user.browser-box.hash')
            managed = bool(info.get('fingerprint')) and bool(aliases) and all(re.fullmatch(r'browser-box-[0-9a-f]{24}', a)
                                           and a == 'browser-box-'+str(provenance) for a in aliases)
            for alias in aliases:
                if not alias.startswith('browser-box-'):
                    continue
                item = {'alias':alias,'removed':False,'reason':'current' if current in aliases else 'unknown/shared provenance'}
                if managed and current not in aliases:
                    # Re-list the exact alias and refuse changes or concurrent use.
                    rows = json.loads(self.call('image','list',self.ref(),alias,'--format=json').stdout)
                    exact = [row for row in rows if row.get('fingerprint') == info.get('fingerprint')
                             and row.get('aliases') == info.get('aliases') and row.get('properties') == info.get('properties')]
                    if len(exact) != 1 or self.project_instances():
                        raise BoxError('Image or project changed during prune; preserving '+alias)
                    print('PRUNE_IMAGE '+alias, flush=True)
                    self.call('image','delete',self.ref(alias))
                    item.update(removed=True,reason='obsolete runner image')
                print('IMAGE_ALIAS '+json.dumps(item),flush=True)
                report.append(item)
        return report

    def owned_instance(self, name, *, allow_cancel=True):
        records = self.instances(name,allow_cancel=allow_cancel)
        if not records:
            return None
        instance = records[0]
        if instance.get("config", {}).get("user.browser-box.owner") != self.owner:
            raise BoxError(f"Refusing to touch instance not created by this invocation: {name}")
        return instance

    def cleanup(self, name, ephemeral):
        instance = self.owned_instance(name,allow_cancel=False)  # list before every exact removal
        if not instance:
            with self.pending_lock:
                self.pending_cleanup.discard(name)
                self.deferred_cleanup.discard(name)
            return
        if instance["status"].lower() != "stopped":
            self.call("stop", self.ref(name), "--force", allow_cancel=False,check=False)
        instance = self.owned_instance(name,allow_cancel=False)
        if instance:
            # Ephemeral stop normally removes it; a failed start may leave it stopped.
            self.call("delete", self.ref(name), allow_cancel=False,check=False)
        if self.instances(name,allow_cancel=False):
            raise BoxError(f"Instance still exists after cleanup: {name} (ephemeral={ephemeral})")
        with self.pending_lock:
            self.pending_cleanup.discard(name)
            self.deferred_cleanup.discard(name)

    def retry_deferred(self,deadline):
        with self.pending_lock:names=list(self.deferred_cleanup)
        errors={}
        with self.budget(deadline):
            for name in names:
                try:
                    self.cleanup(name,ephemeral=True)
                    if self.state:self.state.instance(name)
                except (BoxError,OSError,ValueError) as exc:
                    errors[name]=str(exc)
                    print('DEFERRED_CLEANUP '+json.dumps({'container':name,'error':str(exc)}),flush=True)
        if self.state:self.state.update(deferredCleanup=errors)

    def launch(self, image, name, *, build=False):
        self.had_launch=True
        # JSON is a YAML subset accepted by launch's config stdin. Declare entire
        # devices here; CLI --device edits one property at a time on a device.
        config = {"config": {"limits.cpu": "2", "limits.memory": "3GiB", "user.browser-box.owner": self.owner,
                              "user.browser-box.controller": json.dumps(self.controller, sort_keys=True)},
                  "devices": {"root": {"type": "disk", "path": "/", "pool": os.environ.get("BROWSER_BOX_POOL", "default")}}}
        args = ["launch", image, self.ref(name), "--no-profiles"]
        if build:
            config["devices"]["eth0"] = {"type": "nic", "nictype": "bridged", "name": "eth0",
                                          "parent": os.environ.get("BROWSER_BOX_BRIDGE", "incusbr0")}
        else:
            args += ["--ephemeral"]
        while True:
            if is_stopping():raise Stopped('Stopped while launching')
            end=getattr(self.context,'deadline',None)
            if end is not None and time.monotonic()>=end:raise Infrastructure('Case budget exhausted waiting for capacity')
            while len(self.project_instances()) >= MAX_JOBS:
                print('WAIT_CAPACITY '+name, flush=True)
                if CANCELLED.wait(2):
                    raise BoxError('Cancelled while waiting for project capacity')
            try:
                # Do not sever an in-flight server creation on operator stop.
                # Wait for its response within the shared stop budget, then
                # exact-name cleanup sees the completed launch.
                result = self.call(*args, input_text=json.dumps(config), check=False,retry=False,allow_cancel=False)
            except ConnectionLost:
                # Creation may have succeeded before its response was lost.
                # Reconcile the same exact tagged name before retrying launch.
                if self.owned_instance(name):break
                continue
            if result.returncode == 0:
                break
            detail = '\n'.join(value for value in (getattr(result,'stderr',''),getattr(result,'stdout','')) if value)
            if 'already exists' in detail.lower() and self.owned_instance(name):break
            if not re.search(r'maximum number of (?:containers|instances)|project.*limit.*(?:exceed|reach)|exceed.*project.*limit|limits\.(?:cpu|memory|containers).*exceed', detail, re.I):
                raise BoxError('Instance launch failed: '+(detail or 'unknown error'))
            print('WAIT_CAPACITY '+name+' '+detail.strip(), flush=True)
            if CANCELLED.wait(2):
                raise BoxError('Cancelled while waiting for project capacity')
        if not build:
            instance = self.owned_instance(name)
            if not instance or "expanded_devices" not in instance or any(
                    device.get("type") == "nic" for device in instance["expanded_devices"].values()):
                raise BoxError("Run instance must have no network device")

    def drain_cleanup(self, deadline):
        with self.budget(deadline):
            # Exact run ownership also finds a launch whose response was lost.
            rows=self.call('list',self.ref(),'--format=json',allow_cancel=False)
            errors=[]
            for instance in json.loads(rows.stdout):
                if instance.get('config',{}).get('user.browser-box.owner')==self.owner:
                    try:
                        self.cleanup(instance['name'],ephemeral=True)
                        if self.state:self.state.instance(instance['name'])
                    except (BoxError,OSError,ValueError) as exc:errors.append(str(exc))
            if errors:raise BoxError('; '.join(errors))
            with self.pending_lock:
                self.pending_cleanup.clear()
                self.deferred_cleanup.clear()

    def exec(self, name, *command, **kwargs):
        return self.call("exec", self.ref(name), "--", *command, **kwargs)

    def push(self, name, source, target):
        self.call("file", "push", str(source), self.ref(name) + target)

    def ensure_image(self, root, output, timeout):
        alias = "browser-box-" + image_hash(root)
        images = json.loads(self.call("image", "list", self.ref(), alias, "--format=json").stdout)
        matches = [info for info in images if any(item["name"] == alias for item in info.get("aliases", []))]
        if matches:
            info = matches[0]
            if info.get("properties", {}).get("user.browser-box.hash") != image_hash(root):
                raise BoxError("Image alias has no matching browser-box provenance")
            json_write(output / "image.json", {"alias": alias, "cached": True, "image": info})
            versions = info.get("properties", {}).get("user.browser-box.versions")
            if versions:
                json_write(output / "versions.json", json.loads(versions))
            return alias
        name = "bb-build-" + uuid.uuid4().hex[:12]
        started = time.monotonic()
        if self.state:
            self.state.instance(name, {'kind':'build'})
        with self.pending_lock:self.pending_cleanup.add(name)
        try:
            self.launch("images:debian/13/amd64", name, build=True)
            self.exec(name, "mkdir", "-p", "/opt/agentexpense/review-dashboard/server", "/opt/agentexpense/scripts", "/opt/browser-box")
            for path in BUILD_INPUTS:
                self.push(name, root / path, "/opt/agentexpense/" + path)
            self.push(name, root / "browser-box-build.sh", "/opt/browser-box/build.sh")
            self.exec(name, "env", *[f"BOX_{key.upper()}={value}" for key, value in PINS.items()],
                      "bash", "/opt/browser-box/build.sh", log=output / "build.log", timeout=timeout,retry=False)
            self.call("file", "pull", self.ref(name) + "/opt/browser-box/versions.json", str(output / "versions.json"))
            self.call("stop", self.ref(name))
            self.call("publish", self.ref(name), self.ref(), "--alias", alias,
                      f"user.browser-box.hash={image_hash(root)}",
                      "user.browser-box.versions=" + (output / "versions.json").read_text().strip(),
                      f"user.browser-box.build-seconds={round(time.monotonic() - started, 2)}")
            images = json.loads(self.call("image", "list", self.ref(), alias, "--format=json").stdout)
            info = next(info for info in images if any(item["name"] == alias for item in info.get("aliases", [])))
            json_write(output / "image.json", {"alias": alias, "cached": False,
                       "buildSeconds": round(time.monotonic() - started, 2), "image": info})
            return alias
        finally:
            self.cleanup(name, ephemeral=False)
            if self.state:
                self.state.instance(name)


def case_script(case, target, focus, capture_prefix):
    env = {"ENGINES": case.engine, "PERSONA": case.persona, "THEMES": case.theme,
           "BROWSER_CASE": f"{case.theme}-{case.width}", "KEEP_ARTIFACTS": "1",
           "AGENTEXPENSE_TEST_ROOT": "/tmp/browser-box/artifacts", "BROWSER_SHOTS": "/tmp/browser-box/shots",
           "UV_OFFLINE": "1", "UV_NO_SYNC": "1", "npm_config_offline": "true",
           "npm_config_update_notifier": "false", "PYTHONDONTWRITEBYTECODE": "1",
           "TRACE_SCREENSHOTS": "0", "TZ": "Asia/Kolkata", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
           "FNM_DIR": "/root/.local/share/fnm", "BROWSER_PROFILE": "1"}
    if focus:
        env["BROWSER_FOCUS"] = focus
    if capture_prefix:
        env["BROWSER_CAPTURE_PREFIX"] = capture_prefix
    assignments = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
    return f"""#!/bin/bash
set -eu
export PATH=/usr/local/bin:$PATH
mkdir -p /tmp/browser-box/artifacts
cd /opt/agentexpense
tar -xzf /opt/browser-box/source.tar.gz --no-same-owner
export {assignments}
test "$(cat .node-version)" = "$(cat /opt/browser-box/node-version)"
# The detached supervisor owns all output and the watchdog, independently of exec.
touch /tmp/browser-box/test.started
exec fnm exec --using=.node-version -- make {target}
"""


def unpack_results(archive_path, folder):
    # Only regular files from our collector; refuse remote links and traversal.
    with tarfile.open(archive_path) as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not member.isfile():
                raise BoxError(f"Unsafe result archive member: {member.name!r}")
            target = folder.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("xb") as destination:
                while chunk:=source.read(1024*1024):
                    if is_stopping() and time.monotonic()>=STOP_DEADLINE-10:
                        raise Stopped('Artifact archive retained; stopped unpacking to reserve cleanup time')
                    destination.write(chunk)


GUEST_PYTHON='/opt/agentexpense/review-dashboard/server/.venv/bin/python'
GUEST_SCRIPT='/opt/browser-box/guest_case.py'


def guest_status(incus,name,*,allow_cancel=True):
    response=incus.exec(name,GUEST_PYTHON,GUEST_SCRIPT,'status',allow_cancel=allow_cancel)
    try:status=json.loads(response.stdout)
    except ValueError as exc:raise StatusUnavailable('Invalid guest status JSON: '+repr(response.stdout[:512])) from exc
    if not isinstance(status,dict) or status.get('state') not in ('missing','starting','running','collecting','finished'):
        raise StatusUnavailable('Invalid guest status: '+repr(response.stdout[:512]))
    if status['state'] in ('collecting','finished') and (type(status.get('exit')) is not int or
            status.get('outcome') not in ('passed','failed','infrastructure','stopped')):
        raise StatusUnavailable('Invalid terminal guest status: '+repr(response.stdout[:512]))
    return status


def collect_results(incus,name,folder,*,recover=False,allow_cancel=True):
    if recover:
        remaining=max(.1,getattr(incus.context,'deadline',time.monotonic()+30)-time.monotonic())
        incus.exec(name,'env','BROWSER_BOX_COLLECTION_SECONDS='+str(remaining),GUEST_PYTHON,'-c',COLLECT_SCRIPT,allow_cancel=allow_cancel,
                   log=folder/'collect.log')
    temporary=folder/'results.tar.gz.partial'
    incus.call('file','pull',incus.ref(name)+'/opt/browser-box/results.tar.gz',str(temporary),
               allow_cancel=allow_cancel)
    temporary.replace(folder/'results.tar.gz')
    unpack_results(folder/'results.tar.gz',folder/'results')


def collection_metadata(record,folder):
    path=folder/'results/collection.json'
    metadata=json.loads(path.read_text()) if path.exists() else {'partial':False,'skipped':[]}
    record['collection']=metadata
    record['collected']=not metadata.get('partial',False)
    if not record['collected']:record['collectionError']='Partial artifact collection; see collection.skipped'
    elif record.get('guest',{}).get('collection_error'):
        record['collectionError']=record['guest']['collection_error']


def guest_finished(record):
    status=record.get('guest',{})
    return status.get('state') in ('collecting','finished') and 'exit' in status and 'outcome' in status


def adopt_guest(record,status,folder):
    if status.get('state') not in ('collecting','finished') or 'exit' not in status:return
    if guest_finished(record) and (record['exit'],record['outcome'])!=(status['exit'],status['outcome']):
        raise BoxError('Guest changed a frozen test outcome')
    record.update(exit=status['exit'],outcome=status['outcome'],guest=status)
    record.pop('error',None)
    if status['exit']==124 and status['outcome']=='infrastructure':record['budgetExhausted']=True
    json_write(folder/'result.json',record)


def wait_for_guest(incus,name,record,folder,deadline):
    unexplained=0
    while True:
        if is_stopping():raise Stopped('Stopped while waiting for guest')
        try:status=guest_status(incus,name)
        except ConnectionLost as exc:
            if time.monotonic()>=deadline:raise Infrastructure('Case budget exhausted waiting for surviving guest: '+str(exc)) from exc
            print('WAIT_GUEST_RECONNECT '+json.dumps({'case':record['case'],'error':safe_error(str(exc),incus.remote)}),flush=True)
            CANCELLED.wait(min(1,max(0,deadline-time.monotonic())))
            continue
        except (BoxError,OSError,ValueError) as exc:
            if isinstance(exc,PermanentError) or re.search(r'instance (?:not found|.*does not exist)',str(exc),re.I):raise
            unexplained+=1
            if unexplained>=MAX_UNEXPLAINED_STATUS_ERRORS or time.monotonic()>=deadline:
                raise StatusUnavailable(f'Guest status unavailable after {unexplained} consecutive unexplained errors: '+str(exc)) from exc
            print('WAIT_GUEST_STATUS '+json.dumps({'case':record['case'],'consecutiveErrors':unexplained,
                                                 'error':safe_error(str(exc),incus.remote)}),flush=True)
            CANCELLED.wait(min(1,max(0,deadline-time.monotonic())))
            continue
        unexplained=0
        adopt_guest(record,status,folder)
        if status['state']=='finished':return status
        if status['state']=='missing':raise Infrastructure('Guest supervisor did not start')
        if CANCELLED.wait(1):raise Stopped('Stopped while polling guest')


def run_attempt(incus, alias, bundle, output, case, attempt, args):
    folder = output / case.name / f"attempt-{attempt}"
    folder.mkdir(parents=True)
    name = "bb-run-" + uuid.uuid4().hex[:12]
    record = {"case": case.name, "attempt": attempt, "rerun": attempt > 1,
              "container": name, "folder": str(folder), "exit": 125,'outcome':'infrastructure','collected':False}
    started = time.monotonic()
    if not hasattr(args,'case_deadlines'):args.case_deadlines={}
    deadline=args.case_deadlines.setdefault(case.name,started+args.timeout)
    collection_end=None
    if incus.state:
        incus.state.instance(name, {'case':case.name,'attempt':attempt,'folder':str(folder)})
    with incus.pending_lock:incus.pending_cleanup.add(name)
    try:
        with incus.budget(deadline,phase='case'):
            incus.launch(incus.ref(alias), name)
            incus.push(name, bundle, "/opt/browser-box/source.tar.gz")
            script = folder / "run.sh"
            script.write_text(case_script(case, args.target, args.focus, args.capture_prefix))
            collector=folder/'collect.py';collector.write_text(COLLECT_SCRIPT)
            incus.push(name, script, "/opt/browser-box/run.sh")
            incus.push(name, collector, "/opt/browser-box/collect.py")
            incus.push(name, TOOLS/'guest_case.py',GUEST_SCRIPT)
            # This exec returns immediately. Repeating it after a lost response
            # is safe: the guest serializes launch and persists its supervisor PID.
            try:
                incus.exec(name,GUEST_PYTHON,GUEST_SCRIPT,'launch','--timeout',str(max(.1,deadline-time.monotonic())),
                           log=folder/'incus.log')
            except (BoxError,OSError):
                # The launch may have succeeded before its response was lost.
                # Probe the same guest, never tear it down on a transport window.
                pass
            status=wait_for_guest(incus,name,record,folder,deadline)
        # The guest enforces the same limit independently. Evidence/cleanup get
        # a separate bounded grace, including when that test budget expires.
        collection_end=max(deadline,time.monotonic()+COLLECTION_GRACE_SECONDS)
        with incus.budget(collection_end,reconnect=True,phase='collection'):
            collect_results(incus,name,folder,recover=not status.get('archive_ready',True))
            if not is_stopping() and record['outcome']!='stopped':
                guest_exit=int((folder/'results/exit').read_text().strip())
                if guest_exit!=record['exit']:raise Infrastructure('Guest exit file disagrees with persisted status')
            collection_metadata(record,folder)
    except (BoxError, OSError, ValueError, tarfile.TarError) as exc:
        if isinstance(exc,PermanentError):record['retryable']=False
        elif isinstance(exc,Infrastructure):record['retryable']=True
        if guest_finished(record):
            record['collectionError']=safe_error(str(exc),incus.remote)
            print('COLLECTION_ERROR '+json.dumps({'case':case.name,'phase':'collection',
                'error':record['collectionError'],
                'remainingSeconds':round(max(0,collection_end-time.monotonic()),3) if collection_end else None}),flush=True)
        else:
            record["error"] = str(exc)
            record.update(outcome='stopped' if is_stopping() else 'infrastructure',exit=130 if is_stopping() else 125)
    finally:
        if (is_stopping() or record.get('error') or record.get('collectionError')) and record.get('retryable') is not False:
            try:
                evidence_end=min(STOP_DEADLINE-10,time.monotonic()+20) if is_stopping() else (
                    collection_end or max(deadline,time.monotonic()+COLLECTION_GRACE_SECONDS))
                with incus.budget(evidence_end,reconnect=guest_finished(record),phase='recovery'):
                    instance = incus.owned_instance(name,allow_cancel=False)
                    if instance and instance['status'].lower()=='running':
                        status=guest_status(incus,name,allow_cancel=False)
                        adopt_guest(record,status,folder)
                        if status['state'] in ('running','starting') and not is_stopping() and time.monotonic()<deadline:
                            # Recovery found a survivor. Resume it rather than
                            # stopping it because an earlier probe was unexplained.
                            with incus.budget(deadline,phase='case-recovery'):
                                status=wait_for_guest(incus,name,record,folder,deadline)
                        if not guest_finished(record) or (is_stopping() and status.get('state')=='collecting'):
                            # Stop an unfinished test first. A completed guest
                            # keeps its exit/status/archive through collection retries.
                            incus.exec(name,GUEST_PYTHON,GUEST_SCRIPT,'stop',allow_cancel=False,check=False)
                            settle=min(evidence_end-3,time.monotonic()+10)
                            while time.monotonic()<settle:
                                status=guest_status(incus,name,allow_cancel=False)
                                adopt_guest(record,status,folder)
                                if status.get('state') in ('finished','missing'):break
                                threading.Event().wait(.2)
                        if not (folder/'results').exists():
                            if collection_end is None:collection_end=max(deadline,time.monotonic()+COLLECTION_GRACE_SECONDS)
                            pull_end=min(evidence_end,collection_end) if is_stopping() else collection_end
                            with incus.budget(pull_end,reconnect=True,phase='recovery-collection'):
                                collect_results(incus,name,folder,recover=not status.get('archive_ready',False),allow_cancel=False)
                            record['partialResults']=str(folder/'results')
                            if guest_finished(record):
                                if int((folder/'results/exit').read_text().strip())!=record['exit']:
                                    raise Infrastructure('Guest exit file disagrees with persisted status')
                                record['collected']=True
                                record.pop('collectionError',None)
                                collection_metadata(record,folder)
            except (BoxError, OSError, ValueError, tarfile.TarError) as exc:
                if isinstance(exc,PermanentError):record['retryable']=False
                record["collectionError"] = safe_error(str(exc),incus.remote)
                print('COLLECTION_ERROR '+json.dumps({'case':case.name,'phase':'recovery',
                    'error':record['collectionError'],'remainingSeconds':round(max(0,evidence_end-time.monotonic()),3)}),flush=True)
                try:
                    with incus.budget(STOP_DEADLINE-5 if is_stopping() else time.monotonic()+incus.reconnect_timeout):
                        incus.call('file','pull',incus.ref(name)+'/tmp/browser-box/run.log',str(folder/'partial-run.log'),
                                   allow_cancel=False)
                except (BoxError,OSError):pass
        try:
            cleanup_end=STOP_DEADLINE if is_stopping() else (
                max(deadline,time.monotonic()+45) if guest_finished(record) else time.monotonic()+min(45,incus.reconnect_timeout))
            with incus.budget(cleanup_end,reconnect=guest_finished(record),phase='cleanup'):incus.cleanup(name, ephemeral=True)
            record["cleaned"] = True
            if incus.state:
                incus.state.instance(name)
        except BoxError as exc:
            record["cleanupError"] = str(exc)
            with incus.pending_lock:incus.deferred_cleanup.add(name)
            if not is_stopping() and not guest_finished(record):record.update(outcome='infrastructure',exit=125)
        record["seconds"] = round(time.monotonic() - started, 2)
        if time.monotonic()>=deadline:record['budgetExhausted']=True
        if is_stopping() and not guest_finished(record):record['outcome']='stopped'
        if record['outcome']=='stopped': record['exit']=130
        if not record['collected'] and record.get('collectionError'):
            metadata=record.setdefault('collection',{'partial':True,'skipped':[]})
            partial=folder/'results.tar.gz.partial'
            if partial.is_file():metadata.update(receivedBytes=partial.stat().st_size,partialArchive=str(partial))
        json_write(folder / "result.json", record)
    return record


def run_cases(cases, jobs, attempt, checkpoint=None, infrastructure_retries=2):
    """Only a real test failure consumes the one unchanged test rerun."""
    def one(case):
        records=[];test_attempt=1;infrastructure=0
        while True:
            record=({'case':case.name,'exit':130,'outcome':'stopped','error':'Stopped before launch'}
                    if is_stopping() else attempt(case,len(records)+1))
            outcome=record.setdefault('outcome','passed' if record['exit']==0 else 'failed')
            record.update(testAttempt=test_attempt,rerun=test_attempt>1)
            records.append(record)
            if record.get('folder'):json_write(Path(record['folder'])/'result.json',record)
            if checkpoint:checkpoint(case,record)
            if (is_stopping() or outcome in ('passed','stopped') or record.get('budgetExhausted')
                    or record.get('retryable') is False
                    or (guest_finished(record) and record.get('collectionError'))):break
            if outcome=='infrastructure':
                infrastructure+=1
                if infrastructure>infrastructure_retries:break
            elif test_attempt==1:test_attempt=2
            else:break
        return records
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(one, cases))


def controller_main(output):
    global CONTROL_FOLDER
    CONTROL_FOLDER = output
    config = json.loads((output/'launch.json').read_text())
    args = argparse.Namespace(**config['args'])
    root = Path(args.checkout)
    args.cases = [parse_case(case) for case in args.cases]
    incus = Incus(config['remote'], config['project'])
    incus.reconnect_timeout=getattr(args,'reconnect_timeout',120)
    args.case_deadlines={}
    state = incus.state = ControllerState(output, incus.controller)
    result_code = 1
    try:
        if is_stopping():raise Stopped('Stopped before controller startup')
        state.update(localClientsReaped=client_watch.reap(incus.client_registry))
        if args.prune_images:
            with incus.budget(time.monotonic()+args.build_timeout):
                incus.call('project','show',incus.ref(incus.project))
                state.update(staleInstances=incus.reap_stale())
                json_write(output/'prune.json', incus.prune_images('browser-box-'+image_hash(root)))
            result_code = 0
            return result_code
        bundle = output/'source.tar.gz'
        files = pack_source(root, bundle)
        inputs = snapshot_build_inputs(bundle, output/'image-inputs')
        json_write(output/'source.json', {'checkout':str(root),'files':files,
                   'sha256':hashlib.sha256(bundle.read_bytes()).hexdigest(),'imageHash':image_hash(inputs)})
        with incus.budget(time.monotonic()+args.build_timeout):
            incus.ensure_project()
            state.update(staleInstances=incus.reap_stale())
            alias = incus.ensure_image(inputs, output, args.build_timeout)
        state.update(image=alias)
        if args.build_only:
            print(f'Image {alias}; metadata and build log: {output}',flush=True)
            result_code = 0
            return result_code
        recorded = {case.name:[] for case in args.cases}
        checkpoint_lock = threading.Lock()
        def checkpoint(case, record):
            with checkpoint_lock:
                recorded[case.name].append(record)
                json_write(output/'summary.json', {'image':alias,'timezone':'Asia/Kolkata',
                           'locale':'en_US.UTF-8','jobs':args.jobs,
                           'cases':[records for records in recorded.values() if records]})
                print('CASE_RESULT '+json.dumps(record),flush=True)
        cleanup_done=threading.Event()
        def cleanup_background():
            while not cleanup_done.wait(1):
                if incus.deferred_cleanup:
                    incus.retry_deferred(time.monotonic()+min(30,incus.reconnect_timeout))
        cleanup_thread=threading.Thread(target=cleanup_background)
        cleanup_thread.start()
        try:
            records = run_cases(args.cases, args.jobs,
                                lambda case, attempt:run_attempt(incus,alias,bundle,output,case,attempt,args),checkpoint,
                                infrastructure_retries=getattr(args,'infrastructure_retries',2))
        finally:
            cleanup_done.set()
            cleanup_thread.join()
        print(f'Summary: {output / "summary.json"}',flush=True)
        result_code = 130 if CANCELLED.is_set() else int(any(rows[-1]['exit'] or rows[-1].get('collectionError') for rows in records))
        return result_code
    except (BoxError,OSError,ValueError,KeyError,tarfile.TarError,subprocess.CalledProcessError) as exc:
        json_write(output/'error.json', {'error':str(exc)})
        print(f'browser-box: {exc}\nArtifacts: {output}',file=sys.stderr,flush=True)
        result_code = 130 if CANCELLED.is_set() else 1
        return result_code
    finally:
        if incus.pending_cleanup or incus.had_launch:
            try:
                cleanup_end=STOP_DEADLINE if is_stopping() else time.monotonic()+incus.reconnect_timeout
                incus.drain_cleanup(cleanup_end)
            except (BoxError,OSError,ValueError) as exc:
                state.update(cleanupError=str(exc),cleanupPending=sorted(incus.pending_cleanup))
                print('CLEANUP_PENDING '+str(exc),flush=True)
                result_code=130 if is_stopping() else 1
        state.update(status='cancelled' if CANCELLED.is_set() else 'finished',exit=result_code)


def inspect_run(output, *, follow=False, stop=False):
    output = output.resolve()
    if not output.is_relative_to(Path('/tmp/agentexpense-browser-box').resolve()):
        raise BoxError('Run folder must be under /tmp/agentexpense-browser-box')
    if stop:
        if not (output/'launch.json').is_file():raise BoxError('No browser-box launch record: '+str(output))
        try:
            with (output/'stop.requested').open('x') as request:json.dump({'requested_at':time.time()},request)
        except FileExistsError:pass
    offset = 0
    startup = time.monotonic()+10
    while True:
        if follow and (output/'runner.log').exists():
            with (output/'runner.log').open() as log:
                log.seek(offset)
                print(log.read(),end='',flush=True)
                offset = log.tell()
        try:
            state = json.loads((output/'runner.json').read_text())
        except FileNotFoundError:
            if stop:
                print('Recorded stop request; controller will honour it on startup')
                return 0
            if not follow or time.monotonic() >= startup:
                raise BoxError('Controller state not available yet: '+str(output))
            if CANCELLED.wait(1):
                return 130
            continue
        if stop and state.get('status') not in ('finished','cancelled'):
            status=owner_status(state.get('controller'))
            if status=='dead':
                raise BoxError('Controller is dead (crashed); no stop signal or cleanup can run. Results remain on disk; the next run checks its tagged stale instances.')
            if status!='alive':
                raise BoxError('Controller is not verified alive on this host; no signal sent')
            try:
                with (output/'stop.requested').open('x') as request:
                    json.dump({'requested_at':time.time()},request)
            except FileExistsError:
                pass
            try:
                os.kill(state['controller']['pid'], signal.SIGTERM)
            except ProcessLookupError:
                raise BoxError('Controller is dead; it exited before the stop signal. No signal sent.')
            print('Sent SIGTERM to verified controller '+str(state['controller']['pid']))
            return 0
        if not follow:
            state['ownerStatus'] = owner_status(state.get('controller'))
            print(json.dumps(state,indent=2))
            return 0
        if state.get('status') in ('finished','cancelled'):
            return state['exit']
        if owner_status(state.get('controller')) == 'dead':
            raise BoxError('Controller died; results remain on disk. Next run checks its tagged stale instances.')
        if CANCELLED.wait(1):
            return 130  # Stopping a follower never stops its detached controller.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="*", type=parse_case)
    parser.add_argument("--checkout", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--jobs", type=jobs_value, default=2)
    parser.add_argument("--target", choices=("browser-tests", "browser-capture"), default="browser-tests")
    parser.add_argument("--focus", default="", help="existing BROWSER_FOCUS selector")
    parser.add_argument("--capture-prefix", default="")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument('--prune-images',action='store_true',help='explicitly prune obsolete runner images in an idle project')
    controls = parser.add_mutually_exclusive_group()
    controls.add_argument('--wait',type=Path,metavar='RUN_FOLDER',help='follow a detached run and return its exit code')
    controls.add_argument('--status',type=Path,metavar='RUN_FOLDER',help='print local controller state')
    controls.add_argument('--stop',type=Path,metavar='RUN_FOLDER',help='SIGTERM a verified local controller')
    controls.add_argument('--_controller',type=Path,help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=int, default=7200, help="per case seconds, including full harness")
    parser.add_argument("--build-timeout", type=int, default=3600)
    parser.add_argument('--reconnect-timeout',type=float,default=120,help='maximum seconds per reconnect window, within each case budget')
    parser.add_argument('--infrastructure-retries',type=int,default=2,help='maximum infrastructure replacements, independent of the test rerun')
    args = parser.parse_args(argv)
    if args._controller:
        return controller_main(args._controller.resolve())
    if args.wait or args.status or args.stop:
        try:
            return inspect_run(args.wait or args.status or args.stop,follow=bool(args.wait),stop=bool(args.stop))
        except (BoxError,OSError,ValueError) as exc:
            print(str(exc),file=sys.stderr)
            return 125
    remote = os.environ.get("BROWSER_BOX_REMOTE", "")
    if not remote:
        parser.error("Set BROWSER_BOX_REMOTE to an already configured Incus remote; "
                     "for example: BROWSER_BOX_REMOTE=<remote> make browser-box ARGS='asha:chromium:light:1440'")
    if not args.build_only and not args.prune_images and not args.cases:
        parser.error("supply one or more persona:engine:theme:width cases, or --build-only")
    if args.timeout <= 0 or args.build_timeout <= 0 or args.reconnect_timeout<=0 or args.infrastructure_retries<0:
        parser.error("timeouts must be positive")
    if len(set(args.cases)) != len(args.cases):
        parser.error("duplicate cases are not allowed")
    if args.prune_images and (args.cases or args.build_only):
        parser.error('--prune-images cannot be combined with cases or --build-only')
    root = args.checkout.resolve()
    output = (args.output or Path("/tmp/agentexpense-browser-box") / ("run-" + uuid.uuid4().hex[:12])).resolve()
    if not output.is_relative_to(Path("/tmp/agentexpense-browser-box").resolve()):
        parser.error("--output must be under /tmp/agentexpense-browser-box")
    if output.is_relative_to(root):
        parser.error("output cannot be inside the source checkout")
    output.mkdir(parents=True, exist_ok=False)
    try:
        # Configuration is local only; no operator environment is sent to guests.
        settings = {key:value for key,value in vars(args).items()
                    if key not in ('wait','status','stop','_controller','cases','checkout','output')}
        settings.update(checkout=str(root),output=str(output),
                        cases=[f'{c.persona}:{c.engine}:{c.theme}:{c.width}' for c in args.cases])
        json_write(output/'launch.json',{'remote':remote,'project':os.environ.get('BROWSER_BOX_PROJECT','agentexpense-ci'),'args':settings})
        child = spawn_controller(output,[sys.executable,'-B','-u',str(Path(__file__).resolve()),'--_controller',str(output)])
        print(f'Detached controller PID {child.pid}; state: {output / "runner.json"}; log: {output / "runner.log"}')
        print(f'Follow: python3 {Path(__file__).resolve()} --wait {output}')
        return 0
    except (OSError,ValueError) as exc:
        print(str(exc),file=sys.stderr)
        return 1


if __name__ == "__main__":
    def interrupted(signum, _frame):
        CANCELLED.set()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    sys.exit(main())
