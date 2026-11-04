import {useEffect,useRef,useState} from 'react';
import {Icon,PageHeader,PanelHeader,Dialog,copyAndAnnounce,money} from '../ui';
import {api,fileUrl,type Attachment} from './api';
import {dashboard} from '../data';
import {formatTimestamp} from '../date-time';
import type {LiveDrafts} from './use-drafts';
import type {Composer} from './composer';
import {AddUnderstood} from './add-understood';
import {AddResults,useAddResults,draftStatus,statementStatus} from './add-results';
import {BalanceNotice} from './balance-refusal';
import type {Draft} from './api';
import type {StatementSession} from './statements-api';
import {statementWords} from './add-intake';
export function FilePreview({ id, file, completed = false, completedUrl }: {id: string; file: Attachment; completed?: boolean; completedUrl?: string | null}) {
  const document = !file.media_type.startsWith('image/');
  const kind = document ? file.original_name.split('.').at(-1)?.toUpperCase() || 'File' : 'Image';
  return <>{!completed || completedUrl ? <a className="attachment-preview" href={completedUrl || fileUrl(id,file.id)} target="_blank" rel="noopener" aria-label={`Open ${file.original_name}`}>{document ? <span className="pdf-page"><Icon name="source"/>{kind}</span> : <img src={completedUrl || fileUrl(id,file.id)} alt=""/>}</a> : <span className="pdf-page"><Icon name="source"/>{document ? 'PDF' : 'Image'}</span>}<span className="attachment-name">{file.original_name}<small>{Math.ceil(file.size / 1024)} KB{completed ? ' · upload filed or removed after completion' : ' · uploaded'}</small></span></>;
}
export default function Add({live,composer:c}:{live:LiveDrafts;composer:Composer}){
 const {form,draft,pending,patternError,error,saving,recovering,update,pick}=c;
 const currency=dashboard.meta.ledger.defaultCurrency;
 const account=Object.values(dashboard.meta.accounts).find(m=>m.kind==='card'&&m.currency===currency);
 const bank=Object.values(dashboard.meta.accounts).find(m=>m.kind==='bank');
 const examples=[`Copper Finch Café, ${money(18.60,currency)}, ${account?.label||'cash'}`,`Bus to work, ${money(2.75,currency)}, cash`,`${bank?.label||'Bank'} statement for ${new Intl.DateTimeFormat(dashboard.meta.ledger.locale||navigator.language,{month:'long'}).format(new Date())}`];
 const [removeErrors,setRemoveErrors]=useState<Record<string,string>>({}),[dragging,setDragging]=useState(false),[showExamples,setShowExamples]=useState(false);
 const input=useRef<HTMLTextAreaElement>(null),picker=useRef<HTMLInputElement>(null);
 const rows=useAddResults(live),current={current:form};
 const busyFiles=pending.some(p=>p.busy),files=c.interpretation.value?.files||[];
 const statement=(c.interpretation.value?.words||statementWords(form.text)).kind==='statement';
 const builtin=files.length>0&&files.every(f=>f.kind==='statement'&&!!f.reader)&&!(!statement&&form.text.trim());
 const blockedAgent=live.agent.state==='signed out'||live.agent.state==='paused'||!!live.agent.agent_pause;
 const blockedFiles=files.some(f=>f.kind==='ambiguous'||f.locked||f.mismatch)||statement&&!draft?.files.length;
 const refused=live.drafts.find(d=>d.state==='failed'&&d.refusal);
 const [refusalError,setRefusalError]=useState('');
 async function resolveRefusal(action:'edit'|'keep'|'discard'){if(!refused)return;try{if(action==='discard'){await live.discard(refused.id);c.forgetDiscarded(refused.id);}else{await api.resolveRefusal(refused.id,'edit');if(action==='edit')await c.restore(refused.id);}await live.refresh();}catch(e){setRefusalError((e as Error).message);}}
 const latest=rows[0],barResult=latest?(latest.type==='draft'?draftStatus(latest.value as Draft):statementStatus(latest.value as StatementSession)):'';
 const empty=!form.text&&!draft?.files.length&&!pending.length;
 useEffect(()=>{void c.restore(new URLSearchParams(window.location.search).get('draft'));},[]);
 useEffect(()=>{if(c.lastSavedId&&!saving)input.current?.focus({preventScroll:true});},[c.lastSavedId,saving]);
 async function remove(file:Attachment){setRemoveErrors(xs=>({...xs,[file.id]:''}));try{await c.remove(file);}catch(e){setRemoveErrors(xs=>({...xs,[file.id]:`Could not remove this file. ${(e as Error).message}`}));}}
 async function save(){if(await c.save())input.current?.focus({preventScroll:true});}
 function chooseExample(example:string){setShowExamples(false);update({...form,text:example,edits:{}});requestAnimationFrame(()=>input.current?.focus({preventScroll:true}));}
 return <section className={`page live-page add-page ${dragging?'is-dragging':''}`} onDragEnter={e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();setDragging(true);}}} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node))setDragging(false);}} onDragOver={e=>{if(e.dataTransfer.types.includes('Files'))e.preventDefault();}} onDrop={e=>{setDragging(false);if(e.dataTransfer.files.length){e.preventDefault();pick(Array.from(e.dataTransfer.files));}}} onPaste={e=>{const files=Array.from(e.clipboardData.files);if(files.length){e.preventDefault();pick(files);}}}>
 <PageHeader title="Add" subtitle="Expenses, receipts and statements. Start with what you have."/>
 {live.online===false&&<aside className="live-notice add-blocking-notice" role="status"><Icon name="alert"/><div><strong>You're offline</strong><p>Your words and files stay here. Reconnect to continue.</p><button className="button" onClick={()=>void live.refresh()}>Check connection</button></div></aside>}
 {live.online!==false&&blockedAgent&&<aside className="agent-notice add-blocking-notice" role="status" data-agent-state={live.agent.agent_pause?'outage':live.agent.state}><Icon name="alert"/><div><strong>{live.agent.state==='signed out'||live.agent.agent_pause?.kind==='auth'?"The agent isn't signed in.":'The agent is paused.'}</strong><p>{live.agent.agent_pause?`Resumes at ${formatTimestamp(live.agent.agent_pause.resumes_at)}.`:live.agent.state==='signed out'?`Run ${live.agent.login_command||'make agent-login'} on the computer running AgentExpense.`:live.agent.reason}</p><p>Statements read by the built-in reader still work.</p></div></aside>}
 <div className="add-layout"><section className="panel composer-panel"><PanelHeader title="What happened?"/><div className="panel-body">
 <label className="sr-only" htmlFor="expense-words">Describe an expense or statement</label><textarea ref={input} id="expense-words" className="expense-words" rows={3} value={form.text} maxLength={100000} placeholder="Type what happened, or attach a file…" onChange={e=>update({...current.current,text:e.target.value})} disabled={saving||recovering} aria-describedby="upload-hint"/>
 <div className="composer-tools"><button type="button" className="button" disabled={live.online!==true||saving||recovering} onClick={()=>{c.retryStorage();picker.current?.click();}}><Icon name="import"/>Upload</button><button className="text-button" type="button" onClick={()=>setShowExamples(true)}>Examples</button><input ref={picker} className="sr-only" tabIndex={-1} aria-label="Upload files" type="file" multiple accept="image/jpeg,image/png,image/webp,.pdf,.csv,.xlsx,.xls" disabled={live.online!==true||saving||recovering} onChange={e=>{pick(Array.from(e.target.files||[]));e.target.value='';}}/><p id="upload-hint" className="live-hint">Photos, PDF, CSV, Excel</p></div>
      {(!!draft?.files.length || !!pending.length) && <ul className="attachments" aria-label="Uploads">{draft?.files.map(f=><li key={f.id}><FilePreview id={draft.id} file={f}/>{removeErrors[f.id] && <p className="live-error" role="alert">{removeErrors[f.id]}</p>}<span className="file-remove" data-tooltip={`Remove ${f.original_name}`}><button title={`Remove ${f.original_name}`} className="icon-button" disabled={saving} onClick={()=>void remove(f)} aria-label={`Remove ${f.original_name}`}><Icon name="close"/></button></span></li>)}{pending.map(p=><li key={p.key} className="pending-upload"><Icon name="source"/><div className="attachment-name">{p.name}{p.busy ? <><progress max={100} value={p.progress} aria-label={`Uploading ${p.name}`}/><small>Uploading · {p.progress}%</small></> : <><p className="live-error" role="alert">{p.error}</p>{(p.file || p.cancelled) && <button className="button" onClick={()=>c.retry(p)}>{p.cancelled?'Retry cancellation':'Retry upload'}</button>}</>}</div>{p.busy && <button className="button" onClick={()=>void c.cancel(p)} disabled={p.cancelled} aria-label={`Cancel ${p.name}`}>{p.cancelled?'Cancelling…':'Cancel'}</button>}{!p.busy && !p.cancelled && <button title={`Remove ${p.name}`} className="icon-button" aria-label={`Remove ${p.name}`} onClick={()=>c.forgetUpload(p.key)}><Icon name="close"/></button>}</li>)}</ul>}
      {(!c.localOK || c.syncError) && <div className="storage-warning" role="alert"><p>{!c.localOK?'Not saved on this device. ':''}{c.syncError || (c.serverSaved?'A copy is saved to Drafts.':'Keep this page open until a copy is saved to Drafts.')}</p><button className="button" onClick={c.retryStorage}>Retry saving</button> <button className="button" onClick={()=>void copyAndAnnounce(form.text,'words')}>Copy words</button></div>}
      {recovering && <p role="status">Restoring your uploaded draft…</p>}
      {patternError && <p className="live-hint">{patternError}</p>}

 </div></section><div className="add-interpretation"><AddUnderstood composer={c} online={live.online}/></div></div>
 <section className="add-savebar" aria-label="Save entry"><div><strong>{saving?'Saving…':busyFiles?'Uploading…':empty&&barResult?barResult:empty?'Ready when you are':statement?'Continue to read the statement':'Ready to continue'}</strong><p className="live-hint">{empty&&latest?'The input is ready for your next entry.':statement?'Checks come before applying any rows.':c.ruleDecisionNeeded?'Choose the payment or bill action above.':'Words or a file are enough to start.'}</p>{error&&<p className="live-error" role="alert">{error}</p>}</div><button type="button" className="button primary" disabled={c.ruleDecisionNeeded||live.online!==true||blockedAgent&&!builtin||saving||recovering||busyFiles||c.interpretation.busy||blockedFiles||pending.some(p=>!!p.error)||(!form.text.trim()&&!draft?.files.length)} onClick={()=>void save()}>Continue</button></section>
 {refused?.refusal&&<BalanceNotice refusal={refused.refusal}><p>{refused.text}</p><button className="button" onClick={()=>void resolveRefusal('edit')}>Change account or date</button><button className="button" onClick={()=>void resolveRefusal('keep')}>Keep as draft</button><button className="button destructive" onClick={()=>void resolveRefusal('discard')}>Discard</button>{refusalError&&<p>{refusalError}</p>}</BalanceNotice>}
 <AddResults live={live} rows={rows}/>
 {showExamples&&<Dialog title="Examples" subtitle="Pick one, then change it to your own words." labelId="add-examples-title" onClose={()=>setShowExamples(false)}><div className="add-examples">{examples.map(example=><button className="example-button" key={example} onClick={()=>chooseExample(example)}>{example}</button>)}</div></Dialog>}
 </section>;
}
