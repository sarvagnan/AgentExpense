import { disposableDashboard, temporaryDirectory } from './test-isolation.mjs';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import {CURRENT_FORMAT} from '../scripts/ledger-manifest.mjs';

const savings = 'Assets:Checking:Day', card = 'Liabilities:Plastic:Main';
const posting = (account, amount) => ({ account, amount: String(amount), commodity: 'INR' });
const cases = [
  { name: 'water-tax advance', amount: 1440, account: savings, label: 'Savings Account', kind:'Reimbursable purchase', category:'People',
    postings: [posting('Casey:Accounts Payable:Morgan:Water Tax:Flat', 1440), posting(savings, -1440)] },
  { name: 'Credit card bill payment', amount: 2500, account: savings, label: 'Savings Account', displayType:'Card payment',
    postings: [posting(card, 2500), posting(savings, -2500)] },
  { name: 'Fictional wallet top-up', amount: 1000, account: card, label: 'Rewards Card', displayType:'Balance top-up',
    postings: [posting('Stored:Lunch', 1000), posting(card, -1000)] },
  { name: 'split-funded transfer prefers largest negative even over a larger receipt', amount: 1000, account: savings, label: 'Savings Account',
    postings: [posting('Stored:Lunch', 1000), posting(card, -400), posting(savings, -600)] },
  { name: 'incoming-only refund candidates keep the largest absolute fallback', amount: 100, account: savings, label: 'Savings Account', kind: 'Refund', signedExpense: -100,
    postings: [posting('Stored:Software', 20), posting(savings, 80), posting('Expenses:Test', -100)] },
  ...['Lunch', 'Software'].map(name => ({ name: `Bank-funded ${name} top-up`, amount: 100, account: savings, label: 'Savings Account', displayType: 'Balance top-up',
    postings: [posting(`Stored:${name}`, 100), posting(savings, -100)] })),
  { name: 'opening fund balance with only a positive candidate retains fallback', amount: 100, account: 'Equity:Opening Balances', label: 'Equity · Opening Balances', kind: 'Transfer', category: 'Investments',
    postings: [posting('Assets:Investments:Test', -100), posting('Equity:Opening Balances', 100)] },
  { name: 'no eligible INR candidate retains the multiple-postings fallback', amount: 100, account: '', label: 'Multiple postings', kind: 'Expense', signedExpense: 100,
    postings: [posting('Expenses:Test', 100), posting('Assets:Investments:Test', -100)] },
];

test('generated payment accounts follow the paying side with unchanged expense and transfer values', async t => {
  const work = await temporaryDirectory('payment-posting-');
  t.after(() => rm(work, { recursive: true, force: true }));
  const copy = await disposableDashboard(join(work, 'copy'));
  const ledger = join(work, 'ledger');
  await mkdir(join(ledger, 'data/expenses'), { recursive: true });
  await mkdir(join(ledger, 'data/prices'), { recursive: true });
  await writeFile(join(ledger, 'ledger.json'), JSON.stringify({format:CURRENT_FORMAT,id:'00000000-0000-4000-8000-000000000001',name:'Offline fixture',defaultCurrency:'INR'}));
  await writeFile(join(ledger, 'data/accounts.json'), JSON.stringify({commodities:['INR'],accounts:{[savings]:{kind:'bank',currency:'INR',label:'Savings Account'},[card]:{kind:'card',currency:'INR',label:'Rewards Card'},'Casey:Accounts Payable:Morgan:Water Tax:Flat':{kind:'person',currency:'INR'},'Stored:Lunch':{kind:'wallet',currency:'INR',label:'Shopping Wallet'},'Stored:Software':{kind:'wallet',currency:'INR',label:'App Store Balance'},'Assets:Investments:Test':{kind:'investment',currency:'INR'}}}));
  await writeFile(join(ledger, 'data/prices/mutual-funds.prices'), '');
  // Both posting orders must choose the same payer; IDs remain explicit store IDs.
  const entries = cases.flatMap((item, index) => [false, true].map((reverse, order) => ({
    id: (index * 2 + order + 1).toString(16).padStart(16, '0'), date: '2026-01-01', status: 'confirmed',
    description: `${item.name} ${order}`, sources: [], postings: reverse ? [...item.postings].reverse() : item.postings,
  })));
  await writeFile(join(ledger, 'data/expenses/2026.json'), JSON.stringify(entries));
  await writeFile(join(ledger, 'data/forecast.json'), '[]');
  execFileSync(process.execPath, [join(copy.dash, 'scripts/build-dashboard-data.mjs')], {
    env: { ...copy.env, LEDGER_DIR: ledger }, stdio: 'pipe',
  });
  const dashboard = JSON.parse(await readFile(join(copy.dash, 'app/data/dashboard.json'), 'utf8'));
  for (const [index, item] of cases.entries()) await t.test(item.name, () => {
    for (const order of [0, 1]) {
      const record = dashboard.transactions[index * 2 + order];
      assert.equal(record.id, entries[index * 2 + order].id);
      assert.equal(record.paymentAccount, item.account);
      assert.equal(record.paymentLabel, item.label);
      if (item.displayType) assert.equal(record.displayType, item.displayType);
      assert.equal(record.kind, item.kind || 'Transfer');
      assert.equal(record.amount, item.amount);
      assert.equal(record.signedExpense, item.signedExpense || 0);
      assert.equal(record.category, item.category || (item.kind === 'Expense' || item.kind === 'Refund' ? 'Test' : 'Transfers'));
    }
  });
});
