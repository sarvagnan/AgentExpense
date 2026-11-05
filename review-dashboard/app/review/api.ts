import {useCallback,useEffect,useRef,useState} from 'react';
import {request} from '../live/api';
export type Decision={status:'resolved'|'followup'|'unreviewed';account:string;label:string;notes:string;updatedAt:string};
export type Entry={id:string;version:string;date:string;originalDate:string;description:string;amount:string;signedAmount?:string;signedAmounts?:Record<string,string>;amounts?:Record<string,string>;currency:string;account:string;accountLabel:string;currentAccount:string;kind:string;sources:string[];source:string;sourceReference:string;statement:boolean;notes:string[];status:string;postings:{account:string;amount:string;commodity:string}[]};
export type Item={id:string;kind:'duplicate'|'doubt'|'pending'|'overlap';important:boolean;reason:string;machineReason?:string;doubts?:string[];added_at?:string;entries:Entry[];kept_id?:string;statement_id?:string;row_serial?:string;row?:{date:string;description:string;amount:string}};
export type ReviewChange={entry_id:string;other_id?:string;reason?:string;decision?:Decision;kept_id?:string;removed_id?:string;sources?:string[];notes?:string[]};
export type History={id:string;time:string;action:string;canUndo:boolean;changes:ReviewChange[]};
export type Snapshot={export_entries:Record<string,Entry>;count:number;important:number;items:Item[];entries:Record<string,Entry>;decisions:Record<string,Decision>;accounts:string[];history:History[]};
export type Preview={drafts:number;valid:number;unknown:number;changed:number;already:number;token:string};
export function useReview(){
 const [data,setData]=useState<Snapshot|null>(null),[error,setError]=useState('');
 // Polls and post-action refreshes overlap. Publish only a read issued after the one
 // already shown, so a poll that read the ledger before a decision committed cannot
 // bring the decided item, or the old badge count, back.
 const issued=useRef(0),shown=useRef(0),latest=useRef<Snapshot|null>(null);
 const refresh=useCallback(async(signal?:AbortSignal)=>{const ticket=++issued.current;try{const next=await request<Snapshot>('review',{signal},120000);if(signal?.aborted)return null;if(ticket<shown.current)return latest.current;shown.current=ticket;latest.current=next;setData(next);setError('');return next;}catch(e){if(!signal?.aborted&&ticket>shown.current)setError(e instanceof Error?e.message:String(e));return null;}},[]);
 useEffect(()=>{const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;const poll=async()=>{await refresh(controller.signal);if(!controller.signal.aborted)timer=setTimeout(poll,2000);};void poll();return()=>{controller.abort();clearTimeout(timer);};},[refresh]);
 return {data,error,refresh};
}
export const postReview=<T,>(path:string,body:unknown)=>request<T>('review/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)},180000);
export const requestId=()=>crypto.randomUUID().replaceAll('-','');
