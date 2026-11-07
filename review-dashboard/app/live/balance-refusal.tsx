import type {ReactNode} from 'react';
import {dashboard} from '../data';
import {formatCalendarDate} from '../date-time';
import {money} from '../ui';
export type BalanceRefusal = {
  kind:'confirmed_balance';restriction?:'locked_posting'|'date_window'|'same_day_order';account:string;account_label:string;date:string;entry_id:string;
  source:{kind:'statement'|'balance';label:string;date:string;path?:string};
  commodity:string;asserted:string;calculated:string;difference:string;detail:string;
  possible_duplicates:{entry_id:string;date:string;description:string;amount:string;commodity:string}[];
  other_problems?:string[];  // what else the save would break; the detail lists them too
};
export type BalanceLock={posting:number;account:string;account_label:string;date:string;entry_id:string;source:BalanceRefusal['source'];start:string|null;end:string;previous_id:string|null};
export function balanceSource(source:BalanceRefusal['source'], sentence=false) {
  const article=sentence?'The':'the';
  return source.kind==='statement'?`${article} ${source.label} statement to ${formatCalendarDate(source.date)}`:`${article} confirmed balance on ${formatCalendarDate(source.date)}`;
}
export function BalanceNotice({refusal:r,children}:{refusal:BalanceRefusal;children?:ReactNode}) {
  const month=new Intl.DateTimeFormat(dashboard.meta.ledger.locale||navigator.language,{month:'long'}).format(new Date(r.date+'T12:00:00'));
  return <aside className="live-notice failure balance-refusal" role="alert" aria-label="Confirmed balance refusal"><div>
    <strong>{r.restriction==='locked_posting'?`Amount and paying account are locked by ${balanceSource(r.source)}`:r.restriction?`Not saved: the date must stay between the neighbouring confirmed balances${r.restriction==='same_day_order'?', before the closing entry on the same day':''}`:`Not saved: ${r.account_label}’s ${month} balance is already confirmed`}</strong>
    <p>{balanceSource(r.source,true)}</p>
    <dl className="facts"><dt>Confirmed balance</dt><dd>{money(Number(r.asserted),r.commodity)}</dd><dt>After this change</dt><dd>{money(Number(r.calculated),r.commodity)}</dd><dt>Difference</dt><dd>{money(Number(r.difference),r.commodity)}</dd></dl>
    {r.possible_duplicates.length>0&&<section aria-label="Possible duplicates"><h3>Possible duplicates</h3>{r.possible_duplicates.map(e=><p key={e.entry_id}>{formatCalendarDate(e.date)} · {e.description} · {money(Math.abs(Number(e.amount)),e.commodity)} <a className="button" href={`${import.meta.env.BASE_URL}explorer/?month=${e.date.slice(0,7)}&entry=${e.entry_id}`}>View entry</a></p>)}</section>}
    {children&&<div className="rule-actions">{children}</div>}
    <details><summary>Details</summary><pre>{r.detail}</pre></details>
  </div></aside>;
}
