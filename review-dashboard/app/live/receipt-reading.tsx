import {useWriteCompletion,useCommittedError} from './write-completion';
import {ActionFeedback,useWriteOwner} from './pending-actions';
import {useState} from 'react';
import {dayLabel,money} from '../ui';
import {dashboard} from '../data';
import {BalanceNotice,type BalanceRefusal} from './balance-refusal';
import {api,ApiError,type Draft} from './api';
export default function ReceiptReading({draft,onAction,compact=false}:{draft:Draft;onAction:()=>Promise<void>;compact?:boolean}) {
 const owner=useWriteOwner(`receipt:${draft.id}`);
 const [refusal,setRefusal]=useState<BalanceRefusal|undefined>(undefined);
 const [error,setError]=useCommittedError(write=>write.path===`drafts/${draft.id}/receipt-choice`),[busy,setBusy]=useState(false),[more,setMore]=useState(false),reading=draft.receipt_reading;
 useWriteCompletion<Draft>(write=>write.path===`drafts/${draft.id}/receipt-choice`,write=>{setError('');setRefusal(write.result.receipt_reading?.refusal);void onAction().catch(e=>setError((e as Error).message));});
 if(!reading)return null;
 const answer=reading.answer;
 async function choose(choice:'use'|'keep'){setBusy(true);setError('');setRefusal(undefined);try{await api.receiptChoice(draft.id,choice,{writeOwner:owner});}catch(e){if(e instanceof ApiError&&e.refusal)setRefusal(e.refusal);else setError((e as Error).message);}finally{setBusy(false);}}
 const totals=Object.entries(reading.entry_totals||{}).map(([code,value])=>money(Number(value),code)).join(' + ');
 const detail=answer?<p>The receipt says {answer.date?dayLabel(answer.date):'no date'}, {answer.amount&&answer.currency?money(Number(answer.amount),answer.currency):answer.amount?'an amount without a currency':'no amount'}{answer.paying_account?`, paid with ${dashboard.meta.accounts[answer.paying_account]?.label||answer.paying_account}`:''}; the entry says {reading.entry?dayLabel(reading.entry.date):''}, {totals}. Use the receipt’s details, or keep the entry?</p>:null;
 return <section aria-label="Receipt reading" className={compact?'receipt-inline':'live-notice'}><div>{!compact&&<h3>Receipt reading</h3>}{reading.state==='question'&&answer?<>
 {compact?<p>Receipt: {answer.amount&&answer.currency?money(Number(answer.amount),answer.currency):'details differ'} · Entry: {totals}</p>:detail}
 <div className="detail-footer"><button className="button primary" disabled={busy} onClick={()=>void choose('use')}>Use the receipt</button><button className="button" disabled={busy} onClick={()=>void choose('keep')}>Keep the entry</button></div>
 {compact&&<><button className="text-button" aria-expanded={more} onClick={()=>setMore(!more)}>{more?'Less':'More'}</button>{more&&<>{detail}<p>Both answers keep the attached receipt.</p></>}</>}
 </>:<p role="status">{reading.message||(['queued','reading'].includes(reading.state)?'The payment is saved. Its filed receipt is waiting to be read.':'Receipt details are being saved.')}</p>}{(refusal||reading.refusal)&&<BalanceNotice refusal={(refusal||reading.refusal)!}/>} <div className="action-error-slot">{error&&<p className="live-error" role="alert">{error}</p>}</div><ActionFeedback owner={owner} scope="entry" path={`drafts/${draft.id}/receipt-choice`}/></div></section>;
}
