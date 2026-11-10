import {discardEdit} from './edit-recovery';
import {subscribeWrites,writeSnapshot,dismissWrite} from './direct-writes';
import {useRetainedState,clearRetained,retainedSnapshot} from './retained-state';
import {ActionFeedback} from './pending-actions';
import {useEffect,useRef,useState} from 'react';
import {BalanceNotice,balanceSource,type BalanceRefusal} from './balance-refusal';
import {formatCalendarDate} from '../date-time';
import {ApiError} from './api';
import {dashboard} from '../data';
import {Dialog,Icon,plural,copyAndAnnounce} from '../ui';
import {accountFieldLabel} from '../account-labels';
import {ExtensionFields} from '../extensions';
import {api,apiUrl,fileUrl,upload,type Draft,type Proposal,type HandChecks,type CorrectionResult} from './api';

const liveEditors=new Map<string,string>();
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

/** Edit entry opened from Ledger check › Ask the agent to fix it: what hledger found and what the agent proposes. */
export function LedgerFixNotice({fix,busy,onDiscard,onEdit,summary=false,actions=true}:{fix:NonNullable<Draft['ledger_fix']>;busy:boolean;onDiscard:()=>void;onEdit?:()=>void;summary?:boolean;actions?:boolean}){
  // The check passed since (the entry was fixed another way): the proposal is no longer needed.
  if(fix.state==='resolved')return <div className="live-notice live-success ledger-fix-notice" role="status"><Icon name="check"/><div><p><strong>The ledger check now passes</strong></p><p>This proposal for “{fix.title}” is no longer needed. Discard it, or save it only if you still want the change.</p>{actions&&<div className="notice-actions"><button type="button" className="button" disabled={busy} onClick={onDiscard}>Discard proposal</button>{onEdit&&<button type="button" className="button" disabled={busy} onClick={onEdit}>Edit entry</button>}</div>}</div></div>;
  return <div className="live-notice ledger-fix-notice" role="status"><Icon name="alert"/><div><p><strong>{fix.state==='ready'?'The agent’s proposed fix for the ledger check':'Fix the ledger check'}</strong></p><p>{fix.title}. {fix.summary}</p>
    {fix.state==='ready'?<p>{summary?'The agent proposed a change. Continue editing to check it; nothing changes until you save.':'The form below holds the agent’s proposal. Check it, then Save; nothing changes until you save.'}</p>:<p>{fix.question?`The agent asks: ${fix.question}`:fix.message||'Correct the entry yourself, then Save.'}</p>}
    {fix.state==='ready'&&fix.message&&<p>{fix.message}</p>}{fix.state==='ready'&&!!fix.doubts?.length&&<p className="live-hint">Doubts: {fix.doubts.join('; ')}</p>}{actions&&<div className="notice-actions"><button type="button" className="button" disabled={busy} onClick={onDiscard}>Discard proposal</button>{onEdit&&<button type="button" className="button" disabled={busy} onClick={onEdit}>Edit entry</button>}</div>}</div></div>;
}

export default function HandFinish({draft,onDone,backLabel='Back to Queue',onCorrected}:{draft:Draft;onDone:()=>Promise<void>;backLabel?:string;onCorrected?:(result:CorrectionResult)=>void}) {
  const correction=draft.correction;
  const locks=correction?.balance_locks||[];
  const minimum=locks.map(l=>l.start).filter((d):d is string=>!!d).sort().at(-1);
  const maximum=locks.map(l=>l.end).sort()[0];
  const [confirmCancel,setConfirmCancel]=useState(false);
  const [refusal,setRefusal]=useState<BalanceRefusal|undefined>(undefined);
  const recovery=`hand-${draft.id}`;
  const [sources,setSources]=useRetainedState(recovery+'-sources',correction?.sources||[]),[reason,setReason]=useRetainedState(recovery+'-reason',correction?.reason||'');
  const [baseVersion,setBaseVersion]=useRetainedState<string|null>(recovery+'-version',correction?.version||null);
  const staleRecovery=!!correction&&!!baseVersion&&baseVersion!==correction.version;
  const remoteForm=useRef<Proposal|null>(null);
  const latestExtras=useRef('');latestExtras.current=JSON.stringify({sources,reason});
  const original=useRef(''),baseline=useRef({sources:correction?.before.evidence||[],reason:'',files:[] as Draft['files']});
  const operation=useRef(draft.id),uncertainCorrection=useRef<string|null>(null);
  const writePath=correction?`entries/${correction.entry_id}/corrections/${operation.current}`:`drafts/${draft.id}/hand-add`;
  const pendingSave=()=>writeSnapshot().some(item=>item.path===writePath&&['queued','saving','waiting'].includes(item.state));
  const unknownSave=()=>writeSnapshot().some(item=>item.path===writePath&&['failed','interrupted'].includes(item.state)&&item.outcomeUnknown!==false);
  const [directBusy,setDirectBusy]=useState(pendingSave);
  const [unknownWrite,setUnknownWrite]=useState(unknownSave);
  useEffect(()=>subscribeWrites(()=>{setDirectBusy(pendingSave());setUnknownWrite(unknownSave());}),[writePath]);
  const [files,setFiles]=useState(draft.files);
  const picker=useRef<HTMLInputElement>(null);
  const [form,setForm,initializeForm]=useRetainedState<Proposal|null>(recovery+'-form',null),[checks,setChecks]=useState<HandChecks|null>(null),[ticks,setTicks]=useState<string[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const lastActivity=useRef(Date.now());
  const departing=useRef(false),cancelling=useRef(false),discarding=useRef(false),autosave=useRef<ReturnType<typeof setTimeout>|null>(null);
  const generation=useRef('');
  const token=useRef(''),latest=useRef<Proposal|null>(form),active=useRef(true),saved=useRef(false),submitting=useRef(false),chain=useRef<Promise<unknown>>(Promise.resolve());
  const key=`hand-claim-${dashboard.meta.ledger.id}-${draft.id}`;
  const ownsGeneration=()=>liveEditors.get(key)===generation.current&&[generation.current,token.current].includes(sessionStorage.getItem(key)||'');
  const clearClaim=()=>{if(ownsGeneration())sessionStorage.removeItem(key);};
  const persist=(force=false)=>{if(!ownsGeneration()||!token.current||!latest.current||departing.current&&!force)return chain.current;const value=latest.current,claim=token.current;chain.current=chain.current.catch(()=>{}).then(()=>{if(ownsGeneration())return api.handForm(draft.id,claim,value,correction?sources:undefined,correction?reason:undefined);});return chain.current;};
  const stopAutosave=()=>{departing.current=true;if(autosave.current)clearTimeout(autosave.current);};
  useEffect(()=>{
    active.current=true;departing.current=false;
    // Keep the claim identity before sending: reload can recover a lost reply.
    const previousToken=sessionStorage.getItem(key)||undefined;
    const claimToken=crypto.randomUUID().replaceAll('-','')+crypto.randomUUID().replaceAll('-','');
    generation.current=claimToken;liveEditors.set(key,claimToken);
    sessionStorage.setItem(key,claimToken);
    void api.claim(draft.id,claimToken,previousToken).then(result=>{
      // Left before the claim arrived: cleanup had no token to release, so give it back now.
      if(!ownsGeneration()){
        // A reordered request may have won after its successor was refused.
        // Releasing only this token (with no form) cannot affect a successor.
        void api.release(draft.id,result.claim_token).catch(()=>{});return;
      }
      if(!active.current){token.current=result.claim_token;void api.release(draft.id,result.claim_token).then(()=>{if(ownsGeneration())clearClaim();}).catch(()=>{});return;}
      remoteForm.current=result.form;token.current=result.claim_token;sessionStorage.setItem(key,token.current);const value=latest.current||result.form;latest.current=value;original.current=JSON.stringify(result.original_form||result.form);if(result.baseline)baseline.current=result.baseline;if(active.current)initializeForm({...value});}).catch(e=>{if(active.current&&ownsGeneration())setError(e.message);});
    const leave=()=>{stopAutosave();if(!saved.current&&!submitting.current&&!cancelling.current&&!discarding.current&&!pendingSave()&&token.current&&ownsGeneration()){void fetch(apiUrl(`drafts/${draft.id}/release`),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({claim_token:token.current,proposal:latest.current}),keepalive:true}).catch(()=>{});}};
    window.addEventListener('pagehide',leave);

    return()=>{active.current=false;stopAutosave();window.removeEventListener('pagehide',leave);if(!saved.current&&!submitting.current&&!cancelling.current&&!discarding.current&&!pendingSave()&&token.current)void chain.current.catch(()=>{}).then(()=>{if(ownsGeneration())return api.release(draft.id,token.current,latest.current||undefined);}).catch(()=>{});};
  },[draft.id]);
  useEffect(()=>{if(!form||departing.current)return;autosave.current=setTimeout(()=>{void persist().catch(e=>{if(active.current)setError(e.message);});},500);return()=>{if(autosave.current)clearTimeout(autosave.current);};},[form,sources,reason]);
  function activity(){if(Date.now()-lastActivity.current<60000||!token.current||departing.current)return;lastActivity.current=Date.now();void persist().catch(e=>{if(active.current)setError(e.message);});}
  function change(next:Proposal,version=baseVersion){setBaseVersion(version);latest.current=next;setForm(next);setChecks(null);setRefusal(undefined);setTicks([]);setError('');}
  async function check(){if(staleRecovery){setError('The saved entry changed. Your kept edits are still here; compare them before starting again.');return;}setBusy(true);setError('');const value=latest.current;try{if(autosave.current)clearTimeout(autosave.current);const result=await persist() as HandChecks;if(latest.current===value){setChecks(result);setRefusal(result.refusal);setTicks([]);}}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function add(){if(!checks||busy||staleRecovery)return;if(correction&&uncertainCorrection.current&&uncertainCorrection.current!==JSON.stringify({form:latest.current,sources,reason})){setError('The earlier correction may already be saved. Open the saved entry and compare it before saving these newer edits.');return;}const submitted=latest.current,submittedExtras=latestExtras.current;submitting.current=true;stopAutosave();setBusy(true);setError('');try{await chain.current;if(correction){uncertainCorrection.current=JSON.stringify({form:submitted,sources,reason});const result=await api.correct(correction.entry_id,operation.current,{version:baseVersion||correction.version,proposal:{...submitted!,'new-accounts':checks.new_accounts},sources,reason,approved_accounts:ticks,draft_id:draft.id},{clearRetained:retainedSnapshot(['form','sources','reason','version'].map(field=>recovery+'-'+field))});uncertainCorrection.current=null;const acknowledged=saved.current;saved.current=true;clearClaim();if(active.current&&latest.current===submitted&&latestExtras.current===submittedExtras)clearRetained(['form','sources','reason','version'].map(field=>recovery+'-'+field));if(active.current&&!acknowledged)onCorrected?.(result);}else{await api.handAdd(draft.id,token.current,checks.proposal_hash,ticks,{clearRetained:retainedSnapshot(['form','sources','reason','version'].map(field=>recovery+'-'+field))});saved.current=true;clearClaim();if(active.current&&latest.current===submitted&&latestExtras.current===submittedExtras)clearRetained(['form','sources','reason','version'].map(field=>recovery+'-'+field));}}catch(e){departing.current=false;const reason=(e as Error).message;if(correction&&e instanceof ApiError&&e.status>=400&&e.status<500)uncertainCorrection.current=null;setError(reason);if(e instanceof ApiError&&e.refusal)setRefusal(e.refusal);else setError(reason);if(!correction&&(e as {terminal?:boolean}).terminal){try{const reclaimed=await api.claim(draft.id,token.current);token.current=reclaimed.claim_token;sessionStorage.setItem(key,token.current);const value=latest.current;const checked=await api.handForm(draft.id,token.current,value!);if(active.current&&value===latest.current){setChecks(checked);setRefusal(checked.refusal);}}catch{if(active.current){setChecks(value=>value?{...value,ok:false}:null);setError(reason+' Open Queue and reopen the form to continue.');}}}}finally{submitting.current=false;setBusy(false);}}
  useEffect(()=>{const complete=(event:Event)=>{const detail=(event as CustomEvent<{path:string;result:CorrectionResult}>).detail;if(detail?.path!==(correction?`entries/${correction.entry_id}/corrections/${operation.current}`:`drafts/${draft.id}/hand-add`)||saved.current)return;saved.current=true;clearClaim();setError('');if(correction)onCorrected?.(detail.result);};window.addEventListener('direct-write-saved',complete);return()=>window.removeEventListener('direct-write-saved',complete);},[draft.id]);
  async function back(){clearRetained(['form','sources','reason','version'].map(field=>recovery+'-'+field));if(submitting.current||pendingSave()){await onDone();return;}if(saved.current){await onDone();return;}stopAutosave();setBusy(true);try{await persist(true);await api.release(draft.id,token.current,latest.current||undefined);saved.current=true;clearClaim();if(active.current)await onDone();}catch(e){departing.current=false;if(e instanceof ApiError&&e.refusal)setRefusal(e.refusal);else setError((e as Error).message);setBusy(false);}}
  async function cancel(){
    if(cancelling.current||busy||directBusy||pendingSave()||unknownWrite||uncertainCorrection.current||!token.current)return;
    const names=['form','sources','reason','version'].map(field=>recovery+'-'+field),retained=retainedSnapshot(names);
    cancelling.current=true;stopAutosave();setBusy(true);setError('');
    for(const item of writeSnapshot())if(item.path===writePath)dismissWrite(item.id);
    try{
      await chain.current.catch(()=>{});
      try{await api.cancelEdit(draft.id,token.current);}catch(failure){
        // A lost acknowledgement is resolved by an idempotent Cancel replay.
        if(failure instanceof ApiError&&failure.status>=400&&failure.status<500)throw failure;
        await api.cancelEdit(draft.id,token.current);
      }
      saved.current=true;
      if(ownsGeneration()&&Object.entries(retained).every(([name,value])=>{try{return sessionStorage.getItem(name)===value;}catch{return false;}}))clearRetained(names);
      clearClaim();
      if(active.current){setConfirmCancel(false);await onDone();}
    }catch(e){cancelling.current=false;departing.current=false;if(active.current){setError((e as Error).message);setBusy(false);}}
  }
  async function discard(){
    if(busy||directBusy||unknownWrite||uncertainCorrection.current)return;
    discarding.current=true;stopAutosave();setBusy(true);setError('');
    try{await chain.current;await discardEdit(draft,{current:token.current&&latest.current?{token:token.current,form:latest.current,sources,reason}:undefined,released:()=>{saved.current=true;}});if(active.current)await onDone();}
    catch(e){discarding.current=false;departing.current=false;if(active.current){setError((e as Error).message);setBusy(false);}}
  }
  const cancelChanged=JSON.stringify(form)!==original.current||reason!==baseline.current.reason||JSON.stringify(sources)!==JSON.stringify(baseline.current.sources)||JSON.stringify(files)!==JSON.stringify(baseline.current.files);
  const changedUncertain=!!correction&&!!uncertainCorrection.current&&uncertainCorrection.current!==JSON.stringify({form,sources,reason});
  const entry=form?.entry;
  return <article className="panel draft-detail hand-finish" aria-label={correction?'Edit entry':'Finish it yourself'}><div className="panel-header">{correction&&<button type="button" className="button" onClick={()=>void back()} disabled={busy&&!submitting.current}>{backLabel}</button>}<div><h2>{correction?correction.before.description||'Saved entry':'Finish it yourself'}</h2><p>{correction?'The saved entry keeps its identity and statement membership.':'Your form is kept in Queue. Activity renews this page’s 30-minute claim.'}</p></div></div><div className="panel-body">

    {correction&&(!!uncertainCorrection.current||unknownWrite)&&<p role="status" id="cancel-save-unknown">Save’s result is unknown. Check the saved entry or retry Save before cancelling.</p>}
    {changedUncertain&&<div className="live-notice" role="status"><p>The earlier correction may already be saved. Open the saved entry and compare it before saving these newer edits. <a href={`${import.meta.env.BASE_URL}explorer/?entry=${correction!.entry_id}`}>Open saved entry</a></p></div>}
    {staleRecovery&&<div className="live-notice" role="status"><div><strong>The saved entry changed</strong><p>Your kept edits are still below. Copy them before discarding them and starting from the current saved entry.</p><div className="notice-actions"><button type="button" className="button" onClick={()=>{const entry=latest.current?.entry;if(entry)void copyAndAnnounce([entry.date,entry.description,...entry.postings.map(posting=>`${posting.account}: ${posting.amount} ${posting.commodity}`),...entry.notes].join('\n'),'kept entry');}}>Copy kept entry</button><button type="button" className="button" disabled={!remoteForm.current} onClick={()=>{setSources(correction!.sources);setReason('');change(remoteForm.current!,correction!.version);}}>Discard kept edits</button></div></div></div>}
    {draft.ledger_fix&&<LedgerFixNotice fix={draft.ledger_fix} busy={busy||directBusy||unknownWrite||!!uncertainCorrection.current||!token.current} onDiscard={()=>void discard()}/>}

    <div className="action-error-slot">{error&&<p role="alert" className="live-error">{error}</p>}</div>
    {!form||!entry?<p role="status">{error?'The form could not be opened.':'Opening the form…'}</p>:<form onPointerDown={activity} onKeyDown={activity} onSubmit={e=>{e.preventDefault();void check();}}><fieldset className="hand-fields" disabled={cancelling.current||discarding.current}>
      <label>Date<input aria-label="Date" aria-describedby={maximum?`hand-window-${draft.id}`:undefined} id={`hand-date-${draft.id}`} type="date" min={minimum} max={maximum} className="field" value={entry.date} onChange={e=>change({...form,entry:{...entry,date:e.target.value}})} required/>{maximum&&<small id={`hand-window-${draft.id}`} className="live-hint">{minimum?`${formatCalendarDate(minimum)} – ${formatCalendarDate(maximum)}`:`On or before ${formatCalendarDate(maximum)}`} · same-day order must stay before the closing balance</small>}</label>
      <label>Description<input className="field" value={entry.description} onChange={e=>change({...form,entry:{...entry,description:e.target.value}})} required maxLength={1000}/></label>
      {correction?.locked&&<p className="live-notice">This entry belongs to a filed statement. Amounts, currencies, costs, posting count and paying accounts are locked. Date, description, category and notes can be corrected.</p>}
      {locks.filter((l,i)=>locks.findIndex(x=>x.account===l.account)===i).map(l=><p className="live-notice" key={l.account}>Amount and paying account are locked by {balanceSource(l.source)}</p>)}
      <h3>Postings</h3><p className="live-hint">Positive amounts receive money; negative amounts pay. A total cost balances a foreign-currency posting.</p>
      {entry.postings.map((p,i)=><fieldset key={i} className="hand-posting" data-balance-locked={locks.some(l=>l.posting===i)}><legend className="sr-only">Posting {i+1}</legend><p className="hand-posting-title" aria-hidden="true">Posting {i+1}</p>
        <AccountField value={p.account} readOnly={!!correction?.locked&&!!dashboard.meta.accounts[p.account]?.kind||locks.some(l=>l.posting===i)} onChange={account=>change({...form,entry:{...entry,postings:entry.postings.map((row,n)=>n===i?{...row,account}:row)}})}/>
        {(['amount','commodity'] as const).map(key=><label key={key} className={`hand-${key}`}>{key==='commodity'?'Currency':'Amount'}<input readOnly={!!correction?.locked||locks.some(l=>l.posting===i)} className="field" inputMode={key==='amount'?'decimal':undefined} value={p[key]} onChange={e=>{const postings=entry.postings.map((row,n)=>n===i?{...row,[key]:e.target.value}:row);change({...form,entry:{...entry,postings}});}} required/></label>)}
        <button type="button" className="icon-button hand-remove" aria-label={`Remove posting ${i+1}`} title="Remove posting" disabled={!!correction?.locked||locks.length>0||entry.postings.length<=2} onClick={()=>change({...form,entry:{...entry,postings:entry.postings.filter((_,n)=>n!==i)}})}><Icon name="close"/></button>
        <label className="hand-cost-toggle"><input type="checkbox" disabled={!!correction?.locked||locks.some(l=>l.posting===i)} checked={!!p.cost} onChange={e=>change({...form,entry:{...entry,postings:entry.postings.map((row,n)=>n===i?{...row,cost:e.target.checked?{amount:'',commodity:''}:null}:row)}})}/>Total cost</label>
        {p.cost&&(['amount','commodity'] as const).map(key=><label key={key} className={`hand-cost-${key}`}>Cost {key==='commodity'?'currency':'amount'}<input readOnly={!!correction?.locked||locks.some(l=>l.posting===i)} className="field" value={p.cost![key]} onChange={e=>change({...form,entry:{...entry,postings:entry.postings.map((row,n)=>n===i?{...row,cost:{...row.cost!,[key]:e.target.value}}:row)}})} required/></label>)}
      </fieldset>)}
      <button type="button" className="button plain hand-add-posting" disabled={!!correction?.locked||locks.length>0} onClick={()=>change({...form,entry:{...entry,postings:[...entry.postings,{account:'',amount:'',commodity:entry.postings[0]?.commodity||dashboard.meta.ledger.defaultCurrency,cost:null}]}})}><Icon name="plus"/>Add posting</button>
      {entry.extensions&&<ExtensionFields idPrefix={`hand-${draft.id}`} value={entry.extensions} onChange={extensions=>change({...form,entry:{...entry,extensions}})}/>}
      <label>Notes<textarea aria-label="Notes" className="field" value={entry.notes.join('\n')} onChange={e=>change({...form,entry:{...entry,notes:e.target.value.split('\n').filter(Boolean)}})}/></label>
      {correction&&<><label>Reason (optional)<textarea className="field" value={reason} onChange={e=>{setReason(e.target.value);setBaseVersion(baseVersion);}}/></label>{correction.sources.length>0&&<section aria-label="Saved sources"><h3>Sources</h3>{correction.sources.map(source=><label key={source}><input type="checkbox" checked={sources.includes(source)} onChange={e=>{setSources(e.target.checked?[...sources,source]:sources.filter(s=>s!==source));setBaseVersion(baseVersion);setChecks(null);}}/>{source}</label>)}</section>}</>}
      <div className="hand-attachments"><span className="field-label" id={`attachments-${draft.id}`}>Attachments</span><div className="attach-row"><button type="button" className="button" disabled={busy} aria-describedby={`attachments-${draft.id}`} onClick={()=>picker.current?.click()}><Icon name="import"/>Attach files</button><span className="live-hint">{form['attachment-names'].length?plural(form['attachment-names'].length,'attachment'):'Photos or PDFs'}</span></div><input ref={picker} aria-label="Attachments" className="sr-only" tabIndex={-1} type="file" multiple accept="image/jpeg,image/png,image/webp,application/pdf" disabled={busy} onChange={e=>{const files=Array.from(e.target.files||[]);setBusy(true);void(async()=>{let result=draft;for(const file of files)result=await upload(draft.id,file,()=>{},undefined,crypto.randomUUID().replaceAll('-',''),token.current);setFiles(result.files);change({...latest.current!,'attachment-names':result.files.map(f=>f.original_name.replace(/\.[^.]+$/,''))});})().catch(e=>setError(e.message)).finally(()=>setBusy(false));e.target.value='';}}/>
      {!!files.length&&<ul className="filed-evidence">{files.map((file,i)=><li key={file.id}><a href={fileUrl(draft.id,file.id)} target="_blank" rel="noopener">{file.original_name}</a><button className="button plain" type="button" disabled={busy} onClick={()=>{setBusy(true);void api.remove(draft.id,file.id,token.current).then(result=>{setFiles(result.files);change({...latest.current!,'attachment-names':latest.current!['attachment-names'].filter((_,n)=>n!==i)});}).catch(e=>setError(e.message)).finally(()=>setBusy(false));}} aria-label={`Remove ${file.original_name}`}>Remove</button></li>)}</ul>}</div>
      <label>New accounts<textarea aria-label="New accounts" aria-describedby={`new-accounts-${draft.id}`} className="field" rows={2} value={form['new-accounts'].join('\n')} onChange={e=>change({...form,'new-accounts':e.target.value.split('\n').filter(Boolean)})}/><small id={`new-accounts-${draft.id}`} className="live-hint">Only for an account the ledger doesn’t have yet: one exact account per line.</small></label>
      {correction&&!checks&&JSON.stringify(form)===original.current&&!['ready','resolved'].includes(draft.ledger_fix?.state||'')&&<p role="status">No changes yet</p>}
      {checks&&<section aria-label="Entry checks"><h3>{checks.unchanged?'No changes yet':checks.ok?'Checks passed':'Please check the form'}</h3>{checks.checks.map((c,i)=><p key={i}>{c}</p>)}{checks.new_accounts.filter(a=>!['Expenses','Income','Equity'].includes(a.split(':')[0])).map(a=><label key={a}>Kind for {a}<select className="field" value={form['account-metadata']?.[a]?.kind||''} onChange={e=>change({...form,'account-metadata':{...form['account-metadata'],[a]:{kind:e.target.value,currency:entry.postings.find(p=>p.account===a)?.commodity||dashboard.meta.ledger.defaultCurrency}}})}><option value="">Choose a kind if needed</option>{['bank','card','cash','wallet','loan','investment','person'].map(k=><option key={k}>{k}</option>)}</select></label>)}{checks.new_accounts.map(a=><label key={a}><input type="checkbox" checked={ticks.includes(a)} onChange={e=>setTicks(e.target.checked?[...ticks,a]:ticks.filter(x=>x!==a))}/>Create {a}</label>)}</section>}
      <footer className="detail-footer"><button type="button" className="button" onClick={()=>correction&&!saved.current?(cancelChanged?setConfirmCancel(true):void cancel()):void back()} disabled={correction?busy||directBusy||unknownWrite||!!uncertainCorrection.current||!token.current:busy&&!submitting.current}>{correction?(cancelling.current?'Cancelling…':'Cancel'):backLabel}</button><span className="footer-actions"><button className="button" disabled={busy}>Check entry</button><button type="button" className="button primary" disabled={busy||directBusy||saved.current||staleRecovery||changedUncertain||!checks?.ok||checks.new_accounts.some(a=>!ticks.includes(a))} onClick={()=>void add()}>{cancelling.current?'Save':busy||directBusy?'Saving…':error&&!changedUncertain?'Retry':correction?'Save':'Save and add'}</button></span></footer>
    </fieldset></form>}
    {refusal&&<BalanceNotice refusal={refusal}><button type="button" className="button" disabled={busy} onClick={()=>{setRefusal(undefined);document.getElementById(`hand-date-${draft.id}`)?.focus();}}>Change account or date</button><button type="button" className="button" disabled={busy} onClick={()=>void back()}>Keep in Queue</button><button type="button" className="button destructive" disabled={busy||directBusy||unknownWrite} onClick={()=>void discard()}>Discard</button></BalanceNotice>}
 {confirmCancel&&<Dialog title="Discard changes?" labelId="cancel-edit-title" role="alertdialog" onClose={()=>{if(!cancelling.current)setConfirmCancel(false);}} initialFocus=".keep-editing"><div className="alert-body"><p>Your unsaved changes will be discarded.</p></div><div className="alert-actions hand-cancel-actions"><button type="button" className="button keep-editing" disabled={busy} onClick={()=>setConfirmCancel(false)}>Keep editing</button><button type="button" className="button destructive" disabled={busy} onClick={()=>void cancel()}>Discard changes</button></div></Dialog>}
 <ActionFeedback retryAllowed={!changedUncertain&&!busy&&!!checks?.ok} scope="entry" path={correction?`entries/${correction.entry_id}/corrections/${operation.current}`:`drafts/${draft.id}/hand-add`}/>
    {!form&&<button className="button" onClick={()=>void onDone()}>{backLabel}</button>}
  </div></article>;
}
