// Monetary arithmetic always names a currency. Maps never imply an exchange rate.
export const roundMoney = value => Number(value.toFixed(2));
export function sumCurrencies(rows, value = row => row.amount, currency = row => row.currency) {
  const sums = {};
  for (const row of rows) {
    const code = currency(row);
    if (!code) throw new Error('A monetary amount needs a currency');
    sums[code] = (sums[code] || 0) + value(row);
  }
  return Object.fromEntries(Object.entries(sums).map(([code, amount]) => [code, roundMoney(amount)]));
}
export function paymentPosting(transaction, accounts) {
  const candidates = transaction.postings.filter(posting => !/^(Expenses|Income)(:|$)/.test(posting.account)
    && !['investment', 'person'].includes(accounts[posting.account]?.kind));
  return [...candidates].sort((a, b) => a.currency === b.currency ? Math.abs(b.amount) - Math.abs(a.amount) : a.currency.localeCompare(b.currency)).find(posting => posting.amount < 0)
    || [...candidates].sort((a, b) => a.currency === b.currency ? Math.abs(b.amount) - Math.abs(a.amount) : a.currency.localeCompare(b.currency))[0];
}
export function postingValue(posting, currency) {
  if (posting.amount === 0) return 0;
  if (posting.currency === currency) return posting.amount;
  if (posting.costCurrency === currency && posting.cost !== undefined) return posting.cost;
  throw new Error(`Missing ${currency} cost for ${posting.account} (${posting.currency})`);
}
export function transactionCurrency(transaction, accounts, fallback) {
  const payment = paymentPosting(transaction, accounts);
  return accounts[payment?.account]?.currency || payment?.currency || fallback;
}

// Split only independently funded expense/income sides. Each original posting belongs
// to exactly one row, so account balances never count a posting twice.
export function fundedTransactions(transaction, accounts) {
  if (!transaction.postings.some(posting => /^(Expenses|Income):/.test(posting.account))) return [transaction];
  const funding = transaction.postings.filter(posting => !/^(Expenses|Income):/.test(posting.account)
    && !['investment', 'person'].includes(accounts[posting.account]?.kind));
  const expenses = transaction.postings.filter(posting => posting.account.startsWith('Expenses:'));
  const refund = expenses.length && expenses.every(posting => posting.amount <= 0);
  const direction = expenses.length && !refund ? -1 : 1;
  const currencies = [...new Set(funding.filter(posting => Math.sign(posting.amount) === direction)
    .map(posting => accounts[posting.account]?.currency || posting.currency))].sort();
  if (currencies.length < 2) return [transaction];
  const groups = new Map(currencies.map(currency => [currency, []]));
  for (const posting of transaction.postings) {
    const currency = groups.has(posting.costCurrency) ? posting.costCurrency : posting.currency;
    if (!groups.has(currency)) throw new Error(`Cannot determine funding currency for ${transaction.id}: ${posting.account}`);
    groups.get(currency).push(posting);
  }
  return currencies.map(currency => ({...transaction, id: `${transaction.id}-${currency}`, storeId: transaction.id, postings: groups.get(currency)}));
}

// Allocate foreign expenses using the actual net charge. Rounded posting costs
// describe the price; they must not replace the money moved by the funding side.
export function chargedExpenses(transaction, currency) {
  const expenses = transaction.postings.filter(posting => posting.account.startsWith('Expenses:'));
  const foreign = expenses.filter(posting => posting.currency !== currency);
  const nativeTotal = expenses.filter(posting => posting.currency === currency).reduce((sum, posting) => sum + posting.amount, 0);
  if (!foreign.length) return expenses.map(posting => ({account: posting.account, amount: posting.amount}));
  const counterparts = transaction.postings.filter(posting => !posting.account.startsWith('Expenses:')
    && posting.currency === currency);
  const charged = -counterparts.reduce((sum, posting) => sum + posting.amount, 0);
  const remaining = charged - nativeTotal;
  const commodities = new Set(foreign.map(posting => posting.currency));
  // A single original commodity uses exactly the old charged/foreign-total rate.
  // Several original commodities need their explicit costs as relative weights;
  // those weights are still reconciled to the actual charged amount.
  const weight = posting => commodities.size === 1 ? posting.amount : postingValue(posting, currency);
  const foreignTotal = foreign.reduce((sum, posting) => sum + weight(posting), 0);
  if (!foreignTotal && remaining) throw new Error(`Cannot allocate ${currency} charge for ${transaction.id}`);
  const rate = foreignTotal ? remaining / foreignTotal : 0;
  return expenses.map(posting => ({account: posting.account,
    amount: posting.currency === currency ? posting.amount : weight(posting) * rate}));
}
