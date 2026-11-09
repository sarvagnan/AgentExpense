import {useCallback,useEffect,useRef,useState,type SetStateAction} from 'react';
import {writeSnapshot,type WriteOwner} from './direct-writes';

export type WriteIdentity={id?:string;owner?:WriteOwner;owners?:WriteOwner[];path:string;body:string};
export type WriteCompletion<T=unknown>=WriteIdentity&{result:T;previousReason?:string};
/** Page completion is shared by ordinary saves, slot/shelf Retry and recovery.
 * Keep callbacks current without re-subscribing or touching transport state. */
export function useWriteCompletion<T=unknown>(matches:(write:WriteIdentity)=>boolean,complete:(write:WriteCompletion<T>)=>void){
 const current=useRef({matches,complete});current.current={matches,complete};
 const completed=useRef(new Set<string>());
 useEffect(()=>{const saved=(event:Event)=>{const write=(event as CustomEvent<WriteCompletion<T>>).detail;if(!write||!current.current.matches(write)||write.id&&completed.current.has(write.id))return;if(write.id)completed.current.add(write.id);current.current.complete(write);};window.addEventListener('direct-write-saved',saved);return()=>window.removeEventListener('direct-write-saved',saved);},[]);
}
/** Preserve unrelated read/validation errors; remove this committed job's refusal. */
export const clearCommittedError=(error:string,write:WriteCompletion)=>write.previousReason&&error.includes(write.previousReason)?'':error;

/** Associate a local error with the failed job at the catch boundary. An older
 * commit cannot clear a newer job's identical-looking refusal or a read error. */
export function useCommittedError(matches:(write:WriteIdentity)=>boolean):[string,(value:SetStateAction<string>)=>void]{
 const [error,setState]=useState(''),value=useRef(error),failed=useRef<string|undefined>(undefined),scope=useRef(matches);value.current=error;scope.current=matches;
 const setError=useCallback((update:SetStateAction<string>)=>{const next=typeof update==='function'?update(value.current):update;value.current=next;failed.current=next?writeSnapshot().findLast(write=>write.state==='failed'&&scope.current(write)&&!!write.reason&&next.includes(write.reason))?.id:undefined;setState(next);},[]);
 useWriteCompletion(matches,write=>{if(failed.current?failed.current===write.id:clearCommittedError(value.current,write)!==value.current)setError('');});
 return [error,setError];
}

/** Coalesced declarations can belong to more than one mounted form. */
export const writeOwnedBy=(write:WriteIdentity,owner:WriteOwner)=>write.owner?.id===owner.id||write.owners?.some(value=>value.id===owner.id)||!!writeSnapshot().find(value=>value.id===write.id)?.owners?.some(value=>value.id===owner.id);
