// Assign a statement to an account, or create one, while its rows are settled in Queue.
import {useState} from 'react';
import {dashboard,reloadData} from '../data';
import {request} from './api';
import {statementsApi,type StatementSession} from './statements-api';

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
