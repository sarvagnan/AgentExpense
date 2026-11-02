import assert from 'node:assert/strict';
import {gzipSync} from 'node:zlib';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {expenseSummary, previousMonth} from '../../scripts/month-summary.mjs';

// Keep a recorded current month visible while advancing only the published date.
// The financial builder/line arithmetic is checked against real fixtures in Node.
export async function runMonthCutoff({page, stage, expect, go, overflow, snap, record}) {
  const data=JSON.parse(await readFile(join(stage.dash,'app/data/dashboard.json'),'utf8'));
  const month=data.meta.today.slice(0,7),day=Number(data.meta.today.slice(8,10));
  await go('explorer/');
  await expect(page.locator('.page-heading p')).toContainText('recorded through');
  await expect(page.locator('.aside-section-head').filter({hasText:'Categories'})).toContainText(`by day ${day}`);
  const spent=page.locator('.metrics .metric').first();
  const current=data.months.filter(row=>row.month===month && row.currency);
  for(const row of current) {
    if(row.comparison)await expect(spent).toContainText(`by day ${day}`);
  }
  await overflow('current Explorer comparisons');await snap('explorer-current-recorded');

  data.meta.today=month+'-15';
  for(const row of data.months.filter(row=>row.month===month && row.currency)) {
    const before=data.transactions.filter(item=>item.month===previousMonth(month)&&item.currency===row.currency);
    row.comparison=before.length?{month:previousMonth(month),...expenseSummary(before.filter(item=>Number(item.date.slice(8,10))<=15))}:null;
  }
  const currency=current[0].currency;
  const forecast=data.forecasts.find(row=>row.currency===currency);
  const overdue={currency,date:month+'-10',month,description:'Fictional overdue membership',rule:'f000000000000099',kind:'Expense',category:'Leisure',account:'Expenses:Leisure',amount:12.37,signedExpense:12.37,paymentLabel:'Fictional paying account',sources:[]};
  forecast.items.push(overdue);
  forecast.months.find(row=>row.month===month).committed+=overdue.signedExpense;
  for(const f of data.forecasts)f.variableAfterDay=14;
  data.forecast=data.forecasts.find(row=>row.currency===data.meta.ledger.defaultCurrency);
  const response=route=>route.fulfill({status:200,contentType:'application/gzip',body:gzipSync(JSON.stringify(data))});
  await page.route('**/data/dashboard.json.gz',response);
  try {
    await go('explorer/');
    await expect(page.locator('.page-heading p')).toContainText('recorded through');
    await expect(page.locator('.aside-section-head').filter({hasText:'Categories'})).toContainText('by day 15');
    for(const row of current)if(row.comparison)await expect(page.locator('.metrics .metric').first()).toContainText('by day 15');
    await overflow('same-day Explorer comparison');await snap('explorer-same-day');
    await go('forecast/');
    const panel=page.locator('.forecast').filter({hasText:'Fictional overdue membership'});
    await expect(panel).toHaveCount(1);
    await expect(panel.locator('svg[role="img"]')).toHaveAttribute('aria-label',/recorded through day 15/);
    await expect(panel.locator('.forecast-items')).toContainText('Fictional overdue membership');
    await expect(panel.locator('.forecast-items li').last()).toHaveClass('estimate');
    await expect(panel.locator('.estimate .row-sub')).toContainText('The 16 days left');
    const total=await panel.locator('.metrics .metric').first().locator('.metric-value').innerText();
    await expect(panel.locator('tr.current')).toContainText(total);
    assert.ok(await panel.locator('.forecast-dotted').getAttribute('d'));
    await panel.locator('.forecast-chart-panel').scrollIntoViewIfNeeded();
    await panel.locator('.chart-focus').focus();
    await expect(panel.getByText('Expected at today',{exact:true})).toBeVisible();
    await overflow('overdue forecast at today');await snap('forecast-overdue-at-today');
    record('6b2 today cut-off, same-day Explorer/category keys, overdue expected payment and chart/table total agree');
  } finally {
    await page.unroute('**/data/dashboard.json.gz',response);
  }
}
