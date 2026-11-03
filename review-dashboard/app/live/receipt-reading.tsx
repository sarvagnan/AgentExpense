import {useState} from 'react';
import {dayLabel,money} from '../ui';
import {dashboard} from '../data';
import {api,type Draft} from './api';
export default function ReceiptReading({draft,onAction}:{draft:Draft;onAction:()=>Promise<void>}) {
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),reading=draft.receipt_reading;
  if(!reading)return null;
  const answer=reading.answer;
  async function choose(choice:'use'|'keep'){setBusy(true);setError('');try{await api.receiptChoice(draft.id,choice);await onAction();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  const totals=Object.entries(reading.entry_totals||{}).map(([code,value])=>money(Number(value),code)).join(' + ');
  return <section aria-label="Receipt reading" className="live-notice"><div><h3>Receipt reading</h3>{reading.state==='question'&&answer?<><p>The receipt says {answer.date?dayLabel(answer.date):'no date'}, {answer.amount&&answer.currency?money(Number(answer.amount),answer.currency):answer.amount?'an amount without a currency':'no amount'}{answer.paying_account?`, paid with ${dashboard.meta.accounts[answer.paying_account]?.label||answer.paying_account}`:''}; the entry says {reading.entry?dayLabel(reading.entry.date):''}, {totals}. Use the receipt’s details, or keep the entry?</p><div className="detail-footer"><button className="button primary" disabled={busy} onClick={()=>void choose('use')}>Use the receipt</button><button className="button" disabled={busy} onClick={()=>void choose('keep')}>Keep the entry</button></div></>:<p role="status">{reading.message||(['queued','reading'].includes(reading.state)?'The payment is saved. Its filed receipt is waiting to be read.':'Receipt details are being saved.')}</p>}{error&&<p className="live-error" role="alert">{error}</p>}</div></section>;
}
