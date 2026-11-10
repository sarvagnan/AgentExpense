"""Offline protocol doubles for the non-Codex CLIs. Never loads provider code."""
import json
import os
from pathlib import Path
import sys
import time


def run(settings,config):
    adapter=settings['adapter'];args=sys.argv[1:]
    def emit(value):print(json.dumps(value),flush=True)
    def record(suffix,value):
        with config.with_suffix(suffix).open('a') as handle:handle.write(json.dumps(value)+'\n')
    diagnostic=(args==['--version'] or '--help' in args or args[:2] in (['auth','status'],['auth','check'],['auth','list']))
    if diagnostic:
        assert sys.stdin.read()==''
        record('.checks',dict(argv=args,home=os.environ['HOME'],cwd=os.getcwd()))
        if args==['--version']:print(settings.get('version','fixture-1'))
        elif '--help' in args:print('--safe-mode --restricted --setting-sources --tools --strict-mcp-config --json-schema --no-session-persistence --disable-slash-commands --permission-prompts --effort --mode --no-builtin-tools --no-extensions --no-mcp --no-skills --no-context-files --no-prompt-templates --no-themes --no-approve --offline --standalone --format --file --model')
        elif adapter=='claude':emit({'loggedIn':not settings.get('signed_out',False)})
        elif adapter=='pi':emit({'status':'not_ready' if settings.get('signed_out') else 'ready'})
        else:emit([] if settings.get('signed_out') else [{'id':'anthropic','connections':[{'type':'credential'}]}])
        return
    if adapter=='claude':
        message=json.loads(sys.stdin.read());prompt=message['message']['content'][0]['text']
        schema=json.loads(args[args.index('--json-schema')+1])
        lookup=json.loads(Path(args[args.index('--mcp-config')+1]).read_text())['mcpServers']['ledger']['args']
    elif adapter=='pi':
        message=json.loads(sys.stdin.readline());prompt=message['message'];schema={}
        lookup=json.loads(Path(os.environ['EXPENSES_PI_LOOKUP']).read_text())['command']
    else:
        prompt=sys.stdin.read();message=prompt;schema={}
        assert args[-1]=='--' and prompt
        lookup=json.loads(os.environ['OPENCODE_CONFIG_CONTENT'])['mcp']['servers']['ledger']['command']
    probe='Isolation test using a fictional ledger.' in prompt
    ledger=Path(lookup[lookup.index('--ledger')+1]);account=next(iter(json.loads((ledger/'data/accounts.json').read_text())['accounts']))
    if probe:
        answer={'account':account,'shell_unavailable':True};step={}
        if settings.get('probe_mode')=='shell_available':answer['shell_unavailable']=False
        record('.checks',dict(argv=args,home=os.environ['HOME'],cwd=os.getcwd()))
    else:
        path=config.with_suffix('.count');count=int(path.read_text()) if path.exists() else 0;path.write_text(str(count+1))
        step=settings['steps'][min(count,len(settings['steps'])-1)];answer=step.get('answer',{})
        record('.calls',dict(argv=args,home=os.environ['HOME'],cwd=os.getcwd(),input=message,schema=schema))
    kind=step.get('kind')
    marker=config.with_suffix('.started');temporary=marker.with_name(marker.name+'.'+str(os.getpid()))
    temporary.write_text(str(os.getpid()));temporary.replace(marker)
    if kind=='timeout':
        # Abort is observable in RPC mode; other adapters are killed as groups.
        if adapter=='pi':
            abort=json.loads(sys.stdin.readline());record('.abort',abort);emit({'type':'response','id':'cancel','command':'abort','success':True})
            while True:time.sleep(1)
        else:time.sleep(30)
    if kind=='provider_error':
        emit({'type':'message_end','message':{'role':'assistant','stopReason':'error','errorMessage':step['error']['message']}} if adapter=='pi' and step.get('pi_message_error') else {'type':'error','error':step['error']});print('bounded fixture stderr',file=sys.stderr,flush=True)
        if adapter=='pi': emit({'type':'agent_settled','aborted':bool(step.get('pi_message_error'))});assert sys.stdin.read()==''
        sys.exit(step.get('exit_code',0))
    if kind in ('invalid','noanswer','exit'):print('bounded bad-answer stderr',file=sys.stderr,flush=True)
    if kind=='exit':sys.exit(7)
    if kind=='noanswer':return
    tool='shell' if settings.get('extra_tool') else 'list_accounts'
    result=[{'account':account}]
    text='invalid json' if kind=='invalid' else json.dumps(answer)
    if adapter=='claude':
        emit({'type':'system','subtype':'init','tools':['mcp__ledger__'+tool], 'mcp_servers':[{'name':'ledger'}]})
        emit({'type':'assistant','message':{'content':[{'type':'tool_use','id':'lookup1','name':'mcp__ledger__'+tool,'input':{'contains':'AgentCheck'}}]}})
        emit({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'lookup1','content':json.dumps(result)}]}})
        emit({'type':'result','subtype':'error' if kind=='error' else 'success','is_error':kind=='error',
              'structured_output':None if kind=='invalid' else answer,'result':text,'usage':{'input_tokens':123,'output_tokens':45}})
    elif adapter=='pi':
        emit({'type':'response','id':'expense','command':'prompt','success':True,'data':{'disposition':'started'}})
        emit({'type':'tool_execution_start','toolCallId':'lookup1','toolName':tool,'args':{'contains':'AgentCheck'}})
        emit({'type':'tool_execution_end','toolCallId':'lookup1','toolName':tool,'result':{'content':[{'type':'text','text':json.dumps(result)}]},'isError':kind=='error'})
        emit({'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':text}],'usage':{'input':123,'output':45},'stopReason':'stop'}})
        emit({'type':'agent_settled','aborted':False})
        assert sys.stdin.read()==''
    else:
        if settings.get('intermediate_text'):
            emit({'type':'text','part':{'messageID':'lookup-message','id':'progress','text':'Reading fictional accounts.'}})
            emit({'type':'step_finish','part':{'messageID':'lookup-message','reason':'tool-calls','tokens':{}}})
        emit({'type':'tool_use','part':{'tool':'ledger_'+tool,'state':{'status':'completed','input':{'contains':'AgentCheck'},'output':json.dumps(result)}}})
        if kind=='error':emit({'type':'error','error':{'message':'PRIVATE PROVIDER ERROR'}})
        emit({'type':'text','part':{'messageID':'final-message','id':'answer','text':text}})
        emit({'type':'step_finish','part':{'messageID':'final-message','reason':'stop','tokens':{'input':123,'output':45}}})
