// The only extension loaded by the Pi adapter. No model or provider code here.
import {readFileSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {Type} from '@earendil-works/pi-ai';
const allowed=new Set(['search_entries','entries_near','list_accounts','search_old_ledger']);
export default function(pi) {
  const config=JSON.parse(readFileSync(process.env.EXPENSES_PI_LOOKUP,'utf8'));
  let child,sequence=0,buffer='';const pending=new Map();
  function close(){child?.kill('SIGKILL');child=undefined;for(const {reject} of pending.values())reject(new Error('Lookup stopped'));pending.clear();}
  function start(){
    if(child)return;
    child=spawn(config.command[0],config.command.slice(1),{stdio:['pipe','pipe','ignore']});
    child.stdout.setEncoding('utf8');
    child.stdout.on('data',data=>{buffer+=data;let end;while((end=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,end);buffer=buffer.slice(end+1);if(!line.trim())continue;let response;try{response=JSON.parse(line);}catch{close();return;}const item=pending.get(response.id);if(item){pending.delete(response.id);response.error?item.reject(new Error('Lookup protocol failed')):item.resolve(response.result);}}});
    child.on('error',close);child.on('exit',close);
  }
  function request(method,params,signal){
    start();const id=++sequence;
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{pending.delete(id);reject(new Error('Lookup deadline exceeded'));close();},20000);
      const abort=()=>{pending.delete(id);reject(new Error('Lookup cancelled'));close();};
      if(signal?.aborted){clearTimeout(timer);reject(new Error('Lookup cancelled'));return;}
      signal?.addEventListener('abort',abort,{once:true});
      const finish=fn=>value=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);fn(value);};
      pending.set(id,{resolve:finish(resolve),reject:finish(reject)});
      child.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method,params})+'\n');
    });
  }
  let initialized;
  async function ready(signal){
    initialized ??= (async()=>{await request('initialize',{protocolVersion:'2025-06-18',capabilities:{},clientInfo:{name:'expense-pi',version:'1'}},signal);child.stdin.write(JSON.stringify({jsonrpc:'2.0',method:'notifications/initialized'})+'\n');const list=await request('tools/list',{},signal);if(JSON.stringify(list.tools.map(t=>t.name).sort())!==JSON.stringify(config.definitions.map(t=>t.name).sort()))throw new Error('Lookup allow-list mismatch');})();
    return initialized;
  }
  for(const definition of config.definitions){
    if(!allowed.has(definition.name))throw new Error('Extra lookup tool');
    pi.registerTool({name:definition.name,label:definition.name,description:definition.description,parameters:Type.Unsafe(definition.inputSchema),
      async execute(_id,args,signal){await ready(signal);const result=await request('tools/call',{name:definition.name,arguments:args},signal);if(result.isError)throw new Error('Ledger lookup failed');return {...result,details:{}};}});
  }
  pi.on('session_shutdown',async()=>close());
  process.once('exit',close);
}
