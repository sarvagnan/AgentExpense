import currencyMinorUnits from '../../schema/currency-minor-units.json' with {type:'json'};
import {dashboardDataPath, guardDashboardData} from '../../scripts/dashboard-data.mjs';
guardDashboardData();
import { paymentPosting as selectPayment, transactionCurrency, postingValue, sumCurrencies, chargedExpenses, fundedTransactions } from './currency.mjs';
import { today } from './date-time.mjs';
import { expenseSummary, previousMonth } from './month-summary.mjs';
import { auditProvenance } from './audit-provenance.mjs';
import { accountLabel, accountKind, expenseCategory, hasSegment } from './account-metadata.mjs';
import { sourceKind } from './source-labels.mjs';
import { compact, expandForecast, loadExpenseStore } from './expense-store.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const outputPath = dashboardDataPath('app/data/dashboard.json');
import { moduleSettings } from './ledger-manifest.mjs';
const { transactions, rules, prices, manifest, accounts } = await loadExpenseStore();
const provenance = await auditProvenance();
const buildToday = today();
const currentMonth = buildToday.slice(0, 7);


const shortAccount = account => accounts[account]?.kind ? accountLabel(account, accounts) : account.replaceAll(':', ' · ');
const kindOf = account => accountKind(account, accounts);

// Values are normalized to the funding account's currency before aggregation.
const expenseValues = new WeakMap();
// Entries the store marks `variable-estimate: excluded`, with the tag's stated reason and any policy term.
const variableExclusions = new WeakMap();

function toRecord(transaction, sequence) {
  const currency = transactionCurrency(transaction, accounts, manifest.defaultCurrency);
  const source = transaction.sources[0] || 'Derived ledger entry';
  const expensePostings = transaction.postings.filter((posting) => posting.account.startsWith('Expenses:'));
  const incomePostings = transaction.postings.filter((posting) => posting.account.startsWith('Income:'));
  const investmentPostings = transaction.postings.filter((posting) => kindOf(posting.account) === 'investment');
  const reimbursementPostings = transaction.postings.filter(posting => kindOf(posting.account) === 'person' && posting.currency === currency);
  const reimbursementAmount = reimbursementPostings.reduce((sum, posting) => sum + posting.amount, 0);
  const reimbursablePurchase = reimbursementAmount > 0;
  const reimbursementReceipt = reimbursementAmount < 0;

  const allocations = chargedExpenses(transaction, currency);
  const expenseAmount = allocations.reduce((sum, posting) => sum + posting.amount, 0);
  const incomeAmount = incomePostings.reduce((sum, posting) => sum - postingValue(posting, currency), 0);
  const investmentAmount = investmentPostings.reduce((sum, posting) => sum + postingValue(posting, currency), 0);
  const paymentPosting = selectPayment(transaction, accounts);
  // A transfer's headline is the sent side; its postings retain both amounts.
  const received = transaction.postings.filter(posting => posting.amount > 0 && (posting.currency === currency || posting.costCurrency === currency)).reduce((sum, posting) => sum + postingValue(posting, currency), 0);
  const outgoing = transaction.postings.filter(posting => posting.amount < 0 && posting.currency === currency).reduce((sum, posting) => sum - posting.amount, 0);
  const transferAmount = received || outgoing;
  // Holdings opened against equity were bought before the ledger began: a balance adjustment, not an investment.
  const opening = transaction.postings.some(posting => posting.account.startsWith('Equity:'));
  const kind = expensePostings.length ? (expenseAmount < 0 ? 'Refund' : 'Expense') : incomePostings.length ? 'Income' : investmentPostings.length && !opening ? 'Investment' : reimbursablePurchase ? 'Reimbursable purchase' : reimbursementReceipt ? 'Reimbursement' : 'Transfer';
  const bankFunded = transaction.postings.some(posting => posting.amount < 0 && kindOf(posting.account) === 'bank');
  const receives = kind => transaction.postings.some(posting => posting.amount > 0 && kindOf(posting.account) === kind);
  const walletFunded = transaction.postings.some(posting => posting.amount < 0 && ['bank', 'card'].includes(kindOf(posting.account)));
  const displayType = kind !== 'Transfer' ? kind
    : transaction.postings.some(posting => posting.account.startsWith('Equity:')) ? 'Balance adjustment'
    : bankFunded && receives('card') ? 'Card payment'
    : bankFunded && receives('person') ? 'Bill payment'
    : walletFunded && receives('wallet') ? 'Balance top-up'
    : 'Transfer';
  const originalDate = transaction.date;

  const record = {
    id: transaction.id,
    ...(transaction.storeId ? {storeId: transaction.storeId} : {}),
    provenance: provenance.get(transaction.storeId || transaction.id) || null,
    sequence,
    currency,
    date: transaction.date,
    originalDate,
    month: transaction.date.slice(0, 7),
    status: transaction.status === 'confirmed' ? 'Cleared' : 'Pending',
    description: transaction.description,
    kind,
    displayType,
    amount: Number(Math.abs(kind === 'Transfer' ? transferAmount : expensePostings.length ? expenseAmount : incomeAmount || investmentAmount || reimbursementAmount || 0).toFixed(2)),
    signedExpense: Number(expenseAmount.toFixed(2)),
    category: expensePostings.length ? expenseCategory(expensePostings[0].account, accounts) : incomePostings.length ? 'Income' : investmentPostings.length ? 'Investments' : reimbursementPostings.length ? 'People' : 'Transfers',
    ledgerAccount: expensePostings[0]?.account || incomePostings[0]?.account || investmentPostings[0]?.account || transaction.postings[0]?.account || '',
    paymentAccount: paymentPosting?.account || '',
    paymentLabel: paymentPosting ? shortAccount(paymentPosting.account) : 'Multiple postings',
    reimbursementGroup: transaction['reimbursement-group'] || '',
    reimbursementLabel: transaction['reimbursement-label'] || '',
    reimbursementParty: reimbursementPostings[0] ? accountLabel(reimbursementPostings[0].account, accounts) : '',
    reimbursementAmount: Number(reimbursementAmount.toFixed(2)),
    sources: transaction.sources,
    source,
    sourceKind: sourceKind(transaction),
    sourceReference: compact(transaction['serial-number'] || transaction['source-row'] || transaction['statement-page'] || transaction['source-page']),
    classification: compact(transaction.classification),
    originalAmount: transaction['original-amount'] ? `${transaction['original-amount'].amount} ${transaction['original-amount'].commodity}` : '',
    // Physical readings and explicit fuel-log confirmation are evidence, not an amount/date guess.
    fuelLog: moduleSettings(manifest).fuel && /^\d+(?:\.\d+)?$/.test(transaction['fuel-filled-litres'] || '') ? {
      litres: Number(transaction['fuel-filled-litres']),
      tripKm: /^\d+(?:\.\d+)?$/.test(transaction['trip-km'] || '') ? Number(transaction['trip-km']) : null,
      confirmed: transaction.postings.some((posting) => hasSegment(posting.account, ['Fuel']))
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
  expenseValues.set(record, allocations);
  return record;
}
const records = transactions.flatMap(transaction => fundedTransactions(transaction, accounts)).map(toRecord);

const reimbursementGroups = [...new Set(records.filter(record => record.reimbursementGroup).map(record => `${record.reimbursementGroup}\0${record.currency}`))].map(key => {
  const [id, currency] = key.split('\0');
  const rows = records.filter(record => record.reimbursementGroup === id && record.currency === currency);
  const purchases = rows.reduce((sum, row) => sum + Math.max(0, row.reimbursementAmount), 0);
  const received = rows.reduce((sum, row) => sum - Math.min(0, row.reimbursementAmount), 0);
  return { id, currency, label: rows[0].reimbursementLabel, purchases: Number(purchases.toFixed(2)), received: Number(received.toFixed(2)), outstanding: Number((purchases - received).toFixed(2)), paymentDate: rows.filter(row => row.reimbursementAmount < 0).at(-1)?.date || '' };
});

// Forecast the current month and twelve following months. The latest actual
// controls scheduled payments; meta.today controls the recording cut-off.
const isoDay = (date) => date.toISOString().slice(0, 10);
const forecastStart = `${currentMonth}-01`;
const forecastEnd = new Date(Date.UTC(Number(currentMonth.slice(0, 4)), Number(currentMonth.slice(5, 7)) + 12, 1));
const transactionsRule = new WeakMap();
const varyingForecast = new WeakMap();
const forecastRecords = expandForecast(rules, forecastStart, isoDay(forecastEnd))
  .flatMap(transaction => fundedTransactions(transaction, accounts))
  .map((transaction, index) => { const record = toRecord(transaction, records.length + index); transactionsRule.set(record, transaction.rule); if(transaction.amount==='varies')varyingForecast.set(record,{bill:!!transaction.billOccurrence,min:transaction.varying?.min??null,max:transaction.varying?.max??null}); return record; });
const recurringAccounts = new Set(forecastRecords.flatMap((record) => expenseValues.get(record).map((posting) => posting.account)));
const recurringSpend = (record) => expenseValues.get(record)
  .filter((posting) => recurringAccounts.has(posting.account))
  .reduce((sum, posting) => sum + posting.amount, 0);

const monthKeys = [...new Set(records.map((record) => record.month))].sort();
const currencies = [...new Set([manifest.defaultCurrency, ...records.map(record => record.currency), ...forecastRecords.map(record => record.currency)])].sort();
const months = monthKeys.flatMap(month => currencies.filter(currency => records.some(record => record.month === month && record.currency === currency && (month !== currentMonth || record.date <= buildToday))).map(currency => {
  const currencyRecords = records.filter(record => record.currency === currency && (month !== currentMonth || record.date <= buildToday));
  const rows = currencyRecords.filter(record => record.month === month && ['Expense', 'Refund'].includes(record.kind));
  const income = currencyRecords.filter((record) => record.month === month && record.kind === 'Income').reduce((sum, record) => sum + record.amount, 0);
  const investments = currencyRecords.filter((record) => record.month === month && record.kind === 'Investment').reduce((sum, record) => sum + record.amount, 0);
  const before = previousMonth(month);
  const previousRecords = records.filter(record => record.currency === currency && record.month === before);
  const comparison = previousRecords.length ? {month: before, ...expenseSummary(previousRecords.filter(record => month !== currentMonth || Number(record.date.slice(8, 10)) <= Number(buildToday.slice(8, 10))))} : null;
  return {
    month,
    currency,
    ...expenseSummary(rows),
    income: Number(income.toFixed(2)),
    investments: Number(investments.toFixed(2)),
    recurring: Number(rows.reduce((sum, record) => sum + recurringSpend(record), 0).toFixed(2)),
    comparison,
  };
}));


// Variable spending by portion of the month: expenses outside the recurring rule accounts,
// averaged over every full recorded month. Card instalment plans (statement rows such as
// "Principal Amount Amortization - <2/6>") are fixed commitments, and entries the ledger tags
// `variable-estimate: excluded` (multi-year policies, creator memberships forecast separately)
// are not variable spending either.
const portions = [[1, 7], [8, 14], [15, 21], [22, 31]];
const isInstalment = (record) => /Amortization - <\d+\/\d+>/.test(record.description);
function forecastFor(currency) {
const expenseRecords = records.filter(record => record.currency === currency && ['Expense', 'Refund'].includes(record.kind));
const currencyRecords = records.filter(record => record.currency === currency);
const currencyAsOf = currencyRecords.reduce((latest, record) => record.date > latest ? record.date : latest, '');
const daysPassed = currencyAsOf.slice(0, 7) === currentMonth ? Number(currencyAsOf.slice(8, 10)) : 0;
const start = new Date(`${currencyAsOf || forecastStart}T00:00:00Z`);
if (currencyAsOf) start.setUTCDate(start.getUTCDate() + 1);
if (isoDay(start) < forecastStart) start.setTime(Date.parse(forecastStart + 'T00:00:00Z'));
// Today's variable estimate remains until an actual in this currency is dated today.
const variableAfterDay = Number(buildToday.slice(8, 10)) - (currencyRecords.some(record => record.date === buildToday) ? 0 : 1);
const currencyForecastRecords = forecastRecords.filter(record => record.currency === currency && record.date >= isoDay(start));
const currencyMonths = [...new Set(currencyRecords.map(record => record.month))].sort();
const currencyFirstDate = currencyRecords.reduce((earliest, record) => record.date < earliest ? record.date : earliest, currencyAsOf);
const firstFullMonth = currencyFirstDate.endsWith('-01') ? currencyFirstDate.slice(0, 7) : (currencyMonths[1] || currentMonth);
const variableMonths = currencyMonths.filter((month) => month >= firstFullMonth && month < currentMonth);
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
  currency,
  asOf: currencyAsOf,
  currentMonth,
  daysPassed,
  variableAfterDay,
  start: isoDay(start),
  end: isoDay(forecastEnd),
  // Months before this one are partial and excluded from comparison averages.
  firstFullMonth,
  recurringAccounts: [...recurringAccounts].sort(),
  variable,
  months: [...new Set([currentMonth, ...currencyForecastRecords.map(record => record.month)])].sort().map((month) => {
    const rows = currencyForecastRecords.filter((record) => record.month === month);
    const expenses = rows.filter((record) => record.kind === 'Expense' || record.kind === 'Refund');
    return {
      month,
      committed: Number(expenses.reduce((sum, record) => sum + record.signedExpense, 0).toFixed(2)),
      investments: Number(rows.filter((record) => record.kind === 'Investment').reduce((sum, record) => sum + record.amount, 0).toFixed(2)),
    };
  }),
  items: currencyForecastRecords.map((record) => ({
    currency,
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
    ...(varyingForecast.has(record)?{varying:varyingForecast.get(record)}:{}),
  })),
};

return forecast;
}
const forecasts = currencies.map(forecastFor);
// The compatibility projection is explicitly a single currency, never a mixed sum.
const forecast = forecasts.find(item => item.currency === manifest.defaultCurrency);

// An empty calendar month has no currency: never invent one zero per account currency.
if (!months.some(row => row.month === currentMonth)) {
  months.push({ month: currentMonth, currency: '', total: 0, income: 0, investments: 0, recurring: 0, count: 0, pending: 0, categories: [], daily: [], comparison: null });
  months.sort((a, b) => a.month.localeCompare(b.month));
}
const calendarKeys = [...new Set([...monthKeys, currentMonth])].sort();
const payload = {
  meta: {
    currencyMinorUnits,
    ledger: manifest,
    accounts,
    generatedAt: new Date().toISOString(),
    today: buildToday,
    defaultMonth: currentMonth,
    firstMonth: calendarKeys[0],
    lastMonth: calendarKeys.at(-1),
    transactionCount: records.length,
  },
  months,
  forecast,
  forecasts,
  prices,
  reimbursementGroups,
  transactions: records,
};

await mkdir(path.dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
console.log(`Wrote ${records.length} store transactions across ${months.length} months, and ${forecastRecords.length} forecast entries for ${forecast.start}..${forecast.end}, to ${outputPath}`);
