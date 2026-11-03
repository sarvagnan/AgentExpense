import {mainPostings,payer as rulePayer} from './recurring-ui.mjs';
import { moduleSettings } from './ledger-modules.mjs';
import { accountLabel, expenseCategory, hasSegment, paymentAccounts } from './account-metadata.mjs';
// Category/pattern words describe an expense, not the merchant that chose a payment.
const GENERIC_NAMES = new Set(['fuel','fuels','petrol','diesel','bunk','taxi','cab','auto','ride','transport','subscriptions','subscription','renewal','dining','restaurants','eating out','dinner','lunch','breakfast','snacks','coffee','food','groceries','rent','home','housing','utilities','bills']);
/** Historical suggestions use account meanings and generic category segments. */
export function patternOf(entry, metadata = {}) {
  const posts = entry.postings;
  const has = (kind, sign) => posts.some(p => metadata[p.account]?.kind === kind && Math.sign(p.amount) === sign);
  const segment = words => posts.some(p => p.account.split(':')[0] === 'Expenses' && hasSegment(p.account, words));
  if (has('bank', -1) && has('card', 1)) return 'cardbill';
  if (has('person', -1) && (has('bank', 1) || has('cash', 1))) return 'payback';
  if (posts.some(p => p.account.split(':')[0] === 'Income' && hasSegment(p.account, ['Reward', 'Rewards', 'Cashback']))) return 'rewards';
  if (segment(['Fuel'])) return 'fuel';
  if (segment(['Taxi', 'Cab', 'Auto', 'Ride'])) return 'ride';
  if (segment(['Subscriptions'])) return 'sub';
  if (segment(['Dining', 'Restaurants', 'Eating Out'])) return 'dining';
  return null;
}
export function buildPatterns(transactions, metadata = {}, manifest = {}, forecasts = [], rawRules = [], audit = []) {
  forecasts = forecasts.filter(rule => (rule.lifecycle ?? 'active') === 'active');
  const patterns = {}, accounts = Object.keys(metadata);
  const people = Object.entries(metadata).filter(([, m]) => m.kind === 'person').map(([a]) => accountLabel(a, metadata));
  const payments = paymentAccounts(metadata);
  const top = counts => [...counts].sort((a,b) => b[1]-a[1] || a[0].localeCompare(b[0]))[0] || [null, 0];
  for (const entry of transactions) {
    const id = patternOf(entry, metadata); if (!id || id === 'fuel' && !moduleSettings(manifest).fuel) continue;
    const st = patterns[id] ||= {count:0, last:[], pays:new Map(), expenses:new Map(), names:new Map()};
    const sides = entry.postings.filter(p => payments[p.account]);
    const pay = sides.find(p => !['person'].includes(metadata[p.account].kind) && p.amount < 0) || sides.find(p => metadata[p.account].kind !== 'person') || sides[0];
    const expense = entry.postings.find(p => p.account.startsWith('Expenses:') && p.amount > 0);
    st.count++;
    if (pay) st.pays.set(pay.account, (st.pays.get(pay.account) || 0) + 1);
    if (expense) st.expenses.set(expense.account, (st.expenses.get(expense.account) || 0) + 1);
    st.names.set(entry.description, (st.names.get(entry.description) || 0) + 1);
    st.last.push({date:entry.date, description:entry.description, currency:pay ? metadata[pay.account]?.currency || pay.currency : null, amount:pay ? String(Math.abs(pay.amount)) : '', ...(moduleSettings(manifest).fuel && entry['fuel-filled-litres'] ? {litres:entry['fuel-filled-litres']} : {})});
  }
  for (const st of Object.values(patterns)) {
    [st.usualPay, st.usualShare] = top(st.pays); [st.usualExpense] = top(st.expenses);
    st.usualCategory = st.usualExpense ? expenseCategory(st.usualExpense, metadata) : null;
    st.known = [...st.names].sort((a,b) => b[1]-a[1] || a[0].localeCompare(b[0])).slice(0,10).map(([name]) => name);
    st.last = st.last.sort((a,b) => b.date.localeCompare(a.date)).slice(0,3);
    delete st.pays; delete st.expenses; delete st.names;
  }
  // Actual history always wins over forecasts, even when a forecast is newer.
  const merchants = {};
  const candidates = transactions.map((entry,index)=>({entry,index})).sort((a,b)=>b.entry.date.localeCompare(a.entry.date) || b.index-a.index).map(item=>item.entry);
  for (const [source, entries] of [['history', candidates], ['forecast', forecasts]]) for (const entry of entries) {
    const expense = entry.postings.find(p=>p.account.startsWith('Expenses:') && p.amount>0);
    const pay = entry.postings.find(p=>payments[p.account] && p.amount<0 && metadata[p.account].kind!=='person');
    if (!expense || !pay) continue;
    const aliases = [entry.description, entry.description.split(/\s+[—–·]\s+/)[0], expense.account.split(':').at(-1)].filter(Boolean);
    for (const alias of aliases) {
      const key = alias.toLocaleLowerCase();
      if (GENERIC_NAMES.has(key)) continue;
      if (!merchants[key]) merchants[key] = {name:alias, source, date:entry.date, expense:expense.account, category:expenseCategory(expense.account,metadata), pay:pay.account, amount:String(Math.abs(pay.amount)), currency:pay.currency || metadata[pay.account].currency, pattern:patternOf(entry,metadata)};
    }
  }
  const undone = new Set(audit.filter(r=>r.kind==='rule'&&r.rule_action==='undone').map(r=>r.undoes));
  const ignored = new Set([...audit.filter(r=>undone.has(r.id)).map(r=>r.entry_id),...audit.filter(r=>r.kind==='rule'&&r.rule_action==='undone').map(r=>r.reversal_id)]);
  const reviews={},reviewStates={};
  for(const line of audit) {
    if(line.kind==='rule-review'){reviews[line.rule_id]=line;reviewStates[line.rule_id]=line.rule_verdict;}
    if(line.kind==='rule'&&['removed','kept'].includes(line.rule_action))for(const id of new Set([line.rule_id,...line.rule_changes.map(c=>c.id)].filter(Boolean))){delete reviews[id];reviewStates[id]=line.rule_action==='kept'?'owner-kept':'owner-removed';}
  }
  const rules = rawRules.map(rule => {
    const main = [...new Set(mainPostings(rule).map(p=>p.account))].sort().join();
    const payer = rulePayer(rule,metadata);
    const paid = transactions.filter(e=>!ignored.has(e.id) && (e.rule?.id===rule.id || rulePayer(e,metadata) && [...new Set(mainPostings(e).map(p=>p.account))].sort().join()===main && (!payer || rulePayer(e,metadata).account===payer.account)));
    const last_payment = [...paid].sort((a,b)=>b.date.localeCompare(a.date))[0] || null;
    return {...rule, next_due:rule.date, last_payment, review:reviews[rule.id]||null, review_state:reviewStates[rule.id]||'waiting', payments:paid.map(e=>({id:e.id,date:e.date,...(e.rule?{rule:e.rule}:{})}))};
  });
  return {patterns, people, accounts, payments, merchants, rules};
}
