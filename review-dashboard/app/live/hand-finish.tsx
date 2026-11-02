import {useEffect,useRef,useState} from 'react';
import {dashboard} from '../data';
import {api,apiUrl,fileUrl,upload,type Draft,type Proposal,type HandChecks} from './api';

export default function HandFinish({draft,onDone}:{draft:Draft;onDone:()=>Promise<void>}) {
  const [files,setFiles]=useState(draft.files);
  const [form,setForm]=useState<Proposal|null>(null),[checks,setChecks]=useState<HandChecks|null>(null),[ticks,setTicks]=useState<string[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const lastActivity=useRef(Date.now());
  const departing=useRef(false),autosave=useRef<ReturnType<typeof setTimeout>|null>(null);
  const token=useRef(''),latest=useRef<Proposal|null>(null),active=useRef(true),saved=useRef(false),chain=useRef<Promise<unknown>>(Promise.resolve());
  const key=`hand-claim-${dashboard.meta.ledger.id}-${draft.id}`;
  const persist=(force=false)=>{if(!token.current||!latest.current||departing.current&&!force)return chain.current;const value=latest.current,claim=token.current;chain.current=chain.current.catch(()=>{}).then(()=>api.handForm(draft.id,claim,value));return chain.current;};
  const stopAutosave=()=>{departing.current=true;if(autosave.current)clearTimeout(autosave.current);};
  useEffect(()=>{
    active.current=true;departing.current=false;
    void api.claim(draft.id,sessionStorage.getItem(key)||undefined).then(result=>{token.current=result.claim_token;sessionStorage.setItem(key,token.current);latest.current=result.form;if(active.current)setForm(result.form);}).catch(e=>{if(active.current)setError(e.message);});
    const leave=()=>{stopAutosave();if(!saved.current&&token.current){void fetch(apiUrl(`drafts/${draft.id}/release`),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({claim_token:token.current,proposal:latest.current}),keepalive:true}).catch(()=>{});}};
    window.addEventListener('pagehide',leave);

    return()=>{active.current=false;stopAutosave();window.removeEventListener('pagehide',leave);if(!saved.current&&token.current)void chain.current.catch(()=>{}).then(()=>api.release(draft.id,token.current,latest.current||undefined)).catch(()=>{});};
  },[draft.id]);
  useEffect(()=>{if(!form||departing.current)return;autosave.current=setTimeout(()=>{void persist().catch(e=>{if(active.current)setError(e.message);});},500);return()=>{if(autosave.current)clearTimeout(autosave.current);};},[form]);
  function activity(){if(Date.now()-lastActivity.current<60000||!token.current||departing.current)return;lastActivity.current=Date.now();void persist().catch(e=>{if(active.current)setError(e.message);});}
  function change(next:Proposal){latest.current=next;setForm(next);setChecks(null);setTicks([]);setError('');}
  async function check(){setBusy(true);setError('');const value=latest.current;try{if(autosave.current)clearTimeout(autosave.current);const result=await persist() as HandChecks;if(latest.current===value){setChecks(result);setTicks([]);}}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function add(){if(!checks)return;stopAutosave();setBusy(true);setError('');try{await chain.current;await api.handAdd(draft.id,token.current,checks.proposal_hash,ticks);saved.current=true;sessionStorage.removeItem(key);await onDone();}catch(e){departing.current=false;setError((e as Error).message);}finally{setBusy(false);}}
  async function back(){stopAutosave();setBusy(true);try{await persist(true);await api.release(draft.id,token.current,latest.current||undefined);saved.current=true;sessionStorage.removeItem(key);await onDone();}catch(e){departing.current=false;setError((e as Error).message);setBusy(false);}}
  const entry=form?.entry;
  return <article className="panel draft-detail hand-finish" aria-label="Finish it yourself"><div className="panel-header"><div><h2>Finish it yourself</h2><p>Your form is kept in Drafts. Activity renews this page’s 30-minute claim.</p></div></div><div className="panel-body">
    {error&&<p role="alert" className="live-error">{error}</p>}
    {!form||!entry?<p role="status">{error?'The form could not be opened.':'Opening the form…'}</p>:<form onPointerDown={activity} onKeyDown={activity} onSubmit={e=>{e.preventDefault();void check();}}><fieldset disabled={busy} className="hand-fields">
      <label>Date<input type="date" className="field" value={entry.date} onChange={e=>change({...form,entry:{...entry,date:e.target.value}})} required/></label>
      <label>Description<input className="field" value={entry.description} onChange={e=>change({...form,entry:{...entry,description:e.target.value}})} required maxLength={1000}/></label>
      <h3>Postings</h3><p className="live-hint">Positive amounts receive money; negative amounts pay. A total cost balances a foreign-currency posting.</p>
      <datalist id={`accounts-${draft.id}`}>{Object.keys(dashboard.meta.accounts).map(a=><option key={a} value={a}/>)}</datalist>
      {entry.postings.map((p,i)=><fieldset key={i} className="hand-posting"><legend>Posting {i+1}</legend>
        {(['account','amount','commodity'] as const).map(key=><label key={key}>{key==='commodity'?'Currency':key==='account'?'Account':'Amount'}<input className="field" list={key==='account'?`accounts-${draft.id}`:undefined} value={p[key]} onChange={e=>{const postings=entry.postings.map((row,n)=>n===i?{...row,[key]:e.target.value}:row);change({...form,entry:{...entry,postings}});}} required/></label>)}
        <label className="hand-cost-toggle"><input type="checkbox" checked={!!p.cost} onChange={e=>change({...form,entry:{...entry,postings:entry.postings.map((row,n)=>n===i?{...row,cost:e.target.checked?{amount:'',commodity:p.commodity}:null}:row)}})}/>Total cost</label>
        {p.cost&&(['amount','commodity'] as const).map(key=><label key={key}>Cost {key==='commodity'?'currency':'amount'}<input className="field" value={p.cost![key]} onChange={e=>change({...form,entry:{...entry,postings:entry.postings.map((row,n)=>n===i?{...row,cost:{...row.cost!,[key]:e.target.value}}:row)}})} required/></label>)}
        <button type="button" className="button" disabled={entry.postings.length<=2} onClick={()=>change({...form,entry:{...entry,postings:entry.postings.filter((_,n)=>n!==i)}})}>Remove posting {i+1}</button>
      </fieldset>)}
      <button type="button" className="button" onClick={()=>change({...form,entry:{...entry,postings:[...entry.postings,{account:'',amount:'',commodity:entry.postings[0]?.commodity||dashboard.meta.ledger.defaultCurrency,cost:null}]}})}>Add posting</button>
      <label>Notes<textarea aria-label="Notes" className="field" value={entry.notes.join('\n')} onChange={e=>change({...form,entry:{...entry,notes:e.target.value.split('\n').filter(Boolean)}})}/></label>
      <label>Attachments<input type="file" multiple accept="image/jpeg,image/png,image/webp,application/pdf" disabled={busy} onChange={e=>{const files=Array.from(e.target.files||[]);setBusy(true);void(async()=>{let result=draft;for(const file of files)result=await upload(draft.id,file,()=>{},undefined,crypto.randomUUID().replaceAll('-',''),token.current);setFiles(result.files);change({...latest.current!,attachment_names:result.files.map(f=>f.original_name.replace(/\.[^.]+$/,''))});})().catch(e=>setError(e.message)).finally(()=>setBusy(false));e.target.value='';}}/></label>
      <p>{form.attachment_names.length} attachment{form.attachment_names.length===1?'':'s'}</p>
      {!!files.length&&<ul className="filed-evidence">{files.map((file,i)=><li key={file.id}><a href={fileUrl(draft.id,file.id)} target="_blank" rel="noopener">{file.original_name}</a> <button className="button" type="button" disabled={busy} onClick={()=>{setBusy(true);void api.remove(draft.id,file.id,token.current).then(result=>{setFiles(result.files);change({...latest.current!,attachment_names:latest.current!.attachment_names.filter((_,n)=>n!==i)});}).catch(e=>setError(e.message)).finally(()=>setBusy(false));}}>Remove {file.original_name}</button></li>)}</ul>}
      <label>New accounts (one exact account per line)<textarea aria-label="New accounts" className="field" value={form.new_accounts.join('\n')} onChange={e=>change({...form,new_accounts:e.target.value.split('\n').filter(Boolean)})}/></label>
      {checks&&<section aria-label="Entry checks"><h3>{checks.ok?'Checks passed':'Please check the form'}</h3>{checks.checks.map((c,i)=><p key={i}>{c}</p>)}{checks.new_accounts.filter(a=>!['Expenses','Income','Equity'].includes(a.split(':')[0])).map(a=><label key={a}>Kind for {a}<select className="field" value={form.account_metadata?.[a]?.kind||''} onChange={e=>change({...form,account_metadata:{...form.account_metadata,[a]:{kind:e.target.value,currency:entry.postings.find(p=>p.account===a)?.commodity||dashboard.meta.ledger.defaultCurrency}}})}><option value="">Choose a kind if needed</option>{['bank','card','cash','wallet','loan','investment','person'].map(k=><option key={k}>{k}</option>)}</select></label>)}{checks.new_accounts.map(a=><label key={a}><input type="checkbox" checked={ticks.includes(a)} onChange={e=>setTicks(e.target.checked?[...ticks,a]:ticks.filter(x=>x!==a))}/>Create {a}</label>)}</section>}
      <footer className="detail-footer"><button type="button" className="button" onClick={()=>void back()} disabled={busy}>Back to Drafts</button><button className="button" disabled={busy}>Check entry</button><button type="button" className="button primary" disabled={busy||!checks?.ok||checks.new_accounts.some(a=>!ticks.includes(a))} onClick={()=>void add()}>Save and add</button></footer>
    </fieldset></form>}
    {!form&&<button className="button" onClick={()=>void onDone()}>Back to Drafts</button>}
  </div></article>;
}
