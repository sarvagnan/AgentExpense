import {useState} from 'react';
import {dashboard} from './data';
import {localDate} from './date-time';
import {Dialog, money, dayLabel, monthLong, useDocumentScrollLock} from './ui';
import {complexRule, mainPostings, payer, varyingDetails} from '../scripts/recurring-ui.mjs';
import {type Rule,useRuleOperation} from './live/rules-api';
import {accountFieldLabel} from './account-labels';
const metadata=()=>dashboard.meta.accounts;
export function RuleEditor({rule,onClose,onSaved}:{rule?:Rule;onClose:()=>void;onSaved:()=>void|Promise<void>}) {
  useDocumentScrollLock();
  const accounts=metadata(),payments=Object.keys(accounts).filter(a=>['bank','card','cash','wallet'].includes(accounts[a].kind||'')),categories=Object.keys(accounts).filter(a=>a.startsWith('Expenses:'));
  const complex=!!rule&&complexRule(rule),main=rule?mainPostings(rule)[0]:null,pay=rule?payer(rule,accounts):null;
  const [description,setDescription]=useState(rule?.description||''),[amount,setAmount]=useState(main?.amount||''),[account,setAccount]=useState(pay?.account||''),[category,setCategory]=useState(main?.account||''),[every,setEvery]=useState<'month'|'year'>(rule?.schedule.every||'month'),[interval,setInterval]=useState(rule?.schedule.interval||1),[date,setDate]=useState(rule?.date||localDate()),[anchor,setAnchor]=useState(rule?.schedule['anchor-day']||Number((rule?.date||localDate()).slice(8)));
  const [mode,setMode]=useState<'fixed'|'varies'>(rule?.amount==='varies'?'varies':'fixed');
  const op=useRuleOperation(onSaved);
  const chartRule:Rule|undefined=rule?{...rule,varying:rule.varying||varyingDetails(rule,dashboard.transactions.map(e=>({...e,status:e.status==='Cleared'?'confirmed':e.status})),accounts)}:undefined;
  const recent=chartRule?.varying?.history.slice(0,3).map(p=>Number(p.amount)).sort((a,b)=>a-b)||[];
  const preview=mode==='varies'&&rule?.amount!=='varies'&&recent.length?(recent[Math.floor(recent.length/2)]+recent[Math.floor((recent.length-1)/2)])/2:Number(amount);
  const valid=!!description.trim()&&!!date&&Number.isInteger(interval)&&interval>0&&anchor>=1&&anchor<=31&&(complex || Number(amount)>0&&!!account&&!!category);
  async function save(){
    if(!valid)return;
    const currency=accounts[account]?.currency||dashboard.meta.ledger.defaultCurrency;
    const quantity=String(amount).includes('.')?String(amount):String(amount)+'.00';
    const postings=complex?rule!.postings:[{account:category,amount:quantity,commodity:currency},{account,amount:'-'+quantity,commodity:currency}];
    const patch={amount:mode==='varies'?'varies' as const:null,description:description.trim(),postings,schedule:{every,interval,from:date,'anchor-day':anchor},date};
    const body=rule?{action:'edited',rule_id:rule.id,version:rule.version,rule:patch}:{action:'made',rule:{...patch,...(mode==='fixed'?{amount:undefined}:{}),status:'confirmed'}};
    if(await op.operate(body))onClose();
  }
  return <Dialog title={rule?'Edit rule':'New rule'} labelId="rule-editor-title" onClose={()=>{if(!op.busy)onClose();}} initialFocus="#rule-description"><form className="settings-form rule-editor" onSubmit={e=>{e.preventDefault();void save();}} aria-busy={op.busy}>
    <div className="settings-body"><label><span className="field-label">Description</span><input id="rule-description" className="field" value={description} onChange={e=>setDescription(e.target.value)} required disabled={op.busy}/></label>
      <fieldset className="rule-amount-choice"><legend className="field-label">Amount</legend><label><input type="radio" name="rule-amount-mode" checked={mode==='fixed'} onChange={()=>setMode('fixed')} disabled={op.busy||!!rule?.bill}/>Same each time</label><label><input type="radio" name="rule-amount-mode" checked={mode==='varies'} onChange={()=>setMode('varies')} disabled={op.busy||complex}/>Varies</label></fieldset>
      {mode==='varies'&&rule&&<section className="rule-estimate-block" aria-label="Estimate"><h3>Estimate · {money(preview,main?.commodity)}</h3><p className="live-hint">Median of the last 3 payments. With fewer payments, the median of those available.</p><PaymentBars rule={chartRule!}/></section>}
      {rule?.bill&&<section className="rule-bill-block" aria-label="Saved bill"><h3>{monthLong(rule.bill.occurrence)} bill · {money(Number(rule.bill.amount),main?.commodity)}</h3><p>Received {dayLabel(rule.bill.received)} · Pay by {dayLabel(rule.bill.pay_by||rule.bill.occurrence)}</p><p className="live-hint">{rule.bill.sources.length} filed {rule.bill.sources.length===1?'file':'files'}</p><button type="button" className="button destructive" disabled={op.busy} onClick={()=>{void op.operate({action:'bill-removed',rule_id:rule.id,version:rule.version}).then(ok=>{if(ok)onClose();});}}>Remove {monthLong(rule.bill.occurrence)} bill</button></section>}
      {complex?<section className="rule-readonly" aria-label="Read-only postings"><h3>Postings</h3><p className="live-hint">This rule has several postings or a conversion. Edit its complete balanced postings with the rules CLI.</p><dl>{rule!.postings.map((p,i)=><div key={i}><dt title={p.account}>{accountFieldLabel(p.account,accounts)}</dt><dd>{money(Number(p.amount),p.commodity)}{p.cost&&<> · {p.cost.per==='unit'?'each':'total'} {money(Number(p.cost.amount),p.cost.commodity)}</>}</dd></div>)}</dl></section>:<>
      <label><span className="field-label">{mode==='varies'?'Estimate':'Amount'}{account&&` (${accounts[account]?.currency})`}</span><input className="field" inputMode="decimal" pattern="[0-9]+(\.[0-9]+)?" value={mode==='varies'&&rule?String(preview):amount} onChange={e=>setAmount(e.target.value)} required readOnly={mode==='varies'&&!!rule} disabled={op.busy}/></label>
      <label><span className="field-label">Paying account</span><select aria-label="Paying account" className="field" value={account} onChange={e=>setAccount(e.target.value)} required disabled={op.busy||!!rule?.bill}><option value="">Choose an account</option>{payments.map(a=><option key={a} value={a}>{accounts[a].label||a.split(':').at(-1)} · {accounts[a].currency}</option>)}</select></label>
      <label><span className="field-label">Category</span><select aria-label="Category" className="field" value={category} onChange={e=>setCategory(e.target.value)} required disabled={op.busy||!!rule?.bill}><option value="">Choose a category</option>{categories.map(a=><option key={a} value={a}>{accountFieldLabel(a,accounts)}</option>)}</select></label></>}
      <div className="rule-form-grid"><label><span className="field-label">Schedule</span><select aria-label="Schedule" className="field" value={every} onChange={e=>setEvery(e.target.value as 'month'|'year')} disabled={op.busy}><option value="month">Monthly</option><option value="year">Yearly</option></select></label><label><span className="field-label">Every</span><input className="field" type="number" min={1} step={1} value={interval} disabled={op.busy} onChange={e=>setInterval(Number(e.target.value))}/></label>
      <label><span className="field-label">Next date</span><input className="field" type="date" value={date} required disabled={op.busy||!!rule?.bill} onChange={e=>{setDate(e.target.value);setAnchor(Number(e.target.value.slice(8)));}}/></label><label><span className="field-label">Day of month</span><input className="field" type="number" min={1} max={31} value={anchor} disabled={op.busy} onChange={e=>setAnchor(Number(e.target.value))}/></label></div><p className="live-hint">The day clamps to month end in shorter months.</p>
      {op.error&&<p role="alert" className="live-error">{op.error}</p>}</div><footer className="dialog-footer"><button type="button" className="button" disabled={op.busy} onClick={onClose}>Cancel</button><button className="button primary" disabled={op.busy||!valid}>{op.busy?'Saving…':'Save rule'}</button></footer>
  </form></Dialog>;
}

export function PaymentBars({rule}:{rule:Rule}) {
  const history=(rule.varying?.history||[]).slice(0,12),currency=mainPostings(rule)[0]?.commodity;
  if(!history.length)return <p className="live-hint">No payments yet. Keeping the current estimate.</p>;
  const high=Math.max(...history.map(p=>Number(p.amount)),1),rows=[...history].reverse();
  return <><div className="rule-payment-bars" role="img" aria-label="Recent payments; the latest 3 determine the estimate"><svg viewBox="0 0 360 110" aria-hidden="true">{rows.map((p,i)=>{const height=Number(p.amount)/high*90;return <rect key={p.id} x={i*360/rows.length+3} y={100-height} width={360/rows.length-6} height={height} className={history.slice(0,3).some(h=>h.id===p.id)?'estimate-payment':'older-payment'}/>;})}</svg></div><p className="live-hint">Latest 3 highlighted · up to 12 recent payments</p><ul className="rule-payment-values">{rows.map(p=><li key={p.id}>{dayLabel(p.date)} · {money(Number(p.amount),currency)}{history.slice(0,3).some(h=>h.id===p.id)?' · in estimate':''}</li>)}</ul></>;
}
