import { createTestServer } from './test-isolation.mjs';
import assert from 'node:assert/strict';
import test, { after } from 'node:test';
const server = await createTestServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom', optimizeDeps: { noDiscovery: true, include: [] } });
after(() => server.close());
const { paymentSide } = await server.ssrLoadModule('/app/payment-side.ts');
const p = (account, amount) => ({ account, amount, label: account });
const side = (postings, displayType = 'Expense') => paymentSide({ postings, displayType });
const bank = 'Assets:Bank:Savings Account', card = 'Liabilities:Credit Cards:Rewards Card';
test('payment summaries exclude expense discounts, income and equity legs', () => {
  assert.deepEqual(side([p(card, -323), p('Expenses:Food:Discount', -100), p('Expenses:Food', 423), p('Income:Card Rewards', -5), p('Equity:Other', -1)]), { short: 'Rewards Card', full: card });
});
test('genuine split funding aggregates repeated payment accounts before ranking', () => {
  assert.deepEqual(side([p(bank, -60), p(card, -100), p(bank, -60), p('Expenses:Purchase', 220)]), { short: 'Savings Account + 1 more', full: `${bank} + ${card}` });
  assert.deepEqual(side([p(bank, -100), p('Casey:Accounts Receivable:Morgan', -50), p('Expenses:Purchase', 150)]), { short: 'Savings Account + 1 more', full: `${bank} + Casey:Accounts Receivable:Morgan` });
});
test('salary with a meal deduction names the receiving bank despite its expense classification', () => {
  assert.deepEqual(side([p(bank, 900), p('Expenses:Meals', 100), p('Income:Salary', -1000)]), { short: 'Into Savings Account', full: `Into ${bank}` });
});
test('refunds and reimbursements name receiving money accounts only', () => {
  assert.equal(side([p(card, 100), p('Expenses:Food', -100)], 'Refund').short, 'Into Rewards Card');
  assert.equal(side([p(bank, 100), p('Assets:Reimbursements:Employer', -100)], 'Reimbursement').short, 'Into Savings Account');
});
test('personal repayments received say Into and advances keep the paying bank', () => {
  for (const relationship of ['Accounts Payable', 'Accounts Receivable']) {
    const person = `Casey:${relationship}:Morgan:Coffee`;
    assert.equal(side([p(bank, 100), p(person, -100)], 'Transfer').short, 'Into Savings Account');
    assert.equal(side([p(bank, -100), p(person, 100)], 'Transfer').short, 'Savings Account');
    assert.equal(side([p(person, -100), p('Expenses:Food', 100)]).short, 'Casey · Coffee');
  }
});
test('bank transfers, card payments and investment purchases retain their paying accounts in either order', () => {
  for (const target of [card, 'Assets:Bank:Salary Account', 'Assets:Investments:Mutual Funds:Index']) {
    const postings = [p(bank, -100), p(target, 100)];
    assert.equal(side(postings, 'Transfer').short, 'Savings Account');
    assert.equal(side(postings.reverse(), 'Transfer').short, 'Savings Account');
  }
});
test('all balance adjustments have no payer including mixed opening liabilities', () => {
  for (const postings of [[p(bank, 100), p('Equity:Opening Balances', -100)], [p(bank, 150), p(card, -50), p('Equity:Opening Balances', -100)], [p('Assets:Wallets:App Store Balance', 100), p('Equity:Opening Balances', -100)]]) assert.deepEqual(side(postings, 'Balance adjustment'), { short: '—', full: '—' });
});
test('empty and zero-only payment sides have no payer; split incoming accounts stay explicit', () => {
  for (const postings of [[], [p('Expenses:Food', 1), p('Income:Other', -1)], [p(bank, 0)]]) assert.deepEqual(side(postings), { short: '—', full: '—' });
  assert.deepEqual(side([p(bank, 200), p(card, 100), p('Income:Other', -300)]), { short: 'Into Savings Account + 1 more', full: `Into ${bank} + ${card}` });
});
test('Payment account sorting uses the directional display without changing source rows', async () => {
  const { sortTransactions } = await server.ssrLoadModule('/app/transaction-sort.ts');
  const base = { date: '2026-01-01', description: '', category: '', status: 'Cleared', amount: 100, displayType: 'Expense', sequence: 1 };
  const outgoing = { ...base, paymentLabel: 'A stale label', postings: [p(bank, -100), p('Expenses:Food',100)] };
  const incoming = { ...base, paymentLabel: 'Z stale label', postings: [p(bank,100),p('Income:Salary',-100)] };
  const input = [outgoing, incoming];
  assert.deepEqual(sortTransactions(input, 'paymentLabel', 'asc', t=>paymentSide(t).short), [incoming,outgoing]);
  assert.deepEqual(sortTransactions(input, 'paymentLabel', 'desc', t=>paymentSide(t).short), [outgoing,incoming]);
  assert.equal(outgoing.paymentLabel, 'A stale label'); assert.deepEqual(input, [outgoing,incoming]);
});
