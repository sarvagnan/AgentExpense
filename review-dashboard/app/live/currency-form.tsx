import {useState,type ReactNode} from 'react';
import {minorUnits} from '../formatting';
import {money} from '../ui';
import {api,type Draft} from './api';
import {currenciesLinked,positiveDecimal} from './currency-math';

export default function CurrencyForm({draft,onSaved,blocked,discard}:{draft:Draft;onSaved:()=>Promise<void>;blocked:boolean;discard:ReactNode}){
 const r=draft.currency_request!;
 const [charged,setCharged]=useState(''),[rate,setRate]=useState(''),[input,setInput]=useState<{amount?:string;rate?:string}>({}),[estimate,setEstimate]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const transfer=r.kind==='transfer';
 const digits=minorUnits(r.currency);
 function change(value:string,from:'amount'|'rate'){
  const result=currenciesLinked(r.amount,value,from,digits);
  setInput({[from]:value});setCharged(from==='amount'?value:result.amount);setRate(from==='rate'?value:result.rate);setError('');
 }
 const valid=positiveDecimal(charged)&&positiveDecimal(rate);
 return <form className="currency-form form-block" onSubmit={async e=>{e.preventDefault();if(!valid||blocked)return;setBusy(true);setError('');try{await api.currency(draft.id,{...input,estimate});await onSaved();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>
 <h2>{transfer?`Sent ${money(Number(r.amount),r.sourceCurrency)} from ${r.sourceLabel}. How much arrived in ${r.currency} in ${r.accountLabel}?`:`Paid in ${r.sourceCurrency} on your ${r.accountLabel} (${r.currency}). How much was charged in ${r.currency}?`}</h2>
 <p className="live-hint">{transfer?`${r.sourceLabel} (${r.sourceCurrency}) → ${r.accountLabel} (${r.currency}). Enter the amount received or a rate.`:`Enter the ${r.currency} amount or the exchange rate.`}</p>
 <div className="form-grid"><label><span className="field-label">{transfer?'Received in':'Amount in'} {r.currency}</span><input className="field" inputMode="decimal" type="text" value={charged} onBlur={()=>{if(input.amount&&positiveDecimal(charged)){const next=currenciesLinked(r.amount,charged,'amount',digits);setCharged(next.amount);setRate(currenciesLinked(r.amount,next.amount,'amount',digits).rate);setInput({amount:next.amount});}}} onChange={e=>change(e.target.value,'amount')} disabled={busy}/></label><label><span className="field-label">{r.currency} for 1 {r.sourceCurrency}</span><input className="field" inputMode="decimal" type="text" value={rate} onBlur={()=>{if(positiveDecimal(rate))setRate(Number(rate).toFixed(4));}} onChange={e=>change(e.target.value,'rate')} disabled={busy}/></label></div>
 <p className="live-hint currency-preview">{valid?transfer?`${money(Number(r.amount),r.sourceCurrency)} sent · ${money(Number(charged),r.currency)} received`:`${money(Number(r.amount),r.sourceCurrency)} × ${Number(rate).toFixed(4)} = ${money(Number(charged),r.currency)}`:transfer?'Enter the received amount or rate to continue.':'Enter an amount or rate to continue.'}</p>
 {!transfer&&<label className="checkbox"><input type="checkbox" checked={estimate} onChange={e=>setEstimate(e.target.checked)} disabled={busy}/> This is an estimate</label>}
 <p className="inset live-hint">{transfer?'The transfer keeps both amounts, each in its own account’s currency.':'A card statement will later replace an estimate with the amount actually charged.'}</p>
 {error&&<p className="live-error" role="alert">{error}</p>}<footer className="detail-footer">{discard}<button className="button primary" disabled={blocked||busy||!valid}>{transfer?'Save transfer':'Save expense'}</button></footer>
 </form>;
}
