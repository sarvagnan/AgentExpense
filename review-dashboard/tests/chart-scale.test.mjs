import './test-isolation.mjs';
import assert from 'node:assert/strict';
import {after, test} from 'node:test';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {personaData} from './persona-data.mjs';
import {createTestServer} from './test-isolation.mjs';
import {setMoneySettings, axisMoney} from '../scripts/formatting.mjs';

const server = await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
after(() => server.close());
const {niceScale, placeLabels, comparisonWindows, defaultComparisonWindow, trendMonths} = await server.ssrLoadModule('/app/chart-scale.ts');
const personas = Object.fromEntries(await Promise.all(['asha', 'sam', 'jordan'].map(async name => [name, await personaData(name)])));

test('a nice-number axis comes from the data, never a fixed step', () => {
  // Asha's INR month, Sam's EUR month and a few hundred: 1, 2 or 5 × 10ⁿ steps from zero.
  assert.deepEqual(niceScale(0, 18168, 6), {low: 0, top: 20000, step: 5000, ticks: [0, 5000, 10000, 15000, 20000]});
  assert.deepEqual(niceScale(0, 2123, 6).ticks, [0, 500, 1000, 1500, 2000, 2500]);
  assert.deepEqual(niceScale(0, 2123, 4).ticks, [0, 1000, 2000, 3000]);
  assert.deepEqual(niceScale(0, 1846, 4).ticks, [0, 1000, 2000]);
  assert.deepEqual(niceScale(-120, 480, 6).ticks, [-200, 0, 200, 400, 600]);
  assert.deepEqual(niceScale(0, 0.3, 4).ticks, [0, 0.1, 0.2, 0.3]);
  // No data still gives a drawable axis.
  assert.deepEqual(niceScale(0, 0), {low: 0, top: 1, step: 1, ticks: [0, 1]});
  for (const max of [1, 7, 12.5, 99, 999, 1001, 47321, 182000, 2.4e6]) for (const count of [3, 4, 5, 6]) {
    const scale = niceScale(0, max, count);
    assert.ok(scale.ticks.length <= count && scale.top >= max && scale.ticks.includes(0), `${max} in ${count} ticks: ${JSON.stringify(scale)}`);
    assert.ok([1, 2, 5].includes(Number((scale.step / 10 ** Math.floor(Math.log10(scale.step))).toFixed(6))), `${max}: step ${scale.step}`);
    assert.ok(scale.top - scale.step < max, `${max}: no empty step above the data`);
  }
});

test('whole axis ticks print without a decimal, in each locale', () => {
  setMoneySettings({locale: 'en-GB', defaultCurrency: 'EUR'});
  assert.deepEqual([0, 1000, 2000, 3000].map(value => axisMoney(value, 3000, 'EUR')), ['€0', '€1k', '€2k', '€3k']);
  assert.deepEqual([0, 500, 1000, 1500].map(value => axisMoney(value, 1500, 'GBP')), ['£0', '£0.5k', '£1k', '£1.5k']);
  assert.deepEqual([0, 200, 400].map(value => axisMoney(value, 400, 'GBP')), ['£0', '£200', '£400']);
  setMoneySettings({locale: 'en-US', defaultCurrency: 'USD'});
  assert.deepEqual([0, 1000, 2000].map(value => axisMoney(value, 2000, 'USD')), ['$0', '$1K', '$2K']);
  setMoneySettings({locale: 'en-IN', defaultCurrency: 'INR'});
  assert.deepEqual([0, 5000, 20000].map(value => axisMoney(value, 20000)), ['₹0', '₹5K', '₹20K']);
});

test('line-end labels are placed without overlap and inside the plot', () => {
  const placed = placeLabels([{id: 'a', y: 100, height: 15}, {id: 'b', y: 104, height: 15}, {id: 'projected', y: 102, height: 32}, {id: 'c', y: 300, height: 15}], 0, 310);
  const boxes = Object.entries(placed).map(([id, y]) => ({id, top: y - (id === 'projected' ? 16 : 7.5), bottom: y + (id === 'projected' ? 16 : 7.5)})).sort((a, b) => a.top - b.top);
  boxes.slice(1).forEach((box, index) => assert.ok(box.top >= boxes[index].bottom + 2 - 1e-9, `${box.id} clears ${boxes[index].id}: ${JSON.stringify(placed)}`));
  assert.equal(placed.c, 300, 'A label with room keeps its own height');
  // A crowded bottom pushes the column back up, so nothing falls off the plot.
  const low = placeLabels([{id: 'a', y: 98, height: 15}, {id: 'b', y: 99, height: 15}, {id: 'c', y: 100, height: 15}], 0, 100);
  assert.ok(low.c + 7.5 <= 100 && low.b + 7.5 + 2 <= low.c - 7.5 + 1e-9 && low.a + 7.5 + 2 <= low.b - 7.5 + 1e-9, JSON.stringify(low));
  assert.deepEqual(placeLabels([], 0, 10), {});
});

test('Forecast offers the comparison window its figures use', () => {
  assert.deepEqual(comparisonWindows(0), []);
  assert.deepEqual(comparisonWindows(1), [{value: 1, label: 'The one full month'}]);
  assert.deepEqual(comparisonWindows(2).map(option => option.label), ['All 2 full months']);
  assert.deepEqual(comparisonWindows(5).map(option => option.label), ['Last 3 full months', 'All 5 full months']);
  assert.deepEqual(comparisonWindows(6).map(option => option.value), [3, 6]);
  assert.deepEqual(comparisonWindows(8).map(option => option.label), ['Last 3 full months', 'Last 6 full months', 'All 8 full months']);
  assert.deepEqual(comparisonWindows(12).map(option => option.value), [3, 6, 12]);
  assert.deepEqual(comparisonWindows(30).map(option => option.value), [3, 6, 12]);
  for (let full = 1; full <= 30; full += 1) assert.ok(comparisonWindows(full).some(option => option.value === defaultComparisonWindow(full)), `${full} full months: the default is listed`);
});

test('the twelve-month range ends at the last complete month', () => {
  const months = ['2025-12', '2026-01', '2026-09', '2026-10', '2026-11'];
  assert.deepEqual(trendMonths(months, '2026-11-14'), ['2025-11', '2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09', '2026-10']);
  assert.equal(trendMonths(months, '2026-12-01').at(-1), '2026-11');
  assert.equal(trendMonths(['2026-03'], '2026-11-01').at(-1), '2026-03', 'A ledger that stopped earlier ends at its own last month');
  assert.deepEqual(trendMonths(['2026-11'], '2026-11-02'), [], 'Only the month in progress: nothing to plot');
});

async function views(data) {
  (await server.ssrLoadModule('/app/data.ts')).setData({...personas.asha, dashboard: data});
  const {Explorer} = await server.ssrLoadModule('/app/dashboard.tsx');
  const {default: Forecast} = await server.ssrLoadModule('/app/forecast.tsx');
  return {explorer: month => renderToStaticMarkup(createElement(Explorer, {month, setMonth: () => {}})), forecast: () => renderToStaticMarkup(createElement(Forecast, {online: true}))};
}

test('every persona’s Forecast selects the window its figures use, on a nice scale', async () => {
  for (const [name, persona] of Object.entries(personas)) {
    const data = persona.dashboard;
    const html = (await views(data)).forecast();
    for (const forecast of data.forecasts.filter(item => item.items.length || data.months.some(row => row.currency === item.currency && row.count))) {
      const full = data.months.filter(row => row.currency === forecast.currency && row.month >= forecast.firstFullMonth && row.month < forecast.currentMonth).length;
      if (!full) continue;
      const window = defaultComparisonWindow(full);
      assert.match(html, new RegExp(`<option value="${window}" selected="">`), `${name} ${forecast.currency}: ${window} of ${full} full months selected`);
      assert.match(html, new RegExp(`${window}-month average`), `${name} ${forecast.currency}: the figures use the selected window`);
    }
    assert.doesNotMatch(html, /\b0-month|\b0 full months/, name);
    for (const panel of html.split('class="page forecast"').slice(1)) {
      const ticks = [...panel.matchAll(/<text x="[\d.]+" y="[\d.]+" text-anchor="end" class="chart-axis">([^<]+)<\/text>/g)].map(match => match[1]);
      assert.ok(ticks.length >= 3 && ticks.length <= 6, `${name}: ${ticks}`);
    }
  }
  // Sam's EUR line used to sit in the bottom 4% of a ₹50K-style axis; its top tick is now near the data.
  const sam = personas.sam.dashboard;
  const eur = (await views(sam)).forecast().split('class="page forecast"').find(panel => panel.includes('Forecast · EUR'));
  const top = [...eur.matchAll(/class="chart-axis">(€[\d.]+k?)<\/text>/g)].map(match => match[1]).filter(text => /^€[\d.]+k?$/.test(text));
  assert.ok(top.includes('€3k') || top.includes('€2.5k'), `EUR axis follows EUR data: ${top}`);
});

test('a currency with no full months shows a short empty state and no "0-month" wording', async () => {
  const data = structuredClone(personas.asha.dashboard);
  const base = data.forecasts[0];
  const month = base.currentMonth;
  const eur = {...structuredClone(base), currency: 'EUR', firstFullMonth: month,
    variable: {months: [], portions: base.variable.portions.map(portion => ({...portion, average: 0, median: 0, min: 0, max: 0, byMonth: []})), excluded: []},
    months: base.months.map(row => ({...row, committed: row.month === month ? 12.37 : 0})),
    items: [{currency: 'EUR', date: `${month}-10`, month, description: 'Fictional overdue membership', rule: 'f000000000000099', kind: 'Expense', category: 'Leisure', account: 'Expenses:Leisure', amount: 12.37, signedExpense: 12.37, paymentLabel: 'Fictional paying account', sources: []}]};
  data.forecasts = [eur, ...data.forecasts];
  const html = (await views(data)).forecast();
  const panel = html.split('class="page forecast"').find(part => part.includes('Forecast · EUR'));
  assert.ok(panel, 'The EUR panel is shown');
  assert.match(panel, /class="empty forecast-chart-empty"/);
  assert.match(panel, /No full months yet/);
  assert.doesNotMatch(panel, /\b0-month|\b0 full months|Comparison average|forecast-dotted|Show Latest/);
  assert.match(panel, /Fictional overdue membership/, 'The expected payment is still listed');
  assert.match(panel, /It starts once a whole month is recorded\./, 'The variable estimate says when it starts');
});

test('Explorer’s twelve months stop before the month in progress', async () => {
  const data = structuredClone(personas.asha.dashboard);
  const current = data.meta.today.slice(0, 7);
  const october = data.months.find(row => row.month < current && row.count > 0 && row.currency === data.meta.ledger.defaultCurrency);
  // A few entries recorded early in the month in progress must not plot as a plunge.
  data.months = data.months.filter(row => row.month !== current);
  data.months.push({...structuredClone(october), month: current, total: 18.6, count: 1, daily: [{day: 1, amount: 18.6}], comparison: null});
  const html = (await views(data)).explorer(october.month);
  const trend = html.split('class="panel spending-trend"')[1];
  assert.ok(trend, 'The chart is shown');
  const monthName = month => new Date(`${month}-15T12:00:00Z`).toLocaleString('en', {month: 'long', timeZone: 'UTC'});
  assert.match(trend, new RegExp(`– ${monthName(trendMonths(data.months.map(row => row.month), data.meta.today).at(-1))} \\d{4}</p>`));
  assert.doesNotMatch(trend.split('<details>')[0], /<title>[^<]*: ₹18\.60<\/title>/, 'The month in progress has no point');
});
