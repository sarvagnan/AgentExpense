import {useEffect, useRef, useState} from 'react';
import {formatTimestamp} from '../date-time';
import {dayLabel, PageHeader, plural, useQueryState} from '../ui';
import type {AgentStatus} from '../live/agent-status';
import {request} from '../live/api';
import {requestId} from './api';
import {reviewPost, type FinishedRun, type Run, type WeeklyStatus} from './weekly-api';
import {ReviewEvidence} from './weekly-history';

const reviewer = (run:Run) => [run.adapter,run.model,run.effort].join(' · ');
const period = (run:Run) => run.period.filter((day,index,days)=>day && (index===0 || day!==days[0])).map(day=>dayLabel(day!.slice(0,10))).join(' – ') || '—';
const counts = (run:Run) => `${plural(run.checked,'line')} checked · ${plural(run.issues,'issue')}${run.carried?' · '+run.carried+' raised before':''}${run.rule_proposals?' · '+plural(run.rule_proposals,'rule proposal'):''}`;

export function ReviewSections({weekly,select,onHistory,history=false}:{weekly:boolean;select:(weekly:boolean)=>void;onHistory?:()=>void;history?:boolean}) {
  return <nav className="forecast-sections" aria-label="Review sections">
    <button className="button" aria-current={!weekly&&!history?'page':undefined} onClick={()=>select(false)}>Entries for review</button>
    <button className="button" aria-current={weekly?'page':undefined} onClick={()=>select(true)}>Weekly review</button>
    {(onHistory||history)&&<button className="button" aria-current={history?'page':undefined} onClick={onHistory}>Change history</button>}
  </nav>;
}

export function WeeklyReview({agent,onEntries,onHistory,foreground}:{agent:AgentStatus;onEntries:()=>void;onHistory:()=>void;foreground:boolean}) {
  const [value,setValue]=useState<WeeklyStatus|null>(null),[pages,setPages]=useState<FinishedRun[]>([]);
  const [next,setNext]=useState<number|null>(null),[report,setReport]=useQueryState('report','');
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[paging,setPaging]=useState(false);
  const version=useRef(0),generation=useRef<AbortController|null>(null),operation=useRef({action:'',id:''});
  async function refresh(signal?:AbortSignal) {
    const ticket=++version.current;
    try {
      const status=await request<WeeklyStatus>('review/weekly',{signal});
      if(ticket===version.current && !signal?.aborted)setValue(status);
    } catch(e) {
      if(ticket===version.current && !signal?.aborted)setError((e as Error).message);
    }
  }
  useEffect(()=>{
    const controller=new AbortController();generation.current=controller;
    let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{if(!document.hidden)await refresh(controller.signal);if(!controller.signal.aborted)timer=setTimeout(poll,2000);};
    void poll();
    return()=>{controller.abort();clearTimeout(timer);};
  },[]);
  async function act(action:string) {
    const key=action+':'+(value?.active?.run_id||'');
    if(operation.current.action!==key)operation.current={action:key,id:requestId()};
    setBusy(true);setError('');
    try {
      await reviewPost('review/weekly',{action,request_id:operation.current.id,...(action!=='start'?{run_id:value?.active?.run_id}:{})});
      operation.current={action:'',id:''};await refresh(generation.current?.signal);
    } catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function more(offset:number) {
    setPaging(true);setError('');
    try {
      const page=await request<WeeklyStatus>(`review/weekly?offset=${offset}`,{signal:generation.current?.signal});
      setPages(rows=>[...rows,...page.history]);setNext(page.next_offset);
    } catch(e){if(!generation.current?.signal.aborted)setError((e as Error).message);}finally{setPaging(false);}
  }
  const active=value?.active,pause=value?.agent_pause,newest=value?.history||[],history=[...new Map([...newest,...pages.filter(run=>!newest.some(row=>row.run_id===run.run_id))].map(run=>[run.run_id,run])).values()];
  const last=history.find(run=>run.status!=='partial')||history[0],selected=history.find(run=>run.run_id===report);
  const signIn=pause?.kind==='auth'||value?.gate.state==='signed out';
  const summary=active?.stage==='summary';
  const title=active?.state==='failed'?(summary?'Report pending':'Review couldn’t finish'):
    active?.state==='closing'?'Closing partial run':signIn&&active?'Sign in to resume':
    pause?.kind==='usage_limit'&&active?'Paused for usage limit':summary?'Report pending':
    active?.state==='waiting'?'Review waiting':active?'Review running':last?.status==='empty'&&!last.history_errors?.length?'Nothing to review':'Weekly review';
  const action=!active?'start':active.state==='failed'?active.retry_action||'retry':null;
  const diagnostics=active||history[0];
  return <section className="page server-review weekly-review">
    <PageHeader title="Review"/><ReviewSections weekly onHistory={onHistory} select={weekly=>{if(!weekly)onEntries();}}/>
    <div className="server-review-content">
      {error&&<p className="live-error" role="alert">{error} <button className="button" onClick={()=>{setError('');void refresh(generation.current?.signal);}}>Refresh</button></p>}
      {selected?.report?<><button className="button" onClick={()=>setReport('')}>All runs</button><RunReport run={selected}/></>:<>
        <section className="panel panel-body weekly-status" aria-label="Review status"><div role="status">
          <h2>{title}</h2>
          <p className="live-hint">{!value?'Loading weekly review…':value.enabled?`Runs on ${value.day}s when the ledger is idle.`:'Automatic reviews are off. You can start a review any time.'}</p>
          {title==='Nothing to review'&&<p>No audit lines are waiting for a verdict.</p>}
          {active&&<><p>{counts(active)}{active.batch_count>0&&` · Batch ${Math.min(active.committed_batches+1,active.batch_count)} of ${active.batch_count}`}</p>
            {active.state==='running'&&active.batch_count>0&&<progress aria-label="Batches checked" value={active.committed_batches} max={active.batch_count}/>}
            {foreground&&active.state==='running'&&<p>Foreground work is waiting. It will run after this batch.</p>}
            {signIn?<p>Sign in to {agent.reviewer?.adapter?.display_name||active.adapter} on the server. {['openai','anthropic'].includes(active.adapter)?
                <>Set the reviewer’s API key in the server environment configured by <code>local.json</code>.</>:
                <>Run <code>{agent.reviewer?.login_command||'make agent-login ROLE=review'}</code>.</>}</p>:
              pause&&<p>Weekly review resumes automatically after {formatTimestamp(pause.resumes_at)}.</p>}
            {active.reason&&<p>{active.reason}</p>}
          </>}
          {!!diagnostics?.missing_actors?.length&&<p><a href={`${import.meta.env.BASE_URL}queue/`}>{plural(diagnostics.missing_actors.length,'change')} awaiting attribution</a></p>}
          {diagnostics?.history_errors?.map(detail=><p key={detail}>{detail}</p>)}
        </div><div className="review-action-row">
          {value&&action&&<button className="button primary" disabled={busy} onClick={()=>void act(action)}>{action==='start'?'Review now':summary?'Retry summary':'Retry'}</button>}
          {active?.state==='failed'&&<button className="button" disabled={busy} onClick={()=>void act('close-partial')}>Close partial run</button>}
        </div></section>
        {last&&<section className="panel"><div className="panel-header weekly-status"><h2>Last run</h2>{last.report&&<button className="button" onClick={()=>setReport(last.run_id)}>View report</button>}</div>
          <dl className="panel-body weekly-facts">{Object.entries({Finished:formatTimestamp(last.ended),Reviewer:reviewer(last),Period:period(last),'Lines checked':last.checked,'Issues found':last.issues}).map(([label,detail])=><div key={label}><dt>{label}</dt><dd>{detail}</dd></div>)}</dl></section>}
        <section className="panel"><div className="panel-header"><h2>Run history</h2></div><div className="panel-body">
          {history.length?<ol className="weekly-history">{history.map(run=><li key={run.run_id}><div><strong>{formatTimestamp(run.ended)}</strong><p>{run.status==='empty'?'Nothing to review':run.status==='partial'?'Closed partial':'Completed'}</p></div>
            <div>{reviewer(run)}</div><div>{period(run)}</div><div>{counts(run)}</div>{run.report&&<button className="button" onClick={()=>setReport(run.run_id)}>View report</button>}</li>)}</ol>:<p className="live-hint">No runs recorded yet.</p>}
          {(pages.length?next:value?.next_offset)!=null&&<button className="button" disabled={paging} onClick={()=>void more((pages.length?next:value?.next_offset)!)}>Show older runs</button>}
        </div></section>
      </>}
    </div>
  </section>;
}

function RunReport({run}:{run:FinishedRun}) {
  const report=run.report!;
  return <><div className="panel panel-body"><h2>Weekly review report</h2><p className="live-hint">{formatTimestamp(run.ended)} · {period(run)} · {counts(run)}</p></div>
    <div className="weekly-report-grid">
      <section className="panel" aria-label="Decisions"><div className="panel-header"><h2>Decisions</h2></div><div className="panel-body">
        {report.decisions.length?<ol className="weekly-report-list">{report.decisions.map(item=><li key={item.id} value={item.number}>
          <h3><a href={item.href}>{item.title||item.decide}</a></h3>
          {item.first_raised&&<p className="live-hint">Raised before · {formatTimestamp(item.first_raised)}</p>}
          {item.resolution_href&&<p><a href={item.resolution_href}>Resolved</a></p>}
          {item.note&&<p>{item.note}</p>}
          <p><a href={item.source_href}>Cited record</a></p><ReviewEvidence files={item.evidence}/>
          {item.suggestion&&<p><strong>Suggested fix</strong> · {item.suggestion}</p>}
          {item.title&&<p><strong>Question</strong> · {item.decide}</p>}
        </li>)}</ol>:<p className="live-hint">No decisions needed.</p>}
      </div></section>
      <div className="weekly-report-side">
        <section className="panel" aria-label="What was fine"><div className="panel-header"><h2>What was fine</h2></div><div className="panel-body"><ol className="weekly-report-list">
          {report.fine.map(group=><li key={group.number} value={group.number}><strong>{plural(group.lines.length,'line')}</strong> · {group.reason}</li>)}
        </ol></div></section>
        <section className="panel" aria-label="Patterns"><div className="panel-header"><h2>Patterns</h2></div><div className="panel-body"><ol className="weekly-report-list">
          {report.patterns.map(pattern=><li key={pattern.number} value={pattern.number}>{pattern.note}{pattern.suggested_change&&<p><strong>Suggested change</strong> · {pattern.suggested_change}</p>}</li>)}
        </ol></div></section>
      </div>
    </div>
  </>;
}
