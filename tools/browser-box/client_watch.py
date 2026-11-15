"""Bound a local client independently of its controller; register exact identities."""
import argparse
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time
from browser_box_lifecycle import atomic_json, owner_status, process_identity

STOP=threading.Event()


def secure_registry(registry):
    registry=Path(registry)
    if registry.is_symlink():raise PermissionError('Client registry must not be a symlink: '+str(registry))
    registry.mkdir(mode=0o700,parents=True,exist_ok=True)
    registry=registry.resolve()
    info=registry.lstat()
    uid=os.getuid()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=uid or info.st_mode&0o022:
        raise PermissionError('Client registry must be owned by this UID and not group/other writable: '+str(registry))
    # Returning the canonical path also avoids using an untrusted ancestor
    # symlink after this check. Only the system's sticky temporary root may be
    # writable by another UID; ordinary ancestors must be trusted and protected.
    for parent in registry.parents:
        info=parent.lstat()
        system_tmp=info.st_uid==0 and bool(info.st_mode&stat.S_ISVTX)
        if info.st_uid not in (0,uid) or (info.st_mode&0o022 and not system_tmp):
            raise PermissionError('Unsafe client-registry ancestor: '+str(parent))
    return registry


def read_record(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o022:
        raise PermissionError('Untrusted local client record: '+str(path))
    return json.loads(path.read_text())


def valid_identity(identity):
    return (isinstance(identity,dict) and type(identity.get('pid')) is int
            and 1<identity['pid']<=2147483647 and isinstance(identity.get('birth'),str) and bool(identity['birth']))


def matching(identity):
    return valid_identity(identity) and process_identity(identity['pid'])==identity['birth']


def end_process(identity):
    if not matching(identity):return
    try:os.kill(identity['pid'],signal.SIGTERM)
    except ProcessLookupError:return
    end=time.monotonic()+.25
    while matching(identity) and time.monotonic()<end:time.sleep(.02)
    if matching(identity):
        try:os.kill(identity['pid'],signal.SIGKILL)
        except ProcessLookupError:pass
        end=time.monotonic()+.5
        while matching(identity) and time.monotonic()<end:time.sleep(.02)


def reap(registry):
    report=[]
    registry=secure_registry(registry)
    for path in registry.glob('*.json'):
        if path.is_symlink():continue
        try:
            record=read_record(path)
            if record.get('kind')!='browser-box-client' or owner_status(record.get('parent'))!='dead':continue
            for field in ('watcher','client'):end_process(record.get(field))
            uncertain=any(record.get(field) is not None and (not valid_identity(record[field]) or
                          process_identity(record[field]['pid']) is None) for field in ('watcher','client'))
            if not uncertain and not any(matching(record.get(field)) for field in ('watcher','client')):
                path.unlink(missing_ok=True)
                report.append({'record':str(path),'reaped':True})
        except (OSError,ValueError,TypeError,KeyError,OverflowError):continue
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record',type=Path,required=True)
    parser.add_argument('--parent')
    parser.add_argument('--deadline',type=float)
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    args.record=secure_registry(args.record.parent)/args.record.name
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    if args.worker:
        record=read_record(args.record)
        record['client']={'pid':os.getpid(),'birth':process_identity(os.getpid())}
        atomic_json(args.record,record)
        # Registration precedes exec, so even a killed watcher leaves an exact
        # PID/start identity for the next controller, never an unregistered CLI.
        if not matching(record['watcher']) or owner_status(record['parent'])=='dead':return 130
        os.execvp(command[0],command)
    for sig in (signal.SIGTERM,signal.SIGHUP,signal.SIGINT):signal.signal(sig,lambda *_:STOP.set())
    parent=json.loads(args.parent)
    record={'kind':'browser-box-client','parent':parent,'deadline':args.deadline,
            'watcher':{'pid':os.getpid(),'birth':process_identity(os.getpid())}}
    atomic_json(args.record,record)
    child=None
    try:
        if owner_status(parent)=='dead':return 130
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--worker',
                    '--record',str(args.record),'--',*command],start_new_session=True,close_fds=True)
        while child.poll() is None:
            birth=process_identity(parent['pid'])
            if STOP.is_set() or (birth is not None and birth!=parent['birth']):break
            if time.monotonic()>=args.deadline-.3:
                print('Error: i/o timeout: independent local client deadline',file=sys.stderr,flush=True)
                break
            STOP.wait(.05)
        if child.poll() is not None:return child.returncode
        try:os.killpg(child.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:child.wait(timeout=.2)
        except subprocess.TimeoutExpired:
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            child.wait()
        return 124
    finally:
        if child is None or child.poll() is not None:args.record.unlink(missing_ok=True)


if __name__=='__main__':sys.exit(main())
