import {useEffect, useSyncExternalStore} from 'react';
import {api, type Draft} from './api';
import {answerKey, readStored, writeStored} from './persistence';
type Entry={key:string;id:string;index:number;text:string;updatedAt:number;localOK:boolean;serverText:string;error:string;request:Promise<void>|null;generation:number;discarded?:boolean};
const entries=new Map<string,Entry>(),listeners=new Set<()=>void>();let version=0;
function changed(){version++;listeners.forEach(fn=>fn());}
const subscribe=(fn:()=>void)=>{listeners.add(fn);return()=>{listeners.delete(fn);};};
async function sync(entry:Entry){
 if(entry.request)return entry.request;
 const run=entry.generation;
 entry.request=(async()=>{try{while(run===entry.generation && entry.text!==entry.serverText){const text=entry.text;await api.keepAnswer(entry.id,entry.index,text,entry.updatedAt);if(run!==entry.generation)return;entry.serverText=text;entry.error='';changed();}}catch(e){if(run!==entry.generation)return;entry.error=`Could not save this answer to Queue. ${(e as Error).message}`;changed();}finally{entry.request=null;}})();
 return entry.request;
}
export function useAnswer(d:Draft){
 const index=d.questions.findLastIndex(q=>q.answer===null),question=d.questions[index]?.question||'';
 const key=answerKey(d.id,index,question);
 let entry=entries.get(key);
 if(!entry || entry.discarded){
  const saved=readStored<string|{text:string;updatedAt:number}>(key),server=d.unsent_answer?.question_index===index?d.unsent_answer:null;
  const local=typeof saved==='string'?{text:saved,updatedAt:0}:saved;
  const latest=local && local.updatedAt>=(server?.updated_at||0)?local:{text:entry?.discarded?entry.text:(server?.answer||''),updatedAt:server?.updated_at||0};
  entry={key,id:d.id,index,text:latest.text,updatedAt:latest.updatedAt,serverText:server?.answer||'',localOK:true,error:'',request:null,generation:0};entries.set(key,entry);
 }
 const e=entry;
 useSyncExternalStore(subscribe,()=>version,()=>0);
 useEffect(()=>{if(e.text!==e.serverText)void sync(e);},[key]);
 return {text:e.text,localOK:e.localOK,error:e.error,serverSaved:e.text===e.serverText,
  set(text:string){e.text=text;e.updatedAt=Math.max(Date.now(),e.updatedAt+1);e.localOK=writeStored(key,{text,updatedAt:e.updatedAt});changed();void sync(e);},
  retry(){e.localOK=writeStored(key,{text:e.text,updatedAt:e.updatedAt});changed();void sync(e);},
  flush:()=>sync(e),
  clear(discarded=false){e.generation++;if(!discarded)e.text='';e.serverText='';e.error='';e.discarded=discarded;e.localOK=writeStored(key,null);changed();}
 };
}
/** Answer requests and their unload protection outlive the detail route. */
export function useAnswerGuard(){useEffect(()=>{const guard=(event:BeforeUnloadEvent)=>{if([...entries.values()].some(e=>!e.discarded&&e.text&&!e.localOK&&e.text!==e.serverText)){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',guard);return()=>window.removeEventListener('beforeunload',guard);},[]);}
