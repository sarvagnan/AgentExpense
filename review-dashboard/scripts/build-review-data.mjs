import { sourceType } from './source-labels.mjs';
import { compact, loadExpenseStore } from './expense-store.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectDir = path.resolve(scriptDir, '..');
const outputPath = path.join(projectDir, 'app', 'data', 'pending-transactions.json');

const { transactions } = await loadExpenseStore();


function patternFor(description, source) {
  const upi = description.match(/UPI\/DR\/\d+\/([^/]+)/i);
  if (upi) return `UPI · ${compact(upi[1])}`;
  if (source.endsWith('.pdf')) return compact(description).toUpperCase();
  return compact(description);
}

const pending = transactions
  .filter((transaction) => transaction.status === 'pending')
  .map((transaction) => {
    const reviewPosting = transaction.postings.find(
      (posting) => posting.currency === 'INR' && (posting.account.startsWith('Expenses:') || posting.account.startsWith('Income:')),
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
    const amount = Math.abs(reviewPosting.amount);
    return {
      id: transaction.id,
      date: transaction.date,
      originalDate: transaction.date,
      description: transaction.description,
      pattern: patternFor(transaction.description, source),
      amount,
      signedAmount: reviewPosting.amount,
      kind,
      currentAccount: reviewPosting.account,
      source,
      sourceType: sourceType(transaction),
      sourceReference: reference,
      statementEnd: compact(transaction['statement-end']),
      classification: compact(transaction.classification) || 'Pending classification',
    };
  })
  .filter(Boolean);

const totals = pending.reduce(
  (summary, transaction) => {
    summary.net += transaction.signedAmount;
    summary.absolute += transaction.amount;
    summary.byKind[transaction.kind] = (summary.byKind[transaction.kind] || 0) + 1;
    summary.bySource[transaction.sourceType] = (summary.bySource[transaction.sourceType] || 0) + 1;
    return summary;
  },
  { net: 0, absolute: 0, byKind: {}, bySource: {} },
);

const payload = {
  meta: {
    ledger: 'data/expenses/*.json',
    pendingTransactionCount: pending.length,
    pendingNetAmount: Number(totals.net.toFixed(2)),
    pendingAbsoluteAmount: Number(totals.absolute.toFixed(2)),
    byKind: totals.byKind,
    bySource: totals.bySource,
  },
  transactions: pending,
};

await mkdir(path.dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
console.log(`Wrote ${pending.length} pending transactions to ${outputPath}`);
