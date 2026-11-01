import { AgentNotice, agentBlocked } from './agent-status';
import {formatTimestamp} from '../date-time';
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { dashboard } from '../data';
import { Icon, PageHeader, PanelHeader, evidenceHref, useQueryState, announce, copyAndAnnounce } from '../ui';
import { api, type Draft } from './api';
import {StatementHandover} from './statement-handover';
import {useAnswer} from './answers';
import { FilePreview } from './add';
import { isRunning, type LiveDrafts } from './use-drafts';
export function draftStatus(d: Draft) {return d.result?.outcome==='not_expense'?'Not an expense':d.waiting?'Waiting':({editing:'Editing',queued:'Queued',working:'Working',question:'Question',failed:'Failed',added:'Added'}[d.state]);}
export function draftTitle(d: Draft) {return d.result?.description || d.text || d.files[0]?.original_name || 'Files only';}
function elapsed(d: Draft, now: number) {const start=d.runs.at(-1)?.start || d.queued_at || d.created_at;const seconds=Math.max(0,Math.floor((now-Date.parse(start))/1000));return seconds<60?`${seconds}s`:`${Math.floor(seconds/60)}m ${seconds%60}s`;}
export function DraftDetail({draft:d,onAction,onDiscard,blocked=false}: {blocked?:boolean;draft:Draft;onAction:()=>Promise<void>;onDiscard?:(id:string)=>Promise<void>}) {
  const answer=useAnswer(d);
  const [error,setError]=useState<{at:'answer'|'retry'|'discard'|'handover';message:string}|null>(null), [busy,setBusy]=useState(false), [now,setNow]=useState(Date.now());
  const heading=useRef<HTMLHeadingElement>(null);
  useEffect(()=>{if(!isRunning(d))return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[d.state]);
  const entry=dashboard?.transactions?.find(e=>e.id===d.result?.entry_id);
  const question=d.questions.findLast(q=>q.answer===null);
  const doubts=[...new Set(d.runs.flatMap(r=>r.doubts || []))];
  async function act(action:()=>Promise<unknown>,message:string,at:'answer'|'retry'|'discard'|'handover') {setBusy(true);setError(null);try{await action();if(at==='answer')answer.clear();if(at==='discard')answer.clear(true);await onAction();announce(message);}catch(e){setError({at,message:(e as Error).message});}finally{setBusy(false);}}
  return <article data-draft-id={d.id} className="panel draft-detail" aria-label="Draft details"><div className="panel-header"><div><h2 ref={heading} tabIndex={-1}>{draftTitle(d)}</h2><p>{formatTimestamp(d.client_time || d.saved_at || d.created_at)}</p></div><span className={`draft-status state-${d.state}`}><Icon name={d.state==='added'?'check':d.state==='failed'?'alert':'pending'}/>{draftStatus(d)}</span></div>
    <div className="panel-body draft-detail-body">
      {isRunning(d) && <div className="agent-next" role="status"><Icon name="pending"/><div><strong>{d.waiting?'Waiting to add':d.state==='queued'?'In the queue':'The agent is reading it'}</strong>{d.waiting && <p>Waiting for other ledger changes to finish before this addition can be saved.</p>}<p>{d.waiting || 'You can leave this page. Work continues on the server.'}</p><p className="live-hint" aria-live="off">Elapsed {elapsed(d,now)}</p></div></div>}
      {d.state==='question' && <form className="draft-question" onSubmit={e=>{e.preventDefault();if(blocked)return;void act(()=>api.answer(d.id,answer.text),'Answer sent. The agent is working again.','answer');}}><label htmlFor={`answer-${d.id}`}><strong>{question?.question || 'The agent needs more information.'}</strong></label><textarea id={`answer-${d.id}`} className="field" rows={3} value={answer.text} onChange={e=>answer.set(e.target.value)} maxLength={100000} disabled={busy}/>{(!answer.localOK || answer.error) && <div className="storage-warning" role="alert"><p>{!answer.localOK?'Answer not saved on this device. ':''}{answer.error || (answer.serverSaved?'A copy is saved to Drafts.':'Keep this page open until a copy is saved.')}</p><button type="button" className="button" onClick={answer.retry}>Retry saving answer</button> <button type="button" className="button" onClick={()=>void copyAndAnnounce(answer.text,'answer')}>Copy answer</button></div>}<p className="live-hint">{answer.text?(answer.serverSaved?'Answer saved to Drafts':answer.localOK?'Answer kept on this device':'Answer not saved yet'):''}</p><button className="button primary" disabled={blocked || !answer.text.trim() || busy}>Send answer</button>{error?.at==='answer' && <p className="live-error" role="alert">{error.message}</p>}</form>}
      {d.state==='failed' && <div className="live-notice failure"><Icon name="alert"/><div><strong>The agent couldn’t finish this draft</strong><p>The agent stopped before it finished. Your draft is kept. Retry, or discard it.</p>{d.error && <details><summary>Details</summary><p>{d.error}</p></details>}<button className="button primary" disabled={blocked || busy} onClick={()=>void act(()=>api.retry(d.id),'Draft queued again.','retry')}>Retry</button>{error?.at==='retry' && <p className="live-error" role="alert">{error.message}</p>}</div></div>}
      {d.result?.outcome==='not_expense' && <div className="live-notice"><Icon name="source"/><div><strong>{d.statement_file?'A card statement':'Not an expense'}</strong><p>{d.result.explanation || 'This upload does not describe a single expense.'}</p>{d.statement_file&&<StatementHandover draftId={d.id}/>}</div></div>}
      {d.state==='added' && d.result?.outcome!=='not_expense' && <section className="added-result" aria-label="Added entry"><h3>Added to your ledger</h3><p className="added-total">{d.result?.total}</p><dl className="facts"><dt>Description</dt><dd>{d.result?.description}</dd><dt>Payment account</dt><dd>{d.result?.payment_account}</dd><dt>New accounts</dt><dd>{d.result?.new_accounts?.join(', ') || 'None'}</dd></dl>{d.result?.sources && d.result.sources.length>0 && <div><h3>Filed evidence</h3><ul className="filed-evidence">{d.result.sources.map(source=><li key={source}>{evidenceHref(source)?<a href={evidenceHref(source)!} target="_blank" rel="noopener">{source}</a>:source}</li>)}</ul></div>}{d.result?.entry_id && (entry ? <a className="button" href={`${import.meta.env.BASE_URL}explorer/?month=${entry.month}&entry=${encodeURIComponent(d.result.entry_id)}`}>Open in Explorer</a> : <p className="live-hint">Refreshing the entry for Explorer…</p>)}{d.result?.data_stale && <p className="live-error" role="status">The entry is saved, but server data needs a refresh. {d.result.refresh_error}</p>}</section>}
      {d.state==='editing' && <div className="agent-next"><Icon name="pencil"/><div><strong>Ready to continue</strong><p>Your words and uploaded files are saved to Drafts. Continue in Add to let the agent check and add it.</p><a className="button" href={`${import.meta.env.BASE_URL}add/?draft=${d.id}`}>Continue in Add</a></div></div>}
      <section className="draft-words"><h3>Your words</h3><p>{d.text || 'Files only — no typed words.'}</p></section>
      {d.files.length>0 && <section><h3>Uploads</h3><ul className="attachments">{d.files.map((f,i)=><li key={f.id}><FilePreview id={d.id} file={f} completed={d.state==='added'} completedUrl={d.state==='added' && d.result?.sources?.[i] ? evidenceHref(d.result.sources[i]) : null}/></li>)}</ul></section>}
      {d.questions.some(q=>q.answer!==null) && <section><h3>Earlier answers</h3>{d.questions.filter(q=>q.answer!==null).map((q,i)=><div key={i} className="earlier-answer"><p>{q.question}</p><p><strong>You:</strong> {q.answer}</p></div>)}</section>}
      <section><h3>Agent doubts</h3>{doubts.length?<ul className="draft-doubts">{doubts.map(x=><li key={x}>{x}</li>)}</ul>:<p className="live-hint">{isRunning(d)?'None reported yet.':'None reported.'}</p>}</section>
      {d.state!=='working' && <div className="draft-actions"><button className="button destructive" disabled={busy} onClick={()=>void act(async()=>{await answer.flush();await (onDiscard?onDiscard(d.id):api.discard(d.id));},'Draft discarded.','discard')}>Discard{d.state==='added' && d.result?.entry_id?' draft record':''}</button>{error?.at==='discard' && <p className="live-error" role="alert">{error.message}</p>}{d.state==='added' && d.result?.entry_id && <p className="live-hint">This removes the draft record. The ledger entry remains.</p>}</div>}
    </div></article>;
}
export default function Drafts({live}: {live:LiveDrafts}) {
  const [id,setId]=useQueryState('draft','');
  useEffect(()=>{if(id && live.online===true && !live.drafts.some(d=>d.id===id))setId('', 'replace');},[id,live.online,live.drafts]);
  const [cursor,setCursor]=useState('');
  const [desktop,setDesktop]=useState(()=>matchMedia('(min-width: 761px)').matches);
  useEffect(()=>{const media=matchMedia('(min-width: 761px)'),change=()=>setDesktop(media.matches);media.addEventListener('change',change);return()=>media.removeEventListener('change',change);},[]);
  const typeAhead=useRef({text:'',at:0});
  const selected=live.drafts.find(d=>d.id===id) || live.drafts[0];
  const details=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(id)details.current?.querySelector<HTMLElement>('h2')?.focus({preventScroll:true});},[id]);
  function queueKey(event:KeyboardEvent<HTMLUListElement>){
    if(!matchMedia('(min-width: 761px)').matches || event.altKey||event.ctrlKey||event.metaKey)return;
    const links=[...event.currentTarget.querySelectorAll<HTMLAnchorElement>('a.draft-row')],at=links.indexOf(document.activeElement as HTMLAnchorElement);
    let next=-1;
    if(event.key==='ArrowDown')next=Math.min(links.length-1,at+1);
    else if(event.key==='ArrowUp')next=Math.max(0,at-1);
    else if(event.key==='Home')next=0;
    else if(event.key==='End')next=links.length-1;
    else if(event.key.length===1){const now=Date.now();typeAhead.current={text:(now-typeAhead.current.at<700?typeAhead.current.text:'')+event.key.toLowerCase(),at:now};next=links.findIndex((_,i)=>i>at&&draftTitle(live.drafts[i]).toLowerCase().startsWith(typeAhead.current.text));if(next<0)next=links.findIndex((_,i)=>draftTitle(live.drafts[i]).toLowerCase().startsWith(typeAhead.current.text));}
    if(next>=0){event.preventDefault();setCursor(live.drafts[next].id);links[next].focus();}
  }
  return <section className="page live-page drafts-page"><PageHeader title="Drafts" subtitle="Follow the agent, answer a question, or see what was added."><div className="draft-toolbar">{id && <a className="button phone-draft-back" href={`${import.meta.env.BASE_URL}drafts/`}> <Icon name="chevron-left"/>All drafts</a>}<button className="button" onClick={()=>void live.refresh()}>Refresh</button></div></PageHeader>
    {live.online!==false && <AgentNotice status={live.agent}/>}
    {live.online===false && <div className="live-notice" role="status"><Icon name="alert"/><div><strong>Drafts needs the live server</strong><p>{live.error}</p><p>Your other ledger views remain available.</p></div></div>}
    {live.reloadError && <p className="live-error" role="alert">{live.reloadError} <button className="button" onClick={()=>void live.refresh()}>Retry refresh</button></p>}
    {live.online===null && <p role="status">Loading drafts…</p>}
    {live.online===true && !live.drafts.length && <div className="panel empty"><Icon name="check"/><strong>No drafts waiting.</strong><p>New expenses, questions and statements will appear here while the agent works on them.</p><a className="button primary" href={`${import.meta.env.BASE_URL}add/`}>Add an expense</a></div>}
    {!!live.drafts.length && <div className="draft-workspace" data-open={!!id}><section className="panel draft-queue"><PanelHeader title="Your drafts" meta={`${live.attention} need attention · newest first`}/><h2 className="sr-only">Draft list</h2><ul onKeyDown={queueKey}>{live.drafts.map(d=><li key={d.id}><a className="draft-row" href={`${import.meta.env.BASE_URL}drafts/?draft=${d.id}`} aria-current={selected?.id===d.id?'true':undefined} tabIndex={desktop && (live.drafts.some(item=>item.id===cursor)?cursor:selected?.id)!==d.id?-1:0} onFocus={()=>setCursor(d.id)}><span className={`draft-status state-${d.state}`}>{draftStatus(d)}</span><strong>{draftTitle(d)}</strong><span className="live-hint">{formatTimestamp(d.client_time || d.saved_at || d.created_at)}{d.files.length?` · ${d.files.length} file${d.files.length===1?'':'s'}`:''}</span></a></li>)}</ul></section><div ref={details} className="draft-detail-wrap">{selected && <DraftDetail key={selected.id} draft={selected} onAction={live.refresh} onDiscard={live.discard} blocked={agentBlocked(live.agent)}/>}</div></div>}
  </section>;
}
