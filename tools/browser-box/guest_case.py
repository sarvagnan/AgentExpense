"""Detached Linux-container case supervisor; never holds an Incus exec stream."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

STOP = threading.Event()


def write_json(path, value):
    temporary=path.with_suffix('.next')
    temporary.write_text(json.dumps(value)+'\n')
    temporary.replace(path)


def process_table():
    table={}
    if Path('/proc').is_dir():
        for folder in Path('/proc').iterdir():
            if not folder.name.isdigit():continue
            try:
                fields=(folder/'stat').read_text().rpartition(') ')[2].split()
                table[int(folder.name)]=(int(fields[1]),fields[19])
            except (OSError,ValueError,IndexError):pass
    return table


def terminate_case(child):
    table=process_table()
    owned={child.pid}
    while True:
        descendants={pid for pid,(parent,_) in table.items() if parent in owned}
        if descendants <= owned:break
        owned.update(descendants)
    def same_birth(pid,current):
        return pid in current and pid in table and current[pid][1]==table[pid][1]
    def send(signum):
        try:os.killpg(child.pid,signum)
        except ProcessLookupError:pass
        current=process_table()
        for pid in owned-{child.pid}:
            if same_birth(pid,current):
                try:os.kill(pid,signum)
                except ProcessLookupError:pass
    send(signal.SIGTERM)
    grace=time.monotonic()+3
    while time.monotonic()<grace:
        current=process_table()
        if child.poll() is not None and not any(same_birth(pid,current) for pid in owned-{child.pid}):break
        time.sleep(.05)
    # Descendants may have their own session; do not leave them behind after
    # the shell/Make parent exits. Recheck their process birth before signalling.
    send(signal.SIGKILL)
    try:child.wait(timeout=2)
    except subprocess.TimeoutExpired:pass


def status(root):
    try:
        result=json.loads((root/'status.json').read_text())
    except FileNotFoundError:
        try:result={'state':'starting',**json.loads((root/'started.json').read_text())}
        except FileNotFoundError:return {'state':'missing'}
    if result.get('state')!='finished':
        try:os.kill(result['supervisor_pid'],0)
        except ProcessLookupError:
            if result.get('state')=='collecting' and 'outcome' in result and 'exit' in result:
                return {**result,'state':'finished','collection_error':'guest supervisor exited during collection',
                        'archive_ready':Path('/opt/browser-box/results.tar.gz').is_file()}
            return {**result,'state':'finished','outcome':'infrastructure','exit':125,'reason':'guest supervisor exited without a result'}
    return {**result,'archive_ready':Path('/opt/browser-box/results.tar.gz').is_file()}


def run(args):
    root=args.root
    started=time.monotonic()
    result={'state':'running','supervisor_pid':os.getpid(),'started_at':time.time()}
    child=None
    deadline=started+args.timeout
    (root/'collection.deadline').write_text(str(deadline))
    try:
        if (root/'stop.requested').exists():
            result.update(outcome='stopped',exit=130)
        else:
            with (root/'run.log').open('ab') as log:
                child=subprocess.Popen(['bash',str(args.script)],stdin=subprocess.DEVNULL,
                                       stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            result['case_pid']=child.pid
            write_json(root/'status.json',result)
            while child.poll() is None:
                if STOP.is_set() or (root/'stop.requested').exists():
                    if child.poll() is not None:break
                    terminate_case(child)
                    result.update(outcome='stopped',exit=130)
                    break
                if time.monotonic()>=deadline:
                    terminate_case(child)
                    result.update(outcome='infrastructure',exit=124,reason='guest case time limit exceeded')
                    break
                try:child.wait(timeout=min(.25,max(.01,deadline-time.monotonic())))
                except subprocess.TimeoutExpired:pass
            if 'outcome' not in result:
                code=child.returncode
                outcome=('passed' if code==0 else 'failed') if (root/'test.started').exists() else 'infrastructure'
                result.update(outcome=outcome,exit=code)
    except Exception as exc:
        if child and child.poll() is None:terminate_case(child)
        result.update(outcome='infrastructure',exit=125,reason=type(exc).__name__+': '+str(exc))
    (root/'exit').write_text(str(result['exit'])+'\n')
    result['state']='collecting'
    write_json(root/'status.json',result)
    try:
        collector=subprocess.Popen([sys.executable,str(args.collector)],stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT,start_new_session=True)
        stop_deadline=None
        while collector.poll() is None:
            if STOP.is_set() or (root/'stop.requested').exists():
                if stop_deadline is None:stop_deadline=time.monotonic()+10
            end=min(deadline,stop_deadline) if stop_deadline else deadline
            if time.monotonic()>=end:
                terminate_case(collector)
                result['collection_error']='collector exceeded '+('stop' if stop_deadline else 'case')+' budget'
                break
            time.sleep(.05)
        if collector.returncode and 'collection_error' not in result:
            result['collection_error']='collector exit '+str(collector.returncode)
    except (OSError,subprocess.SubprocessError) as exc:
        result['collection_error']=type(exc).__name__
    # Test exit was frozen before collection. Stopping collection cannot change
    # the status or disagree with the already persisted/archived exit file.
    result.update(state='finished',seconds=round(time.monotonic()-started,3))
    write_json(root/'status.json',result)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('launch','run','status','stop'))
    parser.add_argument('--root',type=Path,default=Path('/tmp/browser-box'))
    parser.add_argument('--script',type=Path,default=Path('/opt/browser-box/run.sh'))
    parser.add_argument('--collector',type=Path,default=Path('/opt/browser-box/collect.py'))
    parser.add_argument('--timeout',type=float,default=7200)
    args=parser.parse_args(argv)
    args.root.mkdir(parents=True,exist_ok=True)
    if args.action=='status':
        print(json.dumps(status(args.root)));return
    if args.action=='stop':
        (args.root/'stop.requested').touch()
        print(json.dumps({'stop_requested':True}));return
    if args.action=='run':
        for signum in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):
            signal.signal(signum,lambda *_:STOP.set())
        run(args);return
    # Repeating launch after a lost response must never start another case.
    with (args.root/'launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if (args.root/'started.json').exists():
            print(json.dumps(status(args.root)));return
        with (args.root/'supervisor.log').open('ab') as log:
            child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'run',
                                    '--root',str(args.root),'--script',str(args.script),
                                    '--collector',str(args.collector),'--timeout',str(args.timeout)],
                                   stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                   start_new_session=True,close_fds=True)
        write_json(args.root/'started.json',{'supervisor_pid':child.pid})
        print(json.dumps({'started':True,'supervisor_pid':child.pid}))


if __name__=='__main__':main()
