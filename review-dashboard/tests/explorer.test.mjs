import {dashboardDataPath} from '../../scripts/dashboard-data.mjs';
import './test-isolation.mjs';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createTestServer as createServer } from './test-isolation.mjs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { loadPublishedData } from './load-data.mjs';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  await loadPublishedData(server);
  const { default: Dashboard, Explorer } = await server.ssrLoadModule('/app/dashboard.tsx');
  const data = JSON.parse(await readFile(dashboardDataPath('app/data/dashboard.json')));
  const html = renderToStaticMarkup(createElement(Dashboard, { view: 'explorer' }));
  const body = html.match(/<tbody[^>]*>(.*?)<\/tbody>/s)?.[1] || '';
  const hasExpenses=data.months.some(month=>month.count>0);
  if (!hasExpenses) {assert.match(html,/Nothing recorded this month yet/);assert.match(html,/Your spending starts here\./);assert.match(html,/Add your first expense/);assert.match(html,/See opening balances/);assert.doesNotMatch(html,/class="metrics"|class="spending-trend"/);}
  const count = (body.match(/<tr\b/g) || []).length;
  const expected = hasExpenses ? data.transactions.filter(t => t.month === data.meta.defaultMonth && !t.postings.some(posting=>posting.account==='Equity:Opening Balances')).length : 0;
  assert.equal(count, expected, 'Explorer must render month activity without opening balances');
  for (const month of data.months) {
    const markup = renderToStaticMarkup(createElement(Explorer, { month: month.month, setMonth: () => {} }));
    if(!hasExpenses){assert.match(markup,/Your spending starts here\./);assert.doesNotMatch(markup,/<tbody>|class="metrics"|class="spending-trend"/);continue;}
    if (!month.currency) {assert.match(markup,/Nothing recorded this month yet/);assert.doesNotMatch(markup,/class="metrics"/);continue;}
    const rows = markup.match(/<tbody>(.*?)<\/tbody>/s)[1];
    assert.equal((rows.match(/<tr\b/g) || []).length, data.transactions.filter(t => t.month === month.month && !t.postings.some(posting=>posting.account==='Equity:Opening Balances')).length, month.month);
    assert.equal((markup.match(/class="sort-button"/g) || []).length, 5);
    assert.equal((rows.match(/class="row-open"/g) || []).length, (rows.match(/<tr\b/g) || []).length, 'Every row opens from the keyboard');
    assert.match(markup, /aria-sort="descending"/);
    assert.doesNotMatch(markup, /All dates|Transaction date range/);
  }
  const { sortTransactions } = await server.ssrLoadModule('/app/transaction-sort.ts');
  const base = { date: '2026-09-19', sequence: 1, description: 'Alpha', category: 'Food', paymentLabel: 'Savings Account', status: 'Confirmed', amount: 9 };
  for (const key of ['date', 'description', 'category', 'paymentLabel', 'status', 'amount']) {
    const high = { ...base, [key]: key === 'amount' ? 100 : key === 'date' ? '2026-09-20' : 'Zulu' };
    const input = [high, base];
    assert.deepEqual(sortTransactions(input, key, 'asc'), [base, high], key);
    assert.deepEqual(sortTransactions(input, key, 'desc'), [high, base], key);
    assert.deepEqual(input, [high, base], 'sorting does not mutate shared data');
  }
  const later = { ...base, sequence: 2 };
  assert.deepEqual(sortTransactions([later, base], 'date', 'asc'), [base, later]);
  assert.deepEqual(sortTransactions([base, later], 'date', 'desc'), [later, base]);
  assert.deepEqual(sortTransactions([], 'amount', 'asc'), []);
  assert.equal(sortTransactions(data.transactions, 'date', 'desc').length, data.transactions.length);
  console.log(`Explorer: all transactions rendered for ${data.months.length} months; all six columns sort both ways with stable sequence ordering.`);
} finally {
  await server.close();
}
