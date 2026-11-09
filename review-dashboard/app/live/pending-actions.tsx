import {rememberResults} from './add-visit';
import {useEffect,useLayoutEffect,useRef,useState,useSyncExternalStore} from 'react';
import {dashboard,reloadData} from '../data';
import {resumeWrite} from './api';
import {subscribeWrites,writeSnapshot,recoverWrites,retryWrite,dismissWrite,type DirectWrite,type WriteOwner} from './direct-writes';
function State({item,onRetry,retryAllowed=true}:{retryAllowed?:boolean;item:DirectWrite;onRetry?:(item:DirectWrite)=>void}){
 return <div className={`action-state ${item.state}${item.method==='DELETE'&&['queued','saving','waiting'].includes(item.state)?' pending-removal':''}`} data-action-id={item.id} data-action-path={item.path} data-action-state={item.state}>
  <span role="status" tabIndex={['failed','interrupted'].includes(item.state)?0:undefined} title="Use arrow keys to scroll the full reason.">{item.label}: {item.state==='cancelled'?item.reason:item.state==='saved'?'Saved.':item.state==='queued'?'Saving… Waiting for the earlier action.':item.state==='saving'?(item.method==='DELETE'?'Moving to Trash…':'Saving…'):item.state==='waiting'?'Still in Queue. Waiting for the result…':item.reason}</span>
  {['failed','interrupted'].includes(item.state)&&<small className="action-scroll-cue" aria-hidden="true">Scroll for full reason ↓</small>}
  {retryAllowed&&item.retryable!==false&&['failed','interrupted'].includes(item.state)&&<button type="button" className="button" onClick={()=>onRetry?onRetry(item):retryWrite(item.id)}>Retry</button>}
 </div>;
}
/** A mount adopts only unfinished work for its own resource. Saved acknowledgements
 * belong to the initiating mount, so reopening starts with an empty status. */
export function useWriteOwner(key:string,resume=true):WriteOwner{
 const owner=useRef<WriteOwner|null>(null);
 const items=useSyncExternalStore(subscribeWrites,writeSnapshot,writeSnapshot);
 if(owner.current?.key!==key)owner.current=resume&&items.filter(item=>!['saved','cancelled'].includes(item.state)).flatMap(item=>[item.owner,...(item.owners||[])]).findLast(owner=>owner?.key===key)||{id:crypto.randomUUID(),key};
 if(resume&&!items.some(item=>item.owner?.id===owner.current!.id||item.owners?.some(value=>value.id===owner.current!.id))){const recovered=items.filter(item=>item.recovered&&!['saved','cancelled'].includes(item.state)).flatMap(item=>[item.owner,...(item.owners||[])]).findLast(value=>value?.key===key);if(recovered)owner.current=recovered;}
 return owner.current;
}
/** A permanent slot below the controls: feedback never pushes the form down. */
export function ActionFeedback({scope,path,resource,match,onRetry,owner,retryAllowed=true,notice}:{notice?:string;retryAllowed?:boolean;owner?:WriteOwner;scope:string|string[];path?:string|null;resource?:string;match?:(item:DirectWrite)=>boolean;onRetry?:(item:DirectWrite)=>void}){
 const items=useSyncExternalStore(subscribeWrites,writeSnapshot,writeSnapshot),previous=useRef(new Set(items.filter(item=>['saved','cancelled'].includes(item.state)).map(item=>item.id))),item=items.filter(item=>{if(previous.current.has(item.id)||owner&&item.owner?.id!==owner.id&&!item.owners?.some(value=>value.id===owner.id))return false;if(path===null||path&&item.path!==path||match&&!match(item))return false;if(resource){try{const body=JSON.parse(item.body);if((body.rule_id||body.rule?.id||'new')!==resource)return false;}catch{return false;}}return Array.isArray(scope)?scope.includes(item.scope):item.scope===scope;}).at(-1);
 return <div className="action-feedback" aria-label="Save status">{notice?<p role="status" className="live-hint">{notice}</p>:item&&<State item={item} onRetry={onRetry} retryAllowed={retryAllowed}/>}</div>;
}
/** A separate, fixed-size shelf keeps progress available after route changes. */
export function PendingActions(){
 const items=useSyncExternalStore(subscribeWrites,writeSnapshot,writeSnapshot);
 useEffect(()=>{recoverWrites(dashboard.meta.ledger.id,(path,options,accepted,waiting)=>resumeWrite(path,options,120000,true,accepted,waiting));const saved=(event:Event)=>{const detail=(event as CustomEvent<{clearedRetained?:string[];path:string;body:string;result:{state?:string;sessions?:{id:string;state:string}[]}}>).detail;if(detail){const draft=detail.path.match(/^drafts\/([^/]+)\//),statement=detail.path.match(/^statements\/([^/]+)\/apply$/);if(draft&&detail.result?.state==='added')rememberResults([{id:draft[1],type:'draft'}]);if(statement&&detail.result?.state==='applied')rememberResults([{id:statement[1],type:'statement'}]);const prefix=`ledger-form-${dashboard.meta.ledger.id}-settings-`;try{const body=JSON.parse(detail.body||'{}'),dirty=JSON.parse(sessionStorage.getItem(prefix+'dirty')||'{}');for(const [path,field,key] of [['settings/name','name','name'],['settings/default-currency','currency','currency']] as const)if(detail.path===path&&(detail.clearedRetained?.includes(prefix+field)||JSON.parse(sessionStorage.getItem(prefix+field)||'null')===body[key]))dirty[field]=false;if(detail.path.startsWith('retention/operations/')&&body.action==='settings'&&sessionStorage.getItem(prefix+'limits')===JSON.stringify(body.limits))dirty.limits=false;if(Object.values(dirty).some(Boolean))sessionStorage.setItem(prefix+'dirty',JSON.stringify(dirty));else sessionStorage.removeItem(prefix+'dirty');}catch{}}window.dispatchEvent(new Event('drafts-changed'));void reloadData().catch(()=>{});};window.addEventListener('direct-write-saved',saved);return()=>window.removeEventListener('direct-write-saved',saved);},[]);
 const [localIds,setLocalIds]=useState<string[]>([]);
 useLayoutEffect(()=>{const update=()=>{const next=Array.from(document.querySelectorAll?.<HTMLElement>('.action-feedback [data-action-id]')||[]).filter(node=>node.getClientRects().length).map(node=>node.dataset.actionId!);setLocalIds(old=>JSON.stringify(old)===JSON.stringify(next)?old:next);};update();const observer=new MutationObserver(update);observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['data-action-id']});return()=>observer.disconnect();},[]);
 const waiting=items.filter(item=>!['saved','cancelled'].includes(item.state)&&!localIds.includes(item.id));
 if(!waiting.length)return null;
 return <aside className="pending-actions" aria-label="Pending saves"><details><summary>{waiting.length} {waiting.length===1?'save':'saves'} {waiting.some(item=>['queued','saving','waiting'].includes(item.state))?'in progress':'needs attention'}</summary><div>{waiting.map(item=><div key={item.id}><State item={item}/><a href={item.href}>Return to form</a>{!['queued','saving','waiting'].includes(item.state)&&<button className="text-button" onClick={()=>dismissWrite(item.id)}>Dismiss notice</button>}</div>)}</div></details></aside>;
}

/** Both labels reserve their natural width, including wider system fonts. */
export function PendingLabel({pending,children,text}:{pending:boolean;children:string;text?:string}){return <span className="pending-label" data-label={children}><span>{pending?'Saving…':text||children}</span></span>;}
