import assert from 'node:assert/strict';
import test from 'node:test';
import {execFileSync} from 'node:child_process';
import {mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {disposableDashboard, temporaryDirectory} from './test-isolation.mjs';
import {toTransaction} from '../scripts/expense-store.mjs';
import {balanceAccounts} from '../app/account-balances.ts';
import {CURRENT_FORMAT} from '../scripts/ledger-manifest.mjs';

const posting = (account, amount, commodity, cost) => ({account, amount: String(amount), commodity, ...(cost ? {cost} : {})});
const cost = (amount, commodity) => ({amount: String(amount), commodity, per: 'unit'});
const purchase = (currency = 'INR', rate = '83.3333333333', sign = 1) => [
  ...['Food', 'Software', 'Other'].map(category => posting(`Expenses:${category}`, sign, 'USD', cost(rate, currency))),
  posting('Pocket', -250 * sign, currency),
];
const round = value => Number(value.toFixed(2));

async function build(t, postings, {accounts = {}, rules = [], status = 'pending'} = {}) {
  const work = await temporaryDirectory('charged-amounts-');
  t.after(() => rm(work, {recursive: true, force: true}));
  const copy = await disposableDashboard(join(work, 'copy')), root = join(work, 'ledger');
  await mkdir(join(root, 'data/expenses'), {recursive: true});
  const write = (file, value) => writeFile(join(root, file), JSON.stringify(value));
  await write('ledger.json', {format: CURRENT_FORMAT, id: '00000000-0000-4000-8000-000000000006', name: 'Charged amount fixture', defaultCurrency: 'INR', locale: 'en-IN'});
  await write('data/accounts.json', {commodities: ['INR', 'USD', 'GBP', 'EUR', 'CHF'], accounts: {
    Pocket: {kind: 'card', currency: 'INR'}, Friend: {kind: 'person', currency: 'INR'},
    'Expenses:Food': {}, 'Expenses:Software': {}, 'Expenses:Other': {}, ...accounts,
  }});
  await write('data/expenses/2026.json', [{id: '0000000000000001', date: '2026-10-10', status, description: 'Fictional multi-line purchase', postings}]);
  await write('data/forecast.json', rules);
  for (const script of ['build-dashboard-data', 'build-review-data', 'build-add-patterns', 'check-dashboard-data']) {
    execFileSync(process.execPath, [join(copy.dash, 'scripts', `${script}.mjs`)], {env: {...copy.env, LEDGER_DIR: root}, stdio: 'pipe'});
  }
  const read = async name => JSON.parse(await readFile(join(copy.dash, 'app/data', `${name}.json`), 'utf8'));
  return {dashboard: await read('dashboard'), review: await read('pending-transactions')};
}

function assertCharge(data, currency, charged) {
  const record = data.dashboard.transactions.find(row => row.currency === currency);
  assert.equal(record.amount, Math.abs(charged));
  assert.equal(record.signedExpense, charged);
  const month = data.dashboard.months.find(row => row.currency === currency);
  assert.equal(month.total, charged);
  assert.equal(round(month.categories.reduce((sum, row) => sum + row.amount, 0)), charged);
  assert.equal(round(month.daily.reduce((sum, row) => sum + row.amount, 0)), charged);
  const review = data.review.transactions.find(row => row.currency === currency);
  assert.equal(review.amount, Math.abs(charged));
  assert.equal(review.signedAmount, charged);
  assert.equal(data.review.meta.pendingNetAmount[currency], charged);
  assert.equal(data.review.meta.pendingAbsoluteAmount[currency], Math.abs(charged));
}

test('foreign multi-line charges and refunds retain the pre-currency builder amounts and Review sums', async t => {
  for (const sign of [1, -1]) {
    const postings = purchase('INR', '83.3333333333', sign);
    const normalized = toTransaction({id: '0000000000000001', date: '2026-10-10', status: 'pending', postings});
    assert.equal(round(normalized.postings.slice(0, 3).reduce((sum, p) => sum + p.cost, 0)), sign * 249.99);
    // The old builder inferred one rate from the card amount, even with explicit costs.
    const charged = sign * 250;
    const oldRate = Math.abs(normalized.postings.at(-1).amount / (3 * sign));
    assert.equal(round(3 * sign * oldRate), charged);
    assertCharge(await build(t, postings), 'INR', charged);
  }
});

test('charged amounts net all funding postings and same-currency non-expense allocations', async t => {
  const postings = purchase().slice(0, 3).concat([
    posting('Expenses:Food', 2, 'INR'), posting('Friend', 3, 'INR'),
    posting('Pocket', -120, 'INR'), posting('Reserve', -135, 'INR'),
  ]);
  assertCharge(await build(t, postings, {accounts: {Reserve: {kind: 'bank', currency: 'INR'}}}), 'INR', 252);
});

test('separately funded currencies retain their own charge and allocations', async t => {
  const postings = [
    ...['Food', 'Software', 'Other'].map(category => posting(`Expenses:${category}`, 1, 'USD', cost('0.8333333333', 'GBP'))),
    ...['Food', 'Software', 'Other'].map(category => posting(`Expenses:${category}`, 1, 'CHF', cost('1.0033333333', 'EUR'))),
    posting('Pocket', -2.50, 'GBP'), posting('Travel', -3.01, 'EUR'),
  ];
  const data = await build(t, postings, {accounts: {Pocket: {kind: 'card', currency: 'GBP'}, Travel: {kind: 'card', currency: 'EUR'}}});
  assertCharge(data, 'GBP', 2.50);
  assertCharge(data, 'EUR', 3.01);
  assert.equal(data.dashboard.transactions.length, 2);
  assert.equal(data.review.transactions.length, 2);
  assert.deepEqual(data.dashboard.transactions.map(row => [row.id, row.storeId]), [
    ['0000000000000001-EUR', '0000000000000001'], ['0000000000000001-GBP', '0000000000000001'],
  ]);
  assert.equal(data.dashboard.transactions.flatMap(row => row.postings).length, postings.length);
  assert.deepEqual(balanceAccounts(data.dashboard.transactions, '2026-10-31', data.dashboard.meta.accounts)
    .map(row => [row.account, row.currency, row.balance]), [['Pocket', 'GBP', -2.50], ['Travel', 'EUR', -3.01]]);
});

test('foreign forecast commitments and recurring shares allocate the actual charge', async t => {
  const rule = {id: '0000000000000002', date: '2026-11-01', status: 'confirmed', description: 'Fictional foreign commitment',
    schedule: {every: 'month', from: '2026-11-01'}, postings: [posting('Expenses:Food', 1, 'USD', cost('83.3333333333', 'INR')),
      posting('Expenses:Software', 1, 'USD', cost('83.3333333333', 'INR')), posting('Pocket', -166.67, 'INR')]};
  const data = await build(t, purchase(), {rules: [rule]});
  assertCharge(data, 'INR', 250);
  assert.equal(data.dashboard.months[0].recurring, 166.67);
  assert.equal(data.dashboard.forecast.months[0].committed, 166.67);
  assert.equal(data.dashboard.forecast.items[0].amount, 166.67);
});

test('a single-line USD subscription keeps the amount charged to its INR card', async t => {
  const data = await build(t, [posting('Expenses:Software', 1, 'USD', {amount: '90.00', commodity: 'INR', per: 'total'}), posting('Pocket', -90, 'INR')]);
  assertCharge(data, 'INR', 90);
  assert.deepEqual(data.dashboard.transactions[0].postings.map(p => [p.amount, p.currency]), [[1, 'USD'], [-90, 'INR']]);
});
