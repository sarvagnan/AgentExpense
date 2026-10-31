import './test-isolation.mjs';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createTestServer as createServer } from './test-isolation.mjs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { loadPublishedData } from './load-data.mjs';

const data = JSON.parse(await readFile(new URL('../app/data/dashboard.json', import.meta.url)));
for (const row of data.transactions.filter(row => row.kind === 'Transfer')) {
  const values = row.postings.map(p => p.currency === 'INR' ? p.amount : p.cost ?? 0);
  const debit = values.filter(value => value > 0).reduce((sum, value) => sum + value, 0);
  const credit = -values.filter(value => value < 0).reduce((sum, value) => sum + value, 0);
  assert.equal(row.amount, Number(debit.toFixed(2)), row.description);
  assert.equal(row.amount, Number(credit.toFixed(2)), 'Count movement once, not both sides');
  assert.equal(row.signedExpense, 0);
}
for (const type of ['Card payment', 'Balance top-up', 'Balance adjustment']) {
  assert.ok(data.transactions.some(row => row.displayType === type && row.amount > 0), type);
}
const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  await loadPublishedData(server);
  const { Journal } = await server.ssrLoadModule('/app/dashboard.tsx');
  for (const month of data.months) {
    const html = renderToStaticMarkup(createElement(Journal, { month: month.month, setMonth: () => {} }));
    const book = html.split('class="panel journal-book"')[1].split('<aside')[0];
    assert.equal((book.match(/class="row-open"/g) || []).length, data.transactions.filter(row => row.month === month.month).length, 'Journal must not truncate days');
    assert.match(book, /From → To/);
    assert.match(book, /Net expenses for/);
  }
  console.log('Journal: fictional transfers balance without expense impact and every month renders all rows.');
} finally { await server.close(); }
