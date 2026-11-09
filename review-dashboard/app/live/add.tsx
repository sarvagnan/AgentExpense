import {useActionGuard} from './interaction';
import {ActionFeedback} from './pending-actions';
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
import {firstRunDone} from '../first-run';
import {AgentNotice,type AgentStatus} from './agent-status';
/** The save bar never reads as ready while the agent or the connection keeps Continue disabled. */
export function blockedSaveText(agent: AgentStatus, online: boolean | null): [string, string] | null {
 if (online === false) return ['You’re offline', 'Continue works again when you reconnect.'];
 if (agent.agent_pause) return agent.agent_pause.kind === 'auth' ? ['Waiting for the agent to sign in', 'Your words stay here. Sign-in is retried every 30 seconds.'] : [`Paused until ${formatTimestamp(agent.agent_pause.resumes_at)}`, 'Your words stay here until the agent resumes.'];
 if (agent.state === 'signed out') return ['Sign in to continue', 'Your words stay here. Sign the agent in, then continue.'];
 if (agent.state === 'paused') return ['Add is paused', 'Your words stay here until AgentExpense supports the agent update.'];
 return null;
}
export function FilePreview({ id, file, completed = false, completedUrl, note }: {id: string; file: Attachment; completed?: boolean; completedUrl?: string | null; note?: string}) {
  const document = !file.media_type.startsWith('image/');
  const kind = document ? file.original_name.split('.').at(-1)?.toUpperCase() || 'File' : 'Image';
  return <>{!completed || completedUrl ? <a className="attachment-preview" href={completedUrl || fileUrl(id,file.id)} target="_blank" rel="noopener" aria-label={`Open ${file.original_name}`}>{document ? <span className="pdf-page"><Icon name="source"/>{kind}</span> : <img src={completedUrl || fileUrl(id,file.id)} alt=""/>}</a> : <span className="pdf-page"><Icon name="source"/>{document ? 'PDF' : 'Image'}</span>}<span className="attachment-name"><span className="attachment-file" title={file.original_name}>{file.original_name}</span><small>{Math.ceil(file.size / 1024)} KB{completed ? ' · upload filed or removed after completion' : ' · uploaded'}</small>{note && <small>{note}</small>}</span></>;
}
export default function Add({live,composer:c}:{live:LiveDrafts;composer:Composer}){
 const {form,draft,pending,patternError,error,saving,recovering,update,pick}=c;
 const guard=useActionGuard();
 const [setup]=useState(firstRunDone);
 const currency=dashboard.meta.ledger.defaultCurrency;
 const account=Object.values(dashboard.meta.accounts).find(m=>m.kind==='card'&&m.currency===currency);
 const bank=Object.values(dashboard.meta.accounts).find(m=>m.kind==='bank');
 const examples=[`Copper Finch Café, ${money(18.60,currency)}, ${account?.label||'cash'}`,`Bus to work, ${money(2.75,currency)}, cash`,`${bank?.label||'Bank'} statement for ${new Intl.DateTimeFormat(dashboard.meta.ledger.locale||navigator.language,{month:'long'}).format(new Date())}`];
 const [removeErrors,setRemoveErrors]=useState<Record<string,string>>({}),[dragging,setDragging]=useState(false),[showExamples,setShowExamples]=useState(false);
 const reach=useRef(0),userCleared=useRef(false);
 const page=useRef<HTMLElement>(null),input=useRef<HTMLTextAreaElement>(null),picker=useRef<HTMLInputElement>(null);
 const rows=useAddResults(live),current={current:form};
 const busyFiles=pending.some(p=>p.busy),files=c.interpretation.value?.files||[];
 const statement=(c.interpretation.value?.words||statementWords(form.text)).kind==='statement';
 const builtin=files.length>0&&files.every(f=>f.kind==='statement'&&!!f.reader)&&!(!statement&&form.text.trim());
 const blockedAgent=live.agent.state==='signed out'||live.agent.state==='paused'||!!live.agent.agent_pause;
 const blockedFiles=files.some(f=>f.kind==='ambiguous'||f.locked||f.mismatch)||statement&&!draft?.files.length;
 const refused=live.drafts.find(d=>d.state==='failed'&&d.refusal);
 const [refusalError,setRefusalError]=useState(''),[refusalBusy,setRefusalBusy]=useState<''|'edit'|'keep'|'discard'>('');
 // Discard is a verified Trash commit that can take seconds. Release the input first: the
 // refused item keeps its words in Queue until then, and words typed meanwhile are the next
 // expense, not edits of the discarded one. One action at a time.
 async function resolveRefusal(action:'edit'|'keep'|'discard'){if(!refused||refusalBusy)return;const id=refused.id;setRefusalBusy(action);setRefusalError('');try{if(action==='discard'){c.forgetDiscarded(id);await live.discard(id);}else{await api.resolveRefusal(id,'edit');if(action==='edit')await c.restore(id);}await live.refresh();}catch(e){setRefusalError((e as Error).message);}finally{setRefusalBusy('');}}
 const latest=rows[0],barResult=latest?(latest.type==='draft'?draftStatus(latest.value as Draft):statementStatus(latest.value as StatementSession)):'';
 const empty=!form.text&&!draft?.files.length&&!pending.length;
 const blockedSave=!empty&&!builtin?blockedSaveText(live.agent,live.online):!empty&&live.online===false?blockedSaveText(live.agent,false):null;
 // A typed new account or another chosen account is being checked: say so, not the mismatch it resolves.
 const mismatch=files.find(f=>f.mismatch),mismatchChoice=mismatch?form.fileChoices?.[mismatch.id]:undefined;
 const resolvingMismatch=!!mismatch&&c.interpretation.busy&&!!(mismatchChoice?.new_account?.name.trim()||mismatchChoice?.account&&mismatchChoice.account!==mismatch.account);
 // Every other reason Continue is disabled is said in the bar, never "Ready to continue".
 const waitingSave:[string,string]|null=empty?null:pending.some(p=>!!p.error)?['An upload didn’t finish','Retry or remove it above to continue.']
  :files.some(f=>f.kind==='ambiguous')?['Choose statement or receipt','Pick one above to continue.']
  :mismatch&&!resolvingMismatch?[`Choose an account in ${mismatch.currency}`,'The file and the account must use the same currency.']
  :files.some(f=>f.locked)?['Type the file’s password','It stays in memory only and opens the file.']
  :statement&&!draft?.files.length?['Attach the statement file','Its printed figures are checked before anything is applied.']
  :c.ruleDecisionNeeded?['Choose an action above','Or choose Not this to send your words to the agent.']
  :c.interpretation.busy&&!!draft?.files.length?['Checking the file…','Continue opens when the check finishes.']
  :c.usualCard==='fixed'?['Add as usual above, or send it to the agent','The usual payment is added at once. The agent reads your words instead.']:null;
 // With a fixed usual-payment card, Add as usual is the one primary action; Continue steps back.
 const usualFirst=c.usualCard==='fixed';
 // A shorter background result must not clamp scrollY and move the composer
 // under the pointer. Keep the measured page height until the person returns
 // to the top; scroll events must never manufacture more trailing space.
 useEffect(()=>{
  const preserveScroll=()=>{const node=page.current;if(node){reach.current=window.scrollY<=0?0:Math.max(reach.current,Math.ceil(node.getBoundingClientRect().height));if(reach.current)node.style.minHeight=`${reach.current}px`;else node.style.removeProperty('min-height');}};
  preserveScroll();window.addEventListener('scroll',preserveScroll,{passive:true});window.addEventListener('resize',preserveScroll);
  return()=>{window.removeEventListener('scroll',preserveScroll);window.removeEventListener('resize',preserveScroll);};
 },[]);
 function releaseReach(){reach.current=0;page.current?.style.removeProperty('min-height');}
 useEffect(()=>{if(empty&&userCleared.current){userCleared.current=false;releaseReach();}},[empty]);
 useEffect(()=>{void c.restore(new URLSearchParams(window.location.search).get('draft'));},[]);

 async function remove(file:Attachment){setRemoveErrors(xs=>({...xs,[file.id]:''}));try{await c.remove(file);}catch(e){setRemoveErrors(xs=>({...xs,[file.id]:`Could not remove this file. ${(e as Error).message}`}));}}
 async function completeSave(action:()=>ReturnType<Composer['save']>){const ticket=guard.begin(),saved=await action();if(saved&&guard.unchanged(ticket)){releaseReach();input.current?.focus({preventScroll:true});}return saved;}
 function save(...args:Parameters<Composer['save']>){return completeSave(()=>c.save(...args));}
 function chooseExample(example:string){setShowExamples(false);update({...form,text:example,edits:{}});requestAnimationFrame(()=>input.current?.focus({preventScroll:true}));}
 return <section ref={page} className={`page live-page add-page ${dragging?'is-dragging':''}`} onDragEnter={e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();setDragging(true);}}} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node))setDragging(false);}} onDragOver={e=>{if(e.dataTransfer.types.includes('Files'))e.preventDefault();}} onDrop={e=>{setDragging(false);if(e.dataTransfer.files.length){e.preventDefault();pick(Array.from(e.dataTransfer.files));}}} onPaste={e=>{const files=Array.from(e.clipboardData.files);if(files.length){e.preventDefault();pick(files);}}}>
 <PageHeader title="Add" subtitle={setup?'Your ledger is ready. Add your first expense.':'Expenses, receipts and statements. Start with what you have.'}/>
 {setup&&<aside className="live-notice first-run-done" role="status"><Icon name="check"/><div><strong>{setup.path==='import'?'Statements imported.':'You’re ready to start.'}</strong><p>{setup.path==='import'?<>{setup.entries} {setup.entries===1?'entry':'entries'} found. {setup.review} {setup.review===1?'is':'are'} waiting in <a href={`${import.meta.env.BASE_URL}review/`}>Review</a>.{setup.unsaved?` ${setup.unsaved} ${setup.unsaved===1?'statement':'statements'} couldn’t be saved; ${setup.unsaved===1?'it is':'they are'} in Queue.`:''}{setup.unread?` ${setup.unread} ${setup.unread===1?'file':'files'} couldn’t be read; ${setup.unread===1?'it is':'they are'} in Queue to try again.`:''}</>:<>Your opening balances are in <a href={`${import.meta.env.BASE_URL}account-summary/`}>Accounts</a>.</>}</p></div></aside>}
 {live.online===false&&<aside className="live-notice add-blocking-notice" role="status"><Icon name="alert"/><div><strong>You’re offline</strong><p>Your words and files stay here. Reconnect to continue.</p><button className="button" onClick={()=>void live.refresh()}>Check connection</button></div></aside>}
 {live.online!==false&&<AgentNotice status={live.agent} place="add" className="add-blocking-notice"/>}
 <div className="add-layout"><section className="panel composer-panel"><PanelHeader title="What happened?"/><div className="panel-body">
 <label className="sr-only" htmlFor="expense-words">Describe an expense or statement</label><textarea ref={input} id="expense-words" className="expense-words" rows={3} value={form.text} maxLength={100000} placeholder="Type what happened, or attach a file…" onChange={e=>{guard.touch();userCleared.current=!e.target.value;update({...current.current,text:e.target.value});}} aria-describedby="upload-hint"/>
 <div className="composer-tools"><button type="button" className="button" disabled={live.online!==true||saving||recovering} onClick={()=>{c.retryStorage();picker.current?.click();}}><Icon name="import"/>Upload</button><button className="text-button" type="button" onClick={()=>setShowExamples(true)}>Examples</button><input ref={picker} className="sr-only" tabIndex={-1} aria-label="Upload files" type="file" multiple accept="image/jpeg,image/png,image/webp,.pdf,.csv,.xlsx,.xls" disabled={live.online!==true||saving||recovering} onChange={e=>{pick(Array.from(e.target.files||[]));e.target.value='';}}/><p id="upload-hint" className="live-hint">Photos, PDF, CSV, Excel</p></div>
      {(!!draft?.files.length || !!pending.length) && <ul className="attachments" aria-label="Uploads">{draft?.files.map(f=><li key={f.id}><FilePreview id={draft.id} file={f}/>{removeErrors[f.id] && <p className="live-error" role="alert">{removeErrors[f.id]}</p>}<span className="file-remove" data-tooltip={`Remove ${f.original_name}`}><button title={`Remove ${f.original_name}`} className="icon-button" disabled={saving} onClick={()=>void remove(f)} aria-label={`Remove ${f.original_name}`}><Icon name="close"/></button></span></li>)}{pending.map(p=><li key={p.key} className="pending-upload"><Icon name="source"/><div className="attachment-name">{p.name}{p.busy ? <><progress max={100} value={p.progress} aria-label={`Uploading ${p.name}`}/><small>Uploading · {p.progress}%</small></> : <><p className="live-error" role="alert">{p.error}</p>{(p.file || p.cancelled) && <button className="button" onClick={()=>c.retry(p)}>{p.cancelled?'Retry cancellation':'Retry upload'}</button>}</>}</div>{p.busy && <button className="button" onClick={()=>void c.cancel(p)} disabled={p.cancelled} aria-label={`Cancel ${p.name}`}>{p.cancelled?'Cancelling…':'Cancel'}</button>}{!p.busy && !p.cancelled && <button title={`Remove ${p.name}`} className="icon-button" aria-label={`Remove ${p.name}`} onClick={()=>c.forgetUpload(p.key)}><Icon name="close"/></button>}</li>)}</ul>}
      {(!c.localOK || c.syncError) && <div className="storage-warning" role="alert"><p>{!c.localOK?'Not saved on this device. ':''}{c.syncError || (c.serverSaved?'A copy is saved to Queue.':'Keep this page open until a copy is saved to Queue.')}</p><button className="button" onClick={c.retryStorage}>Retry saving</button> <button className="button" onClick={()=>void copyAndAnnounce(form.text,'words')}>Copy words</button></div>}
      {recovering && <p role="status">Restoring your uploaded item…</p>}
      {patternError && <p className="live-hint">{patternError}</p>}

 </div></section><div className="add-interpretation"><AddUnderstood composer={{...c,save}} online={live.online}/></div></div>
 <section className="add-savebar" aria-label="Save entry"><div><strong>{saving?'Saving…':busyFiles?'Uploading…':blockedSave?blockedSave[0]:empty&&barResult?barResult:empty?'Ready when you are':waitingSave?waitingSave[0]:statement||files.some(f=>f.kind==='statement')?'Continue to read the statement':'Ready to continue'}</strong><p className="live-hint">{blockedSave&&!saving&&!busyFiles?blockedSave[1]:empty&&latest?'The input is ready for your next entry.':waitingSave&&!saving&&!busyFiles?waitingSave[1]:statement||files.some(f=>f.kind==='statement')?'Checks come before applying any rows.':'Words or a file are enough to start.'}</p>{error&&<p className="live-error" role="alert">{error}</p>}</div><button type="button" className={usualFirst?'button':'button primary'} disabled={c.ruleDecisionNeeded||live.online!==true||blockedAgent&&!builtin||saving||recovering||busyFiles||c.interpretation.busy||blockedFiles||pending.some(p=>!!p.error)||(!form.text.trim()&&!draft?.files.length)} onClick={()=>void save()}>{usualFirst?'Send to the agent instead':'Continue'}</button></section>
 {refused?.refusal&&<BalanceNotice refusal={refused.refusal}><p>{refused.text}</p><button className="button" disabled={!!refusalBusy} onClick={()=>void resolveRefusal('edit')}>{refusalBusy==='edit'?'Opening…':'Change account or date'}</button><button className="button" disabled={!!refusalBusy} onClick={()=>void resolveRefusal('keep')}>{refusalBusy==='keep'?'Keeping…':'Keep in Queue'}</button><button className="button destructive" disabled={!!refusalBusy} onClick={()=>void resolveRefusal('discard')}>{refusalBusy==='discard'?'Moving to Trash…':'Discard'}</button>{refusalError&&<p>{refusalError}</p>}</BalanceNotice>}
 <AddResults live={live} rows={rows}/>
 <ActionFeedback scope="entry" path={c.directPath}/>
 {showExamples&&<Dialog title="Examples" subtitle="Pick one, then change it to your own words." labelId="add-examples-title" onClose={()=>setShowExamples(false)}><div className="add-examples">{examples.map(example=><button className="example-button" key={example} onClick={()=>chooseExample(example)}>{example}</button>)}</div></Dialog>}
 </section>;
}
