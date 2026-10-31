import { accountLabel, expenseCategory, hasSegment, paymentAccounts } from './account-metadata.mjs';
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
export function buildPatterns(transactions, metadata = {}) {
  const patterns = {}, accounts = Object.keys(metadata);
  const people = Object.entries(metadata).filter(([, m]) => m.kind === 'person').map(([a]) => accountLabel(a, metadata));
  const payments = paymentAccounts(metadata);
  const top = counts => [...counts].sort((a,b) => b[1]-a[1] || a[0].localeCompare(b[0]))[0] || [null, 0];
  for (const entry of transactions) {
    const id = patternOf(entry, metadata); if (!id) continue;
    const st = patterns[id] ||= {count:0, last:[], pays:new Map(), expenses:new Map(), names:new Map()};
    const sides = entry.postings.filter(p => payments[p.account]);
    const pay = sides.find(p => !['person'].includes(metadata[p.account].kind) && p.amount < 0) || sides.find(p => metadata[p.account].kind !== 'person') || sides[0];
    const expense = entry.postings.find(p => p.account.startsWith('Expenses:') && p.amount > 0);
    st.count++;
    if (pay) st.pays.set(pay.account, (st.pays.get(pay.account) || 0) + 1);
    if (expense) st.expenses.set(expense.account, (st.expenses.get(expense.account) || 0) + 1);
    st.names.set(entry.description, (st.names.get(entry.description) || 0) + 1);
    st.last.push({date:entry.date, description:entry.description, currency:pay ? metadata[pay.account]?.currency || pay.currency : null, amount:pay ? String(Math.abs(pay.amount)) : '', ...(entry['fuel-filled-litres'] ? {litres:entry['fuel-filled-litres']} : {})});
  }
  for (const st of Object.values(patterns)) {
    [st.usualPay, st.usualShare] = top(st.pays); [st.usualExpense] = top(st.expenses);
    st.usualCategory = st.usualExpense ? expenseCategory(st.usualExpense, metadata) : null;
    st.known = [...st.names].sort((a,b) => b[1]-a[1] || a[0].localeCompare(b[0])).slice(0,10).map(([name]) => name);
    st.last = st.last.sort((a,b) => b.date.localeCompare(a.date)).slice(0,3);
    delete st.pays; delete st.expenses; delete st.names;
  }
  return {patterns, people, accounts, payments};
}
