"""Pinned CLI adapters. No inherited CLI setup, no PATH agent executables."""
from dataclasses import replace
import json
import hashlib
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

from jsonschema import validate, ValidationError
from . import PACKAGES, pinned_version
from .base import AdapterError, AdapterPolicyError, Description, LookupTools, Result, Task, image_content, strict_json
from .process import communicate, kill
from .failures import event_failure, diagnostic

HERE = Path(__file__).resolve().parent
# Model-visible tools that OpenCode keeps registered even when permissions deny them.
OPEN_CODE_BUILTINS = {'read','glob','grep','edit','write','patch','shell','webfetch','websearch',
                      'question','skill','subagent','execute','experimental_image_generate','todowrite'}
CLEAN = {'instructions': [], 'skills': [], 'plugins': [], 'hooks': [], 'settings': [],
         'extensions': [], 'prompts': [], 'themes': [], 'commands': []}


def inspect_inventory(inventory, expected):
    from agent_check import OfflineFailure
    if not isinstance(inventory, dict) or set(inventory) != set(expected):
        raise OfflineFailure(['Unrecognized adapter inventory'])
    problems = [key for key in expected if sorted(inventory[key]) != sorted(expected[key])]
    if problems:
        raise OfflineFailure(['Adapter allow-list: ' + ', '.join(sorted(problems))])


def child_environment(settings):
    # Inherited model/config/agent toggles could override the generated launch.
    # Keep ordinary OS utilities plus explicit provider keys, not arbitrary hooks.
    keep = {'PATH','LANG','LC_ALL','LC_CTYPE','TMPDIR','SYSTEMROOT','WINDIR',
            'ANTHROPIC_API_KEY','OPENAI_API_KEY','EXPENSES_FAKE_CONFIG'}
    env = {key: value for key, value in os.environ.items() if key in keep}
    home = settings.agent_home
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home/'config'), XDG_DATA_HOME=str(home/'data'),
               XDG_STATE_HOME=str(home/'state'), XDG_CACHE_HOME=str(home/'cache'), TMPDIR='/tmp',
               PYTHONDONTWRITEBYTECODE='1', NODE_COMPILE_CACHE=str(home/'cache/node'))
    if settings.adapter == 'claude':
        env.update(CLAUDE_CONFIG_DIR=str(home/'config'), CLAUDE_CODE_SAFE_MODE='1',
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', DISABLE_AUTOUPDATER='1')
    elif settings.adapter == 'pi':
        env.update(PI_CODING_AGENT_DIR=str(home/'config'), PI_CODING_AGENT_SESSION_DIR=str(home/'state'),
                   PI_OFFLINE='1', PI_TELEMETRY='0')
    elif settings.adapter == 'opencode':
        env.update(OPENCODE_CONFIG_DIR=str(home/'config/opencode'), OPENCODE_DISABLE_PROJECT_CONFIG='1',
                   OPENCODE_DISABLE_CLAUDE_CODE='1', OPENCODE_DISABLE_MODELS_FETCH='1',
                   OPENCODE_DISABLE_FILEWATCHER='1')
    return env


def home_problems(home):
    # Names only. Do not open credentials, auth files or session stores.
    forbidden = {'AGENTS.md','AGENTS.override.md','CLAUDE.md','settings.json','settings.local.json',
                 'config.toml','config.json','opencode.json','opencode.jsonc','mcp.json','models.json',
                 'extensions','skills','plugins','hooks','commands','agents','rules','prompts','themes',
                 'SYSTEM.md','APPEND_SYSTEM.md','.claude','.agents','.pi','.opencode'}
    problems = set()
    if home.is_symlink():
        return {'home-symlink'}
    # Inspect only config roots, not credential/database/runtime contents.
    for root in (home, home/'config', home/'config/opencode'):
        if root.is_symlink():
            problems.add('config-symlink')
        elif root.is_dir():
            for path in root.iterdir():
                if path.name in forbidden or path.is_symlink():
                    problems.add('forbidden-config-name')
    return problems


class CLI:
    def __init__(self, settings):
        self.settings = settings
        self.id = settings.adapter
        self.test_double = bool(settings.agent_binary and os.environ.get('EXPENSES_FAKE_CONFIG'))
        self.binary = settings.agent_binary or str(settings.application / 'review-dashboard/node_modules/.bin' / PACKAGES[self.id][1])

    def describe(self):
        names = {'claude':'Claude Code','pi':'Pi','opencode':'OpenCode'}
        enforced = self.id != 'opencode'
        how = {'claude':'Dedicated HOME/CLAUDE_CONFIG_DIR; safe and restricted modes; no built-ins; strict lookup MCP; empty settings sources.',
               'pi':'Dedicated HOME/PI_CODING_AGENT_DIR; no built-ins/discovery/MCP; only the application lookup extension.',
               'opencode':'Dedicated XDG roots and private server; deny-all permissions with exact lookup exceptions. Built-in tool definitions and managed customization cannot be removed completely.'}[self.id]
        return Description(self.id,names[self.id],pinned_version(self.settings,PACKAGES[self.id][0]),enforced,how)

    def environment(self):
        from agent_check import OfflineFailure
        home=self.settings.agent_home
        if any(path.is_symlink() for path in (home,home/'config',home/'config/opencode',home/'data',home/'state',home/'cache')):
            raise OfflineFailure(['Dedicated home/config symlink refused'])
        self.settings.agent_home.mkdir(parents=True,exist_ok=True)
        env = child_environment(self.settings)
        for key in ('XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME','XDG_CACHE_HOME','CLAUDE_CONFIG_DIR','PI_CODING_AGENT_DIR','OPENCODE_CONFIG_DIR'):
            if key in env:
                Path(env[key]).mkdir(parents=True,exist_ok=True)
        return env

    def require_binary(self):
        from agent_check import DiagnosticUnavailable
        if not Path(self.binary).is_file():
            raise DiagnosticUnavailable('The pinned agent binary is missing. Run: make dashboard')

    def version(self, work, *, stop=None):
        from agent_check import invoke
        self.require_binary()
        return invoke([self.binary,'--version'],work,self.settings.timeout,environment=self.environment(),stop=stop).strip()

    def expected_inventory(self, lookups):
        tools = sorted(d['name'] for d in lookups.definitions)
        expected = {**CLEAN,'mcp':['ledger'],'tools':tools}
        if self.id == 'pi':
            expected['extensions'] = [str(HERE/'pi-lookup.mjs')]
            expected['hooks'] = ['session_shutdown']
        elif self.id == 'opencode':
            expected['tools'] = sorted(OPEN_CODE_BUILTINS | {'ledger_'+name for name in tools})
        return expected

    def config(self, task):
        if self.id == 'claude':
            return {'mcpServers':{'ledger':{'type':'stdio','command':task.lookups.command[0],
                                          'args':list(task.lookups.command[1:])}}}
        if self.id == 'pi':
            return {'command':list(task.lookups.command),'definitions':list(task.lookups.definitions)}
        permissions = [{'action':'*','resource':'*','effect':'deny'},
                       *({'action':'ledger_'+d['name'],'resource':'*','effect':'allow'} for d in task.lookups.definitions)]
        return {'update':'disable','share':'disabled','snapshots':False,'warming':False,'formatter':False,
                'instructions':[],'skills':[],'plugins':[], 'permissions':permissions,
                'mcp':{'servers':{'ledger':{'type':'local','command':list(task.lookups.command),'codemode':False}}},
                'agents':{'expense':{'mode':'primary','system':'Return only the requested JSON. Use only ledger lookup tools.',
                                     'steps':12,'permissions':permissions}},'default_agent':'expense',
                'experimental':{'todo':False,'background_compaction':False,'image_generate':False,'schedules':False,
                                'artifacts':False,'subagent_depth':0},
                'policies':[{'action':'plugin.use','resource':'*','effect':'deny'},
                            {'action':'skill.use','resource':'*','effect':'deny'},
                            {'action':'mcp.use','resource':'*','effect':'deny'},
                            {'action':'mcp.use','resource':'ledger','effect':'allow'}]}

    def launch(self, task):
        env = self.environment()
        config = self.config(task)
        target = task.work/'lookup-config.json'
        target.write_text(json.dumps(config))
        schema_instruction = '\nReturn only one JSON object matching this schema: '+json.dumps(task.schema)
        if self.id == 'claude':
            command = [self.binary,'-p','--safe-mode','--restricted','--setting-sources','',
                       '--strict-mcp-config','--mcp-config',str(target),'--tools','',
                       '--allowedTools',','.join('mcp__ledger__'+d['name'] for d in task.lookups.definitions),
                       '--disable-slash-commands','--no-session-persistence','--no-chrome',
                       '--permission-mode','dontAsk','--permission-prompts','none',
                       '--json-schema',json.dumps(task.schema),'--output-format','stream-json',
                       '--input-format','stream-json','--verbose','--model',self.settings.model,'--effort',self.settings.effort]
            content = [{'type':'text','text':task.prompt}]
            for path in task.images:
                image = image_content(path)
                content.append({'type':'image','source':{'type':'base64','media_type':image['mimeType'],'data':image['data']}})
            data = json.dumps({'type':'user','message':{'role':'user','content':content}})+'\n'
        elif self.id == 'pi':
            env['EXPENSES_PI_LOOKUP'] = str(target)
            command = [self.binary,'--mode','rpc','--no-session','--no-builtin-tools','--no-extensions',
                       '--no-mcp','--no-skills','--no-prompt-templates','--no-themes','--no-context-files',
                       '--no-approve','--offline','--extension',str(HERE/'pi-lookup.mjs'),
                       '--tools',','.join(d['name'] for d in task.lookups.definitions),
                       '--model',self.settings.model,'--thinking',self.settings.effort]
            data = json.dumps({'id':'expense','type':'prompt','message':task.prompt+schema_instruction,
                               'images':[image_content(path) for path in task.images]})+'\n'
        else:
            env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
            model = self.settings.model + ('#'+self.settings.effort if self.settings.effort else '')
            command = [self.binary,'run','--standalone','--format','json','--agent','expense','--model',model,
                       *(arg for path in task.images for arg in ('--file',str(path))), '--',task.prompt+schema_instruction]
            data = None
        return command,env,data

    def fingerprint(self):
        # Work/ledger paths are intentionally absent; their offered names are hashed by the gate.
        return {'launch policy':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'extension':hashlib.sha256((HERE/'pi-lookup.mjs').read_bytes()).hexdigest() if self.id=='pi' else None,
                'inventory':self.expected_inventory(LookupTools.for_settings(self.settings))}

    def offline_check(self, work, *, stop=None):
        from agent_check import OfflineFailure, invoke, OFFLINE_PROMPT
        self.require_binary()
        self.environment()
        if home_problems(self.settings.agent_home):
            raise OfflineFailure(['Home names: forbidden configuration or symlink'])
        if self.id=='claude':
            managed=[Path('/Library/Application Support/ClaudeCode/managed-settings.json'),
                     Path('/etc/claude-code/managed-settings.json')]
            if any(path.exists() for path in managed):
                raise OfflineFailure(['Managed Claude settings require an isolation policy review'])
        lookups=LookupTools.for_settings(self.settings)
        task=Task(OFFLINE_PROMPT,(),lookups,{'type':'object'},time.monotonic()+self.settings.timeout,
                  stop or threading.Event(),work)
        command,env,_=self.launch(task)
        help_args=['run','--help'] if self.id=='opencode' else ['--help']
        help_text=invoke([self.binary,*help_args],work,self.settings.timeout,environment=env,stop=stop)
        required = {'claude':['--safe-mode','--restricted','--tools','--strict-mcp-config','--setting-sources','--json-schema','--no-session-persistence','--disable-slash-commands','--permission-prompts','--effort'],
                    'pi':['--mode','--no-builtin-tools','--no-extensions','--no-mcp','--no-skills','--no-context-files','--no-prompt-templates','--no-themes','--no-approve','--offline'],
                    'opencode':['--standalone','--format','--file','--model']}[self.id]
        if any(flag not in help_text for flag in required):
            raise OfflineFailure(['Missing isolation flag in pinned CLI help'])
        # CLI/native packages without a no-model prompt renderer are checked by
        # exact generated config and flags. Pi exposes its actual resource loader.
        expected=self.expected_inventory(lookups)
        rendered=json.loads((work/'lookup-config.json').read_text())
        inventory={**CLEAN,'tools':[], 'mcp':[]}
        if self.id=='claude':
            if set(rendered)!={'mcpServers'}:
                raise OfflineFailure(['Unknown generated settings source'])
            inventory['mcp']=list(rendered['mcpServers'])
            inventory['tools']=[n.removeprefix('mcp__ledger__') for n in command[command.index('--allowedTools')+1].split(',')]
            if rendered['mcpServers'].get('ledger') != {'type':'stdio','command':lookups.command[0],'args':list(lookups.command[1:])}:
                raise OfflineFailure(['Lookup MCP configuration mismatch'])
        elif self.id=='pi':
            if set(rendered)!={'command','definitions'} or rendered['command']!=list(lookups.command):
                raise OfflineFailure(['Lookup extension configuration mismatch'])
            inventory.update(mcp=['ledger'],tools=[d['name'] for d in rendered['definitions']],extensions=[str(HERE/'pi-lookup.mjs')],hooks=['session_shutdown'])
        else:
            # Permission and policy rules are order-sensitive: do not merely
            # compare sets or allow a later wildcard to re-enable built-ins.
            permissions=[{'action':'*','resource':'*','effect':'deny'},
                         *({'action':'ledger_'+d['name'],'resource':'*','effect':'allow'} for d in lookups.definitions)]
            if (rendered != self.config(task) or rendered['permissions']!=permissions or
                    rendered.get('agents',{}).get('expense',{}).get('permissions')!=permissions or
                    rendered.get('mcp',{}).get('servers',{}).get('ledger')!={'type':'local','command':list(lookups.command),'codemode':False}):
                raise OfflineFailure(['OpenCode generated policy mismatch'])
            inventory.update(mcp=list(rendered['mcp']['servers']),
                             tools=sorted(OPEN_CODE_BUILTINS | {r['action'] for r in rendered['permissions'] if r['effect']=='allow'}),
                             instructions=rendered['instructions'],skills=rendered['skills'],plugins=rendered['plugins'])
        if self.id=='opencode' and not self.test_double:
            entries=json.loads(invoke([self.binary,'debug','config','--standalone'],work,self.settings.timeout,environment=env,stop=stop))
            documents=[e for e in entries if e.get('type')=='document']
            directories=[e for e in entries if e.get('type')=='directory']
            if (len(entries)!=2 or len(documents)!=1 or len(directories)!=1 or
                    documents[0].get('info') != rendered or documents[0].get('path') is not None or
                    Path(directories[0].get('path','')).resolve()!=Path(env['OPENCODE_CONFIG_DIR']).resolve()):
                raise OfflineFailure(['OpenCode rendered configuration/settings sources differ from allow-list'])
        if self.id=='pi' and not self.test_double:
            package=next((parent for parent in Path(self.binary).resolve().parents
                          if (parent/'package.json').is_file() and json.loads((parent/'package.json').read_text()).get('name')==PACKAGES['pi'][0]),None)
            if package is None:
                raise OfflineFailure(['Pi package root unavailable'])
            env['EXPENSES_PI_PACKAGE']=str(package)
            output=invoke(['node',str(HERE/'pi-render.mjs')],work,self.settings.timeout,environment=env,stop=stop)
            inventory=json.loads(output)
        inspect_inventory(inventory,expected)
        # A second read catches unexpected discovery/config generated by startup.
        if home_problems(self.settings.agent_home):
            raise OfflineFailure(['Home names changed during offline check'])
        print('PASS '+self.id+' offline: flags, generated configuration, tool/discovery allow-lists; no model')
        # Login is deliberately not required for the no-model checker. The gate
        # checks it separately before probing. No auth file is opened here.

    def signed_in(self, work, *, stop=None):
        from agent_check import invoke, CheckCancelled
        if self.id=='claude':
            command=[self.binary,'auth','status','--json']
        elif self.id=='pi':
            command=[self.binary,'auth','check','--model',self.settings.model,'--json','--no-refresh']
        else:
            command=[self.binary,'auth','list','--format','json','--standalone']
        try:
            data=json.loads(invoke(command,work,self.settings.timeout,environment=self.environment(),stop=stop))
            if self.id=='claude': return data.get('loggedIn') is True
            if self.id=='pi': return data.get('status') == 'ready'
            return any(item.get('id') == self.settings.model.split('/')[0] and item.get('connections') for item in data)
        except CheckCancelled: raise
        except (ValueError,OSError): return False

    def login_command(self):
        self.require_binary()
        if self.id=='claude': return [self.binary,'auth','login']
        if self.id=='pi': return [self.binary]  # Owner runs /login in the isolated TUI.
        return [self.binary,'auth','login','--standalone']

    def run(self, task):
        started=time.monotonic();task.remaining()
        command,env,data=self.launch(task)
        process=subprocess.Popen(command,cwd=task.work,env=env,stdin=subprocess.PIPE if data else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:
            if self.id=='pi':
                output=self._rpc(process,task,data)
            else:
                output,err=communicate(process,task,data=data)
            result=self.parse(output,task)
            if process.returncode: raise AdapterError("Agent exited "+str(process.returncode))
            result.duration=time.monotonic()-started
            result.stderr_tail=diagnostic(locals().get('err',getattr(self,'_stderr','')))
            validate(result.answer,task.schema)
            return result
        except subprocess.TimeoutExpired as exc:
            raise AdapterError('Agent deadline exceeded',kind='transient',stderr_tail=diagnostic(exc.stderr or '')) from None
        except AdapterError as exc:
            exc.stderr_tail = diagnostic(locals().get('err', getattr(self, '_stderr', '')))
            raise
        except (ValueError, TypeError, KeyError, OSError, ValidationError):
            raise AdapterError('Agent answer or protocol validation failed',stderr_tail=diagnostic(locals().get('err',getattr(self,'_stderr','')))) from None
        finally:
            if process.poll() is None: kill(process)
            process.communicate()

    def _rpc(self,process,task,data):
        # Keep stdin open until agent_settled; EOF immediately after prompt aborts
        # real RPC runtimes. Both pipes are drained to avoid backpressure deadlock.
        records=queue.Queue();lines=[];stderr=[];self._stderr=""
        def read(pipe,stdout):
            for line in iter(pipe.readline,''):
                if stdout: records.put(line)
                else:
                    stderr.append(line);stderr[:]=stderr[-40:];self._stderr="".join(stderr)[-4000:]
            if stdout: records.put(None)
        threads=[threading.Thread(target=read,args=(process.stdout,True),daemon=True),
                 threading.Thread(target=read,args=(process.stderr,False),daemon=True)]
        for thread in threads: thread.start()
        process.stdin.write(data);process.stdin.flush()
        try:
            while True:
                task.remaining()
                try: line=records.get(timeout=min(.05,task.remaining()))
                except queue.Empty: continue
                if line is None: raise AdapterError('Pi exited before agent_settled')
                lines.append(line)
                event=json.loads(line)
                if event.get('type')=='response' and not event.get('success'):
                    raise event_failure([event])
                if event.get('type')=='agent_settled':
                    break
            process.stdin.close();process.stdin=None
            process.wait(timeout=task.remaining())
        except BaseException:
            try:
                process.stdin.write('{"id":"cancel","type":"abort"}\n');process.stdin.flush()
                # Give the RPC peer a bounded acknowledgement window; a provider
                # that ignores abort still gets process-group termination.
                until=time.monotonic()+.25
                while time.monotonic()<until:
                    try: acknowledgement=records.get(timeout=max(.001,until-time.monotonic()))
                    except queue.Empty:break
                    if acknowledgement is None:break
                    if json.loads(acknowledgement).get('command')=='abort':break
            except (OSError,ValueError,AttributeError): pass
            kill(process);process.wait()
            raise
        finally:
            for thread in threads: thread.join(timeout=1)
        return ''.join(lines)

    def parse(self, output, task):
        usage,calls,text={},[],[];answer=None;finished=False;pending={}
        probe = task.schema.get('required') == ['account', 'shell_unavailable']
        message_texts={};final_message=None
        allowed={d['name'] for d in task.lookups.definitions}
        def call(name,args=None,result=None,failed=False):
            if name not in allowed: raise AdapterPolicyError('Agent exposed a tool outside the lookup allow-list',usage=usage,lookups=calls)
            calls.append(dict(type='mcp_tool_call',server='ledger',tool=name,arguments=args,
                              status='failed' if failed else 'completed',error='Lookup failed' if failed else None,result=result))
            if failed: raise AdapterError('Agent lookup failed',usage=usage,lookups=calls)
        def add_usage(values):
            for key,value in (values or {}).items():
                if isinstance(value,(int,float)):usage[key]=usage.get(key,0)+value
        failures=[]
        for line in output.splitlines():
            try: event=json.loads(line)
            except ValueError: continue
            kind=event.get('type')
            if (kind=='error' or self.id=='claude' and kind=='result' and (event.get('is_error') or event.get('subtype')!='success') or
                    self.id=='pi' and kind=='message_end' and event.get('message',{}).get('stopReason') in ('error','aborted')):
                failures.append(event)
        for line in output.split('\n'):
            if not line.strip():continue
            event=json.loads(line);kind=event.get('type')
            if kind=='error':continue
            if self.id=='claude':
                if kind=='system' and event.get('subtype')=='init':
                    extra=set(event.get('tools',[]))-{'mcp__ledger__'+n for n in allowed}
                    if extra: raise AdapterPolicyError('Agent startup exposed extra tools')
                    if set(s.get('name') for s in event.get('mcp_servers',[])) - {'ledger'}:
                        raise AdapterPolicyError('Agent startup exposed extra MCP servers')
                if kind=='assistant':
                    message=event.get('message',{});add_usage(message.get('usage'))
                    for block in message.get('content',[]):
                        if block.get('type')=='tool_use':
                            name=block['name']
                            if not name.startswith('mcp__ledger__') or name.removeprefix('mcp__ledger__') not in allowed:
                                raise AdapterPolicyError('Agent exposed a tool outside the lookup allow-list')
                            pending[block['id']] = (name.removeprefix('mcp__ledger__'),block.get('input'))
                if kind=='user':
                    for block in event.get('message',{}).get('content',[]):
                        if block.get('type')=='tool_result':
                            name,args=pending.pop(block['tool_use_id'])
                            call(name,args,block.get('content'),block.get('is_error',False))
                if kind=='result':
                    if event.get('is_error') or event.get('subtype')!='success':
                        usage={};add_usage(event.get('usage'));continue
                    answer=event.get('structured_output')
                    if answer is None:answer=strict_json(event.get('result',''))
                    # result.usage is the authoritative total, not another turn.
                    usage={};add_usage(event.get('usage'));finished=True
            elif self.id=='pi':
                if kind=='tool_execution_start':
                    if event['toolName'] not in allowed:raise AdapterPolicyError('Agent exposed a tool outside the lookup allow-list')
                    pending[event['toolCallId']]=event.get('args')
                if kind=='tool_execution_end':call(event['toolName'],pending.pop(event['toolCallId'],None),event.get('result'),event.get('isError',False))
                if kind=='message_end' and event.get('message',{}).get('role')=='assistant':
                    message=event['message'];add_usage(message.get('usage'))
                    if message.get('stopReason') in ('error','aborted'):continue
                    text=[''.join(b.get('text','') for b in message.get('content',[]) if b.get('type')=='text')]
                if kind=='agent_settled':finished=not event.get('aborted')
            else:
                part=event.get('part',{})
                if kind=='tool_use':
                    name=part.get('tool','');state=part.get('state',{})
                    if name=='shell' and probe and state.get('status')=='error' and 'denied' in state.get('error','').lower():
                        continue
                    if not name.startswith('ledger_'):raise AdapterPolicyError('Agent exposed a tool outside the lookup allow-list')
                    call(name.removeprefix('ledger_'),state.get('input'),state.get('output'),state.get('status')!='completed')
                if kind=='text':
                    parts=message_texts.setdefault(part.get('messageID','final'),{})
                    parts[part.get('id') or part.get('partID') or str(len(parts))]=part.get('text','')
                if kind=='step_finish':
                    add_usage(part.get('tokens'));finished=part.get('reason') in ('stop','end_turn')
                    if finished:final_message=part.get('messageID')
        if failures: raise event_failure(failures,usage=usage,lookups=calls)
        if self.id=='opencode' and message_texts:
            # Progress before a lookup belongs to an earlier assistant message.
            # Validate only the final message, retaining all of its text parts.
            text=list(message_texts.get(final_message or next(reversed(message_texts)),{}).values())
        if not finished or pending:raise AdapterError('Agent final response or tool results missing',usage=usage,lookups=calls)
        if answer is None:answer=strict_json(''.join(text))
        return Result(answer,usage,calls)

    def live_probe(self,work,account,*,stop=None):
        from agent_check import PROBE_PROMPT,PROBE_SCHEMA,DiagnosticUnavailable
        task=Task(PROBE_PROMPT,(),LookupTools.for_settings(self.settings),PROBE_SCHEMA,
                  time.monotonic()+self.settings.timeout,stop or threading.Event(),work)
        result=CLI(replace(self.settings,effort='low')).run(task)
        if result.answer!={'account':account,'shell_unavailable':True} or not any(
                c['tool']=='list_accounts' and account in json.dumps(c.get('result')) for c in result.lookups):
            raise DiagnosticUnavailable('Probe account, shell refusal, or lookup evidence failed')
        print('PASS '+self.id+' probe: lookup evidence, shell unavailable, valid JSON')
