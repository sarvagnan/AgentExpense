import {useState} from 'react';
import {dashboard} from './data';
import {localDate} from './date-time';
import {Dialog, money, useDocumentScrollLock} from './ui';
import {complexRule, mainPostings, payer} from '../scripts/recurring-ui.mjs';
import {type Rule,useRuleOperation} from './live/rules-api';
const metadata=()=>dashboard.meta.accounts;
export function RuleEditor({rule,onClose,onSaved}:{rule?:Rule;onClose:()=>void;onSaved:()=>void|Promise<void>}) {
  useDocumentScrollLock();
  const accounts=metadata(),payments=Object.keys(accounts).filter(a=>['bank','card','cash','wallet'].includes(accounts[a].kind||'')),categories=Object.keys(accounts).filter(a=>a.startsWith('Expenses:'));
  const complex=!!rule&&complexRule(rule),main=rule?mainPostings(rule)[0]:null,pay=rule?payer(rule,accounts):null;
  const [description,setDescription]=useState(rule?.description||''),[amount,setAmount]=useState(main?.amount||''),[account,setAccount]=useState(pay?.account||''),[category,setCategory]=useState(main?.account||''),[every,setEvery]=useState<'month'|'year'>(rule?.schedule.every||'month'),[interval,setInterval]=useState(rule?.schedule.interval||1),[date,setDate]=useState(rule?.date||localDate()),[anchor,setAnchor]=useState(rule?.schedule['anchor-day']||Number((rule?.date||localDate()).slice(8)));
  const op=useRuleOperation(onSaved);
  const valid=!!description.trim()&&!!date&&Number.isInteger(interval)&&interval>0&&anchor>=1&&anchor<=31&&(complex || Number(amount)>0&&!!account&&!!category);
  async function save(){
    if(!valid)return;
    const currency=accounts[account]?.currency||dashboard.meta.ledger.defaultCurrency;
    const postings=complex?rule!.postings:[{account:category,amount:String(amount),commodity:currency},{account,amount:'-'+amount,commodity:currency}];
    const patch={description:description.trim(),postings,schedule:{every,interval,from:date,'anchor-day':anchor},date};
    const body=rule?{action:'edited',rule_id:rule.id,version:rule.version,rule:patch}:{action:'made',rule:{...patch,status:'confirmed'}};
    if(await op.operate(body))onClose();
  }
  return <Dialog title={rule?'Edit rule':'New rule'} labelId="rule-editor-title" onClose={()=>{if(!op.busy)onClose();}} initialFocus="#rule-description"><form className="settings-form rule-editor" onSubmit={e=>{e.preventDefault();void save();}} aria-busy={op.busy}>
    <div className="settings-body"><label><span className="field-label">Description</span><input id="rule-description" className="field" value={description} onChange={e=>setDescription(e.target.value)} required disabled={op.busy}/></label>
      {complex?<section className="rule-readonly" aria-label="Read-only postings"><h3>Postings</h3><p className="live-hint">This rule has several postings or a conversion. Edit its complete balanced postings with the rules CLI.</p><dl>{rule!.postings.map((p,i)=><div key={i}><dt>{p.account}</dt><dd>{money(Number(p.amount),p.commodity)}{p.cost&&<> · {p.cost.per==='unit'?'each':'total'} {money(Number(p.cost.amount),p.cost.commodity)}</>}</dd></div>)}</dl></section>:<>
      <label><span className="field-label">Amount{account&&` (${accounts[account]?.currency})`}</span><input className="field" inputMode="decimal" pattern="[0-9]+(\.[0-9]+)?" value={amount} onChange={e=>setAmount(e.target.value)} required disabled={op.busy}/></label>
      <label><span className="field-label">Paying account</span><select aria-label="Paying account" className="field" value={account} onChange={e=>setAccount(e.target.value)} required disabled={op.busy}><option value="">Choose an account</option>{payments.map(a=><option key={a} value={a}>{accounts[a].label||a.split(':').at(-1)} · {accounts[a].currency}</option>)}</select></label>
      <label><span className="field-label">Category</span><select aria-label="Category" className="field" value={category} onChange={e=>setCategory(e.target.value)} required disabled={op.busy}><option value="">Choose a category</option>{categories.map(a=><option key={a}>{a}</option>)}</select></label></>}
      <div className="rule-form-grid"><label><span className="field-label">Schedule</span><select aria-label="Schedule" className="field" value={every} onChange={e=>setEvery(e.target.value as 'month'|'year')} disabled={op.busy}><option value="month">Monthly</option><option value="year">Yearly</option></select></label><label><span className="field-label">Every</span><input className="field" type="number" min={1} step={1} value={interval} disabled={op.busy} onChange={e=>setInterval(Number(e.target.value))}/></label>
      <label><span className="field-label">Next date</span><input className="field" type="date" value={date} required disabled={op.busy} onChange={e=>{setDate(e.target.value);setAnchor(Number(e.target.value.slice(8)));}}/></label><label><span className="field-label">Day of month</span><input className="field" type="number" min={1} max={31} value={anchor} disabled={op.busy} onChange={e=>setAnchor(Number(e.target.value))}/></label></div><p className="live-hint">The day clamps to month end in shorter months.</p>
      {op.error&&<p role="alert" className="live-error">{op.error}</p>}</div><footer className="dialog-footer"><button type="button" className="button" disabled={op.busy} onClick={onClose}>Cancel</button><button className="button primary" disabled={op.busy||!valid}>{op.busy?'Saving…':'Save rule'}</button></footer>
  </form></Dialog>;
}
