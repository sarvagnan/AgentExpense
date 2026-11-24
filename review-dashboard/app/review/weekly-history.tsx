import {useEffect,useState} from 'react';
import {request} from '../live/api';
import {formatTimestamp} from '../date-time';
import {EvidenceName} from '../ui';
import type {Evidence,HistoryPage,ReviewEvent} from './weekly-api';

export function ReviewEvidence({files=[]}:{files?:Evidence[]}) {
  return files.length>0&&<ul className="weekly-evidence">{files.map(({path,href})=>
    <li key={path}>{href?<a href={href}><EvidenceName path={path}/></a>:<EvidenceName path={path}/>}</li>)}</ul>;
}

const eventLabel = (item:ReviewEvent) => item.kind==='review-message'?(item.actor==='user'?'Owner answer':'Reviewer'):
  item.kind==='review-carry-over'?'Raised again':item.kind==='review-resolution'?'Kept as is':item.kind==='rule'?'Rule saved':'Correction saved';
export function ReviewEvents({items,conversation=false}:{items:ReviewEvent[];conversation?:boolean}) {
  return <ol className="weekly-thread">{items.map(item=><li key={item.id}>
    <strong>{conversation&&eventLabel(item)==='Owner answer'?'You':eventLabel(item)}</strong>
    <span className="live-hint"> · {formatTimestamp(item.time)}</span>
    {item.title&&<p>{item.title}</p>}
    {(item.text||item.note||item.decide||item.reason)&&<p>{item.text||item.note||item.decide||item.reason}</p>}
    <ReviewEvidence files={item.message_evidence}/>
    {item.source_href&&<a href={item.source_href}>Open saved record</a>}
  </li>)}</ol>;
}

export function ReviewHistory({entry,issue,revision,paused=false}:{entry?:string;issue?:string;revision?:string;paused?:boolean}) {
  const [initial,setInitial]=useState<HistoryPage>(),[extra,setExtra]=useState<HistoryPage>(),[error,setError]=useState(''),[busy,setBusy]=useState(false),[retry,setRetry]=useState(0);
  const read=async(offset:number,signal?:AbortSignal)=>entry?
    request<HistoryPage>(`entries/${encodeURIComponent(entry)}/review-history?offset=${offset}`,{signal}):
    (await request<{review:{history:HistoryPage}}>(`drafts?include_review=true&review_issue_id=${encodeURIComponent(issue!)}&review_offset=${offset}`,{signal})).review.history;
  useEffect(()=>{
    if(paused)return;
    const controller=new AbortController();
    void read(0,controller.signal).then(page=>{if(!controller.signal.aborted){setInitial(page);setError('');}},
      e=>{if(!controller.signal.aborted)setError(e.message);});
    return()=>controller.abort();
  },[entry,issue,revision,retry,paused]);
  const next=extra?extra.next_offset:initial?.next_offset;
  const items=[...new Map([...(initial?.items||[]),...(extra?.items||[])].map(item=>[item.id,item])).values()];
  async function more(){
    setBusy(true);
    try {const page=await read(next!);setExtra({items:[...(extra?.items||[]),...page.items],next_offset:page.next_offset});setError('');}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  if(!items.length&&!error)return null;
  const title=entry?'Entry history':'Conversation';
  return <section className={entry?'inspector-section weekly-activity':'weekly-activity'} aria-label={title}>
    <h3>{title}</h3>
    <ReviewEvents items={items} conversation={!!issue}/>
    {next!=null&&<button className="button" disabled={busy} onClick={()=>void more()}>Show more</button>}
    {error&&<p className="live-hint" role="status">{error} <button className="button" onClick={()=>setRetry(n=>n+1)}>Retry</button></p>}
  </section>;
}
