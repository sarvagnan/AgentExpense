import {api,type Draft,type Proposal} from './api';
import {dashboard} from '../data';
import {clearRetained,retainedSnapshot} from './retained-state';
import {writeSnapshot,type DirectWrite} from './direct-writes';

export function editSaveReason(draft:Draft,items:DirectWrite[]=writeSnapshot()){
 if(draft.correction_save)return 'Save is in progress. Check its result before discarding this item.';
 for(const item of items){
  let matches=item.path===`entries/${draft.correction?.entry_id}/corrections/${draft.id}`;
  if(!matches&&/^entries\/[^/]+\/corrections\//.test(item.path))try{matches=JSON.parse(item.body).draft_id===draft.id;}catch{}
  if(!matches)continue;
  if(['queued','saving','waiting'].includes(item.state))return 'Save is in progress. Wait for its result before discarding this item.';
  if(['failed','interrupted'].includes(item.state)&&item.outcomeUnknown!==false)return 'Save’s result is unknown. Check the saved entry before discarding this item.';
  if(item.state==='saved')return 'Save has finished. Refresh Queue to view the saved entry.';
 }
 return '';
}

export const editRecoveryNames=(id:string)=>['form','sources','reason','version'].map(field=>`hand-${id}-${field}`);
export function clearEditRecovery(id:string,snapshot:Record<string,string|null>){
 if(Object.entries(snapshot).every(([key,value])=>sessionStorage.getItem(key)===value))clearRetained(editRecoveryNames(id));
}
async function claimRecovery(draft:Draft,snapshot:Record<string,string|null>){
 const key=`hand-claim-${dashboard.meta.ledger.id}-${draft.id}`;
 const previous=sessionStorage.getItem(key)||undefined,token=crypto.randomUUID().replaceAll('-','')+crypto.randomUUID().replaceAll('-','');
 sessionStorage.setItem(key,token);
 const claim=await api.claim(draft.id,token,previous);
 if(sessionStorage.getItem(key)!==token){await api.release(draft.id,claim.claim_token).catch(()=>{});throw Error('Another editor opened this item. Reopen Queue to continue.');}
 const read=<T,>(field:string,fallback:T):T=>{try{return JSON.parse(snapshot[`ledger-form-${dashboard.meta.ledger.id}-hand-${draft.id}-${field}`]||'null')??fallback;}catch{return fallback;}};
 return {token:claim.claim_token,form:read('form',claim.form),sources:read('sources',draft.correction!.sources),reason:read('reason',draft.correction!.reason||''),state:claim.draft?.state};
}

export async function prepareFixRetry(draft:Draft){
 const reason=editSaveReason(draft);if(reason)throw Error(reason);
 const recovery=retainedSnapshot(editRecoveryNames(draft.id));
 const key=`hand-claim-${dashboard.meta.ledger.id}-${draft.id}`;
 if(Object.values(recovery).some(value=>value!==null)){
  const current=await claimRecovery(draft,recovery);
  try{
   if(current.state&&current.state!=='failed')throw Error('The proposal is already running. Wait for it to finish before asking again.');
   await api.handForm(draft.id,current.token,current.form,current.sources,current.reason);
  }finally{await api.release(draft.id,current.token);}
 }
 return {...await api.retryCheck(draft.id),recovery,claimToken:sessionStorage.getItem(key)};
}
export async function runFixRetry(draft:Draft,prepared:Awaited<ReturnType<typeof prepareFixRetry>>){
 if(!Object.entries(prepared.recovery).every(([key,value])=>sessionStorage.getItem(key)===value))throw Error('Your kept changes changed. Choose Retry again to review them.');
 const result=await api.retry(draft.id,prepared.replace_edits?prepared.version:undefined);
 const key=`hand-claim-${dashboard.meta.ledger.id}-${draft.id}`;
 if(sessionStorage.getItem(key)===prepared.claimToken){clearEditRecovery(draft.id,prepared.recovery);sessionStorage.removeItem(key);}
 return result;
}

/** Both proposal-discard controls preserve exactly the current form before Trash. */
export async function discardEdit(draft:Draft,options:{current?:{token:string;form:Proposal;sources:string[];reason:string};discard?:(token?:string)=>Promise<unknown>;released?:()=>void}={}){
 const reason=editSaveReason(draft);if(reason)throw Error(reason);
 const snapshot=retainedSnapshot(editRecoveryNames(draft.id));
 const key=`hand-claim-${dashboard.meta.ledger.id}-${draft.id}`;
 let current=options.current;
 if(current)await api.claim(draft.id,current.token);
 const hasRecovery=Object.values(snapshot).some(value=>value!==null);
 const resolvedNotice=draft.ledger_fix?.state==='resolved'&&!hasRecovery&&!sessionStorage.getItem(key);
 if(!current&&draft.correction&&!resolvedNotice&&(['question','failed'].includes(draft.state)||hasRecovery))current=await claimRecovery(draft,snapshot);
 if(current){
  await api.handForm(draft.id,current.token,current.form,current.sources,current.reason);
 }
 await (options.discard?options.discard(current?.token):api.discard(draft.id,current?.token));
 options.released?.();
 if(!current||sessionStorage.getItem(key)===current.token){clearEditRecovery(draft.id,snapshot);sessionStorage.removeItem(key);}
}
