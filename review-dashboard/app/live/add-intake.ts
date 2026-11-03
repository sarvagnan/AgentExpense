import {useEffect,useRef,useState} from 'react';
import {dashboard} from '../data';
import {request} from './api';
export type FileChoice={kind?:'statement'|'receipt';account?:string;new_account?:{name:string;kind:string}};
export type InterpretedFile={id:string;name:string;kind:'statement'|'receipt'|'ambiguous';account?:string;account_label?:string;account_currency?:string;currency?:string;start?:string;end?:string;reader?:string;reader_name?:string;builtin?:boolean;locked:boolean;can_choose_kind?:boolean;mismatch?:boolean;checks:string[];candidates?:string[];error?:string;password_error?:boolean;new_account?:{name:string;kind:string;currency:string}};
export type Interpretation={words:{kind:'statement'|'expense';account?:string};files:InterpretedFile[]};
export function statementWords(text:string){
 const locale=dashboard.meta.ledger.locale||navigator.language;
 const months=['long','short'].flatMap(month=>Array.from({length:12},(_,i)=>new Intl.DateTimeFormat(locale,{month:month as 'long'|'short'}).format(new Date(2000,i,1))));
 const lower=text.toLocaleLowerCase(locale),month=months.some(name=>lower.includes(name.toLocaleLowerCase(locale)))||/\b\d{4}-\d{2}\b/.test(text);
 const accounts=Object.entries(dashboard.meta.accounts).filter(([,m])=>['bank','card'].includes(m.kind||''));
 const account=accounts.filter(([a,m])=>[m.label||a.split(':').at(-1)||'',...(m.aliases||[])].filter(n=>!['bank','card'].includes(n.toLowerCase())).some(n=>lower.includes(n.toLocaleLowerCase(locale))));
 return {kind:(/\b(?:bank|card)\s+statement\b|\bstatement\s+(?:for|of)\b/i.test(text)||month&&account.length?'statement':'expense') as 'statement'|'expense',account:account.length===1?account[0][0]:undefined};
}
export function useAddInterpretation(id:string|null,text:string,files:string,choices:Record<string,FileChoice>,passwords:Record<string,string>,active:boolean){
 const [value,setValue]=useState<Interpretation|null>(null),[valueContext,setValueContext]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const generation=useRef(0);
 // Autosave assigning an ID does not change words-only recognition. Keeping
 // that reading stable also keeps Continue enabled during a pointer click.
 const sourceId=files?id:null;
 const context=JSON.stringify([sourceId,text,files]),signature=JSON.stringify([sourceId,text,files,choices,passwords]);
 useEffect(()=>{
  const run=++generation.current;
  if(!active)return;
  setBusy(true);
  const controller=new AbortController(),timer=setTimeout(()=>{
   const operation=id&&files?request<Interpretation>(`drafts/${id}/interpret`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,choices,passwords}),signal:controller.signal},120000):request<Interpretation['words']>('add/interpret',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text}),signal:controller.signal}).then(words=>({words,files:[]}));
   void operation.then(next=>{if(run===generation.current){setValue(next);setValueContext(context);setError('');}},e=>{if(run===generation.current&&!controller.signal.aborted)setError((e as Error).message);}).finally(()=>{if(run===generation.current)setBusy(false);});
  },250);
  return()=>{clearTimeout(timer);controller.abort();};
 },[signature,active]);
 return {value:valueContext===context?value:null,busy,error};
}
