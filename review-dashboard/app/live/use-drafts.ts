import { useCallback, useEffect, useRef, useState } from 'react';
import { reloadData } from '../data';
import { api, type Draft, request, isRemoved, rememberRemoved } from './api';
import type { AgentStatus } from './agent-status';
import { statementsApi, type StatementSession } from './statements-api';
export const statementNeedsAttention = (s: StatementSession) => ['account_needed', 'currency_mismatch', 'locked', 'wrong_file', 'couldnt_read', 'found', 'review', 'failed'].includes(s.state) && !s.discarded_until && !s.duplicate_of;
export const needsAttention = (d: Draft) => d.receipt_reading?.state==='question' || d.state === 'question' || d.state === 'failed' || (d.state === 'added' && !!d.statement_file && !d.handover_session);
export const isRunning = (d: Draft) => ['queued','reading','ready'].includes(d.receipt_reading?.state||'') || d.state === 'queued' || d.state === 'working';
export const newestFirst = (drafts: Draft[]) => [...drafts].sort((a,b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
export const pollDelay = (drafts: Draft[], statements: StatementSession[] = []) => drafts.some(isRunning) || statements.some(s=>['reading','applying','agent_waiting'].includes(s.state)) ? 3000 : 30000;
export function useDrafts() {
  const [agent,setAgent] = useState<AgentStatus>({state:'checking',reason:null});
  const [statements,setStatements] = useState<StatementSession[]>([]);
  const [discarded,setDiscarded]=useState(''),[trashCount,setTrashCount]=useState(0);
  const [drafts, setDrafts] = useState<Draft[]>([]), [online, setOnline] = useState<boolean | null>(null), [error, setError] = useState('');
  const [reloadError, setReloadError] = useState('');
  const latest = useRef<Draft[]>([]), latestStatements = useRef<StatementSession[]>([]), reloaded = useRef(new Set<string>()), busy = useRef<Promise<void> | null>(null), again = useRef(false), mounted = useRef(false);
  const leaving=useRef(false), controller=useRef<AbortController | null>(null);
  const refresh = useCallback(async () => {
    if(leaving.current || document.hidden) return;
    if (busy.current) { again.current=true; return busy.current; }
    busy.current = (async () => { do {
    again.current=false;
    controller.current=new AbortController();
    try {
      // Let this cheap read finish across navigation. Aborting a health fetch
      // during document teardown can surface a WebKit access-control error.
      const health = await api.health(); if (!health.ok) throw new Error('The live server is unavailable.');
      if (!mounted.current || leaving.current || document.hidden || controller.current.signal.aborted) continue;
      const [listed, status, statementList, trash] = await Promise.all([api.list(controller.current.signal), request<AgentStatus>('agent-status', {signal:controller.current.signal}), statementsApi.list(controller.current.signal),request<{items:unknown[]}>('trash',{signal:controller.current.signal})]);
      // A write/focus refresh queued while these reads were in flight. Publish
      // only the follow-up snapshot, never the older rows or receipt facts.
      if(again.current)continue;
      if(!mounted.current||leaving.current||document.hidden||controller.current.signal.aborted)return;
      for(const d of listed)if(d.trashed_at)rememberRemoved(d);
      const next = newestFirst(listed.filter(d=>!d.trashed_at&&!isRemoved(d.id)));
      if (mounted.current) {setTrashCount(trash.items.length);setAgent(status);setStatements(statementList.sessions.filter(s=>!s.trashed_at&&!isRemoved(s.id)));latestStatements.current=statementList.sessions.filter(s=>!s.trashed_at&&!isRemoved(s.id));}
      if (!mounted.current) return;
      setOnline(true); setError(''); latest.current=next; setDrafts(next);
      const additions = next.filter(d => d.state === 'added' && d.result?.entry_id && !reloaded.current.has(d.id));
      if (additions.length) {
        try { await reloadData(); if (mounted.current) setReloadError(''); additions.forEach(d => reloaded.current.add(d.id)); }
        catch { if (mounted.current) setReloadError('The entry was added, but the dashboard could not refresh. Retry refresh below.'); }
      }
    } catch (e) { if (mounted.current && !again.current && !controller.current?.signal.aborted) { setOnline(false); setError(e instanceof Error ? e.message : String(e)); } }
    } while(again.current && mounted.current && !leaving.current && !document.hidden);
    })().finally(()=>{busy.current=null;});
    return busy.current;
  }, []);
  useEffect(() => { mounted.current = true; leaving.current=false; return () => { mounted.current = false; leaving.current=true; controller.current?.abort(); }; }, [refresh]);
  const running=drafts.some(isRunning)||statements.some(s=>['reading','applying','agent_waiting'].includes(s.state));
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let active=true, generation=0;
    const tick = async () => {
      const run=++generation;
      clearTimeout(timer);
      if(document.hidden || leaving.current) {controller.current?.abort();return;}
      await refresh();
      if(active && run===generation && !document.hidden) timer=setTimeout(tick,pollDelay(latest.current,latestStatements.current));
    };
    const focus = () => { void tick(); };
    const hide=()=>{leaving.current=true;generation++;clearTimeout(timer);controller.current?.abort();};
    const show=()=>{leaving.current=false;void tick();};
    void tick();
    window.addEventListener('pagehide',hide);window.addEventListener('pageshow',show);
    window.addEventListener('focus',focus);document.addEventListener('visibilitychange',focus);window.addEventListener('drafts-changed',focus);
    return () => {active=false;generation++;clearTimeout(timer);window.removeEventListener('pagehide',hide);window.removeEventListener('pageshow',show);window.removeEventListener('focus',focus);document.removeEventListener('visibilitychange',focus);window.removeEventListener('drafts-changed',focus);};
  },[refresh,running]);
  useEffect(()=>{const restored=()=>setDiscarded('');window.addEventListener('item-restored',restored);return()=>window.removeEventListener('item-restored',restored);},[]);
  async function discard(id:string){await api.discard(id);setDiscarded(id);latest.current=latest.current.filter(d=>d.id!==id);setDrafts(latest.current);await refresh();}
  async function restore(id:string){await api.restore(id);setDiscarded('');await refresh();}
  return {agent,statements,trashCount,discarded,discard,restore, drafts, online, error, reloadError, refresh, attention: drafts.filter(d=>(d.state!=='added'||!!d.statement_file&&!d.handover_session)&&needsAttention(d)).length + statements.filter(statementNeedsAttention).length };

}
export type LiveDrafts = ReturnType<typeof useDrafts>;
export const draftsChanged = () => window.dispatchEvent(new Event('drafts-changed'));
