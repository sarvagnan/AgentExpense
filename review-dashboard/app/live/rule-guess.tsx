import {useEffect,useMemo,useState} from 'react';
import {dashboard} from '../data';
import {localDate} from '../date-time';
import {dayLabel,money,monthLong} from '../ui';
import {matchRules,payer,paymentInPeriod,ruleAmounts,textAmounts,nextDueForPayment,varyingAmountCheck} from '../../scripts/recurring-ui.mjs';
import {RuleSummary,entryHref} from '../rules';
import type {Composer} from './composer';
import {type MatchResult,type Rule,rulesApi,useRules} from './rules-api';
/** Self-contained so the guess can move with the composer into a future layout. */
export function RuleGuess({composer:c,online}:{composer:Composer;online:boolean|null}) {
  const source=useRules(online),rules=source.data.rules,metadata=dashboard.meta.accounts,locale=dashboard.meta.ledger.locale||navigator.language;
  const instant=useMemo(()=>matchRules(rules,c.form.text,metadata,localDate(),dashboard.meta.ledger.defaultCurrency,locale),[rules,c.form.text,metadata,locale]);
  const [live,setLive]=useState<{text:string;result:MatchResult}|null>(null),[matchError,setMatchError]=useState('');
  const [selected,setSelected]=useState('');
  useEffect(()=>setSelected(''),[c.form.text]);
  useEffect(()=>{if(!c.form.text.trim()||online!==true){setLive(null);return;}const controller=new AbortController();const timer=setTimeout(()=>{void rulesApi.match(c.form.text,controller.signal).then(result=>{setLive({text:c.form.text,result});setMatchError('');},e=>{if(!controller.signal.aborted)setMatchError((e as Error).message);});},180);return()=>{clearTimeout(timer);controller.abort();};},[c.form.text,online,rules]);
  const authoritative=live?.text===c.form.text;
  const attached=c.draft?.files.length||0,interpreted=c.interpretation.value?.files;
  // Recognition can insert file details above this action. Wait for the
  // settled receipt layout, including the upload-to-recognition handoff.
  const filesReady=attached===0||!!interpreted&&interpreted.length===attached&&interpreted.every(file=>file.kind==='receipt');
  const result=authoritative?live!.result:instant;
  const matches=result.matches.filter(m=>!c.form.declined_rules?.includes(m.id));
  const match=matches.find(m=>m.id===selected)||(!result.pick_list||matches.length===1?matches[0]:undefined),rule=rules.find(r=>r.id===match?.id);
  useEffect(()=>{c.setRuleDecisionNeeded?.(!!rule&&rule.amount==='varies');return()=>c.setRuleDecisionNeeded?.(false);},[rule?.id,rule?.amount]);
  if(!matches.length||!c.form.text.trim() || c.form.declined_rules?.includes(selected||result.rule_id||''))return null;
  return <aside className="rule-guess" aria-label="Usual payment"><p className="eyebrow">A usual payment?</p>{!rule?<><label><span className="field-label">Which rule did you mean?</span><select className="field" value="" onChange={e=>setSelected(e.target.value)}><option value="">Choose a rule</option>{matches.map(m=><option key={m.id} value={m.id}>{m.description}{(()=>{const r=rules.find(r=>r.id===m.id);if(!r)return '';const p=payer(r,metadata);return ' · '+Object.entries(ruleAmounts(r)).map(([code,value])=>money(Number(value),code)).join(' + ')+(p?' · '+(metadata[p.account]?.label||p.account):'');})()}</option>)}</select></label><p className="live-hint">Several rules match these words.</p><button className="button" onClick={()=>c.update({...c.form,declined_rules:[...new Set([...(c.form.declined_rules||[]),...matches.map(m=>m.id)])]})}>Not this</button></>:<RuleGuessChoice key={rule.id} rule={rule} match={match!} text={c.form.text} initialDate={c.built.fields.date?.value} disabled={!authoritative||online!==true||!source.ready||c.saving||c.recovering||c.interpretation.busy||!filesReady||c.pending.some(p=>p.busy||!!p.error)} onAdd={body=>c.save(body)} onDecline={()=>{c.update({...c.form,declined_rules:[...(c.form.declined_rules||[]),rule.id]});}}/>}{matchError&&<p className="live-hint">Live matching is unavailable. {matchError}</p>}</aside>;
}
export function RuleGuessChoice(props:Parameters<typeof FixedRuleChoice>[0]) {return props.rule.amount==='varies'?<VaryingRuleChoice {...props}/>:<FixedRuleChoice {...props}/>;}
function FixedRuleChoice({rule,match,text,initialDate,disabled,onAdd,onDecline}:{rule:Rule;match:MatchResult['matches'][number];text:string;initialDate?:string;disabled:boolean;onAdd:Composer['save'];onDecline:()=>void}) {
  const accounts=dashboard.meta.accounts,amounts=ruleAmounts(rule),code=Object.keys(amounts)[0],value=amounts[code],pay=payer(rule,accounts);
  const quantities=textAmounts(text,dashboard.meta.ledger.locale||navigator.language);
  const explicit=(text.match(/\b[A-Za-z]{3}\b/g)||[]).map(c=>c.toUpperCase()).filter(c=>Intl.supportedValuesOf('currency').includes(c));
  const typed=Object.keys(amounts).length===1&&(!explicit.length?code===dashboard.meta.ledger.defaultCurrency:explicit.includes(code))?quantities[0]:undefined;
  const different=typed!==undefined&&typed>0&&Math.abs(typed-value)>1e-9;
  const [date,setDate]=useState(initialDate||localDate()),[update,setUpdate]=useState(false),[account,setAccount]=useState(''),[remember,setRemember]=useState(false),[another,setAnother]=useState(false);
  useEffect(()=>{setDate(initialDate||localDate());setAnother(false);},[initialDate]);
  const relative=date===localDate()?'today':date===new Date(Date.parse(localDate())-86400000).toISOString().slice(0,10)?'yesterday':'';
  const paid=paymentInPeriod(rule,date),available=Object.entries(accounts).filter(([,m])=>['bank','card','cash','wallet'].includes(m.kind||'')&&m.currency===(pay?.commodity||rule.postings.find(p=>Number(p.amount)<0)?.commodity));
  const conversions=rule.postings.filter(p=>p.cost&&p.cost.commodity!==p.commodity);
  return <><h3>{rule.description}</h3><p className="rule-match-reason">Matched {match.matched_words.map(w=>`‘${w}’`).join(', ')}{match.matched_amounts.length>0&&<> and {match.matched_amounts.map(a=>money(Number(a.amount),a.currency)).join(', ')}</>}{match.matched_alias&&<> · {match.matched_alias}</>}</p><div className="rule-summary"><RuleSummary rule={rule}/></div><p className="live-hint">Next due {dayLabel(nextDueForPayment(rule,date))}</p>
    <p className="live-hint">Adds {date?dayLabel(date):'on the chosen payment date'}{relative?` (${relative})`:''}</p><label><span className="field-label">Payment date</span><input type="date" className="field" value={date} required onChange={e=>{setDate(e.target.value);setAnother(false);}}/></label>
    {different&&<fieldset className="rule-amount-choice"><legend>{money(typed!,code)} this time · usual {money(value,code)}</legend><label><input type="radio" name={`amount-${rule.id}`} checked={!update} onChange={()=>setUpdate(false)}/>This payment only</label><label><input type="radio" name={`amount-${rule.id}`} checked={update} onChange={()=>setUpdate(true)}/>Update the rule too</label></fieldset>}
    {!pay&&<><label><span className="field-label">Paying account</span><select aria-label="Paying account" className="field" value={account} onChange={e=>setAccount(e.target.value)}><option value="">Choose an account</option>{available.map(([a,m])=><option key={a} value={a}>{m.label||a} · {m.currency}</option>)}</select></label><label className="rule-check"><input type="checkbox" checked={remember} onChange={e=>setRemember(e.target.checked)}/>Use this account from now on</label></>}
    {conversions.length>0&&<p className="rule-conversion">Planned conversion: {conversions.map(p=>`${money(Number(p.amount),p.commodity)} at ${money(Number(p.cost!.amount),p.cost!.commodity)} ${p.cost!.per==='total'?'total':'per unit'}`).join('; ')}. Statement reconciliation will correct it.</p>}
    {paid&&<div className="rule-already"><p>Already added for {monthLong(date)} on {dayLabel(paid.date)}.</p><a className="button" href={entryHref(paid.id)}>View entry</a><label className="rule-check"><input type="checkbox" checked={another} onChange={e=>setAnother(e.target.checked)}/>Add another anyway</label></div>}
    <div className="rule-actions"><button className="button primary" disabled={disabled||!date||!pay&&!account||!!paid&&!another} onClick={()=>void onAdd({rule_id:rule.id,date,...(different?{amount:String(typed),update_rule:update}:{}),...(!pay?{account,remember_account:remember}:{}),another_anyway:another})}>Add as usual</button><button className="button" onClick={onDecline}>Not this</button></div>
  </>;
}

function VaryingRuleChoice({rule,match,text,initialDate,disabled,onAdd,onDecline}:Parameters<typeof FixedRuleChoice>[0]) {
  const amounts=ruleAmounts(rule),code=Object.keys(amounts)[0],estimate=amounts[code],bill=rule.bill;
  const quantities=textAmounts(text,dashboard.meta.ledger.locale||navigator.language);
  const explicit=(text.match(/\b[A-Za-z]{3}\b/g)||[]).map(c=>c.toUpperCase()).filter(c=>Intl.supportedValuesOf('currency').includes(c));
  const typed=(!explicit.length?code===dashboard.meta.ledger.defaultCurrency:explicit.includes(code))?quantities[0]:undefined;
  const key=JSON.stringify([text,bill?.amount??null]),empty={choice:null as 'paid'|'bill'|null,anyway:false,another:false};
  const [selection,setSelection]=useState({...empty,key}),[date,setDate]=useState(initialDate||localDate()),[payBy,setPayBy]=useState('');
  const {choice,anyway,another}=selection.key===key?selection:empty;
  const choose=(change:Partial<typeof empty>)=>setSelection(old=>({...empty,...(old.key===key?old:{}),key,...change}));
  useEffect(()=>setDate(initialDate||localDate()),[initialDate]);
  const amount=bill?Number(bill.amount):typed,valid=amount!==undefined&&amount>0,check=valid?varyingAmountCheck(rule,amount):null,paid=paymentInPeriod(rule,bill?.occurrence||date);
  const label=valid?money(amount!,code):'',paying=bill||choice==='paid';
  const add=()=>onAdd({rule_id:rule.id,date,amount:String(amount),...(choice==='bill'&&!bill?{bill_only:true,...(payBy?{pay_by:payBy}:{})}:{}),...(check?{amount_anyway:anyway}:{}),...(paying?{another_anyway:another}:{})});
  return <><h3>{rule.description}</h3><p className="rule-match-reason">Matched {match.matched_words.map(w=>`‘${w}’`).join(', ')}</p>
    <div className="rule-summary"><span>{bill?<><strong>{label}</strong> <span className="bill-badge">{monthLong(bill.occurrence)} bill</span></>:valid?<>about {money(estimate,code)}</>:<>≈ {money(estimate,code)} estimate</>}</span><span>{payer(rule,dashboard.meta.accounts)?.account&&dashboard.meta.accounts[payer(rule,dashboard.meta.accounts)!.account]?.label}</span></div>
    {bill?<p>Pay by {dayLabel(bill.pay_by||bill.occurrence)}</p>:valid?<fieldset className="rule-amount-choice"><legend>Have you paid it?</legend><label><input type="radio" name={`paid-${rule.id}`} checked={choice==='paid'} onChange={()=>choose({choice:'paid'})}/>Paid {label}</label><label><input type="radio" name={`paid-${rule.id}`} checked={choice==='bill'} onChange={()=>choose({choice:'bill'})}/>Bill only. Not paid yet</label></fieldset>:<p className="live-hint">Needs an amount</p>}
    {(bill||choice)&&<label><span className="field-label">{choice==='bill'&&!bill?'Received':'Payment date'}</span><input type="date" className="field" value={date} onChange={e=>{setDate(e.target.value);choose({another:false});}} required/></label>}
    {!bill&&valid&&<label><span className="field-label">Pay by (optional)</span><input type="date" className="field" value={payBy} onChange={e=>setPayBy(e.target.value)}/></label>}
    {check&&<aside className="rule-amount-notice" role="status"><p>{label} is {amount!>2*check.max?'more than twice the highest':'less than half the lowest'} of the last 6 payments ({money(check.min,code)}–{money(check.max,code)}).</p><label><input type="checkbox" checked={anyway} onChange={e=>choose({anyway:e.target.checked})}/>Add {label} anyway</label></aside>}
    {paying&&paid&&<div className="rule-already"><p>Already added for {monthLong(bill?.occurrence||date)}.</p><a className="button" href={entryHref(paid.id)}>View entry</a><label className="rule-check"><input type="checkbox" checked={another} onChange={e=>choose({another:e.target.checked})}/>Add another anyway</label></div>}
    <div className="rule-actions"><button className="button primary" disabled={disabled||!valid||!date||!bill&&!choice||!!check&&!anyway||!!paying&&!!paid&&!another} onClick={()=>void add()}>{choice==='bill'&&!bill?'Save bill':bill?`Add ${label}`:choice==='paid'?`Paid ${label}`:'Add'}</button><button className="button" onClick={onDecline}>Not this</button></div>
  </>;
}
