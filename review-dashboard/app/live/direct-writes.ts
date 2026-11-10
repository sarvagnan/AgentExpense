/** Page-owned FIFO for direct ledger writes. Reads and draft autosaves stay independent.
 * The transport is supplied by api.ts, so retries use the same HTTP/error handling.
 * A rejected response is never interpreted as a saved expense. */
export type WriteState='queued'|'saving'|'saved'|'failed'|'interrupted'|'waiting'|'cancelled';
export type WriteOwner={id:string;key:string};
export type WriteOptions=RequestInit & {writeOwner?:WriteOwner;clearRetained?:Record<string,string|null>};
export type DirectWrite={outcomeUnknown?:boolean;recovered?:boolean;owner?:WriteOwner;owners?:WriteOwner[];clearRetained?:Record<string,string|null>;createdAt?:number;retryable?:boolean;id:string;path:string;body:string;method:string;label:string;scope:string;state:WriteState;reason:string;href:string;attempts:number};
type Job=DirectWrite & {send:(retry?:boolean,accepted?:()=>void,waiting?:()=>void)=>Promise<unknown>;promise?:Promise<unknown>};
let jobs:Job[]=[],snapshot:DirectWrite[]=[],tail:Promise<unknown>=Promise.resolve(),storageKey='';
const listeners=new Set<()=>void>();
export const subscribeWrites=(listener:()=>void)=>{listeners.add(listener);return()=>{listeners.delete(listener);};};
export const writeSnapshot=()=>snapshot;
function publish(){
 snapshot=jobs.map(({send,promise,...item})=>item);
 if(storageKey)try{sessionStorage.setItem(storageKey,JSON.stringify(snapshot.filter(item=>!['saved','cancelled'].includes(item.state))));}catch{/* The visible operation and unload guard remain available. */}
 listeners.forEach(listener=>listener());
}
export function writeDescription(path:string,method:string,body=''):{scope:string;label:string}|null{
 if(['GET','HEAD'].includes(method))return null;
 if(/^entries\/[^/]+\/corrections\//.test(path))return {scope:'entry',label:'Entry correction'};
 if(/^drafts\/[^/]+\/(hand-add|usual|bill|approve-accounts|receipt-choice)$/.test(path))return {scope:'entry',label:path.endsWith('/bill')?'Unpaid bill':'Entry'};
 if(/^retention\/operations\//.test(path)){
  let action='';try{action=JSON.parse(body).action;}catch{}
  return {scope:'retention',label:({delete:'Move to Trash',restore:'Restore entry',remove:'Remove for good',settings:'Time limits','queue-restore':'Restore Queue item'} as Record<string,string>)[action]||'Trash change'};
 }
 if(/^rules\/operations\//.test(path))return {scope:'rules',label:'Rule change'};
 if(/^statements\/[^/]+\/apply$/.test(path))return {scope:'statement',label:'Statement'};
 if((method==='DELETE'&&/^(drafts|statements)\/[^/]+$/.test(path))||/^(drafts|statements)\/[^/]+\/restore$/.test(path))return {scope:'retention',label:path.endsWith('/restore')?'Restore Queue item':'Move Queue item to Trash'};
 if(path==='review/actions')return {scope:'review',label:'Review decision'};
 if(['setup/accounts','accounts'].includes(path)){let name='',currency='';try{const value=JSON.parse(body);name=typeof (value.label||value.name)==='string'?(value.label||value.name).trim():'';currency=typeof value.currency==='string'?value.currency:'';}catch{}return {scope:path==='accounts'?'settings':'setup',label:name?`Account · ${name}${currency?' ('+currency+')':''}`:'Account'};}
 if(/^setup\/(about|opening|import)$/.test(path))return {scope:'setup',label:'Setup'};
 if(/^settings\/(name|default-currency)$/.test(path)||path==='accounts/currency')return {scope:'settings',label:'Settings'};
 return null;
}
const active=(state:WriteState)=>['queued','saving','waiting'].includes(state);
function start(job:Job){
 const retry=job.attempts++>0,previousReason=job.reason;if(retry)jobs=[...jobs.filter(item=>item!==job),job];job.state='queued';job.reason='';job.retryable=true;publish();
 const earlier=tail;let release!:()=>void;
 tail=new Promise<void>(resolve=>{release=resolve;});
 const operation=earlier.catch(()=>{}).then(async()=>{
  job.state='saving';publish();
  try{const result=await job.send(retry,release,()=>{job.state='waiting';publish();});if(result&&typeof result==='object'&&'refused' in result&&result.refused){job.state='failed';job.retryable=false;job.reason='reason' in result&&typeof result.reason==='string'?result.reason:'This change was refused.';publish();return result;}job.state='saved';const clearedRetained:string[]=[];try{const group=Object.entries(job.clearRetained||{});if(group.every(([key,value])=>sessionStorage.getItem(key)===value))for(const [key] of group){sessionStorage.removeItem(key);clearedRetained.push(key);}}catch{}publish();window.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{id:job.id,owner:job.owner,path:job.path,body:job.body,result,previousReason,clearedRetained}}));return result;}
  catch(error){job.outcomeUnknown=!(error&&typeof error==='object'&&'status' in error&&typeof error.status==='number'&&error.status>=400&&error.status<500);if(error&&typeof error==='object'&&'resultUnavailable' in error&&error.resultUnavailable){job.state='interrupted';job.retryable=false;job.reason=error instanceof Error?error.message:String(error);publish();throw error;}if(error&&typeof error==='object'&&'applyCancelled' in error&&error.applyCancelled){job.state='cancelled';job.retryable=false;job.reason='Apply cancelled. Your checklist is kept.';publish();throw error;}job.state='failed';job.retryable=!(error&&typeof error==='object'&&(('retryable' in error&&error.retryable===false)||'refusal' in error&&!!error.refusal));job.reason=error instanceof Error?error.message:String(error);publish();throw error;}
  finally{release();}
 });
 job.promise=operation;return operation;
}
export function enqueueWrite<T>(path:string,options:WriteOptions,send:(retry?:boolean,accepted?:()=>void,waiting?:()=>void)=>Promise<T>):Promise<T>{
 const method=(options.method||'GET').toUpperCase(),body=typeof options.body==='string'?options.body:'',description=writeDescription(path,method,body);
 if(!description)return send();
 const resource=(body:string)=>{try{const value=JSON.parse(body);if(['accounts','setup/accounts'].includes(path))return value.kind+':'+(value.label||value.name||'').trim();return value.account||value.entry_id||value.rule_id||value.rule?.id||'';}catch{return '';}};
 const newest=jobs.filter(item=>item.path===path&&item.method===method&&resource(item.body)===resource(body)).at(-1);
 const existing=newest?.body===body&&!['saved','cancelled'].includes(newest.state)?newest:undefined;
 if(existing){existing.send=send;if(options.writeOwner){if(!existing.owner)existing.owner=options.writeOwner;else if(existing.owner.id!==options.writeOwner.id&&!existing.owners?.some(owner=>owner.id===options.writeOwner!.id)){existing.owners=[...(existing.owners||[]),options.writeOwner];}}if(options.clearRetained)existing.clearRetained={...existing.clearRetained,...options.clearRetained};publish();return (active(existing.state)?existing.promise!:start(existing)) as Promise<T>;}
 const job:Job={owner:options.writeOwner,clearRetained:options.clearRetained,createdAt:Date.now(),id:crypto.randomUUID(),path,body,method,...description,state:'queued',reason:'',href:location.pathname+location.search,attempts:0,send};
 // Keep active/recovery work and the last eight acknowledgements. No timer can
 // remove a notice or move a control while somebody is using it.
 // A new value replaces an earlier failed edit of one setting. Declarations
 // and Review decisions are independent resources even when their URL matches.
 const replaces=(item:Job)=>{
  if(item.path!==path||item.method!==method||!['failed','interrupted'].includes(item.state))return false;
  if(/^drafts\/[^/]+\/(hand-add|usual|bill|approve-accounts|receipt-choice)$/.test(path))return true;
  if(['settings/name','settings/default-currency','setup/about'].includes(path))return true;
  if(path==='accounts/currency')try{return JSON.parse(item.body).account===JSON.parse(body).account;}catch{}
  return false;
 };
 jobs=[...jobs.filter(item=>item.state!=='saved'&&!replaces(item)).concat(jobs.filter(item=>item.state==='saved').slice(-7)),job];
 return start(job) as Promise<T>;
}
export function retryWrite(id:string){const job=jobs.find(item=>item.id===id);if(job&&job.retryable!==false&&!active(job.state))return start(job).catch(()=>undefined);}
export function dismissWrite(id:string){jobs=jobs.filter(item=>item.id!==id||active(item.state));publish();}
/** Reload never silently submits a new financial action. The exact original
 * request is retained for an explicit retry; version checks still apply. */
export function recoverWrites(ledger:string,send:(path:string,options:RequestInit,accepted?:()=>void,waiting?:()=>void)=>Promise<unknown>){
 const key=`ledger-direct-writes-${ledger}`;if(storageKey===key)return;storageKey=key;
 try{const stored:DirectWrite[]=JSON.parse(sessionStorage.getItem(key)||'[]');
  for(const item of stored){if(!item||typeof item.createdAt!=='number'||Date.now()-item.createdAt>86400000||typeof item.path!=='string'||typeof item.body!=='string'||!writeDescription(item.path,item.method,item.body)||jobs.some(job=>job.id===item.id))continue;
   jobs.push({...item,recovered:true,attempts:Math.max(1,item.attempts||1),state:item.state==='failed'?'failed':'interrupted',reason:item.state==='failed'||item.retryable===false?item.reason:'The page reloaded before the result arrived. Check the saved entry or Queue, then retry if needed.',send:(_retry,accepted,waiting)=>send(item.path,{method:item.method,headers:{'Content-Type':'application/json'},body:item.body||undefined},accepted,waiting)});
  }
 }catch{/* A damaged recovery copy cannot authorize an action. */}
 publish();
}
if(typeof window!=='undefined')window.addEventListener('beforeunload',event=>{
 if(jobs.some(job=>active(job.state))){event.preventDefault();event.returnValue='';}
});

/** Statement Apply retains page order until completion; other workers release at acceptance. */
const cancelledApplies=new Set<string>();
export function markApplyCancellation(source:string,active=true){if(active)cancelledApplies.add(source);else cancelledApplies.delete(source);}
type WorkerResult={state?:string;result?:unknown;error?:string|null;trashed_at?:string|null;refusal?:{message?:string;kind?:string};receipt_reading?:{state?:string;result?:unknown;refusal?:{message?:string;kind?:string}}};
export async function committedResult<T>(path:string,send:()=>Promise<T>,read:(path:string)=>Promise<unknown>,pause:(slow?:boolean)=>Promise<void>,refuse?:(failure:WorkerResult)=>Error,accepted?:()=>void,waiting?:()=>void):Promise<T>{
 const failure=(value:WorkerResult)=>{
  const detail=value.state==='already_filed'?{...value,error:'Another session has already filed this statement. Open statement history to view its stored reconciliation.'}:value;
  return Object.assign(refuse?refuse(detail):new Error(detail.error||detail.refusal?.message||'The entry was not saved. Open Queue to check it.'),{retryable:false,terminal:true});
 };
 const progress=(value:WorkerResult)=>['applying','queued','working'].includes(value.state||'');
 const unavailable=()=>Object.assign(new Error(path==='setup/import'?'Some statements may already have been filed. The remaining result is no longer in Queue. Open statement history or Queue to check.':path.startsWith('statements/')?"This statement's result is no longer in Queue. It may already have been saved. Open statement history or Queue to check.":"This item's result is no longer in Queue. It may already have been saved. Check the entry or Queue."),{resultUnavailable:true,retryable:false});
 const inspect=async<R,>(operation:()=>Promise<R>):Promise<R>=>{try{return await operation();}catch(error){if(error&&typeof error==='object'&&'status' in error&&error.status===404)throw unavailable();throw error;}};
 if(path==='setup/import'){
  let result:T=await inspect(send);accepted?.();let slow=false;
  for(let attempts=0;;attempts++){
   const sessions=(result as {sessions:(WorkerResult & {id:string})[]}).sessions;
   if(sessions.every(item=>item.state==='applied'&&!item.trashed_at))return result;
   const terminal=sessions.find(item=>item.trashed_at||item.state!=='applied'&&!progress(item));
   if(terminal)throw failure(terminal);
   if(attempts===480)waiting?.();
   await pause(slow||attempts>=480);
   try{result={sessions:await Promise.all(sessions.map(item=>item.state==='applied'?item:inspect(()=>read('statements/'+item.id))))} as T;}
   catch(error){if(error&&typeof error==='object'&&'resultUnavailable' in error)throw error;slow=true;waiting?.();}
  }
 }
 const match=path.match(/^(drafts\/[^/]+)\/(hand-add|usual|bill|approve-accounts|receipt-choice)$|^(statements\/[^/]+)\/apply$/);
 if(!match)return send();
 const source=match[1]||match[3],statement=!!match[3],receipt=match[2]==='receipt-choice';
 try{
  let result:T=await inspect(send);if(!statement)accepted?.();let slow=false;
  for(let attempts=0;;attempts++){
   const value=result as WorkerResult;
   if(value.trashed_at)throw failure({...value,error:value.error||'This item moved to Trash.'});
   if(receipt){
    if(value.receipt_reading?.refusal)throw failure({...value,refusal:value.receipt_reading.refusal});
    if(value.receipt_reading?.state==='done'&&value.receipt_reading.result)return result;
   }else if(statement?value.state==='applied':value.state==='added'&&!!value.result)return result;
   if(statement&&value.state==='review'&&!value.error&&cancelledApplies.has(source))throw Object.assign(new Error('Apply cancelled. Your checklist is kept.'),{applyCancelled:true,retryable:false,terminal:true});
   if(!progress(value))throw failure(value);
   if(attempts===480)waiting?.();
   await pause(slow||attempts>=480);
   try{result=await inspect(()=>read(source)) as T;}catch(error){if(error&&typeof error==='object'&&'resultUnavailable' in error)throw error;slow=true;waiting?.();}
  }
 }finally{if(statement)cancelledApplies.delete(source);}
}
