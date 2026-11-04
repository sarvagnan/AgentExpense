"""Own one temporary root for an offline test command and all its children."""
from __future__ import annotations
import atexit
import errno
import json
import secrets
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

APPLICATION = Path(__file__).resolve().parents[1]
_configured_root=None
_temporary_cleanup=tempfile.TemporaryDirectory._rmtree
_temporary_archive_installed=False


def archive_temporary_directory(name):
    if os.environ.get('KEEP_ARTIFACTS')!='1' or _configured_root is None:return
    path=Path(name).resolve();kept=_configured_root/'kept'
    if path==_configured_root or not path.is_relative_to(_configured_root) or path.is_relative_to(kept) or not path.is_dir():return
    kept.mkdir(exist_ok=True)
    destination=kept/(path.name+'-'+secrets.token_hex(8))
    shutil.copytree(path,destination,symlinks=True)


def configure(root):
    global _configured_root,_temporary_archive_installed
    root = Path(root).resolve()
    _configured_root=root
    root.mkdir(parents=True, exist_ok=True)
    for name in ('tmp','npm-cache','uv-cache','node-cache','oracles'):
        (root/name).mkdir(exist_ok=True)
    os.environ.update(AGENTEXPENSE_TEST_ROOT=str(root), TMPDIR=str(root/'tmp'), TMP=str(root/'tmp'), TEMP=str(root/'tmp'),
                      EXPENSES_TEST_TMP=str(root/'tmp'), EXPENSES_TEST_ORACLE_CACHE=str(root/'oracles'),
                      npm_config_cache=str(root/'npm-cache'), UV_CACHE_DIR=str(root/'uv-cache'),
                      NODE_COMPILE_CACHE=str(root/'node-cache'), npm_config_update_notifier='false',
                      npm_config_offline='true', UV_OFFLINE='1', UV_NO_SYNC='1', PYTHONDONTWRITEBYTECODE='1')
    environment=APPLICATION/'review-dashboard/server/.venv'
    if environment.is_dir():
        os.environ['VIRTUAL_ENV']=str(environment)
        os.environ['PATH']=str(environment/'bin')+os.pathsep+os.environ.get('PATH','')
    os.environ.setdefault('DASHBOARD_DATA_DIR',str(root/'dashboard-data'))
    tempfile.tempdir = str(root/'tmp')
    if not _temporary_archive_installed:
        def archived_cleanup(cls,name,*args,**kwargs):
            archive_temporary_directory(name)
            return _temporary_cleanup(name,*args,**kwargs)
        tempfile.TemporaryDirectory._rmtree=classmethod(archived_cleanup)
        _temporary_archive_installed=True
    return root


def cleanup(root, failed=False):
    root=Path(root)
    stop_group(root)
    if os.environ.get('KEEP_ARTIFACTS')=='1':
        print(f'Test artifacts kept: {root}',flush=True)
        return
    if failed and root.exists():
        traces=[p for p in root.rglob('failure-*.zip') if p.is_file() and not p.is_symlink()]
        if traces:
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            destination=Path('/tmp')/f'agentexpense-failures-{stamp}-{os.getpid()}'
            destination.mkdir()
            for i,path in enumerate(traces): shutil.copyfile(path,destination/f'{i}-{path.name}')
            print(f'Failing traces: {destination}',flush=True)
    if sys.platform=='darwin' and root.exists():
        # Redirected browser homes can contain deny-delete ACLs. Work only inside
        # this owned root and do not follow links to dependency inputs.
        for folder, dirs, files in os.walk(root,followlinks=False):
            if Path(folder).name=='firefox-app-data':
                subprocess.run(['chmod','-RN',folder],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                dirs[:]=[]
    for attempt in range(4):
        try:
            if root.exists():shutil.rmtree(root)
            break
        except OSError as exc:
            if exc.errno!=errno.ENOTEMPTY or attempt==3:raise
            time.sleep(.05)


def stop_group(root):
    path=Path(root)/'process-group.json'
    if not path.exists():return
    group=json.loads(path.read_text())['pgid']
    if group==os.getpgrp():raise RuntimeError('A test cleanup group must be isolated')
    try:os.killpg(group,signal.SIGTERM)
    except ProcessLookupError:return
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        try:os.killpg(group,0)
        except ProcessLookupError:return
        time.sleep(.05)
    try:os.killpg(group,signal.SIGKILL)
    except ProcessLookupError:pass


def ensure_root():
    existing=os.getenv('AGENTEXPENSE_TEST_ROOT')
    if existing: return configure(existing)
    root=configure(tempfile.mkdtemp(prefix='agentexpense-run-',dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp')))
    atexit.register(cleanup,root,True)
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
        signal.signal(sig,lambda signum,frame:sys.exit(128+signum))
    return root


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--cleanup':
        cleanup(sys.argv[2],int(sys.argv[3])!=0);return 0
    owns=not os.getenv('AGENTEXPENSE_TEST_ROOT')
    root=configure(os.getenv('AGENTEXPENSE_TEST_ROOT') or tempfile.mkdtemp(prefix='agentexpense-run-',dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp')))
    child=None;status=1;descendants=[]
    owns_session=os.environ.get('AGENTEXPENSE_TEST_SUPERVISED')!=str(root)
    def stop(signum, frame):
        nonlocal descendants
        if child:
            rows=subprocess.check_output(['ps','-axo','pid=,ppid='],text=True)
            parents=[tuple(map(int,row.split())) for row in rows.splitlines() if row.strip()]
            owned={child.pid}
            while True:
                extra={pid for pid,parent in parents if parent in owned}-owned
                if not extra:break
                owned.update(extra)
            descendants=list(owned-{child.pid})
        if child and child.poll() is None:
            child.send_signal(signum)
        raise SystemExit(128+signum)
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP): signal.signal(sig,stop)
    try:
        child=subprocess.Popen(sys.argv[1:],start_new_session=owns_session,env={**os.environ,'AGENTEXPENSE_TEST_SUPERVISED':str(root)})
        if owns_session:(root/'process-group.json').write_text(json.dumps(dict(pgid=child.pid))+'\n')
        status=child.wait()
        return status if status>=0 else 128-status
    finally:
        if child and child.poll() is None:
            try: child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                if owns_session:os.killpg(child.pid,signal.SIGKILL)
                else:child.kill()
                child.wait()
        for pid in descendants:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass
        # Recursive make launchers can exit before their browser/worker children.
        # Allow their handlers to flush failing traces before removing the root.
        deadline=time.monotonic()+8
        pending=set(descendants)
        while pending and time.monotonic()<deadline:
            for pid in list(pending):
                try:os.kill(pid,0)
                except ProcessLookupError:pending.remove(pid)
            if pending:time.sleep(.05)
        for pid in pending:
            try:os.kill(pid,signal.SIGKILL)
            except ProcessLookupError:pass
        if owns_session:stop_group(root)
        if owns: cleanup(root,status!=0)


if __name__=='__main__': sys.exit(main())
