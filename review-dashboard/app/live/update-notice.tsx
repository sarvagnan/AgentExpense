import {request} from './api';
import {formatTimestamp} from '../date-time';

export type UpdateStatus = {enabled:boolean;current:string;latest?:string|null;checkedAt?:string|null;failed?:boolean;command?:string};
let known: UpdateStatus | null = null, pending: Promise<void> | null = null;

/** Read the server's recorded update check (it never contacts the remote on request).
 *  The result is kept for the next Settings view and never changes an open one. */
export function refreshUpdateStatus() {
  pending ??= request<UpdateStatus>('update').then(value => {known = value;}, () => undefined).finally(() => {pending = null;});
  return pending;
}
export const knownUpdateStatus = () => known;

export function UpdateNotice({status}: {status: UpdateStatus | null}) {
  if (!status?.enabled) return null;
  const command = status.command || 'make update';
  return <section className="settings-section update-notice" data-update-notice={status.latest ? 'available' : status.failed ? 'failed' : 'current'}><h3>Updates</h3>
    {status.latest ? <><p><strong>AgentExpense {status.latest} is available.</strong> You have {status.current}.</p><p className="live-hint">Run <code>{command}</code> in the AgentExpense folder on the computer that runs it.</p></>
      : <p className="live-hint">AgentExpense {status.current}. {status.failed ? 'The last check couldn’t read the list of releases; it tries again daily.' : status.checkedAt ? `No newer release as of ${formatTimestamp(status.checkedAt)}.` : 'It checks for a newer release once a day.'}</p>}
  </section>;
}
