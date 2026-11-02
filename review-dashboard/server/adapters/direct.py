"""Bounded direct API tool loop. Only in-process read-only lookups execute."""
import asyncio
import json
import hashlib
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlparse
import httpx
from jsonschema import validate
from .failures import http_reset, diagnostic
from .base import AdapterError, AdapterPolicyError, Description, LookupTools, Result, Task, image_content, strict_json


class DirectAPI:
    def __init__(self, settings):
        self.settings=settings
        self.id=settings.adapter
        self.options=settings.api
        self.url=self.options.get('baseUrl','https://api.anthropic.com/v1' if self.id=='anthropic' else '')
        self.limits={name:self.options.get(name,default) for name,default in {
            'maxTurns':12,'maxTools':40,'maxTokens':100000,'maxOutputTokens':8192,'retries':2}.items()}
        if any(type(n) is not int or n<0 or n>1000000 for n in self.limits.values()) or any(
                self.limits[k]==0 for k in ('maxTurns','maxTools','maxTokens','maxOutputTokens')) or self.limits['retries']>5:
            raise ValueError('Invalid direct API limits')
        parsed=urlparse(self.url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or (parsed.scheme!='https' and not (
                parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1','::1'))):
            raise ValueError('Direct API requires an HTTPS base URL (loopback HTTP is allowed for offline tests)')
        if not settings.model:
            raise ValueError('Direct API model must be configured')
        if any(type(self.options.get(k,True)) is not bool for k in ('structuredOutput','sendEffort')):
            raise ValueError('structuredOutput must be boolean')

    def describe(self):
        return Description(self.id,'Anthropic API' if self.id=='anthropic' else 'OpenAI-compatible API',
                           'interface-1',True,'Server-owned bounded tool loop; only explicit in-process read-only lookups; no CLI, plugins, instruction discovery or shell.')

    def environment(self):return {}
    def version(self,work,*,stop=None):return 'direct-api-interface-1'
    def fingerprint(self):
        return {'provider':self.id,'base URL':self.url,'options':self.options,
                'tool-loop policy':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}

    def key(self):
        # Never read a conventional agent credential file. Only explicit machine
        # API secret files are supported, and errors cannot include their contents.
        name=self.options.get('keyEnv','ANTHROPIC_API_KEY' if self.id=='anthropic' else 'OPENAI_API_KEY')
        if not isinstance(name,str) or not __import__('re').fullmatch('[A-Za-z_][A-Za-z0-9_]*',name):
            raise ValueError('Invalid API key environment variable name')
        value=os.environ.get(name,'')
        if not value and self.options.get('keyFile'):
            path=Path(self.options['keyFile']).expanduser()
            if not path.is_absolute():path=self.settings.application/path
            if path.name=='auth.json' or any(p in ('.codex','.claude','.pi','opencode') for p in path.parts):
                raise ValueError('Use a dedicated API key file, not an agent credential store')
            try:value=path.read_text().strip()
            except OSError:raise ValueError('Configured API key file is unavailable') from None
        return value

    def signed_in(self,work,*,stop=None):return bool(self.key())
    def login_command(self):return []  # Notice/docs explain the configured env/file.

    def inventory(self,lookups):
        return {'tools':sorted(d['name'] for d in lookups.definitions),'mcp':[],
                'instructions':[],'skills':[],'plugins':[],'hooks':[],'settings':[],'extensions':[],'prompts':[],'themes':[],'commands':[]}

    def offline_check(self,work,*,stop=None):
        from .cli import inspect_inventory
        lookups=LookupTools.for_settings(self.settings)
        allowed={'search_entries','entries_near','list_accounts','search_old_ledger'}
        expected=self.inventory(lookups)
        expected['tools']=sorted(d['name'] for d in lookups.definitions if d['name'] in allowed)
        inspect_inventory(self.inventory(lookups),expected)
        print('PASS '+self.id+' offline: in-process lookup allow-list, limits, endpoint; no HTTP request')

    def response_errors(self, response, headers):
        # Keep structured provider diagnostics, never echoed credential values.
        try:
            body=response.json()
            error=body.get('error') if isinstance(body,dict) else None
        except ValueError: error=None
        message=diagnostic(error) if error else 'HTTP '+str(response.status_code)
        for value in headers.values():
            if value and value not in ('2023-06-01',):
                message=message.replace(value, '[redacted]')
                if value.startswith('Bearer '):message=message.replace(value[7:], '[redacted]')
        return [message]

    async def request(self,client,task,payload,headers):
        endpoint='/messages' if self.id=='anthropic' else '/chat/completions'
        task.remaining()
        request=asyncio.create_task(client.post(self.url.rstrip('/')+endpoint,json=payload,headers=headers))
        try:
            while not request.done():
                task.remaining()
                await asyncio.wait({request},timeout=min(.05,task.remaining()))
            response=await request
            if response.status_code==429:
                raise AdapterError('API usage limit (HTTP 429)',kind='usage_limit',reset_at=http_reset(response.headers),errors=self.response_errors(response,headers))
            if response.status_code in (401,403):
                raise AdapterError('API authentication was refused',kind='auth',errors=self.response_errors(response,headers))
            if 500<=response.status_code<=599:
                raise AdapterError('API server unavailable',kind='transient',errors=self.response_errors(response,headers))
            if response.status_code>=400:
                raise AdapterError('API request refused (HTTP '+str(response.status_code)+'); response withheld',errors=self.response_errors(response,headers))
            return response.json()
        except httpx.TransportError:
            raise AdapterError('API transport unavailable',kind='transient') from None
        finally:
            if not request.done():request.cancel()
            await asyncio.gather(request,return_exceptions=True)

    def run(self,task):
        return asyncio.run(self._run(task))

    async def _run(self,task):
        started=time.monotonic();usage={};lookups=[]
        key=self.key()
        if not key:raise AdapterError('API key is not configured', kind='auth')
        headers={'x-api-key':key,'anthropic-version':'2023-06-01'} if self.id=='anthropic' else {'Authorization':'Bearer '+key}
        system='Use only the provided read-only ledger lookups. Return one JSON object matching the output schema.\n'+json.dumps(task.schema)
        images=[image_content(path) for path in task.images]
        if self.id=='anthropic':
            content=[{'type':'text','text':task.prompt},*({'type':'image','source':{'type':'base64','media_type':i['mimeType'],'data':i['data']}} for i in images)]
            messages=[{'role':'user','content':content}]
            tools=[{'name':d['name'],'description':d['description'],'input_schema':d['inputSchema']} for d in task.lookups.definitions]
        else:
            content=[{'type':'text','text':task.prompt},*({'type':'image_url','image_url':{'url':'data:'+i['mimeType']+';base64,'+i['data']}} for i in images)]
            messages=[{'role':'system','content':system},{'role':'user','content':content}]
            tools=[{'type':'function','function':{'name':d['name'],'description':d['description'],'parameters':d['inputSchema']}} for d in task.lookups.definitions]
        definitions={d['name']:d for d in task.lookups.definitions}
        try:
            # No proxy inheritance, redirects or logging of headers/response bodies.
            async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=None) as client:
                for turn in range(self.limits['maxTurns']):
                    task.remaining()
                    # A conservative payload-size bound also prevents huge lookup
                    # responses from bypassing a provider's reported token usage.
                    if len(json.dumps(messages))>self.limits['maxTokens']*16:
                        raise AdapterError('API context size limit reached')
                    payload=dict(model=self.settings.model,messages=messages,tools=tools)
                    output_limit=min(self.limits['maxOutputTokens'],self.limits['maxTokens']-sum(usage.values()))
                    if output_limit<=0:raise AdapterError('API token limit reached')
                    if self.id=='anthropic':
                        payload.update(system=system,max_tokens=output_limit)
                        if self.options.get('sendEffort',True):payload['output_config']={'effort':self.settings.effort}
                        if self.options.get('structuredOutput',True):payload.setdefault('output_config',{})['format']={'type':'json_schema','schema':task.schema}
                    else:
                        payload['max_completion_tokens']=output_limit
                        if self.options.get('sendEffort',True):payload['reasoning_effort']=self.settings.effort
                        if self.options.get('structuredOutput',True):payload['response_format']={'type':'json_schema','json_schema':{'name':'expense_answer','schema':task.schema,'strict':True}}
                    data=await self.request(client,task,payload,headers)
                    for k,v in data.get('usage',{}).items():
                        if k in ('input_tokens','output_tokens','prompt_tokens','completion_tokens') and isinstance(v,int):usage[k]=usage.get(k,0)+v
                    if sum(usage.values())>self.limits['maxTokens']:raise AdapterError('API token limit reached')
                    if self.id=='anthropic':
                        blocks=data.get('content',[])
                        calls=[dict(id=b['id'],name=b['name'],arguments=b['input']) for b in blocks if b.get('type')=='tool_use']
                        text=''.join(b.get('text','') for b in blocks if b.get('type')=='text')
                        if any(b.get('type') not in ('text','tool_use','thinking','redacted_thinking') for b in blocks):raise AdapterError('API returned an unsupported content block')
                        if data.get('stop_reason') in ('refusal','max_tokens'):raise AdapterError('API refused or truncated the answer')
                        messages.append({'role':'assistant','content':blocks})
                    else:
                        choice=data['choices'][0];message=choice['message']
                        if message.get('refusal') or choice.get('finish_reason') in ('length','content_filter'):raise AdapterError('API refused or truncated the answer')
                        calls=[dict(id=c['id'],name=c['function']['name'],arguments=strict_json(c['function']['arguments'])) for c in message.get('tool_calls',[])]
                        text=message.get('content') or ''
                        messages.append({k:v for k,v in message.items() if k in ('role','content','tool_calls')})
                    if not calls:
                        answer=strict_json(text);validate(answer,task.schema)
                        return Result(answer,usage,lookups,duration=time.monotonic()-started)
                    if len(lookups)+len(calls)>self.limits['maxTools']:raise AdapterError('API tool-call limit reached')
                    results=[]
                    for call in calls:
                        task.remaining()
                        if call['name'] not in definitions:raise AdapterPolicyError('API requested a tool outside the lookup allow-list')
                        validate(call['arguments'],definitions[call['name']]['inputSchema'])
                        result=task.lookups.call(call['name'],call['arguments'])
                        task.remaining()
                        lookups.append(dict(type='mcp_tool_call',server='ledger',tool=call['name'],arguments=call['arguments'],
                                            status='completed',error=None,result=result))
                        if self.id=='anthropic':results.append({'type':'tool_result','tool_use_id':call['id'],'content':json.dumps(result)})
                        else:messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(result)})
                    if results:messages.append({'role':'user','content':results})
            raise AdapterError('API turn limit reached')
        except AdapterError as exc:
            exc.usage=usage;exc.lookups=lookups;raise
        except Exception:
            # Provider errors, malformed JSON, schema diagnostics and HTTP errors
            # can include keys or private text; expose none of those values.
            raise AdapterError('API response or lookup validation failed; contents withheld',usage=usage,lookups=lookups) from None

    def live_probe(self,work,account,*,stop=None):
        from agent_check import PROBE_PROMPT,PROBE_SCHEMA,DiagnosticUnavailable
        task=Task(PROBE_PROMPT,(),LookupTools.for_settings(self.settings),PROBE_SCHEMA,
                  time.monotonic()+self.settings.timeout,stop or threading.Event(),work)
        result=self.run(task)
        if result.answer!={'account':account,'shell_unavailable':True} or not any(
                c['tool']=='list_accounts' and account in json.dumps(c.get('result')) for c in result.lookups):
            raise DiagnosticUnavailable('Probe account, shell refusal, or lookup evidence failed')
        print('PASS '+self.id+' probe: lookup evidence, shell unavailable, valid JSON')
