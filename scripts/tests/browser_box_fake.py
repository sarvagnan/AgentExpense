"""Executable Incus boundary double: local files only, no containers/network."""
import fcntl
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import time
import signal
import subprocess


def archive_payload(code,config):
    payload=io.BytesIO()
    with tarfile.open(fileobj=payload,mode='w:gz') as archive:
        for name,data in [('exit',(str(code)+'\n').encode()),('run.log',b'fake case completed\n'),('server.log',b'fixture server log\n'),
                          ('artifacts/failure-partial.zip',b'partial trace'),
                          ('collection.json',json.dumps({'partial':bool(config.get('collection_partial')),
                              'skipped':['shots/late.png'] if config.get('collection_partial') else []}).encode())]:
            member=tarfile.TarInfo(name);member.size=len(data);archive.addfile(member,io.BytesIO(data))
    return payload.getvalue()


root=Path(os.environ['BOX_FAKE_HOME'])
args=sys.argv[1:]
assert args[:2]==['--project','agentexpense-ci'],args
args=args[2:]
assert any(arg.startswith('configured:') for arg in args),args
operation=(' '.join(args[:2]) if args[0] in ('file','project','image') else args[0])
if args[0]=='exec' and '/opt/browser-box/guest_case.py' in args:
    operation=args[args.index('/opt/browser-box/guest_case.py')+1]
with (root/'lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    store=root/'instances.json'; counts=root/'counts.json'
    instances=json.loads(store.read_text()) if store.exists() else {}
    counters=json.loads(counts.read_text()) if counts.exists() else {}
    config=json.loads((root/'faults.json').read_text()) if (root/'faults.json').exists() else {}
    with (root/'commands.jsonl').open('a') as output:output.write(json.dumps(args)+'\n')
    counters[operation]=counters.get(operation,0)+1
    def save():
        for path,value in ((store,instances),(counts,counters)):
            temporary=path.with_suffix('.next');temporary.write_text(json.dumps(value));temporary.replace(path)
    failure=config.get('failures',{}).get(operation,0)
    offset=config.get('fail_after',{}).get(operation,0)
    if offset<counters[operation]<=offset+failure:
        if operation=='file pull' and config.get('partial_failure'):
            data=archive_payload(0,config)
            Path(args[-1]).write_bytes(data[:len(data)//2]);(root/'prefix-written').touch()
        save()
        if operation in config.get('failure_stdout',{}):print(config['failure_stdout'][operation],flush=True)
        print(config.get('failure_texts',{}).get(operation,'Error: dial tcp fictional.invalid:8443: i/o timeout'),file=sys.stderr);sys.exit(1)
    if operation in config.get('permanent_errors',{}):
        save();print(config['permanent_errors'][operation],file=sys.stderr);sys.exit(1)
    wait=False; wait_seconds=30; late_marker=None; drop=False; response=''
    if args[:2]==['project','show']:response='name: agentexpense-ci'
    elif args[:2]==['project','get']:
        response={'features.images':'true','features.profiles':'true','limits.cpu':'4','limits.memory':'6GiB','limits.containers':'2'}[args[-1]]
    elif args[:2]==['image','list']:
        alias=args[3]
        response=json.dumps([{'fingerprint':alias,'aliases':[{'name':alias}], 'properties':{'user.browser-box.hash':alias.removeprefix('browser-box-')}}])
    elif args[0]=='list':
        selected=list(instances.values())
        if len(args)>2 and args[2].startswith('^'):selected=[row for row in selected if re.match(args[2],row['name'])]
        response=json.dumps(selected)
    elif args[0]=='launch':
        # Global Incus launch, distinct from guest launch below.
        spec=json.load(sys.stdin);name=args[2].split(':',1)[1]
        instances[name]={'name':name,'status':'Running','config':spec['config'],'expanded_devices':spec['devices']}
        if config.get('late_launch'):
            instance=instances.pop(name)
            late_marker=root/'late-created'
            program=('import fcntl,json,time,sys\nfrom pathlib import Path\n'
                'r=Path(sys.argv[1]);item=json.loads(sys.argv[2]);time.sleep(2)\n'
                'with (r/"lock").open("a") as lock:\n'
                ' fcntl.flock(lock,fcntl.LOCK_EX)\n'
                ' p=r/"instances.json";rows=json.loads(p.read_text());rows[item["name"]]=item\n'
                ' t=p.with_suffix(".next");t.write_text(json.dumps(rows));t.replace(p)\n'
                '(r/"late-created").touch()\n')
            subprocess.Popen([sys.executable,'-B','-c',program,str(root),json.dumps(instance)],
                             stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            (root/'launch-entered').touch();wait=True
        if config.get('hold_launch'):
            (root/'launch-entered').touch();wait=True;wait_seconds=2
    elif args[0] in ('stop','delete'):
        if config.get('poison_cleanup'):
            blocked=root/'blocked-cleanup'
            if not blocked.exists():blocked.write_text(args[1].split(':',1)[1])
            if blocked.read_text()==args[1].split(':',1)[1]:
                save();print('Error: storage busy',file=sys.stderr);sys.exit(1)
        instances.pop(args[1].split(':',1)[1],None)
    elif args[0]=='exec':
        name=args[1].split(':',1)[1]
        if name not in instances:
            save();print('Error: Instance not found',file=sys.stderr);sys.exit(1)
        item=instances[name]
        if item.get('guest',{}).get('state')=='running' and (root/'release').exists() and (
                not config.get('finish_first_only') or item.get('ordinal')==1 or (root/'release-second').exists()):
            code=config.get('test_exit',0)
            item['guest']={'state':'finished','exit':code,'outcome':'passed' if code==0 else 'failed'}
            if config.get('guest_timeout'):
                item['guest']={'state':'finished','exit':124,'outcome':'infrastructure','reason':'guest case time limit exceeded'}
        if operation=='launch':
            if not item.get('guest'):
                item['guest']={'state':'running'}
                counters['guest_starts']=counters.get('guest_starts',0)+1
                item['ordinal']=counters['guest_starts']
                (root/'entered.json').write_text(json.dumps({'pid':os.getpid(),'container':args[1]}))
            if os.environ.get('BOX_DROP_EXEC') and not (root/'dropped').exists():
                (root/'dropped').touch();drop=True
            response=json.dumps({'started':True})
        elif operation=='status':
            if config.get('hang_status'):
                (root/'client-entered.json').write_text(json.dumps({'pid':os.getpid()}))
                signal.signal(signal.SIGTERM,signal.SIG_IGN)
                wait=True
            if config.get('lose_on_poll') and not (root/'lost').exists():
                (root/'lost').touch();instances.pop(name)
                save();print('Error: Instance not found',file=sys.stderr);sys.exit(1)
            response=json.dumps(item.get('guest',{'state':'missing'}))
            if 'status_reply' in config:response=config['status_reply']
        elif operation=='stop':
            if item.get('guest',{}).get('state') not in ('finished','collecting'):
                item['guest']={'state':'finished','exit':130,'outcome':'stopped'}
            response=json.dumps({'stop_requested':True})
    elif args[:2]==['file','pull']:
        name=args[2].split(':',1)[1].split('/',1)[0]
        if name not in instances:
            save();print('Error: Instance not found',file=sys.stderr);sys.exit(1)
        if config.get('collection_error') and args[2].endswith('/results.tar.gz'):
            save();print('Error: archive deliberately unavailable',file=sys.stderr);sys.exit(1)
        if config.get('partial_pull_bytes'):
            Path(args[-1]).write_bytes(b'R'*config['partial_pull_bytes'])
            save();print('Error: connection reset by peer: i/o timeout',file=sys.stderr);sys.exit(1)
        if config.get('hold_collect') and counters['file pull']==1:
            (root/'collect-entered').touch();wait=True
        else:
            code=instances[name].get('guest',{}).get('exit',125)
            data=archive_payload(code,config)
            duration=config.get('slow_pull_seconds',0)
            with open(args[-1],'wb') as destination:
                for index in range(64 if duration else 1):
                    pieces=64 if duration else 1
                    destination.write(data[len(data)*index//pieces:len(data)*(index+1)//pieces])
                    destination.flush()
                    if duration:
                        with (root/'pull-progress.jsonl').open('a') as progress:
                            progress.write(json.dumps({'bytes':destination.tell(),'at':time.monotonic()})+'\n')
                        time.sleep(duration/pieces)
    elif args[:2]==['file','push']:pass
    else:raise AssertionError(args)
    save()
if drop:
    print('Error: Lost connection to the event listener: i/o timeout',file=sys.stderr);sys.exit(1)
if wait:
    deadline=time.monotonic()+wait_seconds
    while not (late_marker or root/'release-operation').exists() and time.monotonic()<deadline:time.sleep(.01)
print(response)
