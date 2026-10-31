/** Historical suggestions, built from the same normalized store as the dashboard. */
export function patternOf(e) {
  const accounts = e.postings.map(p => p.account), d = e.description.toLowerCase();
  if (accounts.includes('Expenses:Transport:Fuel')) return 'fuel';
  if (d.includes('card bill payment')) return 'cardbill';
  if (accounts.includes('Income:Card Rewards')) return 'rewards';
  if (accounts.some(a => /^(Expenses:Travel:(Auto|Taxi|Airport Cabs))/.test(a)) || /zipcab|townride/.test(d)) return 'ride';
  if (accounts.some(a => a.startsWith('Expenses:Subscriptions'))) return 'sub';
  if (d.includes('mealcart') && !d.includes('pantrydemo')) return 'mealcart';
  if (d.includes('basketdemo')) return 'basketdemo';
  if (accounts.some(a => a.startsWith('Expenses:Food:Dining & Delivery'))) return 'dining';
  if (e.postings.some(p => p.account.includes('Accounts Payable') && p.amount < 0) && accounts.some(a => a.startsWith('Assets:Bank'))) return 'payback';
  return null;
}
export function buildPatterns(transactions) {
  const patterns = {}, people = new Set(), accounts = new Set();
  const top = counts => [...counts].sort((a,b) => b[1]-a[1] || a[0].localeCompare(b[0]))[0] || [null, 0];
  for (const e of transactions) {
    e.postings.forEach(p => { accounts.add(p.account); const m = p.account.match(/^([^:]+):Accounts Payable/); if (m) people.add(m[1]); });
    const id = patternOf(e); if (!id) continue;
    const st = patterns[id] ||= { count: 0, last: [], pays: new Map(), expenses: new Map() };
    const sides = e.postings.filter(p => /^(Assets:|Liabilities:Credit Cards:)/.test(p.account));
    const pay = sides.find(p => p.amount < 0) || sides[0];
    const expense = e.postings.find(p => p.account.startsWith('Expenses:') && p.amount > 0);
    st.count++;
    if (pay) st.pays.set(pay.account, (st.pays.get(pay.account) || 0) + 1);
    if (expense) st.expenses.set(expense.account, (st.expenses.get(expense.account) || 0) + 1);
    st.last.push({ date: e.date, description: e.description, amount: pay ? String(Math.abs(pay.amount)) : '', ...(e['fuel-filled-litres'] ? {litres: e['fuel-filled-litres']} : {}) });
  }
  for (const st of Object.values(patterns)) {
    [st.usualPay, st.usualShare] = top(st.pays); [st.usualExpense] = top(st.expenses);
    st.last = st.last.sort((a,b) => b.date.localeCompare(a.date)).slice(0,3);
    delete st.pays; delete st.expenses;
  }
  return { patterns, people: [...people].sort(), accounts: [...accounts].sort() };
}
