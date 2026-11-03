import type { Piece } from './builder';
import {clientTime} from '../date-time';
export type Attachment = { id: string; original_name: string; media_type: string; size: number };
export type Proposal = {account_metadata?:Record<string,{kind:string;currency:string}>;status:string;question:string|null;explanation:string|null;entry:{date:string;status:string;description:string;notes:string[];postings:{account:string;amount:string;commodity:string;cost:{amount:string;commodity:string}|null}[];[key:string]:unknown};new_accounts:string[];doubts:string[];attachment_names:string[]};
export type AgentPause = {kind:'usage_limit'|'auth'|'transient';reason:string;resumes_at:string;attempt:number};
export type HandChecks = {ok:boolean;checks:string[];new_accounts:string[];proposal_hash:string};
export type Draft = {
  claim?:{expires_at:string}; agent_pause?:AgentPause|null; finished_by?:{actor:string;reason:string};
  account_question?:{proposal_hash:string;accounts:string[];postings:Proposal['entry']['postings']};
  declined_rules?:string[]; id: string; description?:string; client_updated_at?: number; state: 'editing' | 'queued' | 'working' | 'question' | 'failed' | 'added'; text: string;
  pieces: { pattern: string; edits?: Record<string,string>; fields: Record<string, Pick<Piece, 'value' | 'source'>> } | null;
  currency_request?: {kind:'expense'|'transfer';amount:string;sourceCurrency:string;currency:string;sourceAccount:string;account:string;sourceLabel:string;accountLabel:string};
  statement_file?: string; handover_session?: string; client_time?: string; client_zone?: string; created_at: string; saved_at: string | null; queued_at: string | null; files: Attachment[];
  runs: { start: string; doubts: string[]; error?: string | null }[];
  questions: { question: string; answer: string | null }[]; unsent_answer?: {question_index: number; answer: string; updated_at?: number}; error: string | null; waiting: string | null;
  result: null | {rule?:{id:string;occurrence:string};next_due?:string;made_rules?:string[];resume_rules?:{id:string;description:string}[];weekly_review_note?:string;can_undo?:boolean;undo_until?:string|null;undone?:boolean;audit_id?:string;usual_payment?:{estimated:boolean}; outcome: string; explanation?: string; entry_id?: string; description?: string; total?: string; payment_account?: string; new_accounts?: string[]; sources?: string[]; data_stale?: boolean; refresh_error?: string };
};
export const apiUrl = (path: string, base = import.meta.env.BASE_URL) => `${base.replace(/\/?$/, '/')}api/${path.replace(/^\//, '')}`;
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) { super(message); this.status=status; this.name='ApiError'; }
}
export function errorMessage(status: number, body: unknown) {
  const detail = typeof body === 'object' && body !== null && 'detail' in body ? (body as {detail: unknown}).detail : null;
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
export async function request<T>(path: string, options: RequestInit = {}, timeout = 12000): Promise<T> {
  if(departing)throw new DOMException('Request cancelled','AbortError');
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
        if (!response.ok) {const body=await response.json().catch(()=>null);throw new ApiError(response.status,errorMessage(response.status,body));}
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

const json = (method: string, body?: unknown): RequestInit => ({ method, ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}) });
export const api = {
  health: (signal?: AbortSignal) => request<{ok: boolean}>('health', {signal, keepalive:true}, 5000),
  list: (signal?: AbortSignal) => request<Draft[]>('drafts?include_added=true', {signal}),
  get: (id: string) => request<Draft>(`drafts/${encodeURIComponent(id)}`),
  create: (text: string, pieces: unknown, client_updated_at=0,declined_rules:string[]=[]) => request<Draft>('drafts', json('POST', {text, pieces, client_updated_at, declined_rules, ...clientTime()})),
  edit: (id: string, text: string, pieces: unknown, client_updated_at=0,declined_rules:string[]=[]) => request<Draft>(`drafts/${id}`, json('PATCH', {text, pieces, client_updated_at, declined_rules, ...clientTime()})),
  save: (id: string) => request<Draft>(`drafts/${id}/save`, json('POST', clientTime())),
  answer: (id: string, answer: string) => request<Draft>(`drafts/${id}/answer`, json('POST', {answer})),
  currency: (id:string, body:{amount?:string;rate?:string;estimate:boolean}) => request<Draft>(`drafts/${id}/currency`,json('POST',body)),
  claim: (id:string,claim_token?:string)=>request<{draft:Draft;claim_token:string;form:Proposal}>(`drafts/${id}/claim`,json('POST',{claim_token})),
  handForm:(id:string,claim_token:string,proposal:Proposal)=>request<HandChecks>(`drafts/${id}/hand-form`,json('POST',{claim_token,proposal})),
  handAdd:(id:string,claim_token:string,proposal_hash:string,approved_accounts:string[])=>request<Draft>(`drafts/${id}/hand-add`,json('POST',{claim_token,proposal_hash,approved_accounts})),
  release:(id:string,claim_token:string,proposal?:Proposal)=>request<Draft>(`drafts/${id}/release`,json('POST',{claim_token,proposal})),
  approveAccounts:(id:string,proposal_hash:string)=>request<Draft>(`drafts/${id}/approve-accounts`,json('POST',{proposal_hash})),
  retry: (id: string) => request<Draft>(`drafts/${id}/retry`, json('POST')),
  keepAnswer: (id: string, question_index: number, answer: string, updated_at=0) => request<Draft>(`drafts/${id}/unsent-answer`, json('PATCH', {question_index, answer, updated_at})),
  restore: (id: string) => request<Draft>(`drafts/${id}/restore`, json('POST')),
  cancelUpload: (id: string, token: string) => request<Draft>(`drafts/${id}/uploads/${token}`, json('DELETE')),
  discard: (id: string) => request<void>(`drafts/${id}`, json('DELETE')),
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
