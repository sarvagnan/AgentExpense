import { temporaryDirectory } from './test-isolation.mjs';
import assert from 'node:assert/strict';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { decimalNumber, isAssertionOnly, loadExpenseStore, parsePrices, toTransaction } from '../scripts/expense-store.mjs';

const entry = (overrides = {}) => ({ id: '0000000000000001', date: '2026-01-30', status: 'pending', description: 'Test purchase',
  sources: ['statement.pdf', 'receipt.png'], classification: 'Pending receipt',
  postings: [{ account: 'Expenses:Test', commodity: 'INR', amount: '100.00' },
    { account: 'Liabilities:Credit Cards:Rewards Card', commodity: 'INR', amount: '-100.00' }], ...overrides });
const rule = () => entry({ id: '0000000000000004', schedule: { every: 'month', from: '2026-01-30' } });
async function fixture(t, actuals = [entry()], rules = [rule()]) {
  // Scratch stores never live in the source checkout.
  const root = await temporaryDirectory('store-test-');
  t.after(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, 'data/expenses'), { recursive: true });
  await mkdir(path.join(root, 'data/prices'));
  await writeFile(path.join(root, 'ledger.json'), JSON.stringify({format:1,id:'00000000-0000-4000-8000-000000000001',name:'Offline fixture',defaultCurrency:'INR'}));
  await writeFile(path.join(root, 'data/accounts.json'), JSON.stringify({commodities:['INR'],accounts:{'Expenses:Test':{}}}));
  await writeFile(path.join(root, 'data/expenses/2026.json'), JSON.stringify(actuals));
  await writeFile(path.join(root, 'data/forecast.json'), JSON.stringify(rules));
  await writeFile(path.join(root, 'data/prices/mutual-funds.prices'), '; source note\nP 2026-01-01 "FUND" 12.3456 INR\n');
  return root;
}

test('decimal strings use the hledger mantissa/scale conversion without early rounding', () => {
  for (const [text, mantissa, scale] of [['123456.78', 12345678, 2], ['1.234', 1234, 3], ['-27.10', -2710, 2], ['100', 100, 0], ['-0.00', 0, 2]]) {
    assert.equal(decimalNumber(text), Number(mantissa) / 10 ** scale);
  }
  for (const invalid of [100, null, '', '1e3', 'NaN', 'Infinity', ' 1.00', '01.00', '1.2.3', '9'.repeat(400)]) {
    assert.throws(() => decimalNumber(invalid), /decimal/);
  }
});

test('the loader preserves IDs, structured fields, source order and entry dates', async (t) => {
  const original = entry({ 'original-amount': { amount: '1.2300', commodity: 'USD' }, 'reimbursement-group': 'group', notes: ['classification: never read this'] });
  const root = await fixture(t, [original]);
  const manifest = JSON.parse(await readFile(path.join(root, 'ledger.json')));
  manifest.modules = {investments:{prices:'mutual-fund-nav'}};
  await writeFile(path.join(root, 'ledger.json'), JSON.stringify(manifest));
  const { transactions, rules, prices } = await loadExpenseStore(root);
  assert.equal(transactions[0].id, original.id);
  assert.deepEqual(transactions[0].sources, original.sources);
  assert.equal(transactions[0].classification, original.classification);
  assert.deepEqual(transactions[0]['original-amount'], original['original-amount']);
  assert.equal(transactions[0]['reimbursement-group'], 'group');
  assert.deepEqual(transactions[0].postings[1], { account: original.postings[1].account, currency: 'INR', amount: -100 });
  assert.equal(transactions[0].date, '2026-01-30', 'Entry/expense month stays in January');
  assert.deepEqual(rules[0].schedule, rule().schedule);
  assert.deepEqual(prices, [{ commodity: 'FUND', currency: 'INR', dates: ['2026-01-01'], navs: [12.3456] }]);
  assert.equal(JSON.parse(await readFile(path.join(root, 'data/expenses/2026.json')))[0].postings[0].amount, '100.00');
});

test('assertion-only is defined by all postings; it excludes zero assertions in any status or description', async (t) => {
  const assertion = { account: 'Assets:Test', amount: '-0.00', commodity: 'INR', assert: { amount: '10.00', commodity: 'INR' } };
  assert.equal(isAssertionOnly({ postings: [assertion, assertion] }), true);
  assert.equal(isAssertionOnly({ postings: [] }), false);
  assert.equal(isAssertionOnly({ postings: [{ ...assertion, amount: '1.00' }] }), false);
  assert.equal(isAssertionOnly({ postings: [assertion, { ...assertion, assert: undefined }] }), false);
  const root = await fixture(t, [entry({ postings: [assertion] }), entry({ id: '0000000000000002', postings: [{ ...assertion, assert: undefined }] })]);
  const data = await loadExpenseStore(root);
  assert.deepEqual(data.transactions.map(row => row.id), ['0000000000000002']);
});

test('optional absent sources keep the original empty source list, with no notes inference', () => {
  const row = entry();
  delete row.sources;
  assert.deepEqual(toTransaction(row).sources, []);
  assert.throws(() => toTransaction(entry({ status: 'unknown' })), /status/);
  assert.throws(() => toTransaction(entry({ date: '2026-02-30' })), /date/);
});

test('investment costs retain signed total/unit cost calculation and two-decimal rounding', () => {
  const fund = { account: 'Assets:Investments:Test', commodity: 'FUND', amount: '1.234', cost: { amount: '123.4567', commodity: 'INR', per: 'unit' } };
  assert.equal(toTransaction(entry({ postings: [fund] }), {'Assets:Investments:Test':{kind:'investment'}}).postings[0].cost, Number((123.4567 * 1.234).toFixed(2)));
  assert.equal(toTransaction(entry({ postings: [{ ...fund, amount: '-1.234', cost: { ...fund.cost, amount: '100.005', per: 'total' } }] }), {'Assets:Investments:Test':{kind:'investment'}}).postings[0].cost, Number((-100.005).toFixed(2)));
  const expense = { ...fund, account: 'Expenses:Test' };
  assert.equal(toTransaction(entry({ postings: [expense] })).postings[0].costCurrency, 'INR');
  assert.equal(toTransaction(entry({ postings: [expense] })).postings[0].cost, Number((123.4567 * 1.234).toFixed(2)));
});

test('year files load in order and preserve stable same-date array order', async (t) => {
  const root = await fixture(t, [entry(), entry({ id: '0000000000000002' })]);
  await writeFile(path.join(root, 'data/expenses/2025.json'), JSON.stringify([entry({ id: '0000000000000003', date: '2025-12-31' })]));
  assert.deepEqual((await loadExpenseStore(root)).transactions.map(row => row.id), ['0000000000000003', '0000000000000001', '0000000000000002']);
});

test('duplicate/invalid IDs across actuals and rules and unordered actuals fail closed', async (t) => {
  for (const [actuals, rules, error] of [
    [[entry(), entry()], [], /duplicate/], [[entry()], [entry({ schedule: rule().schedule })], /duplicate/],
    [[entry({ id: 'bad' })], [], /ID/], [[entry({ id: 1234567890123456 })], [], /ID/],
    [[entry(), entry({ id: '0000000000000002', date: '2025-01-01' })], [], /date order/],
  ]) {
    const root = await fixture(t, actuals, rules);
    await assert.rejects(loadExpenseStore(root), error);
  }
});

test('prices preserve raw NAV precision and reject invalid lines, duplicates and reversed dates', () => {
  assert.deepEqual(parsePrices('; comment\n\nP 2026-01-01 "B" 2.000001 INR\nP 2026-01-01 A 3.0 INR\n'), [
    { commodity: 'A', currency: 'INR', dates: ['2026-01-01'], navs: [3] }, { commodity: 'B', currency: 'INR', dates: ['2026-01-01'], navs: [2.000001] },
  ]);
  for (const text of ['P 2026-01-01 FUND 1', 'P 2026-01-01 FUND -1 INR', 'garbage']) assert.throws(() => parsePrices(text), /Unexpected price/);
  for (const day of ['2026-01-02', '2026-01-01']) assert.throws(() => parsePrices(`P 2026-01-02 FUND 1 INR\nP ${day} FUND 2 INR`), /date order/);
});

test('published dashboard/review IDs are unique store IDs; review IDs are a subset', async () => {
  const { transactions } = await loadExpenseStore();
  const dashboard = JSON.parse(await readFile(new URL('../app/data/dashboard.json', import.meta.url)));
  const review = JSON.parse(await readFile(new URL('../app/data/pending-transactions.json', import.meta.url)));
  const ids = new Set(transactions.map(entry => entry.id));
  assert.deepEqual(dashboard.transactions.map(entry => entry.id), transactions.map(entry => entry.id));
  assert.equal(ids.size, dashboard.transactions.length);
  assert.equal(new Set(review.transactions.map(entry => entry.id)).size, review.transactions.length);
  assert.ok(review.transactions.every(entry => ids.has(entry.id)));
});

test('manifest gates run before store reads and fill independent defaults', async t => {
  const { readManifest } = await import('../scripts/ledger-manifest.mjs');
  const root = await fixture(t);
  const manifest = await readManifest(root);
  assert.deepEqual(manifest.modules, { fuel: false, weeklyReview: false, investments: { prices: null }, history: { oldLedger: null } });
  assert.deepEqual(manifest.review, { storageKey: null });
  await rm(path.join(root, 'ledger.json'));
  await assert.rejects(loadExpenseStore(root), { message: 'This ledger uses format 0; run make upgrade-ledger.' });
  await writeFile(path.join(root, 'ledger.json'), JSON.stringify({ format: 2 }));
  await assert.rejects(loadExpenseStore(root), { message: 'This app reads format 1; this ledger uses format 2; a newer AgentExpense is needed.' });
});

test('account metadata accepts any root and requires kind and currency outside Income, Expenses and Equity', async t => {
  const root = await fixture(t);
  const file = path.join(root, 'data/accounts.json');
  const person = { kind: 'person', currency: 'USD' };
  const accounts = {
    'Dev Sen:Accounts Payable:Asha': person,
    'Dev Sen:Balances:Accounts Receivable:Asha': person,
    Custom: person,
    'IncomeExtra:Test': person,
    Income: {}, 'Income:Salary': {}, 'Expenses:Test': {}, 'Equity:Opening Balances': {},
  };
  const write = value => writeFile(file, JSON.stringify({ commodities: ['INR', 'USD'], accounts: value }));
  await write(accounts);
  assert.deepEqual((await loadExpenseStore(root)).accounts, accounts);
  for (const [name, metadata] of [
    ['Dev Sen:Accounts Payable:Asha', {}], ['Dev Sen:Balances:Accounts Receivable:Asha', { currency: 'USD' }],
    ['Custom', { kind: 'person' }], ['IncomeExtra:Test', {}], ['Income:Salary', person], ['Expenses:Test', person],
    ['Equity:Opening Balances', person], ['Custom', { kind: 'unknown', currency: 'USD' }],
    ...['', ':Custom', 'Custom:', 'Custom::Test', 'Custom\nTest', 'Custom:Test\n', 'Custom\rTest'].map(name => [name, person]),
  ]) {
    await write({ ...accounts, [name]: metadata });
    await assert.rejects(loadExpenseStore(root), /Invalid data\/accounts.json/);
  }
});

test('the Node reader accepts display categories only on Expenses accounts', async t => {
  const root = await fixture(t);
  const file = path.join(root, 'data/accounts.json');
  const accounts = {'Expenses': {category:'Everyday'}, 'Expenses:Test': {category:'Groceries'}, 'Income:Salary': {}, 'Equity:Opening Balances': {}};
  const write = value => writeFile(file, JSON.stringify({commodities:['INR'], accounts:value}));
  await write(accounts);
  assert.deepEqual((await loadExpenseStore(root)).accounts, accounts);
  for (const [account, metadata] of [
    ...['', ' ', '\t', 'Groceries\n', 'Groceries\rOther', 42, null].map(category => ['Expenses:Test', {category}]),
    ['Income:Salary', {category:'Income'}], ['Equity:Opening Balances', {category:'Opening'}],
    ['Assets:Checking:Alder', {kind:'bank', currency:'INR', category:'Groceries'}],
    ['Expenses:Test', {category:'Groceries', label:'Store'}],
  ]) {
    await write({...accounts, [account]:metadata});
    await assert.rejects(loadExpenseStore(root), /Invalid data\/accounts.json/);
  }
});

test('empty ledger builds and checks without expenses, fuel or prices files', async t => {
  const { disposableDashboard } = await import('./test-isolation.mjs');
  const { execFileSync } = await import('node:child_process');
  const copy = await disposableDashboard();
  t.after(() => rm(copy.application, { recursive: true, force: true }));
  const root = await fixture(t, [], []);
  await rm(path.join(root, 'data/expenses'), { recursive: true });
  await rm(path.join(root, 'data/prices'), { recursive: true });
  const env = { ...copy.env, LEDGER_DIR: root };
  for (const script of ['copy-fuel-log', 'build-dashboard-data', 'build-review-data', 'build-add-patterns', 'check-dashboard-data']) {
    execFileSync(process.execPath, [path.join(copy.dash, 'scripts', script + '.mjs')], { env, stdio: 'pipe' });
  }
  const data = JSON.parse(await readFile(path.join(copy.dash, 'app/data/dashboard.json'), 'utf8'));
  assert.deepEqual(data.transactions, []);
  assert.deepEqual(data.months, []);
  assert.deepEqual(data.prices, []);
  assert.deepEqual(data.forecast.items, []);
  assert.equal(data.forecast.asOf, '1970-01-01');
  assert.equal(data.forecast.start, '1970-01-02');
});
