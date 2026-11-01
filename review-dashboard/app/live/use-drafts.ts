import { useCallback, useEffect, useRef, useState } from 'react';
import { reloadData } from '../data';
import { api, type Draft, request } from './api';
import type { AgentStatus } from './agent-status';
import { statementsApi, type StatementSession } from './statements-api';
export const statementNeedsAttention = (s: StatementSession) => ['locked', 'wrong_file', 'couldnt_read', 'found', 'review', 'failed'].includes(s.state) && !s.discarded_until && !s.duplicate_of;
export const needsAttention = (d: Draft) => d.state === 'question' || d.state === 'failed' || (d.state === 'added' && !!d.statement_file && !d.handover_session);
export const isRunning = (d: Draft) => d.state === 'queued' || d.state === 'working';
export const newestFirst = (drafts: Draft[]) => [...drafts].sort((a,b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
export const pollDelay = (drafts: Draft[]) => drafts.some(isRunning) ? 3000 : 30000;
export function useDrafts() {
  const [agent,setAgent] = useState<AgentStatus>({state:'checking',reason:null});
  const [statements,setStatements] = useState<StatementSession[]>([]);
  const [undo,setUndo]=useState<{id:string;until:number}[]>([]),[undoError,setUndoError]=useState('');
  const [drafts, setDrafts] = useState<Draft[]>([]), [online, setOnline] = useState<boolean | null>(null), [error, setError] = useState('');
  const [reloadError, setReloadError] = useState('');
  const latest = useRef<Draft[]>([]), reloaded = useRef(new Set<string>()), busy = useRef<Promise<void> | null>(null), again = useRef(false), mounted = useRef(false);
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
      const [listed, status, statementList] = await Promise.all([api.list(controller.current.signal), request<AgentStatus>('agent-status', {signal:controller.current.signal}), statementsApi.list(controller.current.signal)]);
      const next = newestFirst(listed);
      if (mounted.current) {setAgent(status);setStatements(statementList.sessions);}
      if (!mounted.current) return;
      setOnline(true); setError(''); latest.current=next; setDrafts(next);
      const additions = next.filter(d => d.state === 'added' && d.result?.entry_id && !reloaded.current.has(d.id));
      if (additions.length) {
        try { await reloadData(); if (mounted.current) setReloadError(''); additions.forEach(d => reloaded.current.add(d.id)); }
        catch { if (mounted.current) setReloadError('The entry was added, but the dashboard could not refresh. Retry refresh below.'); }
      }
    } catch (e) { if (mounted.current && !controller.current?.signal.aborted) { setOnline(false); setError(e instanceof Error ? e.message : String(e)); } }
    } while(again.current && mounted.current && !leaving.current && !document.hidden);
    })().finally(()=>{busy.current=null;});
    return busy.current;
  }, []);
  useEffect(() => { mounted.current = true; leaving.current=false; return () => { mounted.current = false; leaving.current=true; controller.current?.abort(); }; }, [refresh]);
  const running=drafts.some(isRunning);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let active=true, generation=0;
    const tick = async () => {
      const run=++generation;
      clearTimeout(timer);
      if(document.hidden || leaving.current) {controller.current?.abort();return;}
      await refresh();
      if(active && run===generation && !document.hidden) timer=setTimeout(tick,pollDelay(latest.current));
    };
    const focus = () => { void tick(); };
    const hide=()=>{leaving.current=true;generation++;clearTimeout(timer);controller.current?.abort();};
    const show=()=>{leaving.current=false;void tick();};
    void tick();
    window.addEventListener('pagehide',hide);window.addEventListener('pageshow',show);
    window.addEventListener('focus',focus);document.addEventListener('visibilitychange',focus);window.addEventListener('drafts-changed',focus);
    return () => {active=false;generation++;clearTimeout(timer);window.removeEventListener('pagehide',hide);window.removeEventListener('pageshow',show);window.removeEventListener('focus',focus);document.removeEventListener('visibilitychange',focus);window.removeEventListener('drafts-changed',focus);};
  },[refresh,running]);
  useEffect(()=>{if(!undo.length)return;const timer=setTimeout(()=>setUndo(xs=>xs.filter(x=>x.until>Date.now())),Math.max(0,Math.min(...undo.map(x=>x.until))-Date.now()));return()=>clearTimeout(timer);},[undo]);
  async function discard(id:string){await api.discard(id);setUndo(xs=>[...xs,{id,until:Date.now()+30000}]);await refresh();}
  async function restore(id:string){try{setUndoError('');await api.restore(id);setUndo(xs=>xs.filter(x=>x.id!==id));await refresh();}catch(e){setUndoError(`Could not undo Discard. ${(e as Error).message}`);}}
  return {agent,statements,undo,undoError,discard,restore, drafts, online, error, reloadError, refresh, attention: drafts.filter(needsAttention).length + statements.filter(statementNeedsAttention).length };
}
export type LiveDrafts = ReturnType<typeof useDrafts>;
export const draftsChanged = () => window.dispatchEvent(new Event('drafts-changed'));
