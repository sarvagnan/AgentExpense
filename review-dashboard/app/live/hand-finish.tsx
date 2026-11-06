import {useEffect,useRef,useState} from 'react';
import {BalanceNotice,balanceSource,type BalanceRefusal} from './balance-refusal';
import {formatCalendarDate} from '../date-time';
import {ApiError} from './api';
import {dashboard} from '../data';
import {Icon,plural} from '../ui';
import {accountFieldLabel} from '../account-labels';
import {api,apiUrl,fileUrl,upload,type Draft,type Proposal,type HandChecks,type CorrectionResult} from './api';

const NEW='\u0000new';
/** Every account the ledger knows: declared metadata and every account an entry uses. */
function knownAccounts(){const set=new Set(Object.keys(dashboard.meta.accounts));for(const entry of dashboard.transactions)for(const posting of entry.postings)set.add(posting.account);return set;}
const accountGroups=(known:Set<string>)=>{const meta=dashboard.meta.accounts,all=[...known].sort((a,b)=>accountFieldLabel(a,meta).localeCompare(accountFieldLabel(b,meta)));
  return [['Accounts',all.filter(a=>meta[a]?.kind)],['Categories',all.filter(a=>!meta[a]?.kind&&a.startsWith('Expenses:'))],['Income',all.filter(a=>!meta[a]?.kind&&a.startsWith('Income:'))],['Other',all.filter(a=>!meta[a]?.kind&&!/^(Expenses|Income):/.test(a))]] as const;};
/** A posting's account: chosen by the name the rest of the app shows; the value stays the ledger path.
 * An account the ledger doesn't have yet is typed as an exact path. */
function AccountField({value,readOnly,onChange}:{value:string;readOnly:boolean;onChange:(account:string)=>void}){
  const known=useRef<Set<string>>(null as unknown as Set<string>);known.current??=knownAccounts();
  const [typing,setTyping]=useState(()=>!!value&&!known.current.has(value));
  const meta=dashboard.meta.accounts;
  if(readOnly)return <label>Account<input readOnly className="field" value={value&&known.current.has(value)?accountFieldLabel(value,meta):value} title={value}/></label>;
  if(typing)return <label>Account<span className="account-path-field"><input className="field" value={value} placeholder="Expenses:Category:Name" spellCheck={false} autoComplete="off" onChange={e=>onChange(e.target.value)} required/><button type="button" className="button plain" onClick={()=>{setTyping(false);if(!known.current.has(value))onChange('');}}>Choose from list</button></span><small className="live-hint">A new account: its exact ledger path.</small></label>;
  return <label>Account<select className="field" value={value} title={value} onChange={e=>{if(e.target.value===NEW){setTyping(true);onChange('');}else onChange(e.target.value);}} required>
    <option value="">Choose an account</option>
    {accountGroups(known.current).filter(([,list])=>list.length).map(([name,list])=><optgroup key={name} label={name}>{list.map(a=><option key={a} value={a}>{accountFieldLabel(a,meta)}{meta[a]?.kind&&meta[a]?.currency?` · ${meta[a].currency}`:''}</option>)}</optgroup>)}
    <option value={NEW}>New account…</option>
  </select></label>;
}

export default function HandFinish({draft,onDone,backLabel='Back to Queue',onCorrected}:{draft:Draft;onDone:()=>Promise<void>;backLabel?:string;onCorrected?:(result:CorrectionResult)=>void}) {
  const correction=draft.correction;
  const locks=correction?.balance_locks||[];
  const minimum=locks.map(l=>l.start).filter((d):d is string=>!!d).sort().at(-1);
  const maximum=locks.map(l=>l.end).sort()[0];
  const [refusal,setRefusal]=useState<BalanceRefusal|undefined>(undefined);
  const [sources,setSources]=useState(correction?.sources||[]),[reason,setReason]=useState(correction?.reason||'');
  const original=useRef('');
  const operation=useRef(draft.id);
  const [files,setFiles]=useState(draft.files);
  const picker=useRef<HTMLInputElement>(null);
  const [form,setForm]=useState<Proposal|null>(null),[checks,setChecks]=useState<HandChecks|null>(null),[ticks,setTicks]=useState<string[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const lastActivity=useRef(Date.now());
  const departing=useRef(false),autosave=useRef<ReturnType<typeof setTimeout>|null>(null);
  const token=useRef(''),latest=useRef<Proposal|null>(null),active=useRef(true),saved=useRef(false),chain=useRef<Promise<unknown>>(Promise.resolve());
  const key=`hand-claim-${dashboard.meta.ledger.id}-${draft.id}`;
  const persist=(force=false)=>{if(!token.current||!latest.current||departing.current&&!force)return chain.current;const value=latest.current,claim=token.current;chain.current=chain.current.catch(()=>{}).then(()=>api.handForm(draft.id,claim,value,correction?sources:undefined,correction?reason:undefined));return chain.current;};
  const stopAutosave=()=>{departing.current=true;if(autosave.current)clearTimeout(autosave.current);};
  useEffect(()=>{
    active.current=true;departing.current=false;
    void api.claim(draft.id,sessionStorage.getItem(key)||undefined).then(result=>{
      // Left before the claim arrived: cleanup had no token to release, so give it back now.
      if(!active.current){void api.release(draft.id,result.claim_token,result.form).catch(()=>{});return;}
      token.current=result.claim_token;sessionStorage.setItem(key,token.current);latest.current=result.form;original.current=JSON.stringify(result.form);if(active.current)setForm(result.form);}).catch(e=>{if(active.current)setError(e.message);});
    const leave=()=>{stopAutosave();if(!saved.current&&token.current){void fetch(apiUrl(`drafts/${draft.id}/release`),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({claim_token:token.current,proposal:latest.current}),keepalive:true}).catch(()=>{});}};
    window.addEventListener('pagehide',leave);

    return()=>{active.current=false;stopAutosave();window.removeEventListener('pagehide',leave);if(!saved.current&&token.current)void chain.current.catch(()=>{}).then(()=>api.release(draft.id,token.current,latest.current||undefined)).catch(()=>{});};
  },[draft.id]);
  useEffect(()=>{if(!form||departing.current)return;autosave.current=setTimeout(()=>{void persist().catch(e=>{if(active.current)setError(e.message);});},500);return()=>{if(autosave.current)clearTimeout(autosave.current);};},[form,sources,reason]);
  function activity(){if(Date.now()-lastActivity.current<60000||!token.current||departing.current)return;lastActivity.current=Date.now();void persist().catch(e=>{if(active.current)setError(e.message);});}
  function change(next:Proposal){latest.current=next;setForm(next);setChecks(null);setRefusal(undefined);setTicks([]);setError('');}
  async function check(){setBusy(true);setError('');const value=latest.current;try{if(autosave.current)clearTimeout(autosave.current);const result=await persist() as HandChecks;if(latest.current===value){setChecks(result);setRefusal(result.refusal);setTicks([]);}}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function add(){if(!checks)return;stopAutosave();setBusy(true);setError('');try{await chain.current;if(correction){const result=await api.correct(correction.entry_id,operation.current,{version:correction.version,proposal:{...latest.current!,new_accounts:checks.new_accounts},sources,reason,approved_accounts:ticks,draft_id:draft.id});saved.current=true;sessionStorage.removeItem(key);onCorrected?.(result);}else{await api.handAdd(draft.id,token.current,checks.proposal_hash,ticks);saved.current=true;sessionStorage.removeItem(key);await onDone();}}catch(e){departing.current=false;if(e instanceof ApiError&&e.refusal)setRefusal(e.refusal);else setError((e as Error).message);}finally{setBusy(false);}}
  async function back(){stopAutosave();setBusy(true);try{await persist(true);await api.release(draft.id,token.current,latest.current||undefined);saved.current=true;sessionStorage.removeItem(key);await onDone();}catch(e){departing.current=false;if(e instanceof ApiError&&e.refusal)setRefusal(e.refusal);else setError((e as Error).message);setBusy(false);}}
  const entry=form?.entry;
  return <article className="panel draft-detail hand-finish" aria-label={correction?'Edit entry':'Finish it yourself'}><div className="panel-header"><div><h2>{correction?correction.before.description||'Saved entry':'Finish it yourself'}</h2><p>{correction?'The saved entry keeps its identity and statement membership.':'Your form is kept in Queue. Activity renews this page’s 30-minute claim.'}</p></div></div><div className="panel-body">
    {refusal&&<BalanceNotice refusal={refusal}><button type="button" className="button" disabled={busy} onClick={()=>{setRefusal(undefined);document.getElementById(`hand-date-${draft.id}`)?.focus();}}>Change account or date</button><button type="button" className="button" disabled={busy} onClick={()=>void back()}>Keep in Queue</button><button type="button" className="button destructive" disabled={busy} onClick={()=>{if(busy)return;stopAutosave();setBusy(true);void(async()=>{await chain.current;await api.release(draft.id,token.current,latest.current||undefined);saved.current=true;await api.discard(draft.id);await onDone();})().catch(e=>{setError(e.message);setBusy(false);});}}>Discard</button></BalanceNotice>}
    {error&&<p role="alert" className="live-error">{error}</p>}
    {!form||!entry?<p role="status">{error?'The form could not be opened.':'Opening the form…'}</p>:<form onPointerDown={activity} onKeyDown={activity} onSubmit={e=>{e.preventDefault();void check();}}><fieldset disabled={busy} className="hand-fields">
      <label>Date<input aria-label="Date" aria-describedby={maximum?`hand-window-${draft.id}`:undefined} id={`hand-date-${draft.id}`} type="date" min={minimum} max={maximum} className="field" value={entry.date} onChange={e=>change({...form,entry:{...entry,date:e.target.value}})} required/>{maximum&&<small id={`hand-window-${draft.id}`} className="live-hint">{minimum?`${formatCalendarDate(minimum)} – ${formatCalendarDate(maximum)}`:`On or before ${formatCalendarDate(maximum)}`} · same-day order must stay before the closing balance</small>}</label>
      <label>Description<input className="field" value={entry.description} onChange={e=>change({...form,entry:{...entry,description:e.target.value}})} required maxLength={1000}/></label>
      {correction?.locked&&<p className="live-notice">This entry belongs to a filed statement. Amounts, currencies, costs, posting count and paying accounts are locked. Date, description, category and notes can be corrected.</p>}
      {locks.filter((l,i)=>locks.findIndex(x=>x.account===l.account)===i).map(l=><p className="live-notice" key={l.account}>Amount and paying account are locked by {balanceSource(l.source)}</p>)}
      <h3>Postings</h3><p className="live-hint">Positive amounts receive money; negative amounts pay. A total cost balances a foreign-currency posting.</p>
      {entry.postings.map((p,i)=><fieldset key={i} className="hand-posting" data-balance-locked={locks.some(l=>l.posting===i)}><legend className="sr-only">Posting {i+1}</legend><p className="hand-posting-title" aria-hidden="true">Posting {i+1}</p>
        <AccountField value={p.account} readOnly={!!correction?.locked&&!!dashboard.meta.accounts[p.account]?.kind||locks.some(l=>l.posting===i)} onChange={account=>change({...form,entry:{...entry,postings:entry.postings.map((row,n)=>n===i?{...row,account}:row)}})}/>
        {(['amount','commodity'] as const).map(key=><label key={key} className={`hand-${key}`}>{key==='commodity'?'Currency':'Amount'}<input readOnly={!!correction?.locked||locks.some(l=>l.posting===i)} className="field" inputMode={key==='amount'?'decimal':undefined} value={p[key]} onChange={e=>{const postings=entry.postings.map((row,n)=>n===i?{...row,[key]:e.target.value}:row);change({...form,entry:{...entry,postings}});}} required/></label>)}
        <button type="button" className="icon-button hand-remove" aria-label={`Remove posting ${i+1}`} title="Remove posting" disabled={!!correction?.locked||locks.length>0||entry.postings.length<=2} onClick={()=>change({...form,entry:{...entry,postings:entry.postings.filter((_,n)=>n!==i)}})}><Icon name="close"/></button>
        <label className="hand-cost-toggle"><input type="checkbox" disabled={!!correction?.locked||locks.some(l=>l.posting===i)} checked={!!p.cost} onChange={e=>change({...form,entry:{...entry,postings:entry.postings.map((row,n)=>n===i?{...row,cost:e.target.checked?{amount:'',commodity:p.commodity}:null}:row)}})}/>Total cost</label>
        {p.cost&&(['amount','commodity'] as const).map(key=><label key={key} className={`hand-cost-${key}`}>Cost {key==='commodity'?'currency':'amount'}<input readOnly={!!correction?.locked||locks.some(l=>l.posting===i)} className="field" value={p.cost![key]} onChange={e=>change({...form,entry:{...entry,postings:entry.postings.map((row,n)=>n===i?{...row,cost:{...row.cost!,[key]:e.target.value}}:row)}})} required/></label>)}
      </fieldset>)}
      <button type="button" className="button plain hand-add-posting" disabled={!!correction?.locked||locks.length>0} onClick={()=>change({...form,entry:{...entry,postings:[...entry.postings,{account:'',amount:'',commodity:entry.postings[0]?.commodity||dashboard.meta.ledger.defaultCurrency,cost:null}]}})}><Icon name="plus"/>Add posting</button>
      <label>Notes<textarea aria-label="Notes" className="field" value={entry.notes.join('\n')} onChange={e=>change({...form,entry:{...entry,notes:e.target.value.split('\n').filter(Boolean)}})}/></label>
      {correction&&<><label>Reason (optional)<textarea className="field" value={reason} onChange={e=>setReason(e.target.value)}/></label>{correction.sources.length>0&&<section aria-label="Saved sources"><h3>Sources</h3>{correction.sources.map(source=><label key={source}><input type="checkbox" checked={sources.includes(source)} onChange={e=>{setSources(e.target.checked?[...sources,source]:sources.filter(s=>s!==source));setChecks(null);}}/>{source}</label>)}</section>}</>}
      <div className="hand-attachments"><span className="field-label" id={`attachments-${draft.id}`}>Attachments</span><div className="attach-row"><button type="button" className="button" disabled={busy} aria-describedby={`attachments-${draft.id}`} onClick={()=>picker.current?.click()}><Icon name="import"/>Attach files</button><span className="live-hint">{form.attachment_names.length?plural(form.attachment_names.length,'attachment'):'Photos or PDFs'}</span></div><input ref={picker} aria-label="Attachments" className="sr-only" tabIndex={-1} type="file" multiple accept="image/jpeg,image/png,image/webp,application/pdf" disabled={busy} onChange={e=>{const files=Array.from(e.target.files||[]);setBusy(true);void(async()=>{let result=draft;for(const file of files)result=await upload(draft.id,file,()=>{},undefined,crypto.randomUUID().replaceAll('-',''),token.current);setFiles(result.files);change({...latest.current!,attachment_names:result.files.map(f=>f.original_name.replace(/\.[^.]+$/,''))});})().catch(e=>setError(e.message)).finally(()=>setBusy(false));e.target.value='';}}/>
      {!!files.length&&<ul className="filed-evidence">{files.map((file,i)=><li key={file.id}><a href={fileUrl(draft.id,file.id)} target="_blank" rel="noopener">{file.original_name}</a><button className="button plain" type="button" disabled={busy} onClick={()=>{setBusy(true);void api.remove(draft.id,file.id,token.current).then(result=>{setFiles(result.files);change({...latest.current!,attachment_names:latest.current!.attachment_names.filter((_,n)=>n!==i)});}).catch(e=>setError(e.message)).finally(()=>setBusy(false));}} aria-label={`Remove ${file.original_name}`}>Remove</button></li>)}</ul>}</div>
      <label>New accounts<textarea aria-label="New accounts" aria-describedby={`new-accounts-${draft.id}`} className="field" rows={2} value={form.new_accounts.join('\n')} onChange={e=>change({...form,new_accounts:e.target.value.split('\n').filter(Boolean)})}/><small id={`new-accounts-${draft.id}`} className="live-hint">Only for an account the ledger doesn’t have yet: one exact account per line.</small></label>
      {correction&&!checks&&JSON.stringify(form)===original.current&&<p role="status">No changes yet</p>}
      {checks&&<section aria-label="Entry checks"><h3>{checks.unchanged?'No changes yet':checks.ok?'Checks passed':'Please check the form'}</h3>{checks.checks.map((c,i)=><p key={i}>{c}</p>)}{checks.new_accounts.filter(a=>!['Expenses','Income','Equity'].includes(a.split(':')[0])).map(a=><label key={a}>Kind for {a}<select className="field" value={form.account_metadata?.[a]?.kind||''} onChange={e=>change({...form,account_metadata:{...form.account_metadata,[a]:{kind:e.target.value,currency:entry.postings.find(p=>p.account===a)?.commodity||dashboard.meta.ledger.defaultCurrency}}})}><option value="">Choose a kind if needed</option>{['bank','card','cash','wallet','loan','investment','person'].map(k=><option key={k}>{k}</option>)}</select></label>)}{checks.new_accounts.map(a=><label key={a}><input type="checkbox" checked={ticks.includes(a)} onChange={e=>setTicks(e.target.checked?[...ticks,a]:ticks.filter(x=>x!==a))}/>Create {a}</label>)}</section>}
      <footer className="detail-footer"><button type="button" className="button" onClick={()=>void back()} disabled={busy}>{backLabel}</button><span className="footer-actions"><button className="button" disabled={busy}>Check entry</button><button type="button" className="button primary" disabled={busy||!checks?.ok||checks.new_accounts.some(a=>!ticks.includes(a))} onClick={()=>void add()}>{correction?'Save correction':'Save and add'}</button></span></footer>
    </fieldset></form>}
    {!form&&<button className="button" onClick={()=>void onDone()}>{backLabel}</button>}
  </div></article>;
}
