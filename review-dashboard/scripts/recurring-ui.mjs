import calendars from '../../schema/recurring-locales.json' with {type:'json'};
// Instant display only. The live matcher and checked server own every decision.
const generic = new Set(['subscription','membership','renewal','monthly','yearly','annual','payment','paid','the','a','by','on','for']);
const ancillary = new Set(['fee','fees','tax','taxes','discount','discounts','rounding']);
export const tokens = text => new Set((text.toLowerCase().match(/[^\P{L}\d_]+/gu) || []));
export const mainPostings = rule => rule.postings.filter(p=>p.account.startsWith('Expenses:') && Number(p.amount)>0 && ![...tokens(p.account.split(':').slice(1).join(':'))].some(w=>ancillary.has(w)));
export const payer = (rule,accounts) => {const ps=rule.postings.filter(p=>['bank','card','cash','wallet'].includes(accounts[p.account]?.kind) && Number(p.amount)<0);return ps.length===1?ps[0]:null;};
export function ruleAmounts(rule) {const totals=/** @type {Record<string,number>} */ ({});for(const p of mainPostings(rule))totals[p.commodity]=(totals[p.commodity]||0)+Number(p.amount);return totals;}
const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
export function textAmounts(text,locale='en',calendar) {
  calendar ||= calendars[locale.split('-')[0]] || calendars.en;
  text=text.replace(/\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b/g,' ').replace(/\b\d{1,2}\/\d{1,2}\b/g,' ');
  const english='jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?';
  text=text.replace(new RegExp(`\\b(?:\\d{1,2}(?:st|nd|rd|th)?[\\s-]+(?:${english})(?:[\\s-]+\\d{4})?|(?:${english})[\\s-]+\\d{1,2}(?:st|nd|rd|th)?(?:,?[\\s-]+\\d{4})?)\\b`,'gi'),' ');
  const names=(calendar?.months || Array.from({length:12},(_,i)=>['long','short'].map(month=>new Intl.DateTimeFormat(locale,{month,timeZone:'UTC'}).format(new Date(Date.UTC(2026,i,1))).replace(/\.$/,''))).flat()).sort((a,b)=>b.length-a.length).map(escape).join('|');
  text=text.replace(new RegExp(`(?<![\\p{L}\\p{N}_])(?:\\d{1,2}[.\\s-]+(?:${names})\\.?|(?:${names})\\.?[\\s-]+\\d{1,2})(?:[,\\s-]+\\d{4})?(?![\\p{L}\\p{N}_])`,'giu'),' ').replace(/\d{4}年\d{1,2}月\d{1,2}日/g,' ');
  const parts=new Intl.NumberFormat(locale).formatToParts(1234.5),decimal=calendar?.decimal || parts.find(p=>p.type==='decimal')?.value,group=calendar?.group ?? parts.find(p=>p.type==='group')?.value;
  if(decimal===',') {if(group)text=text.replaceAll(group,'');return (text.match(/(?<![\p{L}\p{N}_./-])\d+(?:,\d+)?(?![\p{L}\p{N}_./-])/gu)||[]).map(n=>Number(n.replace(',','.')));}
  return (text.match(/(?<![\p{L}\p{N}_./-])\d+(?:,\d{2,3})*(?:\.\d+)?(?![\p{L}\p{N}_./-])/gu)||[]).map(n=>Number(n.replaceAll(',','')));
}
export function matchRules(rules,text,accounts,today,currency,locale='en',calendar) {
  const words=tokens(text),quantities=textAmounts(text,locale,calendar),codes=new Set([...Intl.supportedValuesOf('currency'),...rules.flatMap(r=>r.postings.map(p=>p.commodity))]);
  const explicit=new Set((text.match(/\b[A-Za-z]{3}\b/g)||[]).map(c=>c.toUpperCase()).filter(c=>codes.has(c)));
  const paymentWords=new Set(Object.values(accounts).filter(m=>['bank','card','cash','wallet'].includes(m.kind)).flatMap(m=>[m.kind,m.label||'',...(m.aliases||[])].flatMap(x=>[...tokens(x)])));
  const matches=[],suggestions=[];
  for(const r of rules) {
    if((r.lifecycle||'active')!=='active')continue;
    const names=new Set([...tokens(r.description),...mainPostings(r).flatMap(p=>[...tokens(p.account.split(':').at(-1))])]);
    const matched_words=[...names].filter(w=>!generic.has(w)&&!paymentWords.has(w)&&words.has(w)).sort();if(!matched_words.length)continue;
    let score=4*matched_words.length;const pay=payer(r,accounts),meta=pay?accounts[pay.account]:null;
    const matched_alias=meta?[meta.label||'',...(meta.aliases||[])].find(a=>a&&[...tokens(a)].every(w=>words.has(w)))||null:null;
    if(matched_alias)score+=3;
    const compared=ruleAmounts(r);if(pay && compared[pay.commodity]===undefined)compared[pay.commodity]=Math.abs(Number(pay.amount));
    const matched_amounts=[];
    for(const [code,value] of Object.entries(compared))if(explicit.size?explicit.has(code):code===currency){const amount=quantities.find(q=>Math.abs(q-value)<=Math.abs(value)*.1+1e-9);if(amount!==undefined){score+=2;matched_amounts.push({amount:String(amount),currency:code});}}
    score+=Math.max(0,1-Math.abs(Date.parse(r.date)-Date.parse(today))/86400000/40);
    // One shared word alone is a quiet suggestion; a proposal needs two words, or one with the amount or paying account.
    (matched_words.length>1||matched_amounts.length||matched_alias?matches:suggestions).push({id:r.id,description:r.description,score,next_due:r.date,matched_words,matched_alias,matched_amounts});
  }
  const order=(a,b)=>b.score-a.score||a.id.localeCompare(b.id);matches.sort(order);suggestions.sort(order);const pick_list=matches.length>1&&matches[0].score-matches[1].score<2;
  return {matches,suggestions,pick_list,rule_id:matches.length&&!pick_list?matches[0].id:null};
}
export function periodFor(rule,day) {
  const [year,month]=day.split('-').map(Number),[startYear,startMonth,startDay]=rule.schedule.from.split('-').map(Number),step=(rule.schedule.interval||1)*(rule.schedule.every==='year'?12:1);
  const offset=Math.floor(((year-startYear)*12+month-startMonth)/step)*step;
  const date=new Date(Date.UTC(startYear,startMonth-1+offset,1));const end=new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,0)).getUTCDate();date.setUTCDate(Math.min(rule.schedule['anchor-day']||startDay,end));return date.toISOString().slice(0,10);
}
export const paymentInPeriod = (rule,day) => !/^\d{4}-\d{2}-\d{2}$/.test(day)||!Number.isFinite(Date.parse(day))?undefined:(rule.payments||[rule.last_payment].filter(Boolean)).find(p=>p.rule?.id===rule.id?p.rule.occurrence===periodFor(rule,day):periodFor(rule,p.date)===periodFor(rule,day));
export const complexRule = rule => rule.postings.length!==2 || rule.postings.some(p=>p.cost) || mainPostings(rule).length!==1;
export function varyingPayments(rule,actual,accounts) {
  const main=mainPostings(rule)[0],pay=payer(rule,accounts),code=main.commodity||main.currency;
  return [...actual].reverse().filter(e=>{
    const m=mainPostings(e),p=payer(e,accounts);
    return (e.status==='confirmed'||e.rule?.id===rule.id)&&e.postings.length===2&&m.length===1&&p&&!e.postings.some(p=>p.cost||p.costCurrency||p.assert)
      &&(m[0].commodity||m[0].currency)===code&&(p.commodity||p.currency)===code&&Number(m[0].amount)===-Number(p.amount)
      &&(e.rule?e.rule.id===rule.id:m[0].account===main.account&&p.account===pay?.account&&[...tokens(e.description)].some(w=>!generic.has(w)&&tokens(rule.description).has(w)));
  }).sort((a,b)=>b.date.localeCompare(a.date));
}
export function varyingDetails(rule,actual,accounts) {
  const history=varyingPayments(rule,actual,accounts).slice(0,12).map(e=>({id:e.id,date:e.date,amount:String(mainPostings(e)[0].amount)})),last=history.slice(0,3).map(e=>Number(e.amount));
  return {history,min:last.length?String(Math.min(...last)):null,max:last.length?String(Math.max(...last)):null};
}
export function varyingAmountCheck(rule,value) {
  const values=(rule.varying?.history||[]).slice(0,6).map(p=>Number(p.amount));
  return values.length&&(value>2*Math.max(...values)||value<Math.min(...values)/2)?{min:Math.min(...values),max:Math.max(...values)}:null;
}
export function ruleStatus(rule,today) {
  const paid=(rule.payments||[rule.last_payment].filter(Boolean)).some(p=>(p.rule?.id===rule.id&&p.rule.occurrence>p.date?p.rule.occurrence:p.date)>=rule.date);
  return rule.lifecycle&&rule.lifecycle!=='active'?rule.lifecycle:rule.bill?'bill-received':paid?'stale':rule.date<today?'overdue':rule.date===today?'due':'on-track';
}
export const statusLabel = status => ({'on-track':'On track','bill-received':'Bill received',due:'Due',overdue:'Overdue',stale:'Stale',paused:'Paused',ended:'Ended',removed:'Removed'}[status]||status);
export const originReason = origin => ({'subscription-words':'From subscription or membership words.','monthly-history':'Two consecutive monthly payments.','history-pass':'Two consecutive monthly payments found in history.'}[origin?.kind]||'Made by you.');

/** First due date after this chosen payment, using its cadence bucket. */
export function nextDueForPayment(rule,day) {
  if(!/^\d{4}-\d{2}-\d{2}$/.test(day)||!Number.isFinite(Date.parse(day)))return rule.next_due||rule.date;
  const latest=(rule.payments||[]).reduce((last,p)=>p.date>last?p.date:last,'');
  if(latest&&day<latest)return rule.next_due||rule.date;
  const bucket=periodFor(rule,day),through=day>bucket?day:bucket;
  const start=rule.schedule.from.split('-').map(Number),step=(rule.schedule.interval||1)*(rule.schedule.every==='year'?12:1);
  let cursor=bucket;
  do {
    const [year,month]=cursor.split('-').map(Number),first=new Date(Date.UTC(year,month-1+step,1));
    const end=new Date(Date.UTC(first.getUTCFullYear(),first.getUTCMonth()+1,0)).getUTCDate();
    first.setUTCDate(Math.min(rule.schedule['anchor-day']||start[2],end));cursor=first.toISOString().slice(0,10);
  } while(cursor<=through);
  return cursor<rule.date?rule.date:cursor;
}
