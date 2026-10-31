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
  const { transactions, rules, prices } = await loadExpenseStore(root);
  assert.equal(transactions[0].id, original.id);
  assert.deepEqual(transactions[0].sources, original.sources);
  assert.equal(transactions[0].classification, original.classification);
  assert.deepEqual(transactions[0]['original-amount'], original['original-amount']);
  assert.equal(transactions[0]['reimbursement-group'], 'group');
  assert.deepEqual(transactions[0].postings[1], { account: original.postings[1].account, currency: 'INR', amount: -100 });
  assert.equal(transactions[0].date, '2026-01-30', 'Entry/expense month stays in January');
  assert.deepEqual(rules[0].schedule, rule().schedule);
  assert.deepEqual(prices, [{ commodity: 'FUND', dates: ['2026-01-01'], navs: [12.3456] }]);
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
  assert.equal(toTransaction(entry({ postings: [fund] })).postings[0].cost, Number((123.4567 * 1.234).toFixed(2)));
  assert.equal(toTransaction(entry({ postings: [{ ...fund, amount: '-1.234', cost: { ...fund.cost, amount: '100.005', per: 'total' } }] })).postings[0].cost, Number((-100.005).toFixed(2)));
  const expense = { ...fund, account: 'Expenses:Test' };
  assert.equal('cost' in toTransaction(entry({ postings: [expense] })).postings[0], false);
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
    { commodity: 'A', dates: ['2026-01-01'], navs: [3] }, { commodity: 'B', dates: ['2026-01-01'], navs: [2.000001] },
  ]);
  for (const text of ['P 2026-01-01 FUND 1 USD', 'P 2026-01-01 FUND -1 INR', 'garbage']) assert.throws(() => parsePrices(text), /Unexpected price/);
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
