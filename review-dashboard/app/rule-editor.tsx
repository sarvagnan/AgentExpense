import {retryWrite,writeSnapshot,type DirectWrite} from './live/direct-writes';
import {useActionGuard} from './live/interaction';
import {useRetainedState,clearRetained,retainedSnapshot,hasRetained} from './live/retained-state';
import {ActionFeedback} from './live/pending-actions';
import {useRef,useState} from 'react';
import {dashboard} from './data';
import {localDate} from './date-time';
import {Dialog, money, dayLabel, monthLong, useDocumentScrollLock} from './ui';
import {complexRule, mainPostings, payer, varyingDetails} from '../scripts/recurring-ui.mjs';
import {type Rule,useRuleOperation} from './live/rules-api';
import {accountFieldLabel} from './account-labels';
const metadata=()=>dashboard.meta.accounts;
export function RuleEditor({rule,onClose,onSaved}:{rule?:Rule;onClose:()=>void;onSaved:(unchanged?:()=>boolean)=>void|Promise<void>}) {
  useDocumentScrollLock();
  const guard=useActionGuard();
  const accounts=metadata(),payments=Object.keys(accounts).filter(a=>['bank','card','cash','wallet'].includes(accounts[a].kind||'')),categories=Object.keys(accounts).filter(a=>a.startsWith('Expenses:'));
  const complex=!!rule&&complexRule(rule),main=rule?mainPostings(rule)[0]:null,pay=rule?payer(rule,accounts):null;
  const formKey=`rule-${rule?.id||'new'}-`;
  const [description,setDescription]=useRetainedState(formKey+'description',rule?.description||''),[amount,setAmount]=useRetainedState(formKey+'amount',main?.amount||''),[account,setAccount]=useRetainedState(formKey+'account',pay?.account||''),[category,setCategory]=useRetainedState(formKey+'category',main?.account||''),[every,setEvery]=useRetainedState<'month'|'year'>(formKey+'every',rule?.schedule.every||'month'),[interval,setInterval]=useRetainedState(formKey+'interval',rule?.schedule.interval||1),[date,setDate]=useRetainedState(formKey+'date',rule?.date||localDate()),[anchor,setAnchor]=useRetainedState(formKey+'anchor',rule?.schedule['anchor-day']||Number((rule?.date||localDate()).slice(8)));
  const [mode,setMode]=useRetainedState<'fixed'|'varies'>(formKey+'mode',rule?.amount==='varies'?'varies':'fixed');
  const fields=['description','amount','account','category','every','interval','date','anchor','mode','version'].map(key=>formKey+key);
  const [baseVersion,setBaseVersion]=useRetainedState<string|null>(formKey+'version',rule?.version||null);
  const [keptOnOpen,setKeptOnOpen]=useState(()=>hasRetained(fields));
  const stale=!!rule&&!!baseVersion&&baseVersion!==rule.version;
  const close=()=>{clearRetained(fields);onClose();};
  const discard=()=>{setKeptOnOpen(false);setDescription(rule?.description||'');setAmount(main?.amount||'');setAccount(pay?.account||'');setCategory(main?.account||'');setEvery(rule?.schedule.every||'month');setInterval(rule?.schedule.interval||1);setDate(rule?.date||localDate());setAnchor(rule?.schedule['anchor-day']||Number((rule?.date||localDate()).slice(8)));setMode(rule?.amount==='varies'?'varies':'fixed');setBaseVersion(rule?.version||null);clearRetained(fields);};
  const saveTicket=useRef<number|null>(null);
  const op=useRuleOperation(()=>onSaved(()=>saveTicket.current!==null&&guard.unchanged(saveTicket.current)),rule?.id||'new',!!rule||hasRetained(fields));
  const chartRule:Rule|undefined=rule?{...rule,varying:rule.varying||varyingDetails(rule,dashboard.transactions.map(e=>({...e,status:e.status==='Cleared'?'confirmed':e.status})),accounts)}:undefined;
  const recent=chartRule?.varying?.history.slice(0,3).map(p=>Number(p.amount)).sort((a,b)=>a-b)||[];
  const preview=mode==='varies'&&rule?.amount!=='varies'&&recent.length?(recent[Math.floor(recent.length/2)]+recent[Math.floor((recent.length-1)/2)])/2:Number(amount);
  const valid=!!description.trim()&&!!date&&Number.isInteger(interval)&&interval>0&&anchor>=1&&anchor<=31&&(complex || Number(amount)>0&&!!account&&!!category);
  const latest=useRef('');latest.current=JSON.stringify([description,amount,account,category,every,interval,date,anchor,mode]);
  async function save(){
    const submitted=latest.current,ticket=guard.begin();saveTicket.current=ticket;
    if(!valid||stale)return;
    const currency=accounts[account]?.currency||dashboard.meta.ledger.defaultCurrency;
    const quantity=String(amount).includes('.')?String(amount):String(amount)+'.00';
    const postings=complex?rule!.postings:[{account:category,amount:quantity,commodity:currency},{account,amount:'-'+quantity,commodity:currency}];
    const patch={amount:mode==='varies'?'varies' as const:'fixed' as const,description:description.trim(),postings,schedule:{every,interval,from:date,'anchor-day':anchor},date};
    const body=rule?{action:'edited',rule_id:rule.id,version:baseVersion||rule.version,rule:patch}:{action:'made',rule:{...patch,status:'confirmed'}};
    if(await op.operate(body,retainedSnapshot(fields))&&guard.unchanged(ticket)&&latest.current===submitted){clearRetained(fields);onClose();}
  }
  async function retry(item:DirectWrite){
    const body=JSON.parse(item.body),savedRule=body.rule;
    const matches=savedRule?description.trim()===savedRule.description&&date===savedRule.date&&mode===savedRule.amount&&every===savedRule.schedule?.every&&interval===savedRule.schedule?.interval&&anchor===savedRule.schedule?.['anchor-day']&&(complex||savedRule.postings?.some((posting:{account:string;amount:string})=>posting.account===category&&Number(posting.amount)===Number(amount))&&savedRule.postings?.some((posting:{account:string;amount:string})=>posting.account===account&&Number(posting.amount)===-Number(amount))):!hasRetained(fields);
    const ticket=guard.begin();saveTicket.current=ticket;await retryWrite(item.id);
    if(writeSnapshot().find(value=>value.id===item.id)?.state==='saved'){await onSaved(()=>matches&&guard.unchanged(ticket));if(matches&&guard.unchanged(ticket))onClose();}
  }
  return <Dialog title={rule?'Edit rule':'New rule'} labelId="rule-editor-title" onClose={close} initialFocus="#rule-description"><form onChangeCapture={()=>setBaseVersion(baseVersion)} {...guard.interaction} className="settings-form rule-editor" onSubmit={e=>{e.preventDefault();void save();}} aria-busy={op.busy}>
    <div className="settings-body">{(stale||keptOnOpen)&&<div className="live-notice" role="status"><div>{stale&&<><strong>The saved rule changed</strong><p>Your kept edits are still below. Compare or copy them before starting from the current saved rule.</p></>}<button type="button" className="button" onClick={discard}>Discard kept edits</button></div></div>}<label><span className="field-label">Description</span><input id="rule-description" className="field" value={description} onChange={e=>setDescription(e.target.value)} required/></label>
      <fieldset className="rule-amount-choice"><legend className="field-label">Amount</legend><label><input type="radio" name="rule-amount-mode" checked={mode==='fixed'} onChange={()=>setMode('fixed')} disabled={!!rule?.bill}/>Same each time</label><label><input type="radio" name="rule-amount-mode" checked={mode==='varies'} onChange={()=>setMode('varies')} disabled={complex}/>Varies</label></fieldset>
      {mode==='varies'&&rule&&<section className="rule-estimate-block" aria-label="Estimate"><h3>Estimate · {money(preview,main?.commodity)}</h3><p className="live-hint">Median of the last 3 payments. With fewer payments, the median of those available.</p><PaymentBars rule={chartRule!}/></section>}
      {rule?.bill&&<section className="rule-bill-block" aria-label="Saved bill"><h3>{monthLong(rule.bill.occurrence)} bill · {money(Number(rule.bill.amount),main?.commodity)}</h3><p>Received {dayLabel(rule.bill.received)} · Pay by {dayLabel(rule.bill['pay-by']||rule.bill.occurrence)}</p><p className="live-hint">{rule.bill.evidence.length} filed {rule.bill.evidence.length===1?'file':'files'}</p><button type="button" className="button destructive" disabled={op.busy} onClick={()=>{const ticket=guard.begin();saveTicket.current=ticket;void op.operate({action:'bill-removed',rule_id:rule.id,version:rule.version}).then(ok=>{if(ok&&guard.unchanged(ticket))onClose();});}}>Remove {monthLong(rule.bill.occurrence)} bill</button></section>}
      {complex?<section className="rule-readonly" aria-label="Read-only postings"><h3>Postings</h3><p className="live-hint">This rule has several postings or a conversion. Edit its complete balanced postings with the rules CLI.</p><dl>{rule!.postings.map((p,i)=><div key={i}><dt title={p.account}>{accountFieldLabel(p.account,accounts)}</dt><dd>{money(Number(p.amount),p.commodity)}{p.cost&&<> · {p.cost.per==='unit'?'each':'total'} {money(Number(p.cost.amount),p.cost.commodity)}</>}</dd></div>)}</dl></section>:<>
      <label><span className="field-label">{mode==='varies'?'Estimate':'Amount'}{account&&` (${accounts[account]?.currency})`}</span><input className="field" inputMode="decimal" pattern="[0-9]+(\.[0-9]+)?" value={mode==='varies'&&rule?String(preview):amount} onChange={e=>setAmount(e.target.value)} required readOnly={mode==='varies'&&!!rule}/></label>
      <label><span className="field-label">Paying account</span><select aria-label="Paying account" className="field" value={account} onChange={e=>setAccount(e.target.value)} required disabled={!!rule?.bill}><option value="">Choose an account</option>{payments.map(a=><option key={a} value={a}>{accounts[a].label||a.split(':').at(-1)} · {accounts[a].currency}</option>)}</select></label>
      <label><span className="field-label">Category</span><select aria-label="Category" className="field" value={category} onChange={e=>setCategory(e.target.value)} required disabled={!!rule?.bill}><option value="">Choose a category</option>{categories.map(a=><option key={a} value={a}>{accountFieldLabel(a,accounts)}</option>)}</select></label></>}
      <div className="rule-form-grid"><label><span className="field-label">Schedule</span><select aria-label="Schedule" className="field" value={every} onChange={e=>setEvery(e.target.value as 'month'|'year')} disabled={op.busy}><option value="month">Monthly</option><option value="year">Yearly</option></select></label><label><span className="field-label">Every</span><input className="field" type="number" min={1} step={1} value={interval} onChange={e=>setInterval(Number(e.target.value))}/></label>
      <label><span className="field-label">Next date</span><input className="field" type="date" value={date} required disabled={!!rule?.bill} onChange={e=>{setDate(e.target.value);setAnchor(Number(e.target.value.slice(8)));}}/></label><label><span className="field-label">Day of month</span><input className="field" type="number" min={1} max={31} value={anchor} onChange={e=>setAnchor(Number(e.target.value))}/></label></div><p className="live-hint">The day clamps to month end in shorter months.</p>
      <div className="action-error-slot">{op.error&&<p role="alert" className="live-error">{op.error}</p>}</div></div><ActionFeedback owner={op.owner} scope="rules" resource={rule?.id||'new'} onRetry={item=>void retry(item)}/><footer className="dialog-footer"><button type="button" className="button" onClick={close}>Cancel</button><button className="button primary" disabled={op.busy||!valid||stale}>{op.busy?'Saving…':'Save rule'}</button></footer>
  </form></Dialog>;
}

export function PaymentBars({rule}:{rule:Rule}) {
  const history=(rule.varying?.history||[]).slice(0,12),currency=mainPostings(rule)[0]?.commodity;
  if(!history.length)return <p className="live-hint">No payments yet. Keeping the current estimate.</p>;
  const high=Math.max(...history.map(p=>Number(p.amount)),1),rows=[...history].reverse();
  return <><div className="rule-payment-bars" role="img" aria-label="Recent payments; the latest 3 determine the estimate"><svg viewBox="0 0 360 110" aria-hidden="true">{rows.map((p,i)=>{const height=Number(p.amount)/high*90;return <rect key={p.id} x={i*360/rows.length+3} y={100-height} width={360/rows.length-6} height={height} className={history.slice(0,3).some(h=>h.id===p.id)?'estimate-payment':'older-payment'}/>;})}</svg></div><p className="live-hint">Latest 3 highlighted · up to 12 recent payments</p><ul className="rule-payment-values">{rows.map(p=><li key={p.id}>{dayLabel(p.date)} · {money(Number(p.amount),currency)}{history.slice(0,3).some(h=>h.id===p.id)?' · in estimate':''}</li>)}</ul></>;
}
