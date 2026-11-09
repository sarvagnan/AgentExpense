import {useCommittedError,writeOwnedBy} from './write-completion';
import {retryWrite,writeSnapshot,type DirectWrite} from './direct-writes';
import {ActionFeedback,useWriteOwner} from './pending-actions';
import {useRetainedState,clearRetained,retainedSnapshot} from './retained-state';
// Assign a statement to an account, or create one, while its rows are settled in Queue.
import {useEffect,useRef,useState} from 'react';
import {dashboard,reloadData} from '../data';
import {request} from './api';
import {statementsApi,type StatementSession} from './statements-api';

export function StatementAccount({session,onSaved}:{session:StatementSession;onSaved:(s:StatementSession)=>void}) {
 const prefix=`statement-account-${session.id}-`,owner=useWriteOwner(prefix),fields=['account','adding','name','kind'].map(key=>prefix+key);
 const [account,setAccount,initializeAccount]=useRetainedState(prefix+'account',''),[adding,setAdding,initializeAdding]=useRetainedState(prefix+'adding',false),[name,setName,initializeName]=useRetainedState(prefix+'name',''),[kind,setKind,initializeKind]=useRetainedState(prefix+'kind','card'),[busy,setBusy]=useState(false),[error,setError]=useCommittedError(write=>write.path==='accounts'&&writeOwnedBy(write,owner));
 const latest=useRef({name,kind,account});latest.current={name,kind,account};
 const active=useRef(true);useEffect(()=>{active.current=true;return()=>{active.current=false;};},[session.id]);
 const toggle=()=>{if(adding){clearRetained(fields);initializeAdding(false);initializeName('');initializeKind('card');initializeAccount('');}else initializeAdding(true);};
 const currency=session.file_currency, choosing=session.state==='account_needed';
 const accounts=Object.entries(dashboard.meta.accounts).filter(([a,m])=>['bank','card'].includes(m.kind||'')&&(!currency||m.currency===currency)&&(!session.candidate_accounts||session.candidate_accounts.includes(a)));
 async function assign(create=false,retry?:DirectWrite){setBusy(true);setError('');try{
  let target=account;
  if(create){const result=retry?await retryWrite(retry.id) as {account:string}:await request<{account:string}>('accounts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,kind,currency}),writeOwner:owner,clearRetained:retainedSnapshot(fields)},120000);if(retry&&writeSnapshot().find(value=>value.id===retry.id)?.state!=='saved')return;target=result.account;if(!active.current)return;if(retry){const submitted=JSON.parse(retry.body);const priorChoice=retry.clearRetained?.[`ledger-form-${dashboard.meta.ledger.id}-${prefix}account`];if(latest.current.name!==submitted.name||latest.current.kind!==submitted.kind||latest.current.account!==(priorChoice?JSON.parse(priorChoice):'')){await reloadData();return;}}initializeAccount(target);initializeAdding(false);await reloadData();}
  if(active.current)onSaved(await statementsApi.assign(session.id,target));
  window.dispatchEvent(new Event('drafts-changed'));
 }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <section className="statement-currency"><strong>{choosing?'Choose the statement account':`Statement currency: ${currency || 'Not printed'}`}</strong>{session.suggested_account&&<p>Assigned account: {dashboard.meta.accounts[session.suggested_account]?.label || session.suggested_account.split(':').at(-1)} ({dashboard.meta.accounts[session.suggested_account]?.currency})</p>}<p className="live-error">{choosing?'Several configured accounts can read this format. Choose the account the file belongs to.':currency?'The file and account currencies must match. Choose another account or add one in '+currency+'.':'The statement needs an explicit printed currency before it can continue.'}</p>
 {(currency||choosing)&&<><label className="field-label" htmlFor={`statement-account-${session.id}`}>{choosing?'Statement account':`Account in ${currency}`}</label><select className="field" id={`statement-account-${session.id}`} value={account} onChange={e=>setAccount(e.target.value)} disabled={busy}><option value="">Choose an account</option>{accounts.map(([a,m])=><option key={a} value={a}>{m.label || a.split(':').at(-1)} ({m.currency})</option>)}</select><div className="statement-actions"><button className="button" disabled={!account||busy} onClick={()=>void assign()}>Use this account</button>{currency&&<button className="button" disabled={busy} onClick={toggle}>Add an account in {currency}</button>}</div>
 {adding&&<form className="inline-account form-block" onSubmit={e=>{e.preventDefault();void assign(true);}}><h3>New account</h3><div><label htmlFor={`new-kind-${session.id}`}>Kind</label><select id={`new-kind-${session.id}`} className="field" value={kind} onChange={e=>{setKind(e.target.value);setAdding(true);}} disabled={busy}><option value="card">Card</option><option value="bank">Bank</option></select></div><label>Name<input className="field" value={name} onChange={e=>{setName(e.target.value);setAdding(true);}} maxLength={100} required disabled={busy}/></label><label>Currency<input className="field" value={currency} readOnly/></label><button className="button primary" disabled={busy||!name.trim()}>Create and assign account</button></form>}</>}
 <div className="action-error-slot">{error&&<p className="live-error" role="alert">{error}</p>}</div><ActionFeedback owner={owner} scope="settings" path="accounts" onRetry={item=>void assign(true,item)}/></section>;
}
