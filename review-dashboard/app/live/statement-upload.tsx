import {useEffect,useRef,useState} from 'react';
import {Icon,PanelHeader} from '../ui';
import {dashboard,reloadData} from '../data';
import {request} from './api';
import {statementsApi,statementHref,type StatementSession} from './statements-api';
import {useStatementIntake,startStatementUpload,cancelStatementUpload,clearStatementIntake} from './statement-intake';
import type {LiveDrafts} from './use-drafts';

export function StatementAccount({session,onSaved}:{session:StatementSession;onSaved:(s:StatementSession)=>void}) {
 const [account,setAccount]=useState(''),[adding,setAdding]=useState(false),[name,setName]=useState(''),[kind,setKind]=useState('card'),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const currency=session.file_currency, choosing=session.state==='account_needed';
 const accounts=Object.entries(dashboard.meta.accounts).filter(([a,m])=>['bank','card'].includes(m.kind||'')&&(!currency||m.currency===currency)&&(!session.candidate_accounts||session.candidate_accounts.includes(a)));
 async function assign(create=false){setBusy(true);setError('');try{
  let target=account;
  if(create){const result=await request<{account:string}>('accounts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,kind,currency})},120000);target=result.account;setAccount(target);setAdding(false);await reloadData();}
  onSaved(await statementsApi.assign(session.id,target));
  window.dispatchEvent(new Event('drafts-changed'));
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <section className="statement-currency"><strong>{choosing?'Choose the statement account':`Statement currency: ${currency || 'Not printed'}`}</strong>{session.suggested_account&&<p>Assigned account: {dashboard.meta.accounts[session.suggested_account]?.label || session.suggested_account.split(':').at(-1)} ({dashboard.meta.accounts[session.suggested_account]?.currency})</p>}<p className="live-error">{choosing?'Several configured accounts can read this format. Choose the account the file belongs to.':currency?'The file and account currencies must match. Choose another account or add one in '+currency+'.':'The statement needs an explicit printed currency before it can continue.'}</p>
 {(currency||choosing)&&<><label className="field-label" htmlFor={`statement-account-${session.id}`}>{choosing?'Statement account':`Account in ${currency}`}</label><select className="field" id={`statement-account-${session.id}`} value={account} onChange={e=>setAccount(e.target.value)} disabled={busy}><option value="">Choose an account</option>{accounts.map(([a,m])=><option key={a} value={a}>{m.label || a.split(':').at(-1)} ({m.currency})</option>)}</select><div className="statement-actions"><button className="button" disabled={!account||busy} onClick={()=>void assign()}>Use this account</button>{currency&&<button className="button" disabled={busy} onClick={()=>setAdding(!adding)}>Add an account in {currency}</button>}</div>
 {adding&&<form className="inline-account form-block" onSubmit={e=>{e.preventDefault();void assign(true);}}><h3>New account</h3><div><label htmlFor={`new-kind-${session.id}`}>Kind</label><select id={`new-kind-${session.id}`} className="field" value={kind} onChange={e=>setKind(e.target.value)} disabled={busy}><option value="card">Card</option><option value="bank">Bank</option></select></div><label>Name<input className="field" value={name} onChange={e=>setName(e.target.value)} maxLength={100} required disabled={busy}/></label><label>Currency<input className="field" value={currency} readOnly/></label><button className="button primary" disabled={busy||!name.trim()}>Create and assign account</button></form>}</>}
 {error&&<p className="live-error" role="alert">{error}</p>}</section>;
}
export default function StatementUpload({live}:{live:LiveDrafts}){
 const picker=useRef<HTMLInputElement>(null),intake=useStatementIntake();
 const [ids,setIds]=useState<string[]>([]),[dragging,setDragging]=useState(false),[error,setError]=useState('');
 useEffect(()=>{if(intake.result){setIds(xs=>[...new Set([...xs,intake.result!.id])]);clearStatementIntake();void live.refresh();}},[intake.result]);
 const [formats,setFormats]=useState('.pdf,.csv,.xlsx,.xls');
 useEffect(()=>{void statementsApi.list().then(data=>{const configured=new Set(Object.values(dashboard.meta.accounts).map(m=>m.statement?.reader));const extensions=(data.readers||[]).filter(r=>configured.has(r.id)).flatMap(r=>r.extensions);if(extensions.length)setFormats([...new Set(extensions)].join(','));}).catch(()=>{});},[]);
 const sessions=live.statements.filter(s=>ids.includes(s.id));
 async function discard(id:string){try{setError('');await statementsApi.discard(id);setIds(xs=>xs.filter(x=>x!==id));await live.refresh();}catch(e){setError((e as Error).message);}}
 return <section className="panel statement-upload-panel"><PanelHeader title="A statement" meta="Check a bank or card statement against your entries."/><div className="panel-body"><div className={`statement-dropzone ${dragging?'dragging':''}`} onDragOver={e=>{e.preventDefault();e.stopPropagation();setDragging(true);}} onDragLeave={()=>setDragging(false)} onDrop={e=>{e.preventDefault();e.stopPropagation();setDragging(false);const file=e.dataTransfer.files[0];if(file)void startStatementUpload(file);}}><Icon name="source"/><strong>Drop a statement here</strong><p className="live-hint">PDF, CSV or spreadsheet. Summary totals and running balances are checked when available.</p><button type="button" className="button" disabled={live.online!==true||!!intake.name} onClick={()=>picker.current?.click()}>Choose statement</button><input ref={picker} type="file" accept={formats} className="sr-only" aria-label="Upload statement PDF, CSV, XLSX or XLS" disabled={live.online!==true||!!intake.name} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void startStatementUpload(file);}}/></div>
 <p className="live-hint statement-upload-hint">You’ll settle its rows in Drafts before applying it.</p>
 {intake.name&&<div role="status"><p>Uploading {intake.name}</p><progress aria-label="Uploading statement"/><button className="button" onClick={()=>void cancelStatementUpload()}>Cancel upload</button></div>}
 {intake.message&&!intake.name&&<p role="status">{intake.message}</p>}{intake.error&&<p className="live-error" role="alert">{intake.error}{intake.token&&<button className="button" onClick={()=>void cancelStatementUpload()}>Retry cancellation</button>}</p>}{error&&<p className="live-error" role="alert">{error}</p>}
 {sessions.map(s=><section className="uploaded-statement" key={s.id}><strong>{s.original_name}</strong>{['currency_mismatch','account_needed'].includes(s.state)?<StatementAccount session={s} onSaved={()=>void live.refresh()}/>:<><p>{s.statement?`${s.statement.readerName || s.reader_name || 'Statement reader'} · ${s.statement.accountLabel} (${s.statement.currency}) · File currency: ${s.statement.currency}`:s.state==='locked'?'Password needed':'Reading statement…'}</p><a className="button" href={statementHref(s.id)}>Open in Drafts</a></>}<button className="button destructive" onClick={()=>void discard(s.id)}>Remove file</button></section>)}
 </div></section>;
}
