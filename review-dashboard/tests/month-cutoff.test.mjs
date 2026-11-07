import './test-isolation.mjs';
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {execFileSync} from 'node:child_process';
import {cp, readFile, writeFile, rm} from 'node:fs/promises';
import {join} from 'node:path';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {disposableDashboard, sourceApplication, createTestServer} from './test-isolation.mjs';
import * as store from './store-fixtures.mjs';
// New entries carry every listed extension of the persona, empty.
const blankExtensions = rows => Object.fromEntries(Object.keys(rows.find(row => row.extensions)?.extensions ?? {}).map(name => [name, null]));

async function fixture(t, persona = 'jordan') {
  const copy = await disposableDashboard();
  t.after(() => rm(copy.application, {recursive: true, force: true}));
  const root = join(copy.application, 'ledger');
  await cp(join(sourceApplication, 'fixtures/ledgers', persona), root, {recursive: true});
  const file = join(root, 'data/expenses/2026.json');
  const build = async today => {
    execFileSync(process.execPath, [join(copy.dash, 'scripts/build-dashboard-data.mjs')], {
      env: {...copy.env, LEDGER_DIR: root, DASHBOARD_TODAY: today}, stdio: 'pipe',
    });
    return JSON.parse(await readFile(join(copy.dash, 'app/data/dashboard.json'), 'utf8'));
  };
  return {...copy, root, file, build};
}

test('current comparisons use the same signed expenses through today, in each currency', async t => {
  const f = await fixture(t, 'sam');
  const initial = await f.build('2026-07-17');
  const fileRows = JSON.parse(await readFile(f.file, 'utf8'));
  const expense = (id, date, amount, currency, payer) => store.entry({id, date, status: 'confirmed', description: 'Fictional comparison',
    postings: [store.posting('Expenses:Food:Groceries', amount, currency), store.posting(payer, (-Number(amount)).toFixed(2), currency)]});
  // Use existing fixture accounts; the added refund must reduce both total and category.
  const sample = initial.transactions.find(row => row.currency === 'GBP' && row.kind === 'Expense');
  const account = sample.ledgerAccount;
  const refund = {...expense('eeeeeeeeeeeeeee1', '2026-06-17', '-8.00', 'GBP', sample.paymentAccount), extensions: blankExtensions(fileRows)};
  refund.postings[0].account = account;
  fileRows.push(refund);
  fileRows.sort((a, b) => a.date.localeCompare(b.date));
  await writeFile(f.file, JSON.stringify(fileRows));
  const data = await f.build('2026-07-17');
  for (const current of data.months.filter(row => row.month === '2026-07')) {
    const expected = data.transactions.filter(row => row.currency === current.currency && row.month === '2026-06' && row.date <= '2026-06-17');
    const previousExists = data.transactions.some(row => row.currency === current.currency && row.month === '2026-06');
    if (!previousExists) { assert.equal(current.comparison, null); continue; }
    assert.equal(current.comparison.month, '2026-06');
    assert.equal(current.comparison.total, Number(expected.reduce((sum, row) => sum + row.signedExpense, 0).toFixed(2)));
    assert.equal(Number(current.comparison.categories.reduce((sum, row) => sum + row.amount, 0).toFixed(2)), current.comparison.total);
    if (current.currency === 'GBP') assert.equal(current.comparison.total, Number((initial.months.find(row => row.month === current.month && row.currency === 'GBP').comparison.total - 8).toFixed(2)));
  }
  const complete = data.months.find(row => row.month === '2026-06' && row.currency === 'GBP');
  const may = data.months.find(row => row.month === '2026-05' && row.currency === 'GBP');
  assert.equal(complete.comparison.total, may.total, 'Past months compare the entire previous calendar month');
});

test('day 31 compares all 30 previous days, and absent currencies have no comparison', async t => {
  const f = await fixture(t, 'sam');
  const data = await f.build('2026-07-31');
  const july = data.months.find(row => row.month === '2026-07' && row.currency === 'GBP');
  assert.equal(july.comparison.total, data.months.find(row => row.month === '2026-06' && row.currency === 'GBP').total);
  assert.equal(data.months.find(row => row.month === '2026-07' && row.currency === 'EUR').comparison, null);
});

test('stale ledger keeps overdue commitments expected; today variable is consumed only by a today entry', async t => {
  const f = await fixture(t);
  const first = await f.build('2026-11-15');
  const forecast = first.forecasts.find(row => row.currency === 'USD');
  assert.ok(forecast.asOf < '2026-11-01');
  assert.equal(forecast.start, '2026-11-01');
  assert.ok(forecast.items.some(row => row.date < first.meta.today && row.month === '2026-11'), 'Overdue scheduled payment remains in the list');
  assert.equal(forecast.variableAfterDay, 14);
  const sample = first.transactions.find(row => row.currency === 'USD' && row.kind === 'Expense');
  const rows = JSON.parse(await readFile(f.file, 'utf8'));
  rows.push(store.entry({extensions: blankExtensions(rows), id: 'eeeeeeeeeeeeeee2', date: '2026-11-15', description: 'Fictional today purchase',
    postings: [store.posting(sample.ledgerAccount, '1.00', 'USD'), store.posting(sample.paymentAccount, '-1.00', 'USD')]}));
  await writeFile(f.file, JSON.stringify(rows));
  const second = await f.build('2026-11-15');
  assert.equal(second.forecasts.find(row => row.currency === 'USD').variableAfterDay, 15);
  assert.equal(second.forecasts.find(row => row.currency === 'EUR').variableAfterDay, 14, 'Each currency consumes today independently');
});

test('Explorer labels same-day comparisons and category keys; Forecast uses today even without new entries', async t => {
  const f = await fixture(t, 'sam');
  const data = await f.build('2026-07-17');
  const server = await createTestServer({server: {middlewareMode: true, hmr: false, ws: false}, appType: 'custom'});
  t.after(() => server.close());
  const runtime = await server.ssrLoadModule('/app/data.ts');
  runtime.setData({dashboard: data, review: {}, fuel: {}});
  const {Explorer} = await server.ssrLoadModule('/app/dashboard.tsx');
  const current = renderToStaticMarkup(createElement(Explorer, {month: '2026-07', setMonth() {}}));
  assert.match(current, /recorded through 17 Jul/);
  assert.match(current, /vs June by day 17/);
  assert.match(current, /aside-section-head.*by day 17/s);
  const past = renderToStaticMarkup(createElement(Explorer, {month: '2026-06', setMonth() {}}));
  assert.match(past, /vs May · whole month/);
  assert.match(past, /aside-section-head.*whole month/s);
  assert.doesNotMatch(past, /by day 17/);
  const stale = await f.build('2026-11-15');
  runtime.setData({dashboard: stale, review: {}, fuel: {}});
  const {default: Forecast} = await server.ssrLoadModule('/app/forecast.tsx');
  const html = renderToStaticMarkup(createElement(Forecast));
  assert.match(html, /Recorded through 15 Nov/);
  assert.match(html, /Nothing recorded this month yet/);
  assert.doesNotMatch(html, /−100%/);
  assert.match(html, /recorded through day 15/);
});

test('forecast line, list and total reconcile overdue payments and variable estimates per currency', async t => {
  const f = await fixture(t, 'sam');
  const server = await createTestServer({server: {middlewareMode: true, hmr: false, ws: false}, appType: 'custom'});
  t.after(() => server.close());
  const {forecastProjection, variableAfter} = await server.ssrLoadModule('/app/forecast-projection.ts');
  for (const date of ['2026-11-01', '2026-11-15', '2027-03-31']) {
    const data = await f.build(date);
    for (const forecast of data.forecasts) {
      const current = forecast.currentMonth;
      for (const include of [false, true]) {
        const p = forecastProjection(forecast, data.months, date, current, include);
        const recorded = data.transactions.filter(row => row.currency === forecast.currency && row.month === current && row.date <= date).reduce((sum, row) => sum + row.signedExpense, 0);
        const due = forecast.items.filter(row => row.month === current).reduce((sum, row) => sum + row.signedExpense, 0);
        const variable = include ? variableAfter(forecast, current, forecast.variableAfterDay) : 0;
        assert.ok(Math.abs(p.total - (recorded + due + variable)) < 1e-7, `${date} ${forecast.currency}: projected = recorded + still due + variable`);
        assert.equal(p.expected[0].day, Number(date.slice(8, 10)));
        const overdue = forecast.items.filter(row => row.month === current && row.date <= date).reduce((sum, row) => sum + row.signedExpense, 0);
        const todayEstimate = include ? variableAfter(forecast, current, p.asOfDay - 1) - variableAfter(forecast, current, p.asOfDay) : 0;
        assert.ok(Math.abs(p.expected[0].value - (recorded + overdue + todayEstimate)) < 1e-7, 'Overdue and today payments/estimate are on the line at today');
        assert.equal(p.committed, due, 'List and month summary retain every still-due amount');
        assert.equal(p.variable, variable);
      }
      const p = forecastProjection(forecast, data.months, date, current, true);
      const withToday = {...forecast, variableAfterDay: p.asOfDay};
      const consumed = forecastProjection(withToday, data.months, date, current, true);
      const todayEstimate = variableAfter(forecast, current, p.asOfDay - 1) - variableAfter(forecast, current, p.asOfDay);
      assert.ok(Math.abs(p.total - consumed.total - todayEstimate) < 1e-7, 'An entry today removes exactly today’s variable estimate');
    }
  }
});

test('a mid-month latest entry leaves later overdue rules expected at today', async t => {
  const f = await fixture(t);
  const data = await f.build('2026-11-15');
  const sample = data.transactions.find(row => row.currency === 'USD' && row.kind === 'Expense');
  const rows = JSON.parse(await readFile(f.file, 'utf8'));
  rows.push(store.entry({extensions: blankExtensions(rows), id: 'eeeeeeeeeeeeeee3', date: '2026-11-05', description: 'Fictional mid-month purchase',
    postings: [store.posting(sample.ledgerAccount, '2.00', 'USD'), store.posting(sample.paymentAccount, '-2.00', 'USD')]}));
  await writeFile(f.file, JSON.stringify(rows));
  const rules = JSON.parse(await readFile(join(f.root, 'data/forecast.json'), 'utf8'));
  const rule = rules.find(rule => rule.lifecycle === 'active');
  rule.date = rule.schedule.from = '2026-11-10';
  await writeFile(join(f.root, 'data/forecast.json'), JSON.stringify(rules));
  const current = await f.build('2026-11-15');
  const forecast = current.forecasts.find(row => row.currency === 'USD');
  assert.equal(forecast.start, '2026-11-06');
  assert.ok(forecast.items.some(item => item.date === '2026-11-10'));
  assert.equal(forecast.variableAfterDay, 14);
});

test('a zero previous same-day total retains the comparison period without a percentage', async t => {
  const f = await fixture(t);
  const first = await f.build('2026-11-01');
  const sample = first.transactions.find(row => row.currency === 'USD' && row.kind === 'Expense');
  const rows = JSON.parse(await readFile(f.file, 'utf8'));
  rows.push(store.entry({extensions: blankExtensions(rows), id: 'eeeeeeeeeeeeeee4', date: '2026-11-01', description: 'Fictional first-day purchase',
    postings: [store.posting(sample.ledgerAccount, '2.00', 'USD'), store.posting(sample.paymentAccount, '-2.00', 'USD')]}));
  await writeFile(f.file, JSON.stringify(rows));
  const data = await f.build('2026-11-01');
  assert.equal(data.months.find(row => row.month === '2026-11' && row.currency === 'USD').comparison.total, 0);
  const server = await createTestServer({server: {middlewareMode: true, hmr: false, ws: false}, appType: 'custom'});
  t.after(() => server.close());
  (await server.ssrLoadModule('/app/data.ts')).setData({dashboard: data, review: {}, fuel: {}});
  const {Explorer} = await server.ssrLoadModule('/app/dashboard.tsx');
  const html = renderToStaticMarkup(createElement(Explorer, {month: '2026-11', setMonth() {}}));
  const metric = html.match(/<div class="metric">(.*?)<\/div>/s)[1];
  assert.match(metric, /No comparison/);
  assert.match(metric, /vs October by day 1/);
  assert.doesNotMatch(metric, /%/);
});
