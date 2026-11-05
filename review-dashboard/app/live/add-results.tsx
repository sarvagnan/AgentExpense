import {useEffect,useRef,useState} from 'react';
import {dashboard} from '../data';
import {announce,money,useQueryState,Icon} from '../ui';
import {plainReason} from '../first-run';
import {ruleHref} from '../rules';
import {api,type Draft,isRemoved} from './api';
import {statementsApi,statementHref,filedHref,type StatementSession} from './statements-api';
import {readVisit,forgetResult,type VisitResult} from './add-visit';
import {RuleSaved} from './rule-saved';
import ReceiptReading from './receipt-reading';
import {TransactionDetails} from '../dashboard';
import type {LiveDrafts} from './use-drafts';
export function draftStatus(d:Draft){return d.receipt_reading?.state==='question'?'Needs an answer':d.state==='added'?(d.result?.outcome==='not_expense'?'Not an expense':d.result?.outcome==='bill'?'Bill saved':'Added'):d.state==='question'?'Needs an answer':d.state==='failed'?'Failed':'Working';}
export function statementStatus(s:StatementSession){return s.state==='applied'?'Applied':['found','review'].includes(s.state)?'Needs decisions':s.state==='applying'?'Applying':s.state==='agent_waiting'?'Waiting for agent':s.state==='reading'?'Reading':s.state==='already_filed'?'Already filed':['locked','account_needed','currency_mismatch'].includes(s.state)?'Needs an answer':'Failed';}
export function useAddResults(live:LiveDrafts){
 const [ids,setIds]=useState(readVisit),[saved,setSaved]=useState<Record<string,Draft|StatementSession>>({});
 const statuses=useRef<Record<string,string>>({}),initialIds=useRef(new Set(ids.map(x=>x.id)));
 useEffect(()=>{const update=()=>setIds(readVisit());window.addEventListener('add-results-changed',update);return()=>window.removeEventListener('add-results-changed',update);},[]);
 useEffect(()=>{let alive=true;void Promise.all(ids.filter(item=>!isRemoved(item.id)&&!live.drafts.some(d=>d.id===item.id&&d.trashed_at)).map(async item=>{try{return [item.id,item.type==='draft'?await api.get(item.id):await statementsApi.get(item.id)] as const;}catch{return null;}})).then(rows=>{if(alive)setSaved(old=>({...old,...Object.fromEntries(rows.filter(x=>x!==null))}));});return()=>{alive=false;};},[ids,live.drafts,live.statements]);
 const rows=ids.map(item=>({...item,value:item.type==='draft'?live.drafts.find(d=>d.id===item.id)||saved[item.id]:live.statements.find(s=>s.id===item.id)||saved[item.id]})).filter((item):item is VisitResult&{value:Draft|StatementSession}=>!!item.value);
 useEffect(()=>{for(const row of rows){const status=row.type==='draft'?draftStatus(row.value as Draft):statementStatus(row.value as StatementSession);const old=statuses.current[row.id];if((old&&old!==status)||(!old&&!initialIds.current.has(row.id)&&['Added','Applied','Needs an answer','Needs decisions'].includes(status)))announce(`${status}: ${row.type==='draft'?(row.value as Draft).result?.description||(row.value as Draft).text:(row.value as StatementSession).original_name}`);statuses.current[row.id]=status;}},[rows]);
 return rows.filter(row=>{
  if(isRemoved(row.id)||(row.value as Draft).trashed_at)return false;
  const entryId=row.type==='draft'?(row.value as Draft).result?.entry_id:undefined;
  return !entryId||!dashboard?.transactions||dashboard.transactions.some(entry=>entry.storeId===entryId||entry.id===entryId);
 });
}
export function AddResults({live,rows}:{live:LiveDrafts;rows:ReturnType<typeof useAddResults>}){
 const [selected,setSelected]=useQueryState('entry','');
 const entry=dashboard.transactions.find(e=>e.id===selected||e.storeId===selected);
 if(!rows.length)return null;
 return <section className="just-added" aria-labelledby="just-added-title"><h2 id="just-added-title">Just added</h2><div className="just-added-rows">{rows.map(row=><ResultRow key={row.id} type={row.type} value={row.value} live={live} onOpen={id=>setSelected(id)}/>)}</div>{entry&&<TransactionDetails transaction={entry} onClose={()=>setSelected('')}/>}<footer><a href={`${import.meta.env.BASE_URL}queue/`}>Questions and work in Queue</a></footer></section>;
}
const queueHref=(id:string)=>`${import.meta.env.BASE_URL}queue/?draft=${id}`;
/** "18.60 USD" → the app's currency format; account paths → their labels. */
export function resultFacts(r:NonNullable<Draft['result']>){
 const total=r.total?.replace(/(-?\d+(?:\.\d+)?) ([A-Z]{3})/g,(all,amount,code)=>dashboard.meta.currencyMinorUnits?.[code]!==undefined?money(Number(amount),code):all)||'';
 const account=r.payment_account?Object.entries(dashboard.meta.accounts).reduce((text,[path,meta])=>text.replaceAll(path,meta.label||path.split(':').at(-1)||path),r.payment_account):'';
 return {total,account};
}
const statusKind=(status:string)=>['Added','Bill saved','Applied'].includes(status)?'done':['Needs an answer','Needs decisions','Failed'].includes(status)?'attention':status==='Not an expense'?'quiet':'working';
function Summary({title,status}:{title:string;status:string}){return <div className="result-summary"><h3>{title}</h3><span className={`result-status status-${statusKind(status)}`}>{status}</span></div>;}
function ResultRow({type,value,live,onOpen}:{onOpen:(id:string)=>void;type:VisitResult['type'];value:Draft|StatementSession;live:LiveDrafts}){
 const [more,setMore]=useState(false),[error,setError]=useState('');
 if(type==='statement'){
  const s=value as StatementSession,status=statementStatus(s),[reason,detail]=plainReason(s.error||'');
  const href=s.state==='applied'&&s.statement?filedHref(s.statement.periodEnd,s.statement.account):statementHref(s.id);
  return <article className="just-added-row" data-result-id={s.id}><Summary title={s.original_name} status={status}/>{s.statement&&<p className="result-meta">{s.statement.accountLabel} · {s.statement.currency}</p>}
  {s.error&&<p className="result-note">{reason}{detail&&<> <button className="text-button" aria-expanded={more} onClick={()=>setMore(!more)}>{more?'Less':'Details'}</button></>}</p>}{more&&detail&&<p className="result-detail">{detail}</p>}
  {['wrong_file','couldnt_read'].includes(s.state)&&<button className="button" onClick={()=>void statementsApi.retry(s.id).then(()=>live.refresh(),e=>setError((e as Error).message))}>Retry reading</button>}{error&&<p className="live-error">{error}</p>}
  {status==='Reading'&&<p className="result-meta">No rows have been applied. You can keep adding.</p>}{status==='Needs decisions'&&<p className="result-meta">Settle its rows in Queue before applying.</p>}
  <a className="result-link" href={href}>{s.state==='applied'?'View the filed statement':status==='Needs decisions'?'Decide in Queue':'Open in Queue'}<Icon name="chevron-right"/></a></article>;
 }
 const d=value as Draft,status=draftStatus(d),r=d.result,facts=r?resultFacts(r):{total:'',account:''};
 const title=(r?.description||d.text||'Expense with an attachment')+(facts.total?` · ${facts.total}`:'');
 // Added items and saved bills have left the Queue: link the entry or the rule's bill instead.
 const link=r?.entry_id?<a className="result-link" href={`?entry=${r.entry_id}`} onClick={e=>{e.preventDefault();onOpen(r.entry_id!);}}>Open entry<Icon name="chevron-right"/></a>
  :status==='Bill saved'?(r?.rule_id?<a className="result-link" href={ruleHref(r.rule_id)}>Open the rule’s bill<Icon name="chevron-right"/></a>:null)
  :status==='Needs an answer'&&!d.receipt_reading?<a className="result-link" href={queueHref(d.id)}>{d.questions.at(-1)?.question||'Answer in Queue'}<Icon name="chevron-right"/></a>
  :status==='Failed'?<a className="result-link" href={queueHref(d.id)}>Retry in Queue<Icon name="chevron-right"/></a>
  :status==='Working'?<a className="result-link" href={queueHref(d.id)}>Open in Queue<Icon name="chevron-right"/></a>:null;
 return <article className="just-added-row" data-result-id={d.id}><Summary title={title} status={status}/>
 {facts.account&&status!=='Not an expense'&&<p className="result-meta">{status==='Bill saved'?'Bill for':'Paid with'} {facts.account}</p>}
 {status==='Not an expense'&&<p className="result-note">{r?.explanation||'Nothing was added.'}</p>}
 {status==='Failed'&&<p className="result-note">Your words and files are kept.{d.error&&<> <button className="text-button" aria-expanded={more} onClick={()=>setMore(!more)}>{more?'Less':'More'}</button></>}</p>}
 {status==='Working'&&<p className="result-meta">The agent is working on it. You can keep adding.</p>}
 {d.receipt_reading&&<ReceiptReading draft={d} onAction={live.refresh} compact/>}
 {d.receipt_reading?.state!=='question'&&<RuleSaved draft={d} online={live.online} compact/>}
 {status!=='Failed'&&status!=='Not an expense'&&r?.explanation&&<><button className="text-button" aria-expanded={more} onClick={()=>setMore(!more)}>{more?'Less':'More'}</button></>}
 {more&&<p className="result-detail">{status==='Failed'?d.error:r?.explanation}</p>}{error&&<p className="live-error">{error}</p>}
 {link}
 {d.statement_file&&!d.handover_session&&<button className="button" onClick={()=>void statementsApi.handover(d.id).then(()=>live.refresh(),e=>setError((e as Error).message))}>Continue as statement</button>}
 </article>;
}
