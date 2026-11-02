import './test-isolation.mjs';
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {execFileSync} from 'node:child_process';
import {cp,readFile,writeFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {disposableDashboard,sourceApplication,createTestServer} from './test-isolation.mjs';
import {today,calendarMonths} from '../scripts/date-time.mjs';

 test('today is a local date, pinnable and validated in both local zones',()=>{
  for(const TZ of ['Pacific/Honolulu','Pacific/Kiritimati']) {
    const code=`import assert from 'node:assert/strict';import {today} from './scripts/date-time.mjs';assert.equal(today(new Date('2026-11-01T03:00:00Z')), process.env.TZ==='Pacific/Honolulu'?'2026-10-31':'2026-11-01');process.env.DASHBOARD_TODAY='2028-02-29';assert.equal(today(),'2028-02-29');for(const value of ['2026-02-30','2026-11','bad']){process.env.DASHBOARD_TODAY=value;assert.throws(()=>today(),/DASHBOARD_TODAY/);}`;
    execFileSync(process.execPath,['--input-type=module','-e',code],{cwd:join(sourceApplication,'review-dashboard'),env:{...process.env,TZ,DASHBOARD_TODAY:''}});
  }
  assert.deepEqual(calendarMonths(['2026-09','2026-10'],'2026-11'),['2026-09','2026-10','2026-11']);
  assert.deepEqual(calendarMonths([],'2027-01'),['2026-12','2027-01']);
 });

for(const persona of ['asha','jordan','sam','empty'])test(`${persona}: first and mid-month builds include an empty current month and recorded-only currency averages`,async t=>{
  const copy=await disposableDashboard();t.after(()=>rm(copy.application,{recursive:true,force:true}));
  const root=join(copy.application,'ledger');await cp(join(sourceApplication,'fixtures/ledgers',persona),root,{recursive:true});
  const build=async date=>{
    execFileSync(process.execPath,[join(copy.dash,'scripts/build-dashboard-data.mjs')],{env:{...copy.env,LEDGER_DIR:root,DASHBOARD_TODAY:date},stdio:'pipe'});
    return JSON.parse(await readFile(join(copy.dash,'app/data/dashboard.json'),'utf8'));
  };
  let first;
  for(const date of ['2026-11-01','2026-11-15','2026-10-31']) {
    const data=await build(date),current=date.slice(0,7);
    assert.equal(data.meta.today,date);assert.equal(data.meta.defaultMonth,current);assert.equal(data.meta.lastMonth,current);
    if(current==='2026-11') {
      const rows=data.months.filter(row=>row.month===current);
      assert.equal(rows.length,1);assert.equal(rows[0].currency,'');assert.deepEqual(rows[0].daily,[]);assert.deepEqual(rows[0].categories,[]);
    }
    for(const forecast of data.forecasts) {
      assert.equal(forecast.currentMonth,current);
      const actual=data.transactions.filter(row=>row.currency===forecast.currency);
      const latest=actual.map(row=>row.date).sort().at(-1);
      assert.equal(forecast.asOf,latest || '');
      assert.equal(forecast.daysPassed,latest?.startsWith(current)?Number(latest.slice(8,10)):0);
      assert.equal(forecast.start,latest?.startsWith(current)?new Date(Date.parse(latest)+86400000).toISOString().slice(0,10):current+'-01');
      const expected=[...new Set(actual.map(row=>row.month))].filter(month=>month>=forecast.firstFullMonth && month<current).sort();
      assert.deepEqual(forecast.variable.months,expected,'An absent currency/month never contributes a zero observation');
      assert.ok(forecast.months.some(row=>row.month===current),'Current month exists without rule occurrences');
      for(const portion of forecast.variable.portions)assert.equal(portion.byMonth.length,expected.length);
    }
    if(date==='2026-11-01')first=data;
    if(date==='2026-11-15')assert.deepEqual(data.forecasts,first.forecasts,'An empty month consumes no recorded days even mid-month');
  }
  if(persona!=='empty') {
    const currency=first.meta.ledger.defaultCurrency;
    const payment=Object.entries(first.meta.accounts).find(([,value])=>value.kind==='bank' && value.currency===currency)[0];
    const expense=Object.keys(first.meta.accounts).find(account=>account.startsWith('Expenses:'));
    const file=join(root,'data/expenses/2026.json'),entries=JSON.parse(await readFile(file,'utf8'));
    entries.push({id:'ffffffffffffffff',date:'2026-11-05',status:'confirmed',description:'Fictional current-month purchase',postings:[{account:expense,amount:'10.00',commodity:currency},{account:payment,amount:'-10.00',commodity:currency}]});
    await writeFile(file,JSON.stringify(entries));
    const mid=await build('2026-11-15');
    const forecast=mid.forecasts.find(row=>row.currency===currency);
    assert.equal(forecast.asOf,'2026-11-05');assert.equal(forecast.daysPassed,5);assert.equal(forecast.start,'2026-11-06');
    assert.deepEqual(forecast.variable,first.forecasts.find(row=>row.currency===currency).variable,'A partially recorded current month is excluded from averages');
    assert.ok(mid.months.some(row=>row.month==='2026-11' && row.currency===currency && row.total===10));
  }
});

test('Forecast renders one final variable row per currency with whole-month and remaining-days labels',async()=>{
  const copy=await disposableDashboard();
  const root=join(copy.application,'ledger');await cp(join(sourceApplication,'fixtures/ledgers/sam'),root,{recursive:true});
  execFileSync(process.execPath,[join(copy.dash,'scripts/build-dashboard-data.mjs')],{env:{...copy.env,LEDGER_DIR:root,DASHBOARD_TODAY:'2026-11-15'},stdio:'pipe'});
  const data=JSON.parse(await readFile(join(copy.dash,'app/data/dashboard.json'),'utf8'));
  const server=await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
  try {
    const runtime=await server.ssrLoadModule('/app/data.ts');
    const {default:Forecast}=await server.ssrLoadModule('/app/forecast.tsx');
    const render=()=>{runtime.setData({dashboard:data,review:{},fuel:{}});return renderToStaticMarkup(createElement(Forecast));};
    for(const recorded of [false,true]) {
      for(const forecast of data.forecasts)if(recorded){forecast.asOf='2026-11-05';forecast.daysPassed=5;}
      const html=render(),lists=[...html.matchAll(/<ol class="ranked-list forecast-items">(.*?)<\/ol>/gs)].map(row=>row[1]);
      assert.equal(lists.length,2,'GBP and EUR have independent rows');
      for(const list of lists) {
        assert.equal((list.match(/class="estimate"/g)||[]).length,1);
        assert.match(list,/<li class="estimate">.*<\/li>$/s);
        assert.match(list,recorded?/The 25 days left/:/The whole month/);
        assert.match(list,/\d+-month average .* a month/);
        assert.match(list,/Estimated variable spending/);
      }
      assert.match(html,/Recorded through/);
      assert.match(html,recorded?/recorded through day 5/:/recorded through day 0/);
    }
  } finally {await server.close();await rm(copy.application,{recursive:true,force:true});}
});
