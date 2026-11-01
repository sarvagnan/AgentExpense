import {request} from './api';
import {clientTime} from '../date-time';
export type StatementRow = {date:string;serial:string;description:string;amount:string;direction:'Debit'|'Credit';page:number;citation?:{page?:number;line?:number;sheet?:string;row?:number};balance?:string|null;reference?:string|null;disposition?:string;foreignAmount:string|null;foreignCurrency:string|null;entry_id?:string;entry_description?:string;conversion_notes?:string[]};
export type Decision = {action:'match';entry_id:string}|{action:'account'|'pending';account?:string}|{action:'reset'|'conversion'};
export type StatementItem = {row:StatementRow;rule:string;why:string;entry_id:string|null;entry_description:string|null;postings:{account:string;amount:string;commodity:string}[]|null;conversion?:{old:string;billed:string};needs_user:boolean;decision:Decision|null;candidates:{entryId:string;date:string;description:string}[]};
export type Summary = {account:string;start:string;end:string;source:string;'previous-balance'?:string;purchases?:string;credits:string;'total-due'?:string;kind?:'bank';'opening-balance'?:string;'closing-balance'?:string;debits?:string;rows:number};
export type StatementSession = {id:string;state:'account_needed'|'currency_mismatch'|'locked'|'reading'|'wrong_file'|'couldnt_read'|'already_filed'|'found'|'review'|'applying'|'failed'|'applied';opening?:{date:string;postings:{amount:string;commodity:string}[]}|null;file_currency?:string;candidate_accounts?:string[];reader?:string;reader_name?:string;suggested_account?:string;original_name:string;client_time?:string;client_zone?:string;created_at:string;updated_at?:string;discarded_until?:string;handover_until?:string;duplicate_of?:string;apply_started?:boolean;failure_reason?:string;error:string|null;waiting:string|null;handover_draft?:string;statement?:{kind?:'bank';readerName?:string;readerPersonal?:boolean;openingBalance?:string;closingBalance?:string;currency:string;account:string;accountLabel:string;periodStart:string;periodEnd:string;totalDue:string;previousBalance:string;purchasesCharges:string;paymentsCredits:string;rows:StatementRow[]};checks?:{ok:boolean;text:string}[];plan?:{items:StatementItem[];counts:Record<string,number>;unmatched:{entry_id:string;date:string;description:string;amount:string}[]};filed?:Summary;result?:{changed:number;added:number;converted:number;end:string;source:string;data_stale?:boolean;refresh_error?:string}|null};
export type StatementList={sessions:StatementSession[];recoverable?:StatementSession[];filed:Summary[];accounts:string[];readers?:{id:string;name:string;extensions:string[];kinds:string[];personal:boolean}[]};
const json=(method:string,body?:unknown):RequestInit=>({method,...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
export const statementsApi={
 assign:(id:string,account:string)=>request<StatementSession>(`statements/${id}/account`,json('POST',{account})),
 list:(signal?:AbortSignal)=>request<StatementList>('statements',{signal}),
 get:(id:string,signal?:AbortSignal)=>request<StatementSession>(`statements/${id}`,{signal}),
 upload:(file:File,token:string,signal?:AbortSignal)=>{const data=new FormData();data.append('file',file);data.append('upload_id',token);for(const [key,value] of Object.entries(clientTime()))data.append(key,value);return request<StatementSession>('statements',{method:'POST',body:data,signal},120000);},
 cancelUpload:(token:string)=>request<void>(`statements/uploads/${token}`,json('DELETE')),
 cancelApply:(id:string)=>request<StatementSession>(`statements/${id}/cancel`,json('POST')),
 keep:(id:string)=>request<StatementSession>(`statements/${id}/keep`,json('POST')),
 unlock:(id:string,password:string)=>request<StatementSession>(`statements/${id}/unlock`,json('POST',{password})),
 review:(id:string)=>request<StatementSession>(`statements/${id}/review`,json('POST')),
 decide:(id:string,serial:string,body:Decision)=>request<StatementSession>(`statements/${id}/rows/${serial}`,json('PATCH',body)),
 apply:(id:string)=>request<StatementSession>(`statements/${id}/apply`,json('POST')),
 discard:(id:string)=>request<void>(`statements/${id}`,json('DELETE')),
 restore:(id:string)=>request<StatementSession>(`statements/${id}/restore`,json('POST')),
 filed:(end:string,signal?:AbortSignal,account?:string)=>request<{summary:Summary;rows:StatementRow[]}>(`statements/filed/${end}${account?`?account=${encodeURIComponent(account)}`:''}`,{signal}),
 handover:(id:string)=>request<StatementSession>(`drafts/${id}/statements`,json('POST')),
};
export const statementHref=(id:string)=>`${import.meta.env.BASE_URL}drafts/?session=${id}`;
export const filedHref=(end:string,account?:string)=>`${import.meta.env.BASE_URL}drafts/?filed=${end}${account?`&account=${encodeURIComponent(account)}`:''}`;
