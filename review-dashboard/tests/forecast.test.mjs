import './test-isolation.mjs';
import path from 'node:path';
import { ledgerRoot } from '../../scripts/project-paths.mjs';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { createTestServer as createServer } from './test-isolation.mjs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { loadPublishedData } from './load-data.mjs';

import { personaData } from './persona-data.mjs';
const persona = await personaData('asha');
const data = persona.dashboard;
const { forecast } = data;
const ledger = path.join(persona.root, 'expense_ledger.ledger');
const nextDay = (day) => { const date = new Date(`${day}T00:00:00Z`); date.setUTCDate(date.getUTCDate() + 1); return date.toISOString().slice(0, 10); };

assert.equal(forecast.asOf, data.transactions.reduce((latest, row) => row.date > latest ? row.date : latest, ''), 'Forecast starts after the latest actual entry');
assert.equal(forecast.start, forecast.asOf.slice(0, 7) === forecast.currentMonth ? nextDay(forecast.asOf) : `${forecast.currentMonth}-01`);
assert.ok(forecast.items.every((item) => item.date >= forecast.start && item.date < forecast.end), 'Only generated entries inside the forecast period');
assert.ok(forecast.items.every((item) => item.description), 'Every forecast entry is named from its rule');
assert.deepEqual(forecast.months.map(row => row.month), [...new Set([forecast.currentMonth, ...forecast.items.map(row => row.month)])].sort(), 'Forecast months follow actual rule occurrences');

// Committed expenses equal the payment-side movement hledger forecasts; investments net to zero there.
for (const month of forecast.months.slice(0, 4)) {
  const from = month.month === forecast.currentMonth ? forecast.start : `${month.month}-01`;
  const [year, monthNumber] = month.month.split('-').map(Number);
  const to = new Date(Date.UTC(year, monthNumber, 1)).toISOString().slice(0, 10);
  const output = execFileSync(process.env.HLEDGER_BIN || 'hledger', ['-f', ledger, 'balance', `--forecast=${from}..${to}`, `date:${from}..${to}`, 'cur:INR', '^Assets', '^Liabilities', '--format=%(total)', '-N', '--flat'], { encoding: 'utf8' })
    .trim().split('\n').map((line) => Number(line.replaceAll(',', '').replace(/ INR$/, '') || 0)).reduce((sum, value) => sum + value, 0);
  assert.equal(Number((-output).toFixed(2)), month.committed, `${month.month} committed expenses match hledger --forecast`);
}
for (const month of data.months) assert.ok(month.recurring <= month.total + 0.01 || month.total < 0, `${month.month} recurring share within total`);

// The variable profile covers every full month, and its portions plus excluded instalments
// account for all spending outside the rule accounts in those months.
const { variable } = forecast;
const fullMonths = data.months.filter((month) => month.month >= forecast.firstFullMonth && month.month < forecast.currentMonth);
assert.deepEqual(variable.months, fullMonths.map((month) => month.month), 'Variable profile uses all full months');
assert.deepEqual(variable.portions.map((portion) => [portion.from, portion.to]), [[1, 7], [8, 14], [15, 21], [22, 31]]);
const portionSum = variable.portions.reduce((sum, portion) => sum + portion.byMonth.reduce((a, b) => a + b, 0), 0) + variable.excluded.reduce((sum, plan) => sum + plan.amount, 0);
const outsideRules = fullMonths.reduce((sum, month) => sum + month.total - month.recurring, 0);
assert.ok(Math.abs(portionSum - outsideRules) < 1, `Portions ${portionSum.toFixed(2)} reconcile with spending outside rule accounts ${outsideRules.toFixed(2)}`);
const tagged = execFileSync(process.env.HLEDGER_BIN || 'hledger', ['-f', ledger, 'balance', '^Expenses', 'tag:variable-estimate=excluded', `date:${fullMonths[0].month}-01..${forecast.currentMonth}-01`, 'cur:INR', '-N', '--depth', '1', '--format=%(total)'], { encoding: 'utf8' }).trim().replaceAll(',', '').replace(/ INR$/, '');
const excludedTagged = variable.excluded.filter((item) => item.reason !== 'instalment plan').reduce((sum, item) => sum + item.amount, 0);
assert.ok(Math.abs(excludedTagged - Number(tagged || 0)) < 0.01, `Tagged entries ${tagged} are excluded from the variable estimate (${excludedTagged.toFixed(2)})`);
for (const portion of variable.portions) assert.ok(Math.abs(portion.average * variable.months.length - portion.byMonth.reduce((a, b) => a + b, 0)) < 0.1 && portion.min <= portion.median && portion.median <= portion.max, `Days ${portion.from}-${portion.to} summary is consistent`);

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
try {
  (await server.ssrLoadModule('/app/data.ts')).setData({ dashboard: data, review: persona.review, fuel: persona.fuel });
  const { default: Forecast } = await server.ssrLoadModule('/app/forecast.tsx');
  const html = renderToStaticMarkup(createElement(Forecast));
  const history = data.months.filter((month) => month.month >= forecast.firstFullMonth && month.month < forecast.currentMonth).slice(-12).length;
  assert.equal((html.match(/<input type="checkbox"/g) || []).length, history + 1, 'Every recent recorded month can be ticked onto the chart (plus the variable-spending checkbox)');
  assert.equal((html.match(/class="month-toggle" aria-pressed="true"/g) || []).length, Math.min(3, history), 'The three latest months are compared by default');
  const addMonth = html.match(/<select class="field add-month".*?<\/select>/s)?.[0] || '';
  assert.equal((addMonth.match(/<option value="20\d\d-\d\d">/g) || []).length, history - Math.min(3, history), 'The others can be added from the pop-up');
  assert.equal((html.match(/class="forecast-dotted"/g) || []).length, 1, 'One dotted forecast line for the chosen month');
  assert.equal((html.match(/<tr class="(actual|current|forecast)/g) || []).length, history + 7, 'Table lists recorded months, this month and six forecast months');
  assert.equal((html.match(/class="forecast-strip"/g) || []).length, 4, 'One strip of monthly values per portion of the month');
  const projected = html.match(/projected<\/p><p class="metric-value">(₹[\d,]+)/)?.[1];
  const tableTotal = html.match(/<tr class="current[^"]*"[^>]*>.*?<td[^>]*><strong>(₹[\d,]+)/)?.[1];
  assert.ok(tableTotal, 'The current month always has a table row, even without commitments');
  assert.ok(projected && projected === tableTotal, `Projected ${projected} matches the current-month table total ${tableTotal}`);
  console.log(`Forecast: ${forecast.items.length} generated entries ${forecast.start}..${forecast.end}; committed totals match hledger; line chart compares ${history} selectable months; projected ${projected}.`);
} finally { await server.close(); }
