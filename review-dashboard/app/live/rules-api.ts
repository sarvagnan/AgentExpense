import { useCallback, useEffect, useRef, useState } from 'react';
import { dashboard, fetchGzipJson, reloadData } from '../data';
import { clientTime, localDate } from '../date-time';
import { request } from './api';
import { draftsChanged } from './use-drafts';
export type RulePosting={account:string;amount:string;commodity:string;cost?:{amount:string;commodity:string;per:'unit'|'total'}};
export type Bill={amount:string;received:string;'pay-by':string|null;occurrence:string;evidence:string[]};
export type Rule={amount?:'fixed'|'varies';bill?:Bill|null;varying?:{history:{id:string;date:string;amount:string}[];min:string|null;max:string|null};id:string;description:string;date:string;status:string;postings:RulePosting[];notes?:string[];schedule:{every:'month'|'year';interval?:number;from:string;'anchor-day'?:number};lifecycle?:string;origin?:{kind:string;words?:string|null;'entry-ids'?:string[];'came-back'?:boolean}|null;version?:string;next_due?:string;last_payment?:{id:string;date:string;rule?:{id:string;occurrence:string}}|null;payments?:{id:string;date:string;rule?:{id:string;occurrence:string}}[];rule_status?:string;review_state?:string;review?:{rule_verdict:string;reason:string}|null};
export type RuleMatch={id:string;description:string;score:number;next_due:string;matched_words:string[];matched_alias:string|null;matched_amounts:{amount:string;currency:string}[]};
/** `matches` are proposals; `suggestions` share one word only and are offered quietly. */
export type MatchResult={matches:RuleMatch[];suggestions?:RuleMatch[];pick_list:boolean;rule_id:string|null};
export type RuleData={rules:Rule[];suggestions:{rule:Rule;reason:string;automatic?:boolean}[]};
export const rulesApi={
  list:(signal?:AbortSignal)=>request<RuleData>('rules',{signal}),
  match:(text:string,signal?:AbortSignal)=>request<MatchResult>('rules/match?'+new URLSearchParams({text,today:localDate(),currency:dashboard.meta.ledger.defaultCurrency}),{signal}),
};
export function useRules(online:boolean|null) {
  const [data,setData]=useState<RuleData>({rules:[],suggestions:[]}),[error,setError]=useState(''),[ready,setReady]=useState(false);
  const receivedLive=useRef(false),generation=useRef(0);
  const refresh=useCallback(async()=>{if(online!==true)return;const run=++generation.current;try{const value=await rulesApi.list();if(run!==generation.current)return;receivedLive.current=true;setData(value);setError('');setReady(true);}catch(e){if(run===generation.current){setError((e as Error).message);setReady(false);}}},[online]);
  useEffect(()=>{void refresh();},[refresh]);
  // The built snapshot belongs to this mount; connection changes refresh only live data.
  useEffect(()=>{let alive=true;void fetchGzipJson<{rules?:Rule[]}>('add-patterns').then(value=>{if(alive&&!receivedLive.current)setData(old=>({...old,rules:value.rules||[]}));},()=>{});return()=>{alive=false;};},[]);
  useEffect(()=>{if(online!==true){setReady(false);return;}const run=()=>void refresh();window.addEventListener('drafts-changed',run);const timer=setInterval(run,30000);return()=>{generation.current++;window.removeEventListener('drafts-changed',run);clearInterval(timer);};},[online,refresh]);
  return {data,error,ready,refresh};
}
/** Preserve operation identity after an uncertain response; a changed payload gets a new identity. */
export function useRuleOperation(onSaved?:()=>void|Promise<void>) {
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const retry=useRef<{signature:string;id:string;body:object}|null>(null),running=useRef(false);
  async function operate(body:object) {
    if(running.current)return false;
    const signature=JSON.stringify(body);
    if(retry.current?.signature!==signature)retry.current={signature,id:crypto.randomUUID().replaceAll('-',''),body:{...body,...clientTime()}};
    running.current=true;setBusy(true);setError('');
    try {
      const result=await request<{data_stale?:boolean;data_pending?:boolean}>('rules/operations/'+retry.current.id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(retry.current.body)},120000);
      retry.current=null;
      try {await reloadData();await onSaved?.();}
      catch {setError('Saved. The dashboard could not refresh; use Refresh rules to load the saved change.');}
      draftsChanged();
      if(result.data_stale&&!result.data_pending)setError('Saved. Dashboard refresh is still needed.');
      return true;
    }catch(e){setError((e as Error).message);return false;}finally{running.current=false;setBusy(false);}
  }
  return {operate,busy,error};
}
