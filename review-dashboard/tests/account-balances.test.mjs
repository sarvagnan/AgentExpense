import './test-isolation.mjs';
import path from 'node:path';
import { ledgerRoot } from '../../scripts/project-paths.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { test } from 'node:test';
import { accountGroup, accountActivity, balanceAccounts, valueSeries } from '../app/account-balances.ts';
import { toTransaction } from '../scripts/expense-store.mjs';
const transaction = (id, date, sequence, amount, currency = 'INR') => ({ id, date, sequence, description: id, postings: [{ account: 'Assets:Bank:Savings Account', currency, amount }] });
test('balances include opening entries, exclude future postings and separate currencies', () => {
 const entries = [transaction('opening', '2026-08-01', 0, 100), transaction('debit', '2026-09-01', 1, -20), transaction('future', '2026-10-01', 2, -90), transaction('usd', '2026-09-01', 3, 5, 'USD')];
 const balances = balanceAccounts(entries, '2026-09-30');
 assert.equal(balances.find(row => row.currency === 'INR').balance, 80);
 assert.equal(balances.find(row => row.currency === 'USD').balance, 5);
 assert.equal(balanceAccounts(entries, '2026-07-31').length, 0);
});
test('running balances retain earlier history and stable same-day order; repeated postings aggregate once', () => {
 const entries = [transaction('late', '2026-09-02', 3, -10), transaction('early', '2026-09-02', 2, 0.1), transaction('opening', '2026-08-01', 1, 100)];
 entries[1].postings.push({ account: 'Assets:Bank:Savings Account', currency: 'INR', amount: 0.2 });
 assert.deepEqual(accountActivity(entries, 'Assets:Bank:Savings Account', 'INR', '2026-09-02').map(row => [row.entry.id, row.balance]), [['opening', 100], ['early', 100.3], ['late', 90.3]]);
});
test('card liabilities and their activity use the entry date', () => {
 const purchase = { ...toTransaction({ id: '0000000000000001', date: '2026-08-28', status: 'confirmed', description: 'Printed date',
  postings: [{ account: 'Expenses:Subscriptions:Test', commodity: 'INR', amount: '100.00' },
   { account: 'Liabilities:Credit Cards:Rewards Card', commodity: 'INR', amount: '-100.00' }] }), sequence: 0 };
 assert.equal(balanceAccounts([purchase], '2026-08-27').length, 0);
 assert.equal(balanceAccounts([purchase], '2026-08-28')[0].balance, -100);
 assert.deepEqual(accountActivity([purchase], 'Liabilities:Credit Cards:Rewards Card', 'INR', '2026-08-28')
  .map(row => [row.entry.date, row.date, row.change, row.balance]), [['2026-08-28', '2026-08-28', -100, -100]]);
});
test('position values use the entry date', () => {
 const holdings = [{ prices: { commodity: 'FUND', dates: ['2026-01-30', '2026-02-01', '2026-02-02'], navs: [10, 11, 12] },
  activity: [{ entry: { date: '2026-02-01' }, balance: 2, cost: 20 }] }];
 assert.deepEqual(valueSeries(holdings, '2026-01-30', '2026-02-02'), [
  { day: '2026-01-30', value: 0, cost: 0 }, { day: '2026-02-01', value: 22, cost: 20 }, { day: '2026-02-02', value: 24, cost: 20 },
 ]);
});
test('every selected account balance reconciles to hledger at historical and current cutoffs', () => {
 const data = JSON.parse(readFileSync(new URL('../app/data/dashboard.json', import.meta.url), 'utf8'));
 const ledger = path.join(ledgerRoot(), 'expense_ledger.ledger');
 for (const asOf of [...new Set(data.transactions.map(row => row.date))]) {
  const end = new Date(Date.parse(asOf) + 86400000).toISOString().slice(0,10);
  for (const row of balanceAccounts(data.transactions, asOf)) {
   const exact = '^' + row.account.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$';
   const output = execFileSync(process.env.HLEDGER_BIN || 'hledger', ['-f', ledger, 'balance', `acct:${exact}`, '-e', end, '--flat', '--no-total', '-O', 'json', '--empty'], {encoding:'utf8'}).trim();
   const amounts = JSON.parse(output)[0].flatMap(account => account[3]);
   const commodity = amounts.filter(amount => amount.acommodity === row.currency);
   assert.ok(commodity.length, `${row.account} retains commodity ${row.currency}`);
   const scale = Math.max(...commodity.map(amount => amount.aquantity.decimalPlaces));
   const expected = commodity.reduce((sum, amount) => sum + Number(amount.aquantity.decimalMantissa) * 10 ** (scale - amount.aquantity.decimalPlaces), 0) / 10 ** scale;
   assert.equal(row.balance, expected, `${row.account} at ${asOf}`);
   assert.equal(accountActivity(data.transactions, row.account, row.currency, asOf).at(-1).balance, expected);
  }
 }
});

test('all account groups follow kinds rather than account path grammar', () => {
 const accounts = {Vault:{kind:'bank'},Plastic:{kind:'card'},Stored:{kind:'wallet'},Units:{kind:'investment'},Friend:{kind:'person'},Borrowed:{kind:'loan'},Pocket:{kind:'cash'}};
 for (const [account,group] of Object.entries({Vault:'Banks',Plastic:'Credit cards',Stored:'Prepaid',Units:'Investments',Friend:'People',Borrowed:'Loans',Pocket:'Cash'})) assert.equal(accountGroup(account,accounts),group);
 assert.equal(accountGroup('Assets:Wallets:Unknown',accounts),'Other accounts');
});
