import {markApplyCancellation,committedResult,enqueueWrite,writeSnapshot,type WriteOptions} from './direct-writes';
import type {BalanceRefusal,BalanceLock} from './balance-refusal';
import type { Piece } from './builder';
import {clientTime} from '../date-time';
export type Attachment = { id: string; original_name: string; media_type: string; size: number };
/** The hand form (and Edit entry): the agent's answer shape, kebab-case, with every extension's fields (docs/STORE.md). */
export type Proposal = {'account-metadata'?:Record<string,{kind:string;currency:string}>;status:string;question:string|null;explanation:string|null;entry:{date:string;status:string;description:string;notes:string[];postings:{account:string;amount:string;commodity:string;cost:{amount:string;commodity:string}|null}[];extensions?:import('../data-types').ExtensionGroups;[key:string]:unknown};'new-accounts':string[];doubts:string[];'attachment-names':string[]};
export type AgentPause = {kind:'usage_limit'|'auth'|'transient';reason:string;resumes_at:string;attempt:number};
export type HandChecks = {refusal?:BalanceRefusal;unchanged?:boolean;ok:boolean;checks:string[];new_accounts:string[];proposal_hash:string};
export type Draft = {proposal?:Proposal|null;trashed_at?:string|null;refusal?:BalanceRefusal;
  intake_complete?:boolean;intake_result?:{statements:{id:string}[];drafts:{id:string}[]};
  correction?:{entry_id:string;version:string;locked:boolean;balance_locks?:BalanceLock[];reason?:string;before:{description:string;postings:Proposal['entry']['postings']};sources:string[]}; receipt_reading?:{refusal?:BalanceRefusal;state:string;message?:string;answer?:{date:string|null;amount:string|null;currency:string|null;paying_account:string|null};entry?:{date:string};entry_totals?:Record<string,string>}; claim?:{expires_at:string}; agent_pause?:AgentPause|null; finished_by?:{actor:string;reason:string};
  account_question?:{proposal_hash:string;accounts:string[];postings:Proposal['entry']['postings']};
  ledger_fix?:{state:'queued'|'working'|'ready'|'question'|'failed'|'resolved';title:string;summary:string;question?:string|null;message?:string|null;doubts?:string[]};
  declined_rules?:string[]; id: string; description?:string; client_updated_at?: number; state: 'editing' | 'queued' | 'working' | 'question' | 'failed' | 'added'; text: string;
  pieces: { pattern: string; edits?: Record<string,string>; fields: Record<string, Pick<Piece, 'value' | 'source'>> } | null;
  currency_request?: {kind:'expense'|'transfer';amount:string;sourceCurrency:string;currency:string;sourceAccount:string;account:string;sourceLabel:string;accountLabel:string};
  statement_file?: string; handover_session?: string; client_time?: string; client_zone?: string; created_at: string; saved_at: string | null; queued_at: string | null; files: Attachment[];
  runs: { start: string; doubts: string[]; error?: string | null }[];
  questions: { question: string; answer: string | null }[]; unsent_answer?: {question_index: number; answer: string; updated_at?: number}; error: string | null; waiting: string | null;
  result: null | {rule?:{id:string;occurrence:string};rule_id?:string;next_due?:string;made_rules?:string[];resume_rules?:{id:string;description:string}[];weekly_review_note?:string;audit_id?:string;usual_payment?:{estimated:boolean}; outcome: string; explanation?: string; entry_id?: string; description?: string; total?: string; payment_account?: string; new_accounts?: string[]; sources?: string[]; data_stale?: boolean;data_pending?:boolean; refresh_error?: string };
};
export type CorrectionResult={entry_id:string;description:string;changed:string[];rule:{status:string;description:string|null;next_due:string|null};data_stale?:boolean;data_pending?:boolean};
export const apiUrl = (path: string, base = import.meta.env.BASE_URL) => `${base.replace(/\/?$/, '/')}api/${path.replace(/^\//, '')}`;
export class ApiError extends Error {
  readonly status: number;
  readonly refusal?:BalanceRefusal;
  constructor(status: number, message: string, body?:unknown) { super(message); const detail=(body as {detail?:BalanceRefusal}|null)?.detail;this.refusal=detail?.kind==='confirmed_balance'?detail:undefined;this.status=status; this.name='ApiError'; }
}
export function errorMessage(status: number, body: unknown) {
  const detail = typeof body === 'object' && body !== null && 'detail' in body ? (body as {detail: unknown}).detail : null;
  if (typeof detail === 'object' && detail!==null && 'kind' in detail && detail.kind==='confirmed_balance') return 'Not saved: a confirmed balance would change.';
  if (typeof detail === 'object' && detail!==null && 'kind' in detail && detail.kind==='amount_check') return 'The recent payment range changed. Check the amount and explicitly add it anyway.';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return 'Please check the entered values and try again.';
  return `The server could not complete this action (HTTP ${status}). Try again.`;
}
// An autosave timer can resume during document teardown. WebKit reports even a
// caught fetch started there as a page error; keep the local recovery copy and
// leave that request for the next document. Already-started writes still finish.
let departing=false;
if(typeof window!=='undefined') {
  window.addEventListener('pagehide',()=>{departing=true;});
  window.addEventListener('pageshow',()=>{departing=false;});
}
export async function request<T>(path: string, options: WriteOptions = {}, timeout = 12000): Promise<T> {
  if(departing)throw new DOMException('Request cancelled','AbortError');
  return enqueueWrite(path,options,(retry=false,accepted,waiting)=>resumeWrite<T>(path,options,timeout,retry,accepted,waiting));
}
export async function resumeWrite<T>(path:string,options:RequestInit,timeout=120000,recover=false,accepted?:()=>void,waiting?:()=>void):Promise<T>{
  if(recover&&['accounts','setup/accounts'].includes(path)){
    const body=JSON.parse(String(options.body||'{}')) as {kind:string;name?:string;label?:string;currency:string};
    const parent=({bank:'Assets:Bank',card:'Liabilities:Credit Cards',cash:'Assets:Cash',wallet:'Assets:Wallet',loan:'Liabilities:Loans',investment:'Assets:Investments',person:'Assets:People'} as Record<string,string>)[body.kind],account=parent+':'+(body.label||body.name||'').trim();
    const known=await transport<{accounts:string[]}>('setup',{},timeout);
    if(parent&&known.accounts.includes(account)){const current=await transport<{account_currency:string;commit_id:string}>('settings?account='+encodeURIComponent(account),{},timeout);if(current.account_currency===body.currency)return {...body,account,commit_id:current.commit_id,replayed:true} as T;}
  }
  if(recover&&path==='setup/opening'){
    const body=JSON.parse(String(options.body||'{}')) as {operation_id?:string};
    if(body.operation_id){const result=await transport<{committed?:boolean}>('setup/operations/'+body.operation_id,{},timeout);if(result.committed!==false)return result as T;}
  }
  if(recover&&['settings/name','settings/default-currency','accounts/currency'].includes(path)){
    const wanted=JSON.parse(String(options.body||'{}')) as {name?:string;currency?:string;account?:string};
    const current=await transport<{name:string;currency:string;account_currency?:string;commit_id:string}>('settings'+(wanted.account?'?account='+encodeURIComponent(wanted.account):''),{},timeout);
    const matches=path==='settings/name'?current.name===wanted.name:path==='accounts/currency'?current.account_currency===wanted.currency:current.currency===wanted.currency;
    if(matches)return {...wanted,commit_id:current.commit_id,replayed:true} as T;
  }
  // Usual and bill endpoints already validate their frozen request hash/body
  // on replay, including queued and completed drafts. Let that check run.
  const source=path.match(/^(drafts\/[^/]+)\/(hand-add|approve-accounts)$|^(statements\/[^/]+)\/apply$/);
  const cancellation=path.match(/^(statements\/[^/]+)\/cancel$/);if(cancellation)markApplyCancellation(cancellation[1]);
  const result=await committedResult(path,async()=>{
    if(recover&&path==='setup/import'){const body=JSON.parse(String(options.body||'{}')) as {sessions:string[]};const sessions=await Promise.all(body.sessions.map(id=>transport<{state:string}>('statements/'+id,{},timeout)));if(sessions.every(item=>['applying','applied'].includes(item.state)))return {sessions} as T;}
    if(recover&&source){
      const value=await transport<{state:string;result?:unknown;claim?:unknown;finished_by?:unknown;account_question?:unknown}>(source[1]||source[3],{},timeout);
      // An added Hand draft counts only if this endpoint finished it: after an
      // expired claim the agent may have added the draft from the original words.
      const action=source[2],pending=['queued','working'].includes(value.state),committed=value.state==='added'&&!!value.result&&(action!=='hand-add'||!!value.finished_by);
      // A queued draft can still be claimed for Hand, or waiting for account
      // consent. Only the marker set by this endpoint proves it was accepted.
      const sent=source[3]?['applying','applied'].includes(value.state):committed||
        action==='hand-add'&&pending&&!!value.finished_by&&!value.claim||
        action==='approve-accounts'&&value.state==='queued'&&!value.account_question;
      if(sent)return value as T;
    }
    return transport<T>(path,options,timeout);
  },source=>transport(source,{},timeout),slow=>new Promise(resolve=>setTimeout(resolve,slow?1000:250)),value=>new ApiError(409,value.error||value.refusal?.message||'The entry was not saved. Open Queue to check it.',{detail:value.refusal}),accepted,waiting);
  completeWrite(path,options,result);return result;
}
/** All successful paths, including shelf retry and reload recovery, publish the
 * same tombstones and notifications before the job is acknowledged. */
function completeWrite(path:string,options:RequestInit,result:unknown){
 const value=result as {refused?:boolean}|undefined;if(value?.refused)return;
 const method=(options.method||'GET').toUpperCase();if(['GET','HEAD'].includes(method))return;const queue=path.match(/^(drafts|statements)\/([^/]+)(\/restore)?$/);
 if(queue&&method==='DELETE'){rememberRemoved({id:queue[2]});window.dispatchEvent(new Event('item-removed'));}
 if(queue&&queue[3]&&method==='POST'){forgetRemoved(queue[2]);window.dispatchEvent(new Event('item-restored'));}
 if(path.startsWith('retention/operations/')){
  const body=JSON.parse(String(options.body||'{}')) as {action:string;item_id?:string;items?:string[]};
  if(body.action==='queue-restore'&&body.item_id){forgetRemoved(body.item_id);window.dispatchEvent(new Event('item-restored'));}
  if(['restore','remove','queue-restore'].includes(body.action))window.dispatchEvent(new CustomEvent('trash-items-removed',{detail:body.items||[body.item_id]}));
 }
 if(queue&&(method==='DELETE'||method==='POST'&&queue[3])||path.startsWith('retention/operations/'))window.dispatchEvent(new Event('drafts-changed'));
}
async function transport<T>(path:string,options:RequestInit,timeout:number):Promise<T>{
  if(departing)throw new DOMException('Request cancelled','AbortError');
  const item=path.match(/^drafts\/([^/]+)/)?.[1],method=(options.method||'GET').toUpperCase();
  if(item&&(isRemoved(item)||isRemovalPending(item))&&!['GET','HEAD','DELETE'].includes(method)&&!path.endsWith('/restore'))throw new DOMException('Item moved to Trash','AbortError');
  const read = ['GET','HEAD'].includes((options.method || 'GET').toUpperCase());
  // A write can already be in flight before pagehide. Keep ordinary JSON writes
  // alive too, without changing explicit cancellation. Eight thousand UTF-16
  // units use at most 24 KiB, leaving room in the browser's 64 KiB budget for
  // another autosave; larger supported inputs retain the normal transport.
  const smallBody=typeof options.body==='string' && options.body.length<=8192;
  const keepalive=read || (options.keepalive ?? smallBody);
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeout);
  let rejectCancellation: ((reason: DOMException) => void) | undefined;
  const cancellation = read && options.signal ? new Promise<never>((_,reject) => {rejectCancellation=reject;}) : null;
  const abortError = () => new DOMException('Request cancelled','AbortError');
  // Read cancellation is logical: the caller stops waiting immediately, while a
  // keepalive transport finishes safely across WebKit document teardown. Writes
  // retain transport cancellation. Both retain the original network deadline.
  const cancelled=()=>{if(read)rejectCancellation?.(abortError());else controller.abort();};
  let started=false;
  try {
    if(options.signal?.aborted) throw abortError();
    options.signal?.addEventListener('abort',cancelled,{once:true});
    started=true;
    const operation=(async()=>{
      try {
        const response = await fetch(apiUrl(path), { ...options, keepalive, signal:controller.signal, cache:'no-store' });
        if (!response.ok) {const body=await response.json().catch(()=>null);throw new ApiError(response.status,errorMessage(response.status,body),body);}
        return response.status===204 ? undefined as T : await response.json() as T;
      } finally {clearTimeout(timer);}
    })();
    return await (cancellation ? Promise.race([operation,cancellation]) : operation);
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError' && options.signal?.aborted) throw error;
    if (error instanceof Error && error.name === 'AbortError') throw new Error('The server took too long. Try again.');
    if (error instanceof TypeError) throw new Error('Could not reach the live server. Check your connection and try again.');
    throw error;
  } finally {if(!started)clearTimeout(timer);options.signal?.removeEventListener('abort',cancelled);}
}

/** A removal is visibly pending until its own committed response. List reads
 * may finish in any order; keep the last visible item in that slot meanwhile. */
const visibleItems=new Map<string,{id:string;trashed_at?:string|null}>();
export const isRemovalPending=(id:string)=>writeSnapshot().some(item=>item.method==='DELETE'&&/^(drafts|statements)\/[^/]+$/.test(item.path)&&item.path.split('/')[1]===id&&['queued','saving','waiting'].includes(item.state));
export const pendingRemovalItem=<T,>(id:string)=>isRemovalPending(id)?visibleItems.get(id) as T|undefined:undefined;
export function keepPendingItems<T extends {id:string;trashed_at?:string|null}>(kind:'drafts'|'statements',items:T[]):T[]{
 const pending=writeSnapshot().filter(item=>item.method==='DELETE'&&item.path.startsWith(kind+'/')&&item.path.split('/').length===2&&['queued','saving','waiting'].includes(item.state)).map(item=>item.path.split('/')[1]);
 const result=items.map(item=>{if(pending.includes(item.id))return (visibleItems.get(item.id)||item) as T;if(!item.trashed_at)visibleItems.set(item.id,item);return item;});
 for(const id of pending)if(!result.some(item=>item.id===id)&&visibleItems.has(id))result.push(visibleItems.get(id) as T);
 return result;
}
// A removal tombstone outlives in-flight list/result reads on this page.
const removed = new Map<string,{id:string;trashed_at?:string|null}>();
export const forgetRemoved = (id:string) => removed.delete(id);
export const isRemoved = (id:string) => removed.has(id);
export const removedItem = <T,>(id:string) => removed.get(id) as T|undefined;
export function rememberRemoved<T extends {id:string;trashed_at?:string|null}>(item:T) { removed.set(item.id,{...item,trashed_at:item.trashed_at||new Date().toISOString()}); }
const json = (method: string, body?: unknown): RequestInit => ({ method, ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}) });
export const api = {
  correctionForm:(id:string)=>request<Draft>(`entries/${id}/correction-form`,json('POST')),
  correct:(id:string,op:string,body:object,options:WriteOptions={})=>request<CorrectionResult>(`entries/${id}/corrections/${op}`,{...json('POST',body),...options},120000),
  receiptChoice:(id:string,choice:'use'|'keep',options:WriteOptions={})=>request<Draft>(`drafts/${id}/receipt-choice`,{...json('POST',{choice}),...options},120000),
  health: (signal?: AbortSignal) => request<{ok: boolean;data?:import('../data').DataStatus}>('health', {signal, keepalive:true}, 5000),
  list: async(signal?:AbortSignal)=>keepPendingItems('drafts',await request<Draft[]>('drafts?include_added=true',{signal})),
  get: async (id: string) => {
    if(removed.has(id))return removedItem<Draft>(id)!;
    const pending=pendingRemovalItem<Draft>(id);if(pending)return pending;
    const value=await request<Draft>(`drafts/${encodeURIComponent(id)}`);
    if(value.trashed_at&&!isRemovalPending(id))rememberRemoved(value);
    return removedItem<Draft>(id)||keepPendingItems('drafts',[value])[0];
  },
  create: (text: string, pieces: unknown, client_updated_at=0,declined_rules:string[]=[]) => request<Draft>('drafts', json('POST', {text, pieces, client_updated_at, declined_rules, ...clientTime()})),
  edit: (id: string, text: string, pieces: unknown, client_updated_at=0,declined_rules:string[]=[]) => request<Draft>(`drafts/${id}`, json('PATCH', {text, pieces, client_updated_at, declined_rules, ...clientTime()})),
  save: (id: string) => request<Draft>(`drafts/${id}/save`, json('POST', clientTime())),
  answer: (id: string, answer: string) => request<Draft>(`drafts/${id}/answer`, json('POST', {answer})),
  currency: (id:string, body:{amount?:string;rate?:string;estimate:boolean}) => request<Draft>(`drafts/${id}/currency`,json('POST',body)),
  claim: (id:string,claim_token?:string)=>request<{draft:Draft;claim_token:string;form:Proposal}>(`drafts/${id}/claim`,json('POST',{claim_token})),
  handForm:(id:string,claim_token:string,proposal:Proposal,sources?:string[],reason?:string)=>request<HandChecks>(`drafts/${id}/hand-form`,json('POST',{claim_token,proposal,...(sources?{sources}:{}),...(reason!==undefined?{reason}:{})})),
  handAdd:(id:string,claim_token:string,proposal_hash:string,approved_accounts:string[],options:WriteOptions={})=>request<Draft>(`drafts/${id}/hand-add`,{...json('POST',{claim_token,proposal_hash,approved_accounts}),...options}),
  release:(id:string,claim_token:string,proposal?:Proposal)=>request<Draft>(`drafts/${id}/release`,json('POST',{claim_token,proposal})),
  approveAccounts:(id:string,proposal_hash:string)=>request<Draft>(`drafts/${id}/approve-accounts`,json('POST',{proposal_hash})),
  resolveRefusal:(id:string,action:'edit'|'answer')=>request<Draft>(`drafts/${id}/resolve-refusal`,json('POST',{action})),
  retry: (id: string) => request<Draft>(`drafts/${id}/retry`, json('POST')),
  keepAnswer: (id: string, question_index: number, answer: string, updated_at=0) => request<Draft>(`drafts/${id}/unsent-answer`, json('PATCH', {question_index, answer, updated_at})),
  restore: (id:string)=>request<Draft>(`drafts/${id}/restore`,json('POST')),
  cancelUpload: (id: string, token: string) => request<Draft>(`drafts/${id}/uploads/${token}`, json('DELETE')),
  discard:(id:string)=>request<void>(`drafts/${id}`,json('DELETE')),
  remove: (id: string, file: string, claimToken?:string) => request<Draft>(`drafts/${id}/files/${file}${claimToken?'?claim_token='+encodeURIComponent(claimToken):''}`, json('DELETE')),
};
export const fileUrl = (id: string, file: string) => apiUrl(`drafts/${encodeURIComponent(id)}/files/${encodeURIComponent(file)}`);
/** XHR exposes upload progress; the browser supplies the multipart boundary. */
export function upload(id: string, file: File, progress: (value: number) => void, signal?: AbortSignal, token?: string, claimToken?:string): Promise<Draft> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(); xhr.open('POST', apiUrl(`drafts/${id}/files`)); xhr.timeout = 120000;
    xhr.upload.onprogress = e => { if (e.lengthComputable) progress(Math.round(e.loaded / e.total * 100)); };
    const abort=()=>{xhr.abort();reject(new DOMException('Upload cancelled','AbortError'));};
    xhr.onabort=()=>reject(new DOMException('Upload cancelled','AbortError'));
    xhr.onloadend=()=>signal?.removeEventListener('abort',abort);
    signal?.addEventListener('abort',abort,{once:true});
    if(signal?.aborted){abort();return;}
    xhr.onerror = () => reject(new Error('Upload interrupted. Check your connection and retry.'));
    xhr.ontimeout = () => reject(new Error('Upload took too long. Retry this file.'));
    xhr.onload = () => { let body; try { body = JSON.parse(xhr.responseText); } catch { reject(new Error('The server returned an unreadable upload response. Retry.')); return; } if (xhr.status >= 200 && xhr.status < 300) resolve(body); else reject(new Error(errorMessage(xhr.status, body))); };
    const data = new FormData(); data.append('files', file); if(token)data.append('upload_id',token);if(claimToken)data.append('claim_token',claimToken); xhr.send(data);
  });
}
