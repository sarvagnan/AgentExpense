import type {ReactNode} from 'react';
import type {AgentPause} from './api';
import {formatTimestamp} from '../date-time';
import { Icon } from '../ui';
export type AgentStatus = {agent_pause?:AgentPause|null;state: 'checking' | 'ready' | 'signed out' | 'paused' | 'unavailable'; reason: string | null; adapter?: {id:string;display_name:string;pinned_version:string;isolation:boolean}; model?:string; effort?:string; login_command?:string; isolation_warning?:string|null};
export const agentBlocked = (status?: AgentStatus) => status?.state === 'signed out' || status?.state === 'paused';
/** Where a notice appears: Add keeps the typed words and still reads built-in statements. */
export type NoticePlace = 'add' | 'queue' | 'settings';
type Copy = {state:string; title:string; body:ReactNode; detail?:string|null};
const BUILT_IN = 'Statements read by the built-in reader still work.';
const login = (status: AgentStatus) => ['openai','anthropic'].includes(status.adapter?.id || '')
  ? <>Set the API key environment variable or key file configured in <code>local.json</code> on the computer that runs AgentExpense.</>
  : <>Run <code>{status.login_command || 'make agent-login'}</code> on the computer that runs AgentExpense.</>;
/**
 * One wording for every screen: a plain title and sentence, with any technical
 * diagnostic behind a disclosure. Add adds only what differs there (the words
 * stay, and built-in statement readers still work).
 */
export function agentNoticeCopy(status: AgentStatus | undefined, place: NoticePlace): Copy | null {
  if (!status) return null;
  const add = place === 'add';
  const pause = status.agent_pause;
  if (pause) {
    const auth = pause.kind === 'auth';
    return {state:'outage', title:auth ? 'The agent isn’t signed in.' : `The agent is paused (${pause.reason}).`,
      body:<>{auth ? 'Sign-in and self-check are retried every 30 seconds.' : `Resumes at ${formatTimestamp(pause.resumes_at)}.`} {add ? `Your words and files stay here. ${BUILT_IN}` : 'Items keep their place in Queue. You can finish one yourself.'}</>};
  }
  if (status.state === 'signed out') return {state:status.state, title:'The agent isn’t signed in.', body:<>{add && `${BUILT_IN} `}{login(status)}</>};
  if (status.state === 'paused') return {state:status.state, title:'Add is paused.', body:<>An agent update changed what it can do. Add is paused until AgentExpense supports this update.{add && ` ${BUILT_IN}`}</>, detail:status.reason};
  // An unavailable check retries with the next item, so Add stays usable and says nothing.
  if (status.state === 'unavailable' && !add) return {state:status.state, title:'The agent couldn’t be checked.', body:'It will retry on the next item. You can still send an item.', detail:status.reason};
  return null;
}
/** The adapter, model and effort Settings shows beside its Agent status heading. */
export const agentIdentity = (status?: AgentStatus) => status?.adapter ? `${status.adapter.display_name} · ${status.model} · ${status.effort}` : '';
export function AgentNotice({status, place = 'queue', className = ''}: {status?: AgentStatus; place?: NoticePlace; className?: string}) {
  const settings = place === 'settings';
  const warning = status?.isolation_warning ? <p className="live-hint" role="status">{status.isolation_warning}</p> : null;
  const copy = agentNoticeCopy(status, place);
  if (copy) return <>{warning}<aside className={`agent-notice ${className}`.trim()} role="status" data-agent-state={copy.state}><Icon name="alert"/><div>
    <strong>{copy.title}</strong><p>{copy.body}</p>
    {copy.detail && <details className="agent-reason"><summary>Check details</summary><p>{copy.detail}</p></details>}
  </div></aside></>;
  if (!settings) return null;
  if (!status || status.state === 'checking') return <p className="live-hint" role="status">Checking the agent…</p>;
  return <div className="agent-ready">{warning}<strong><Icon name="check"/>Ready</strong><p>The agent is signed in and can read new items.</p></div>;
}
