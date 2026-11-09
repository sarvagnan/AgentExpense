import {useCallback,useRef,useState,type Dispatch,type SetStateAction} from 'react';
import {dashboard} from '../data';
/** Local recovery belongs to this ledger and form. Setters persist synchronously,
 * before navigation or a reload can unmount the component. */
export function useRetainedState<T>(name:string,initial:T|(()=>T)):[T,Dispatch<SetStateAction<T>>,Dispatch<SetStateAction<T>>]{
 const key=`ledger-form-${dashboard.meta.ledger.id}-${name}`;
 const [value,setValue]=useState<T>(()=>{try{const saved=sessionStorage.getItem(key);if(saved!==null)return JSON.parse(saved) as T;}catch{}return typeof initial==='function'?(initial as ()=>T)():initial;});
 const current=useRef(value);current.current=value;
 const initialize:Dispatch<SetStateAction<T>>=useCallback(next=>{const result=typeof next==='function'?(next as (old:T)=>T)(current.current):next;current.current=result;setValue(result);},[setValue]);
 const update:Dispatch<SetStateAction<T>>=useCallback(next=>{const result=typeof next==='function'?(next as (old:T)=>T)(current.current):next;current.current=result;try{sessionStorage.setItem(key,JSON.stringify(result));}catch{}setValue(result);},[key,setValue]);
 return [value,update,initialize];
}
export function clearRetained(names:string[]){for(const name of names)try{sessionStorage.removeItem(`ledger-form-${dashboard.meta.ledger.id}-${name}`);}catch{}}

/** Frozen storage bytes, attached to the write rather than a component lifetime. */
export function retainedSnapshot(names:string[]){const result:Record<string,string|null>={};for(const name of names){const key=`ledger-form-${dashboard.meta.ledger.id}-${name}`;try{const value=sessionStorage.getItem(key);result[key]=value;}catch{}}return result;}
export function clearRetainedPrefix(prefix:string){const names:string[]=[];try{const start=`ledger-form-${dashboard.meta.ledger.id}-${prefix}`;for(let i=0;i<sessionStorage.length;i++){const key=sessionStorage.key(i);if(key?.startsWith(start))names.push(key);}names.forEach(key=>sessionStorage.removeItem(key));}catch{}}

export function hasRetained(names:string[]){return Object.values(retainedSnapshot(names)).some(value=>value!==null);}
