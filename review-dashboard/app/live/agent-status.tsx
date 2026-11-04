import type {AgentPause} from './api';
import {formatTimestamp} from '../date-time';
import { Icon } from '../ui';
export type AgentStatus = {agent_pause?:AgentPause|null;state: 'checking' | 'ready' | 'signed out' | 'paused' | 'unavailable'; reason: string | null; adapter?: {id:string;display_name:string;pinned_version:string;isolation:boolean}; model?:string; effort?:string; login_command?:string; isolation_warning?:string|null};
export const agentBlocked = (status?: AgentStatus) => status?.state === 'signed out' || status?.state === 'paused';
export function AgentNotice({status, settings = false}: {status?: AgentStatus; settings?: boolean}) {
  const identity = settings && status?.adapter ? <p className="live-hint">{status.adapter.display_name} · {status.model} · {status.effort}</p> : null;
  const warning = status?.isolation_warning ? <p className="live-hint" role="status">{status.isolation_warning}</p> : null;
  if(status?.agent_pause) return <>{identity}{warning}<aside className="agent-notice" role="status" data-agent-state="outage"><Icon name="alert"/><div><strong>Paused: {status.agent_pause.reason}</strong><p>{status.agent_pause.kind==='auth'?'Sign-in and self-check are retried every 30 seconds.':`Resumes at ${formatTimestamp(status.agent_pause.resumes_at)}.`} Items keep their place in Queue. You can finish one yourself.</p></div></aside></>;
  if (!status || status.state === 'checking') return settings ? <p className="live-hint" role="status">Checking the agent…</p> : null;
  if (status.state === 'ready') return settings ? <div className="agent-ready">{identity}{warning}<strong><Icon name="check"/>Ready</strong><p>The agent is signed in and can read new items.</p></div> : null;
  return <>{identity}{warning}<aside className="agent-notice" role="status" data-agent-state={status.state}><Icon name="alert"/><div>
    <strong>{status.state === 'signed out' ? "The agent isn't signed in." : status.state === 'paused' ? 'Add is paused.' : "The agent couldn't be checked."}</strong>
    {status.state === 'signed out' && ['openai','anthropic'].includes(status.adapter?.id || '') ? <p>Set the API key environment variable or key file configured in local.json on the computer that runs AgentExpense.</p> : status.state === 'signed out' ? <p>Run <code>{status.login_command || 'make agent-login'}</code> on the computer that runs AgentExpense.</p> : status.state === 'paused' ? <><p>An agent update changed what it can do. Add is paused until AgentExpense supports this update.</p>{status.reason && <details className="agent-reason"><summary>Check details</summary><p>{status.reason}</p></details>}</> : <p>It will retry on the next item. You can still send an item.</p>}
  </div></aside></>;
}
