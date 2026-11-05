import './test-isolation.mjs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { groupView, toMinor } from '../app/account-groups.ts';
import { statementFacts } from '../scripts/expense-store.mjs';
import { moneyCode, setMoneySettings } from '../scripts/formatting.mjs';

// Fictional accounts. Banks hold USD and EUR; the card and loan read as amounts owed.
const metadata = {
  'Assets:Bank:Alder': { kind: 'bank', currency: 'USD' },
  'Assets:Bank:Meadow': { kind: 'bank', currency: 'USD' },
  'Assets:Bank:Willow': { kind: 'bank', currency: 'EUR' },
  'Assets:Bank:Yen Box': { kind: 'bank', currency: 'JPY' },
  'Liabilities:Card:Lumen': { kind: 'card', currency: 'USD' },
  'Assets:Wallet:Fern': { kind: 'wallet', currency: 'USD' },
  'Assets:People:Robin': { kind: 'person', currency: 'USD' },
  'Liabilities:Loan:Oak': { kind: 'loan', currency: 'USD' },
  'Expenses:Food': {}, 'Equity:Opening Balances': {},
};
let sequence = 0;
const entry = (id, date, ...postings) => ({ id, date, sequence: sequence++, description: id, postings: postings.map(([account, amount, currency = 'USD', extra = {}]) => ({ account, amount, currency, ...extra })) });
const entries = [
  entry('opening', '2026-09-30', ['Assets:Bank:Alder', 1000], ['Assets:Bank:Meadow', 500], ['Assets:Bank:Willow', 200, 'EUR'], ['Liabilities:Card:Lumen', -10], ['Equity:Opening Balances', -1490], ['Equity:Opening Balances', -200, 'EUR']),
  entry('groceries', '2026-10-02', ['Expenses:Food', 12.34], ['Liabilities:Card:Lumen', -12.34]),
  entry('transfer', '2026-10-05', ['Assets:Bank:Meadow', 100], ['Assets:Bank:Alder', -100]),
  entry('card bill', '2026-10-06', ['Liabilities:Card:Lumen', 22.34], ['Assets:Bank:Alder', -22.34]),
  entry('to euros', '2026-10-07', ['Assets:Bank:Willow', 90, 'EUR'], ['Assets:Bank:Alder', -100, 'USD', { cost: 90, costCurrency: 'EUR' }]),
  entry('wallet top-up', '2026-10-08', ['Assets:Wallet:Fern', 30], ['Assets:Bank:Alder', -30]),
  entry('wallet lunch', '2026-10-09', ['Expenses:Food', 7.5], ['Assets:Wallet:Fern', -7.5]),
  entry('wallet snack', '2026-10-11', ['Expenses:Food', 2.5], ['Assets:Wallet:Fern', -2.5]),
  entry('lent', '2026-10-12', ['Assets:People:Robin', 40], ['Assets:Bank:Alder', -40]),
  entry('paid back', '2026-10-14', ['Assets:Bank:Alder', 40], ['Assets:People:Robin', -40]),
  entry('lent again', '2026-10-15', ['Assets:People:Robin', 5], ['Assets:Bank:Alder', -5]),
  entry('future', '2026-11-03', ['Expenses:Food', 99], ['Assets:Bank:Alder', -99]),
];
const view = (group, extra = {}) => groupView({ entries, group, start: '2026-10-01', asOf: '2026-10-31', metadata, ...extra });

test('opening and closing totals and in/out for the period, per currency', () => {
  const banks = view('Banks');
  const usd = banks.totals.find(total => total.currency === 'USD'), eur = banks.totals.find(total => total.currency === 'EUR');
  assert.deepEqual({ opening: usd.opening, closing: usd.closing, increase: usd.increase, decrease: usd.decrease }, { opening: 1500, closing: 1342.66, increase: 40, decrease: 197.34 });
  assert.deepEqual({ opening: eur.opening, closing: eur.closing, increase: eur.increase, decrease: eur.decrease }, { opening: 200, closing: 290, increase: 90, decrease: 0 });
  // The step chart runs from the opening at the start to the closing on the as-of date.
  assert.deepEqual(usd.points.at(0), { date: '2026-10-01', total: 1500 });
  assert.deepEqual(usd.points.at(-1), { date: '2026-10-31', total: 1342.66 });
  assert.ok(usd.points.every(point => point.date >= '2026-10-01' && point.date <= '2026-10-31'), 'The future entry stays out');
  const alder = banks.accounts.find(row => row.account === 'Assets:Bank:Alder');
  assert.deepEqual({ opening: alder.opening, balance: alder.balance, inflow: alder.inflow, outflow: alder.outflow }, { opening: 1000, balance: 742.66, inflow: 40, outflow: 297.34 });
  // Cards read as owed: charges raise it, payments lower it.
  const cards = view('Credit cards');
  assert.deepEqual(cards.totals.map(({ opening, closing, increase, decrease }) => ({ opening, closing, increase, decrease })), [{ opening: 10, closing: 0, increase: 12.34, decrease: 22.34 }]);
  assert.deepEqual(cards.accounts.map(({ balance, inflow, outflow }) => ({ balance, inflow, outflow })), [{ balance: 0, inflow: 22.34, outflow: 12.34 }]);
  // A narrower period moves the opening forward.
  assert.equal(groupView({ entries, group: 'Banks', start: '2026-10-10', asOf: '2026-10-31', metadata }).totals.find(total => total.currency === 'USD').opening, 1347.66);
});

test('last used, last settled, last confirmed balance and last statement', () => {
  assert.equal(view('Prepaid').accounts[0].lastUsed, '2026-10-11');
  assert.equal(groupView({ entries, group: 'Prepaid', start: '2026-10-01', asOf: '2026-10-10', metadata }).accounts[0].lastUsed, '2026-10-09');
  const robin = view('People').accounts[0];
  assert.deepEqual({ balance: robin.balance, inflow: robin.inflow, outflow: robin.outflow, lastSettled: robin.lastSettled }, { balance: 5, inflow: 45, outflow: 40, lastSettled: '2026-10-14' });
  assert.equal(groupView({ entries, group: 'People', start: '2026-10-01', asOf: '2026-10-13', metadata }).accounts[0].lastSettled, null);
  const confirmedBalances = [
    { account: 'Assets:Bank:Alder', date: '2026-10-06', amount: '877.66', currency: 'USD', entryId: 'a' },
    { account: 'Assets:Bank:Alder', date: '2026-10-20', amount: '742.66', currency: 'USD', entryId: 'b' },
    { account: 'Assets:Bank:Alder', date: '2026-11-20', amount: '643.66', currency: 'USD', entryId: 'c' },
    { account: 'Assets:Bank:Willow', date: '2026-10-20', amount: '290.00', currency: 'EUR', entryId: 'd' },
  ];
  const cardStatements = [
    { account: 'Liabilities:Card:Lumen', start: '2026-09-01', end: '2026-09-30', totalDue: '10.00', currency: 'USD' },
    { account: 'Liabilities:Card:Lumen', start: '2026-10-01', end: '2026-10-28', totalDue: '0.00', currency: 'USD' },
  ];
  const banks = view('Banks', { confirmedBalances });
  assert.deepEqual(banks.accounts.find(row => row.account === 'Assets:Bank:Alder').lastConfirmed, { date: '2026-10-20', amount: 742.66 }, 'The latest on or before the as-of date');
  assert.deepEqual(banks.accounts.find(row => row.account === 'Assets:Bank:Willow').lastConfirmed, { date: '2026-10-20', amount: 290 });
  assert.equal(banks.accounts.find(row => row.account === 'Assets:Bank:Meadow').lastConfirmed, null, 'None without an assertion');
  assert.deepEqual(groupView({ entries, group: 'Banks', start: '2026-10-01', asOf: '2026-10-19', metadata, confirmedBalances }).accounts.find(row => row.account === 'Assets:Bank:Alder').lastConfirmed, { date: '2026-10-06', amount: 877.66 });
  assert.deepEqual(view('Credit cards', { cardStatements }).accounts[0].lastStatement, { end: '2026-10-28', totalDue: 0 });
  assert.deepEqual(groupView({ entries, group: 'Credit cards', start: '2026-10-01', asOf: '2026-10-27', metadata, cardStatements }).accounts[0].lastStatement, { end: '2026-09-30', totalDue: 10 });
});

test('transfers within a group are detected only when every posting is inside and each currency nets to zero', () => {
  const banks = view('Banks');
  const byId = Object.fromEntries(banks.activity.map(row => [row.entry.id, row]));
  assert.equal(byId.transfer.internal, true);
  assert.deepEqual(byId.transfer.changes, { USD: 0 });
  assert.equal(byId['to euros'].internal, false, 'A conversion between banks changes each currency, so it stays visible');
  assert.deepEqual(byId['to euros'].changes, { EUR: 90, USD: -100 });
  assert.equal(byId['card bill'].internal, false, 'A card payment leaves the group');
  assert.equal(byId['wallet top-up'].internal, false);
  assert.deepEqual(banks.activity.map(row => row.date), [...banks.activity.map(row => row.date)].sort().reverse(), 'Newest first');
  assert.ok(!byId.opening && !byId.future, 'Only the period');
  // A card's own group: a balance transfer between two cards is internal too.
  const cards = groupView({ entries: [...entries, entry('balance transfer', '2026-10-20', ['Liabilities:Card:Lumen', 50], ['Liabilities:Card:Other', -50])], group: 'Credit cards', start: '2026-10-01', asOf: '2026-10-31', metadata: { ...metadata, 'Liabilities:Card:Other': { kind: 'card', currency: 'USD' } } });
  assert.equal(cards.activity.find(row => row.entry.id === 'balance transfer').internal, true);
});

test('multi-currency groups never mix their totals or rows', () => {
  const banks = view('Banks');
  assert.deepEqual(banks.totals.map(total => total.currency).sort(), ['EUR', 'USD']);
  assert.ok(banks.accounts.every(row => row.currency === metadata[row.account].currency));
  const eur = banks.totals.find(total => total.currency === 'EUR');
  assert.ok(eur.points.every(point => [200, 290].includes(point.total)), 'EUR points carry only EUR');
  // One account holding two currencies keeps two rows.
  const mixed = groupView({ entries: [entry('usd', '2026-10-02', ['Assets:Bank:Alder', 5], ['Equity:Opening Balances', -5]), entry('eur', '2026-10-03', ['Assets:Bank:Alder', 7, 'EUR'], ['Equity:Opening Balances', -7, 'EUR'])], group: 'Banks', start: '2026-10-01', asOf: '2026-10-31', metadata });
  assert.deepEqual(mixed.accounts.map(row => [row.currency, row.balance]), [['EUR', 7], ['USD', 5]]);
});

test('sums are exact in minor units, including zero- and three-decimal currencies', () => {
  const tenths = Array.from({ length: 10 }, (_, i) => entry(`dime ${i}`, '2026-10-02', ['Assets:Wallet:Fern', 0.1], ['Expenses:Food', -0.1]));
  const prepaid = groupView({ entries: tenths, group: 'Prepaid', start: '2026-10-01', asOf: '2026-10-31', metadata });
  assert.equal(prepaid.totals[0].closing, 1, 'Ten tenths make exactly one, not 0.9999999999999999');
  assert.equal(prepaid.accounts[0].inflow, 1);
  assert.notEqual(Array.from({ length: 10 }, () => 0.1).reduce((a, b) => a + b, 0), 1, 'Floating point accumulation would drift');
  setMoneySettings({}, { USD: 2, JPY: 0, KWD: 3 });
  assert.equal(toMinor(1234, 'JPY'), 1234);
  assert.equal(toMinor('1.005', 'KWD'), 1005);
  assert.equal(toMinor(19.99, 'USD'), 1999);
  const yen = groupView({ entries: [entry('yen', '2026-10-02', ['Assets:Bank:Yen Box', 1500, 'JPY'], ['Equity:Opening Balances', -1500, 'JPY']), entry('yen out', '2026-10-03', ['Expenses:Food', 333, 'JPY'], ['Assets:Bank:Yen Box', -333, 'JPY'])], group: 'Banks', start: '2026-10-01', asOf: '2026-10-31', metadata });
  assert.deepEqual(yen.accounts.map(row => [row.balance, row.inflow, row.outflow]), [[1167, 1500, 333]]);
});

test('statement facts come from bank assertions and filed card summaries', () => {
  const actuals = [
    { id: '0000000000000001', date: '2026-10-31', postings: [{ account: 'Assets:Bank:Alder', amount: '0.00', commodity: 'USD', assert: { amount: '742.66', commodity: 'USD' } }] },
    { id: '0000000000000002', date: '2026-09-01', postings: [{ account: 'Assets:Bank:Alder', amount: '100.00', commodity: 'USD', assert: { amount: '100.00', commodity: 'USD' } }] },
  ];
  const statements = [
    { id: 'a', account: 'Liabilities:Card:Lumen', start: '2026-10-01', end: '2026-10-28', 'total-due': '12.34' },
    { id: 'b', kind: 'bank', account: 'Assets:Bank:Alder', start: '2026-10-01', end: '2026-10-31', 'assertion-id': '0000000000000001' },
    { id: 'c', kind: 'bank', account: 'Liabilities:Card:Lumen', start: '2026-09-01', end: '2026-09-30', 'source-balance-style': 'debt', 'closing-balance': '-56.70' },
  ];
  const facts = statementFacts(actuals, statements, metadata);
  // Only the statement's own assertion counts; the opening assertion is not statement-confirmed.
  assert.deepEqual(facts.confirmedBalances, [{ account: 'Assets:Bank:Alder', date: '2026-10-31', amount: '742.66', currency: 'USD', entryId: '0000000000000001' }]);
  assert.deepEqual(facts.cardStatements, [
    { account: 'Liabilities:Card:Lumen', start: '2026-09-01', end: '2026-09-30', totalDue: '56.70', currency: 'USD' },
    { account: 'Liabilities:Card:Lumen', start: '2026-10-01', end: '2026-10-28', totalDue: '12.34', currency: 'USD' },
  ]);
  assert.throws(() => statementFacts([], [statements[1]], metadata), /no closing balance assertion/);
});

test('list amounts in another currency carry the code after the amount', () => {
  setMoneySettings({ locale: 'en-US', defaultCurrency: 'USD' }, { USD: 2, EUR: 2, JPY: 0 });
  assert.equal(moneyCode(120, 'EUR'), '120.00 EUR');
  assert.equal(moneyCode(-20.5, 'EUR'), '−20.50 EUR');
  assert.equal(moneyCode(1234, 'JPY'), '1,234 JPY');
  assert.equal(moneyCode(-0.001, 'EUR'), '0.00 EUR');
  setMoneySettings({ locale: 'de-DE' }, { EUR: 2 });
  assert.equal(moneyCode(1234.5, 'EUR'), '1.234,50 EUR');
});
