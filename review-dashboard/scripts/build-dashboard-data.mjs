import { auditProvenance } from './audit-provenance.mjs';
import { sourceKind } from './source-labels.mjs';
import { compact, expandForecast, loadExpenseStore } from './expense-store.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectDir = path.resolve(scriptDir, '..');
const outputPath = path.join(projectDir, 'app', 'data', 'dashboard.json');
const { transactions, rules, prices } = await loadExpenseStore();
const provenance = await auditProvenance();


function shortAccount(account) {
  return account
    .replace('Liabilities:Credit Cards:', '')
    .replace('Assets:Bank:', '')
    .replace('Assets:', '')
    .replaceAll(':', ' · ');
}

function topCategory(account) {
  const [, first = 'Other'] = account.split(':');
  const names = {
    Basketdemo: 'Groceries', Donations: 'Donations', Fees: 'Fees', Food: 'Food & dining',
    Housing: 'Housing', Insurance: 'Insurance', Subscriptions: 'Subscriptions', Taxes: 'Taxes',
    Transport: 'Transport', Uncategorized: 'Uncategorized', Utilities: 'Utilities',
  };
  return names[first] || first;
}

// The INR a posting moves: rupees as written, or the recorded cost of fund units.
const inrValue = (posting) => posting.currency === 'INR' ? posting.amount : posting.cost ?? 0;
// INR value of each expense posting, kept beside the records rather than published.
const inrExpenses = new WeakMap();
// Entries the store marks `variable-estimate: excluded`, with the tag's stated reason and any policy term.
const variableExclusions = new WeakMap();

function toRecord(transaction, sequence) {
  const source = transaction.sources[0] || 'Derived ledger entry';
  const expensePostings = transaction.postings.filter((posting) => posting.account.startsWith('Expenses:'));
  const incomePostings = transaction.postings.filter((posting) => posting.account.startsWith('Income:'));
  const investmentPostings = transaction.postings.filter((posting) => posting.account.startsWith('Assets:Investments:'));
  const reimbursementPostings = transaction.postings.filter(posting => posting.account.startsWith('Assets:Reimbursements:') && posting.currency === 'INR');
  const reimbursementAmount = reimbursementPostings.reduce((sum, posting) => sum + posting.amount, 0);
  const reimbursablePurchase = reimbursementAmount > 0;
  const reimbursementReceipt = reimbursementAmount < 0;

  const inrCounterpart = transaction.postings
    .filter((posting) => posting.currency === 'INR' && !posting.account.startsWith('Expenses:'))
    .sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount))[0];
  const foreignExpenses = expensePostings.filter((posting) => posting.currency !== 'INR');
  const foreignCurrencies = new Set(foreignExpenses.map((posting) => posting.currency));
  if (foreignCurrencies.size > 1) {
    throw new Error(`Cannot infer separate currency rates for ${transaction.date}: ${transaction.description}`);
  }
  const foreignTotal = foreignExpenses.reduce((sum, posting) => sum + posting.amount, 0);
  const inrExpenseTotal = expensePostings.filter((posting) => posting.currency === 'INR')
    .reduce((sum, posting) => sum + posting.amount, 0);
  const foreignRate = foreignTotal && inrCounterpart
    ? Math.abs((inrCounterpart.amount + inrExpenseTotal) / foreignTotal) : null;
  const expenseAmount = expensePostings.reduce(
    (sum, posting) => sum + (posting.currency === 'INR' ? posting.amount : posting.amount * (foreignRate || 0)), 0,
  );
  const incomeAmount = incomePostings
    .filter((posting) => posting.currency === 'INR')
    .reduce((sum, posting) => sum - posting.amount, 0);
  const investmentAmount = investmentPostings.reduce((sum, posting) => sum + inrValue(posting), 0);
  const paymentCandidates = transaction.postings
    .filter((posting) => posting.currency === 'INR' && !posting.account.startsWith('Expenses:') && !posting.account.startsWith('Income:') && !posting.account.startsWith('Assets:Investments:') && !posting.account.startsWith('Assets:Reimbursements:'))
    .sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount));
  // Paid With follows the money leaving; incoming-only entries retain the old fallback.
  const paymentPosting = paymentCandidates.find(posting => posting.amount < 0) || paymentCandidates[0];
  // Count one side of the movement, never the net of a balanced entry.
  const transferAmount = transaction.postings
    .filter(posting => inrValue(posting) > 0)
    .reduce((sum, posting) => sum + inrValue(posting), 0);
  // Holdings opened against equity were bought before the ledger began: a balance adjustment, not an investment.
  const opening = transaction.postings.some(posting => posting.account.startsWith('Equity:'));
  const kind = expensePostings.length ? (expenseAmount < 0 ? 'Refund' : 'Expense') : incomePostings.length ? 'Income' : investmentPostings.length && !opening ? 'Investment' : reimbursablePurchase ? 'Reimbursable purchase' : reimbursementReceipt ? 'Reimbursement' : 'Transfer';
  const bankFunded = transaction.postings.some(posting => posting.amount < 0 && posting.account.startsWith('Assets:Bank:'));
  const receives = prefix => transaction.postings.some(posting => posting.amount > 0 && posting.account.startsWith(prefix));
  const displayType = kind !== 'Transfer' ? kind
    : transaction.postings.some(posting => posting.account.startsWith('Equity:')) ? 'Balance adjustment'
    : bankFunded && receives('Liabilities:Credit Cards:') ? 'Card payment'
    : bankFunded && receives('Liabilities:Accounts Payable:') ? 'Bill payment'
    : bankFunded && (receives('Assets:Wallets:App Store Balance') || receives('Assets:Wallets:Shopping Wallet')) ? 'Balance top-up'
    : 'Transfer';
  const originalDate = transaction.date;

  const record = {
    id: transaction.id,
    provenance: provenance.get(transaction.id) || null,
    sequence,
    date: transaction.date,
    originalDate,
    month: transaction.date.slice(0, 7),
    status: transaction.status === 'confirmed' ? 'Cleared' : 'Pending',
    description: transaction.description,
    kind,
    displayType,
    amount: Number(Math.abs(kind === 'Transfer' ? transferAmount : expensePostings.length ? expenseAmount : incomeAmount || investmentAmount || reimbursementAmount || 0).toFixed(2)),
    signedExpense: Number(expenseAmount.toFixed(2)),
    category: expensePostings.length ? topCategory(expensePostings[0].account) : incomePostings.length ? 'Income' : investmentPostings.length ? 'Investments' : reimbursementPostings.length ? (reimbursablePurchase ? 'Software · ' + reimbursementPostings[0].account.split(':').at(-1) : 'Reimbursements') : 'Transfers',
    ledgerAccount: expensePostings[0]?.account || incomePostings[0]?.account || investmentPostings[0]?.account || transaction.postings[0]?.account || '',
    paymentAccount: paymentPosting?.account || '',
    paymentLabel: paymentPosting ? shortAccount(paymentPosting.account) : 'Multiple postings',
    reimbursementGroup: transaction['reimbursement-group'] || '',
    reimbursementLabel: transaction['reimbursement-label'] || '',
    reimbursementParty: reimbursementPostings[0]?.account.split(':')[2] || '',
    reimbursementAmount: Number(reimbursementAmount.toFixed(2)),
    sources: transaction.sources,
    source,
    sourceKind: sourceKind(transaction),
    sourceReference: compact(transaction['serial-number'] || transaction['source-row'] || transaction['statement-page'] || transaction['source-page']),
    classification: compact(transaction.classification),
    originalAmount: transaction['original-amount'] ? `${transaction['original-amount'].amount} ${transaction['original-amount'].commodity}` : '',
    // Physical readings and explicit fuel-log confirmation are evidence, not an amount/date guess.
    fuelLog: /^\d+(?:\.\d+)?$/.test(transaction['fuel-filled-litres'] || '') ? {
      litres: Number(transaction['fuel-filled-litres']),
      tripKm: /^\d+(?:\.\d+)?$/.test(transaction['trip-km'] || '') ? Number(transaction['trip-km']) : null,
      confirmed: transaction.postings.some((posting) => posting.account === 'Expenses:Transport:Fuel')
        && (/user-confirmed.*match from fuel log/.test(transaction.classification || '')
          || (source === 'user-provided fuel log' && /user-confirmed fuel expense/.test(transaction.classification || ''))),
    } : null,
    postings: transaction.postings.map((posting) => ({ ...posting, label: shortAccount(posting.account) })),
  };
  if (transaction['variable-estimate']?.excluded) variableExclusions.set(record, {
    // Keep the existing short display label; the complete reason remains in the store.
    reason: transaction['variable-estimate'].reason.split(',')[0].trim() || 'excluded',
    term: transaction['policy-term'] ? `${transaction['policy-term'].years}-year policy` : undefined,
  });
  inrExpenses.set(record, expensePostings.map((posting) => ({
    account: posting.account,
    amount: posting.currency === 'INR' ? posting.amount : posting.amount * (foreignRate || 0),
  })));
  return record;
}
const records = transactions.map(toRecord);

const reimbursementGroups = [...new Set(records.map(record => record.reimbursementGroup).filter(Boolean))].map(id => {
  const rows = records.filter(record => record.reimbursementGroup === id);
  const purchases = rows.reduce((sum, row) => sum + Math.max(0, row.reimbursementAmount), 0);
  const received = rows.reduce((sum, row) => sum - Math.min(0, row.reimbursementAmount), 0);
  return { id, label: rows[0].reimbursementLabel, purchases: Number(purchases.toFixed(2)), received: Number(received.toFixed(2)), outstanding: Number((purchases - received).toFixed(2)), paymentDate: rows.filter(row => row.reimbursementAmount < 0).at(-1)?.date || '' };
});

// Forecast: the store's periodic rules from the day after the latest actual entry to the end
// of the twelfth following month. Only occurrences inside this window are kept.
const isoDay = (date) => date.toISOString().slice(0, 10);
const asOf = records.reduce((latest, record) => record.date > latest ? record.date : latest, '');
const forecastStart = new Date(`${asOf}T00:00:00Z`);
forecastStart.setUTCDate(forecastStart.getUTCDate() + 1);
const forecastEnd = new Date(Date.UTC(Number(asOf.slice(0, 4)), Number(asOf.slice(5, 7)) + 12, 1));
const transactionsRule = new WeakMap();
const forecastRecords = expandForecast(rules, isoDay(forecastStart), isoDay(forecastEnd))
  .map((transaction, index) => { const record = toRecord(transaction, records.length + index); transactionsRule.set(record, transaction.rule); return record; });
const recurringAccounts = new Set(forecastRecords.flatMap((record) => inrExpenses.get(record).map((posting) => posting.account)));
const recurringSpend = (record) => inrExpenses.get(record)
  .filter((posting) => recurringAccounts.has(posting.account))
  .reduce((sum, posting) => sum + posting.amount, 0);

const expenseRecords = records.filter((record) => record.kind === 'Expense' || record.kind === 'Refund');
const monthKeys = [...new Set(records.map((record) => record.month))].sort();
const months = monthKeys.map((month) => {
  const rows = expenseRecords.filter((record) => record.month === month);
  const income = records.filter((record) => record.month === month && record.kind === 'Income').reduce((sum, record) => sum + record.amount, 0);
  const investments = records.filter((record) => record.month === month && record.kind === 'Investment').reduce((sum, record) => sum + record.amount, 0);
  const total = rows.reduce((sum, record) => sum + record.signedExpense, 0);
  const categoryMap = new Map();
  const dailyMap = new Map();
  for (const row of rows) {
    categoryMap.set(row.category, (categoryMap.get(row.category) || 0) + row.signedExpense);
    const day = Number(row.date.slice(-2));
    dailyMap.set(day, (dailyMap.get(day) || 0) + row.signedExpense);
  }
  return {
    month,
    total: Number(total.toFixed(2)),
    income: Number(income.toFixed(2)),
    investments: Number(investments.toFixed(2)),
    recurring: Number(rows.reduce((sum, record) => sum + recurringSpend(record), 0).toFixed(2)),
    count: rows.length,
    pending: rows.filter((row) => row.status === 'Pending').length,
    categories: [...categoryMap.entries()].map(([category, amount]) => ({ category, amount: Number(amount.toFixed(2)) })).sort((a, b) => b.amount - a.amount),
    daily: [...dailyMap.entries()].map(([day, amount]) => ({ day, amount: Number(amount.toFixed(2)) })).sort((a, b) => a.day - b.day),
  };
});

const forecastMonthKeys = [...new Set(forecastRecords.map((record) => record.month))].sort();
const firstDate = records.reduce((earliest, record) => record.date < earliest ? record.date : earliest, asOf);
const firstFullMonth = firstDate.endsWith('-01') ? firstDate.slice(0, 7) : monthKeys[1];

// Variable spending by portion of the month: expenses outside the recurring rule accounts,
// averaged over every full recorded month. Card instalment plans (statement rows such as
// "Principal Amount Amortization - <2/6>") are fixed commitments, and entries the ledger tags
// `variable-estimate: excluded` (multi-year policies, creator memberships forecast separately)
// are not variable spending either.
const portions = [[1, 7], [8, 14], [15, 21], [22, 31]];
const isInstalment = (record) => /Amortization - <\d+\/\d+>/.test(record.description);
const variableMonths = monthKeys.filter((month) => month >= firstFullMonth && month < asOf.slice(0, 7));
const variableGrid = variableMonths.map(() => portions.map(() => 0));
const exclusions = new Map();
for (const record of expenseRecords) {
  const row = variableMonths.indexOf(record.month);
  if (row < 0) continue;
  const day = Number(record.date.slice(-2));
  const amount = record.signedExpense - recurringSpend(record);
  const instalment = isInstalment(record);
  const tagged = variableExclusions.get(record);
  if (instalment || tagged) {
    const key = instalment ? record.description.replace(/^.*?Amortization - <\d+\/(\d+)>\s*/, '$1 instalments, ') : record.ledgerAccount;
    const entry = exclusions.get(key) || { reason: instalment ? 'instalment plan' : tagged.reason, detail: instalment ? key : tagged.term || tagged.reason, amount: 0, months: new Set(), account: '' };
    entry.amount += amount;
    entry.months.add(record.month);
    // Name the plan from its principal posting's account rather than the card's merchant code.
    if (!record.ledgerAccount.startsWith('Expenses:Uncategorized')) entry.account = instalment ? record.ledgerAccount.split(':').slice(-2).join(' ') : record.ledgerAccount.split(':').slice(1).join(' · ');
    exclusions.set(key, entry);
    continue;
  }
  variableGrid[row][portions.findIndex(([from, to]) => day >= from && day <= to)] += amount;
}
const median = (values) => { const sorted = [...values].sort((a, b) => a - b); const middle = sorted.length / 2; return sorted.length % 2 ? sorted[Math.floor(middle)] : (sorted[middle - 1] + sorted[middle]) / 2; };
const variable = {
  months: variableMonths,
  portions: portions.map(([from, to], index) => {
    const values = variableGrid.map((row) => row[index]);
    return {
      from,
      to,
      average: Number((values.reduce((sum, value) => sum + value, 0) / (values.length || 1)).toFixed(2)),
      median: Number((values.length ? median(values) : 0).toFixed(2)),
      min: Number((values.length ? Math.min(...values) : 0).toFixed(2)),
      max: Number((values.length ? Math.max(...values) : 0).toFixed(2)),
      byMonth: values.map((value) => Number(value.toFixed(2))),
    };
  }),
  excluded: [...exclusions.values()].sort((a, b) => b.amount - a.amount).map(({ reason, detail, amount, months, account }) => ({ reason, description: account ? `${account} (${detail})` : detail, amount: Number(amount.toFixed(2)), from: [...months].sort()[0], to: [...months].sort().at(-1) })),
};
const forecast = {
  asOf,
  start: isoDay(forecastStart),
  end: isoDay(forecastEnd),
  // Months before this one are partial and excluded from comparison averages.
  firstFullMonth,
  recurringAccounts: [...recurringAccounts].sort(),
  variable,
  months: forecastMonthKeys.map((month) => {
    const rows = forecastRecords.filter((record) => record.month === month);
    const expenses = rows.filter((record) => record.kind === 'Expense' || record.kind === 'Refund');
    return {
      month,
      committed: Number(expenses.reduce((sum, record) => sum + record.signedExpense, 0).toFixed(2)),
      investments: Number(rows.filter((record) => record.kind === 'Investment').reduce((sum, record) => sum + record.amount, 0).toFixed(2)),
    };
  }),
  items: forecastRecords.map((record) => ({
    date: record.date,
    month: record.month,
    description: record.description,
    rule: transactionsRule.get(record),
    kind: record.kind,
    category: record.category,
    account: record.ledgerAccount,
    amount: record.amount,
    signedExpense: record.signedExpense,
    paymentLabel: record.paymentLabel,
    sources: record.sources,
  })),
};

const now = new Date();
const currentMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
const defaultMonth = monthKeys.includes(currentMonth) ? currentMonth : monthKeys.at(-1);
const payload = {
  meta: {
    ledger: 'data/expenses/*.json',
    generatedAt: new Date().toISOString(),
    defaultMonth,
    firstMonth: monthKeys[0],
    lastMonth: monthKeys.at(-1),
    transactionCount: records.length,
  },
  months,
  forecast,
  prices,
  reimbursementGroups,
  transactions: records,
};

await mkdir(path.dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
console.log(`Wrote ${records.length} store transactions across ${months.length} months, and ${forecastRecords.length} forecast entries for ${forecast.start}..${forecast.end}, to ${outputPath}`);
