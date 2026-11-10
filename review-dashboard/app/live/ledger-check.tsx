import {useCallback,useEffect,useRef,useState} from 'react';
import {Icon,announce,copyText} from '../ui';
import {formatCalendarDate,formatTimestamp} from '../date-time';
import {dashboard} from '../data';
import {accountFieldLabel} from '../account-labels';
import {entryHref} from '../rules';
import {request,type Draft} from './api';

/** hledger's background second check of each commit (server `ledger_check.py`). */
export type LedgerCheckItem = {
  id:'ledger-check';commit:string;checked_at:string;kind:string;title:string;summary:string;details:string;
  app_problem:boolean;explanation:string|null;
  /** guess: hledger didn't name it (the newest changed entry, or one posting to an asserted account). */
  entry:{id:string;date:string|null;description:string|null;guess:boolean}|null;
  /** changes: known since the last good check; first (no check has passed); missing (that commit left the history). */
  entries:{id:string;date:string;description:string;added:boolean}[];more:number;since:string|null;changes:'known'|'first'|'missing';changes_known:boolean;
  accounts:string[]|null;block:{accounts:string[]|null;reason:string};
  fix:{draft_id:string;state:'queued'|'working'|'ready'|'question'|'failed'|'resolved'|null;question:string|null;message:string|null}|null;
};
export type LedgerCheckStatus = {available:boolean;checking:boolean;outcome?:'pass'|'fail'|null;checked_at?:string|null;item:LedgerCheckItem|null};
export const ledgerCheckApi = {
  status:(signal?:AbortSignal)=>request<LedgerCheckStatus>('ledger-check',{signal}),
  // Check again waits for hledger on the server; a large ledger takes a few seconds.
  again:()=>request<LedgerCheckStatus>('ledger-check/run',{method:'POST'},130000),
  fix:()=>request<Draft>('ledger-check/fix',{method:'POST'},30000),
};

/** First-run import also follows late check results. One read at a time, with
 * three seconds after completion; an error keeps the last known status. */
export function useLedgerCheckStatus(){
  const [status,setStatus]=useState<LedgerCheckStatus|null>(null),generation=useRef(0);
  const update=useCallback((value:LedgerCheckStatus)=>{generation.current++;setStatus(value);},[]);
  useEffect(()=>{
    let alive=true,timer:ReturnType<typeof setTimeout>|undefined;
    const controller=new AbortController();
    async function poll(){
      const run=++generation.current;
      try{const value=await ledgerCheckApi.status(controller.signal);if(alive&&run===generation.current)setStatus(value);}
      catch{/* A transient error cannot remove an existing refusal. */}
      finally{if(alive)timer=setTimeout(poll,3000);}
    }
    void poll();
    return()=>{alive=false;controller.abort();clearTimeout(timer);};
  },[]);
  return [status,update] as const;
}
export const LEDGER_CHECK_HREF=`${import.meta.env.BASE_URL}queue/?check=ledger`;

/** Why statement Apply (and so a confirmed balance) waits for this account, or null. */
export function ledgerCheckBlock(status:LedgerCheckStatus|null|undefined,account:string|undefined){
  const item=status?.item;
  if(!item||!account)return null;
  return item.block.accounts===null||item.block.accounts.includes(account)?item.block.reason:null;
}

/** The persistent banner on every screen. It sits in its own fixed strip at the foot of the
 *  window, so its arrival never moves a heading, an input or the page's main action. */
export function LedgerCheckBanner({status}:{status:LedgerCheckStatus|null}){
  const item=status?.item;
  if(!item)return null;
  return <aside className="live-notice failure ledger-check-banner" role="alert" aria-label="Ledger check problem"><Icon name="alert"/><p><strong>The second ledger check found a problem.</strong> <span className="ledger-check-banner-title">{item.title}.</span></p><a className="button" href={LEDGER_CHECK_HREF}>Open Ledger check</a></aside>;
}

const label=(account:string)=>accountFieldLabel(account,dashboard?.meta?.accounts||{});
const correctHref=(id:string)=>`${import.meta.env.BASE_URL}queue/?correct=${id}&back=${encodeURIComponent(`${import.meta.env.BASE_URL}queue/?check=ledger`)}`;

/** The "Ledger check" item in Queue › Needs you. `onChange` releases Queue's interaction
 *  snapshot and refreshes it, so the fix draft a link opens is shown as it is now. */
export function LedgerCheckDetail({status,onChange}:{status:LedgerCheckStatus;onChange:()=>Promise<void>}){
  const item=status.item;
  const [busy,setBusy]=useState<''|'again'|'fix'|'copy'>(''),[error,setError]=useState(''),[note,setNote]=useState('');
  async function act(kind:'again'|'fix',fn:()=>Promise<unknown>,done:string){setBusy(kind);setError('');setNote('');try{await fn();await onChange();setNote(done);announce(done);
    // A pass closes the item and removes the button that had focus: the item's heading takes it.
    if(kind==='again')requestAnimationFrame(()=>{if(!document.activeElement||document.activeElement===document.body)document.querySelector<HTMLElement>('.ledger-check-detail h2')?.focus({preventScroll:true});});}catch(e){setError((e as Error).message);}finally{setBusy('');}}
  if(!item)return <article className="panel draft-detail ledger-check-detail" aria-label="Ledger check"><div className="panel-header"><div><h2 tabIndex={-1}>Ledger check</h2><p>{status.checked_at?`Checked ${formatTimestamp(status.checked_at)}`:'Not checked yet'}</p></div></div><div className="panel-body"><p role="status">{!status.available?'hledger isn’t installed, so there is no second check. Saves are checked by AgentExpense’s own strict check.':<><Icon name="check"/> {status.checking?'Checking the latest save…':'The second check passed. Nothing needs you.'}</>}</p></div></article>;
  const fix=item.fix,working=fix&&['queued','working'].includes(fix.state||'');
  const again=<button className="button" disabled={!!busy} onClick={()=>void act('again',ledgerCheckApi.again,'Checked again.')}>{busy==='again'?'Checking…':'Check again'}</button>;
  const copy=async()=>{setBusy('copy');const ok=await copyText(`${item.title}\n${item.summary}\n${item.explanation||''}\nCommit: ${item.commit}\nChecked: ${item.checked_at}\n\n${item.details}`);setBusy('');const done=ok?'Details copied.':'The browser blocked copying the details.';setNote(done);announce(done);};
  return <article className="panel draft-detail ledger-check-detail" aria-label="Ledger check" data-kind={item.kind}><div className="panel-header"><div><h2 tabIndex={-1}>Ledger check</h2><p>Checked {formatTimestamp(item.checked_at)}{status.checking?' · checking again…':''}</p></div></div><div className="panel-body draft-detail-body">
    <section className="queue-next" aria-label="Next step">
      <p><strong>{item.title}</strong></p><p>{item.summary}</p>
      {item.app_problem?<><p className="ledger-check-app">{item.explanation}</p><div className="detail-footer">{again}<span className="footer-actions"><button className="button primary" disabled={!!busy} onClick={()=>void copy()}><Icon name="copy"/>Copy details</button></span></div></>
      :<>{fix&&<p className="live-hint" role="status">{working?<>The agent is working on a fix. <a href={`${import.meta.env.BASE_URL}queue/?draft=${fix.draft_id}&edit=1&back=${encodeURIComponent(LEDGER_CHECK_HREF)}`} onClick={()=>void onChange()}>Open it</a></>:fix.state==='ready'?<>The agent proposed a fix. <a href={`${import.meta.env.BASE_URL}queue/?draft=${fix.draft_id}&edit=1&back=${encodeURIComponent(LEDGER_CHECK_HREF)}`} onClick={()=>void onChange()}>Review the fix</a>; nothing changes until you save it.</>:<>{fix.question||fix.message||'The agent couldn’t propose a fix.'} <a href={`${import.meta.env.BASE_URL}queue/?draft=${fix.draft_id}&edit=1&back=${encodeURIComponent(LEDGER_CHECK_HREF)}`} onClick={()=>void onChange()}>Open the entry</a></>}</p>}
        <div className="detail-footer">{again}<span className="footer-actions">{item.entry&&<a className="button" href={correctHref(item.entry.id)}><Icon name="pencil"/>Edit the entry</a>}{item.entry&&!fix&&<button className="button primary" disabled={!!busy} onClick={()=>void act('fix',ledgerCheckApi.fix,'Asked the agent for a fix. It appears in Queue for you to review.')}>{busy==='fix'?'Asking…':'Ask the agent to fix it'}</button>}</span></div></>}
      <p className="live-hint ledger-check-note">{note}</p>
      {error&&<p className="live-error" role="alert">{error}</p>}
    </section>
    {!item.app_problem&&<section aria-label="Changed entries"><h3>{item.changes==='known'?'Changed since the last good check':'Changed entries'}</h3>
      {item.since&&<p className="live-hint">Last good check {formatTimestamp(item.since)}</p>}
      {item.entries.length?<ul className="ledger-check-entries">{item.entries.map(e=><li key={e.id}><a href={entryHref(e.id)}>{e.description}</a><span className="live-hint">{formatCalendarDate(e.date)}{e.added?' · added':' · changed'}</span></li>)}{item.more>0&&<li className="live-hint">and {item.more} more</li>}</ul>
        :<p className="live-hint">{item.changes==='known'?'No entries changed since the last good check.':item.changes==='missing'?'The last good check’s commit is no longer in the ledger’s history, so the changes can’t be listed.':'No earlier check passed, so the changes can’t be listed.'}{item.entry&&!item.entry.guess?<> The finding names <a href={entryHref(item.entry.id)}>{item.entry.description||'an entry'}</a>.</>:null}</p>}
      {item.entry?.guess&&<p className="live-hint">hledger doesn’t name the entry: Edit the entry and the agent start from <a href={entryHref(item.entry.id)}>{item.entry.description||'the newest change'}</a>, the likeliest one.</p>}
    </section>}
    <section aria-label="Statements waiting"><h3>Statements waiting</h3><p>{item.block.accounts?<>Statement Apply and confirmed balances wait for {item.block.accounts.map(label).join(', ')} until a check passes.</>:<>Statement Apply and confirmed balances wait for every account until a check passes: the finding doesn’t name an account.</>}</p></section>
    <details className="ledger-check-details"><summary>Details</summary><p className="live-hint">hledger’s own words, for commit {item.commit.slice(0,12)}:</p><pre>{item.details}</pre></details>
  </div></article>;
}
