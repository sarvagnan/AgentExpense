import {dashboardDataPath, guardDashboardData} from '../../scripts/dashboard-data.mjs';
guardDashboardData();
import { transactionCurrency, postingValue, sumCurrencies, chargedExpenses, fundedTransactions } from './currency.mjs';
import { sourceType } from './source-labels.mjs';
import { compact, loadExpenseStore } from './expense-store.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const outputPath = dashboardDataPath('app/data/pending-transactions.json');

const { transactions, accounts, manifest } = await loadExpenseStore();


function patternFor(description, source) {
  if (source.endsWith('.pdf')) return compact(description).toUpperCase();
  return compact(description);
}

const pending = transactions
  .filter((transaction) => transaction.status === 'pending')
  .flatMap(transaction => fundedTransactions(transaction, accounts))
  .map((transaction) => {
    const reviewPosting = transaction.postings.find(
      (posting) => (posting.account.startsWith('Expenses:') || posting.account.startsWith('Income:')),
    );
    if (!reviewPosting) return null;

    const source = transaction.sources[0] || 'Derived ledger entry';
    const reference = compact(transaction['serial-number'] || transaction['source-row'] || transaction['statement-page'] || transaction['source-page']);
    const isExpense = reviewPosting.account.startsWith('Expenses:');
    const kind = isExpense
      ? reviewPosting.amount >= 0
        ? 'Expense'
        : 'Refund'
      : 'Income';
    const currency = transactionCurrency(transaction, accounts, manifest.defaultCurrency);
    const allocation = isExpense
      ? chargedExpenses(transaction, currency).reduce((sum, posting) => sum + posting.amount, 0)
      : transaction.postings.filter(posting => posting.account.startsWith('Income:')).reduce((sum, posting) => sum + postingValue(posting, currency), 0);
    const foreignExpense = isExpense && transaction.postings.some(posting => posting.account.startsWith('Expenses:') && posting.currency !== currency);
    const signedAmount = foreignExpense ? Number(allocation.toFixed(2)) : allocation;
    const amount = Math.abs(signedAmount);
    return {
      id: transaction.id,
      ...(transaction.storeId ? {storeId: transaction.storeId} : {}),
      date: transaction.date,
      originalDate: transaction.date,
      description: transaction.description,
      pattern: patternFor(transaction.description, source),
      amount,
      currency,
      signedAmount,
      kind,
      currentAccount: reviewPosting.account,
      source,
      sourceType: sourceType(transaction, accounts),
      sourceReference: reference,
      statementEnd: compact(transaction['statement-end']),
      classification: compact(transaction.classification) || 'Pending classification',
    };
  })
  .filter(Boolean);

const totals = pending.reduce(
  (summary, transaction) => {
    summary.byKind[transaction.kind] = (summary.byKind[transaction.kind] || 0) + 1;
    summary.bySource[transaction.sourceType] = (summary.bySource[transaction.sourceType] || 0) + 1;
    return summary;
  },
  { byKind: {}, bySource: {} },
);

const payload = {
  meta: {
    ledger: 'data/expenses/*.json',
    pendingTransactionCount: pending.length,
    pendingNetAmount: sumCurrencies(pending, row => row.signedAmount),
    pendingAbsoluteAmount: sumCurrencies(pending),
    byKind: totals.byKind,
    bySource: totals.bySource,
  },
  transactions: pending,
};

await mkdir(path.dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
console.log(`Wrote ${pending.length} pending transactions to ${outputPath}`);
