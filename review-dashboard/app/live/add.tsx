import {RuleGuess} from './rule-guess';
import {RuleSaved} from './rule-saved';
import { AgentNotice, agentBlocked } from './agent-status';
import { useEffect, useRef, useState } from 'react';
import { Icon, PageHeader, PanelHeader, copyAndAnnounce, money } from '../ui';
import { fileUrl, type Attachment } from './api';
import { dashboard } from '../data';
import { moduleSettings } from '../../scripts/ledger-modules.mjs';
import StatementUpload from './statement-upload';
import { type LiveDrafts } from './use-drafts';
import {type Composer} from './composer';
import {StatementHandover} from './statement-handover';
export function FilePreview({ id, file, completed = false, completedUrl }: {id: string; file: Attachment; completed?: boolean; completedUrl?: string | null}) {
  const pdf = file.media_type === 'application/pdf';
  return <>{!completed || completedUrl ? <a className="attachment-preview" href={completedUrl || fileUrl(id,file.id)} target="_blank" rel="noopener" aria-label={`Open ${file.original_name}`}>{pdf ? <span className="pdf-page"><Icon name="source"/>PDF</span> : <img src={completedUrl || fileUrl(id,file.id)} alt=""/>}</a> : <span className="pdf-page"><Icon name="source"/>{pdf ? 'PDF' : 'Image'}</span>}<span className="attachment-name">{file.original_name}<small>{Math.ceil(file.size / 1024)} KB{completed ? ' · upload filed or removed after completion' : ' · uploaded'}</small></span></>;
}
export default function Add({live,composer:c}: {live: LiveDrafts;composer:Composer}) {
  const {form,draft,pending,patternError,error,saving,success,recovering,built,update,pick}=c;
  const currency=dashboard.meta.ledger.defaultCurrency;
  const card=Object.values(dashboard.meta.accounts).find(m=>m.kind==='card'&&m.currency===currency);
  const examples=[`Copper Finch Café, ${money(18.60,currency)}, ${card?.label || 'cash'}`,`Bus to work, ${money(2.75,currency)}, cash`];
  const [removeErrors,setRemoveErrors]=useState<Record<string,string>>({});
  const [dragging,setDragging]=useState(false);
  const current={current:form};
  const statement=success&&live.drafts.find(d=>d.id===c.lastSavedId&&d.statement_file);
  const input=useRef<HTMLTextAreaElement>(null),picker=useRef<HTMLInputElement>(null),confirmation=useRef<HTMLDivElement>(null);
  const saved=live.drafts.find(d=>d.id===c.lastSavedId&&d.state==='added');
  const busyFiles=pending.some(p=>p.busy);
  useEffect(()=>{void c.restore(new URLSearchParams(window.location.search).get('draft'));},[]);
  async function remove(file:Attachment){setRemoveErrors(xs=>({...xs,[file.id]:''}));try{await c.remove(file);}catch(e){setRemoveErrors(xs=>({...xs,[file.id]:`Could not remove this file. ${(e as Error).message}`}));}}
  async function save(){if(agentBlocked(live.agent))return;if(await c.save())requestAnimationFrame(()=>{if(navigator.maxTouchPoints>0 || matchMedia('(pointer: coarse)').matches)confirmation.current?.focus();else input.current?.focus();});}
  return <section className={`page live-page add-page ${dragging?'is-dragging':''}`} onDragEnter={e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();setDragging(true);}}} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node))setDragging(false);}} onDragOver={e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();setDragging(true);}}} onDrop={e=>{setDragging(false);if(e.dataTransfer.files.length){e.preventDefault();pick(Array.from(e.dataTransfer.files));}}} onPaste={e=>{const files=Array.from(e.clipboardData.files);if(files.length){e.preventDefault();pick(files);}}}>
    <PageHeader title="Add" subtitle="Tell it once. Let the agent take it from here."/>
    {live.online!==false && <AgentNotice status={live.agent}/>}
    {live.online===false && <div className="live-notice" role="status"><Icon name="alert"/><div><strong>Adding needs the live server</strong><p>Your ledger views still work here. Reconnect to the live dashboard to upload and save.</p><button className="button" onClick={()=>void live.refresh()}>Check connection</button></div></div>}
    {live.online===null && <p role="status" className="live-hint">Checking the live server…</p>}
    {success && <div ref={confirmation} tabIndex={-1} className="live-notice live-success" role="status"><Icon name="check"/><p>{success} <a href={`${import.meta.env.BASE_URL}drafts/`}>Open Drafts</a></p></div>}
    {statement&&<div className="live-notice"><Icon name="source"/><div><strong>This is a card statement</strong><p>Continue with its rows in Drafts.</p><StatementHandover draftId={statement.id}/></div></div>}
    {saved&&<RuleSaved key={saved.id} draft={saved} online={live.online}/>}
    <div className="add-layout"><section className="panel composer-panel"><PanelHeader title="An expense" meta="Type what happened, with any photos or files."/><div className="panel-body">
      <label className="field-label" htmlFor="expense-words">What did you spend?</label>
      <textarea ref={input} id="expense-words" className="expense-words" rows={3} value={form.text} maxLength={100000} placeholder={moduleSettings(dashboard.meta.ledger).fuel ? 'e.g. petrol 875.25 8.5l 190km' : 'e.g. lunch 25 paid with cash'} onChange={e=>update({...current.current,text:e.target.value})} disabled={saving || recovering} aria-describedby="upload-hint"/>
      <RuleGuess composer={c} online={live.online}/>
      <div className="composer-tools"><button type="button" className="button" disabled={live.online!==true || saving || recovering} onClick={()=>{c.retryStorage();picker.current?.click();}}><Icon name="import"/>Photos or files</button><input ref={picker} className="sr-only" tabIndex={-1} aria-label="Upload files" type="file" multiple disabled={live.online!==true || saving || recovering} onChange={e=>{pick(Array.from(e.target.files || []));e.target.value='';}}/><p id="upload-hint" className="live-hint">Files upload as soon as you choose them.<span className="desktop-upload-hint"> You can also drop or paste files.</span></p></div>
      {(!!draft?.files.length || !!pending.length) && <ul className="attachments" aria-label="Uploads">{draft?.files.map(f=><li key={f.id}><FilePreview id={draft.id} file={f}/>{removeErrors[f.id] && <p className="live-error" role="alert">{removeErrors[f.id]}</p>}<span className="file-remove" data-tooltip={`Remove ${f.original_name}`}><button title={`Remove ${f.original_name}`} className="icon-button" disabled={saving} onClick={()=>void remove(f)} aria-label={`Remove ${f.original_name}`}><Icon name="close"/></button></span></li>)}{pending.map(p=><li key={p.key} className="pending-upload"><Icon name="source"/><div className="attachment-name">{p.name}{p.busy ? <><progress max={100} value={p.progress} aria-label={`Uploading ${p.name}`}/><small>Uploading · {p.progress}%</small></> : <><p className="live-error" role="alert">{p.error}</p>{(p.file || p.cancelled) && <button className="button" onClick={()=>c.retry(p)}>{p.cancelled?'Retry cancellation':'Retry upload'}</button>}</>}</div>{p.busy && <button className="button" onClick={()=>void c.cancel(p)} disabled={p.cancelled} aria-label={`Cancel ${p.name}`}>{p.cancelled?'Cancelling…':'Cancel'}</button>}{!p.busy && !p.cancelled && <button title={`Remove ${p.name}`} className="icon-button" aria-label={`Remove ${p.name}`} onClick={()=>c.forgetUpload(p.key)}><Icon name="close"/></button>}</li>)}</ul>}
      {(!c.localOK || c.syncError) && <div className="storage-warning" role="alert"><p>{!c.localOK?'Not saved on this device. ':''}{c.syncError || (c.serverSaved?'A copy is saved to Drafts.':'Keep this page open until a copy is saved to Drafts.')}</p><button className="button" onClick={c.retryStorage}>Retry saving</button> <button className="button" onClick={()=>void copyAndAnnounce(form.text,'words')}>Copy words</button></div>}
      {recovering && <p role="status">Restoring your uploaded draft…</p>}
      {patternError && <p className="live-hint">{patternError}</p>}
    <div className="composer-footer"><div><strong>{saving?'Saving…':busyFiles?'Uploading your files…':c.serverSaved?'Saved to Drafts':form.text?(c.localOK?'Kept on this device':'Words not saved yet'):'Ready when you are'}</strong><p className="live-hint">{built.empty?'Words or a file are enough to start.':'Save to let the agent check and add it.'}</p>{error && <p className="live-error" role="alert">{error}</p>}</div><button type="button" className="button primary" disabled={agentBlocked(live.agent) || live.online!==true || saving || recovering || busyFiles || pending.some(p=>!!p.error) || (!form.text.trim() && !draft?.files.length)} onClick={()=>void save()}>Send to agent</button></div>
    </div></section><StatementUpload live={live}/></div>
          <div className="add-examples"><h2>Try a few words</h2>{examples.map(example=><button key={example} className="example-button" onClick={()=>{update({...current.current,text:example,edits:{}});input.current?.focus();}}>{example}<span aria-hidden="true"> ↗</span></button>)}</div>

  </section>;
}
