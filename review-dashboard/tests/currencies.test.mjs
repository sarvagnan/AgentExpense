import './test-isolation.mjs';
import assert from 'node:assert/strict';
import {after, test} from 'node:test';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {personaData} from './persona-data.mjs';
import {createTestServer} from './test-isolation.mjs';
import {setMoneySettings, money, moneyTotal, compactMoney, axisMoney, units, count} from '../scripts/formatting.mjs';
import {sumCurrencies} from '../scripts/currency.mjs';
import {accountActivity, balanceAccounts, priceOn, valueSeries} from '../app/account-balances.ts';
import {parsePrices} from '../scripts/expense-store.mjs';
import {currentAccounts,setCurrentAccounts} from '../scripts/account-metadata.mjs';
import {CURRENT_FORMAT} from '../scripts/ledger-manifest.mjs';

const sam = await personaData('sam');
const jordan = await personaData('jordan');
const server = await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
after(() => server.close());

test('formatting uses the ledger locale, each currency, units, counts and stable mixed totals', () => {
 setMoneySettings({locale:'en-GB',defaultCurrency:'EUR'});
 assert.equal(moneyTotal({GBP:8200,EUR:14500,USD:40}), '€14,500.00 + £8,200.00 + US$40.00');
 assert.equal(moneyTotal({GBP:8200}), '£8,200.00');
 assert.equal(money(-0.001,'GBP'),'£0.00');
 assert.equal(money(-2,'GBP'),'−£2.00');
 assert.equal(money(1.25,'FUND'),'1.250 units');
 assert.equal(money(1.25,'LOT'),'1.250 units');
 assert.equal(units(1234.567),'1,234.567');assert.equal(count(12000),'12,000');
 for(const [locale,currency,symbol] of [['en-US','USD','$'],['en-GB','GBP','£'],['en-GB','EUR','€']]){
  setMoneySettings({locale,defaultCurrency:currency});
  assert.equal(money(1234.5,currency),`${symbol}1,234.50`);
  assert.equal(compactMoney(45000,currency),`${symbol}45${locale==='en-GB'?'k':'K'}`);
  assert.ok(axisMoney(50000,150000,currency).endsWith(locale==='en-GB'?'k':'K'));
  assert.ok(axisMoney(150000,150000,currency).endsWith(locale==='en-GB'?'k':'K'));
 }
 setMoneySettings({locale:'en-IN',defaultCurrency:'INR'});
 assert.equal(money(124500),'₹1,24,500.00');
 assert.deepEqual([0,50000,100000,150000].map(v=>axisMoney(v,150000)),['₹0','₹0.5L','₹1L','₹1.5L']);
 assert.equal(compactMoney(140000),'₹1.4L');
 setMoneySettings({defaultCurrency:'USD'});
 assert.equal(money(1234.5),new Intl.NumberFormat(undefined,{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:2}).format(1234.5));
});

test('Sam spends in GBP before July, both in July and EUR afterward with separate aggregates', () => {
 const map = month => Object.fromEntries(sam.dashboard.months.filter(row=>row.month===month).map(row=>[row.currency,row.total]));
 assert.deepEqual(map('2026-06'),{GBP:1846.2});
 assert.deepEqual(map('2026-07'),{EUR:1268.6,GBP:892.4});
 assert.deepEqual(map('2026-08'),{EUR:2034.8});
 for(const row of sam.dashboard.months){
  assert.equal(row.total,Number(row.categories.reduce((s,c)=>s+c.amount,0).toFixed(2)));
  assert.equal(row.total,Number(row.daily.reduce((s,c)=>s+c.amount,0).toFixed(2)));
  assert.equal(row.total,Number(sam.dashboard.transactions.filter(t=>t.month===row.month&&t.currency===row.currency).reduce((s,t)=>s+t.signedExpense,0).toFixed(2)));
 }
 // Every monetary aggregate is either a currency map or lies under an explicit
 // single-currency month/forecast/group. No unscoped mixed scalar is published.
 const walk=(value,currency)=>{
  if(!value||typeof value!=='object')return;
  if(Array.isArray(value)){value.forEach(item=>walk(item,currency));return;}
  const own=value.currency||currency;
  for(const [key,item]of Object.entries(value)){
   if(['total','income','investments','recurring','committed','purchases','received','outstanding','average','median','min','max'].includes(key)&&typeof item==='number')assert.ok(own,`unscoped ${key}`);
   walk(item,own);
  }
 };
 for(const month of sam.dashboard.months) {
  if(month.currency)walk(month);
  else {assert.equal(month.month,sam.dashboard.meta.defaultMonth);assert.equal(month.total,0);assert.equal(month.count,0);assert.deepEqual(month.daily,[]);}
 }
 for(const forecast of sam.dashboard.forecasts)walk(forecast);
 for(const group of sam.dashboard.reimbursementGroups)walk(group);
 assert.deepEqual(sam.review.meta.pendingNetAmount,{});
 const transfer=sam.dashboard.transactions.find(row=>row.description==='Move money to Germany');
 assert.equal(transfer.amount,1000);assert.equal(transfer.currency,'GBP');assert.equal(transfer.signedExpense,0);
 assert.deepEqual(transfer.postings.map(p=>[p.amount,p.currency]),[[1160,'EUR'],[-1000,'GBP']]);
 const balances=balanceAccounts(sam.dashboard.transactions,'2026-10-31',sam.dashboard.meta.accounts);
 assert.deepEqual(sumCurrencies(balances,row=>row.balance),{GBP:3284.65,EUR:6558.8});
 assert.equal(balances.find(row=>row.account.endsWith('Bramble Card')).balance,0);
});

test('Jordan keeps the original EUR dinner and exactly the USD card cost, with an opening-only EUR account', () => {
 const dinner=jordan.dashboard.transactions.find(row=>row.description==='Juniper Table dinner');
 assert.equal(dinner.currency,'USD');assert.equal(dinner.amount,51.48);assert.equal(dinner.signedExpense,51.48);
 assert.deepEqual(dinner.postings.map(p=>[p.amount,p.currency,p.cost,p.costCurrency]),[[46.8,'EUR',51.48,'USD'],[-51.48,'USD',undefined,undefined]]);
 const travel=jordan.dashboard.transactions.filter(row=>row.postings.some(p=>p.account.endsWith('Willow Travel')));
 assert.equal(travel.length,1);assert.equal(travel[0].displayType,'Balance adjustment');assert.equal(travel[0].currency,'EUR');
});

test('Explorer, Accounts, Journal, Atlas and Forecast render Sam without converting currencies', async () => {
 (await server.ssrLoadModule('/app/data.ts')).setData(sam);
 const {Explorer,Journal,Atlas}=await server.ssrLoadModule('/app/dashboard.tsx');
 const {default:Accounts}=await server.ssrLoadModule('/app/account-summary.tsx');
 const {default:Forecast}=await server.ssrLoadModule('/app/forecast.tsx');
 const render=(component,props={})=>renderToStaticMarkup(createElement(component,props));
 const html=render(Explorer,{month:'2026-07',setMonth:()=>{}});
 assert.match(html,/€1,268.60 \+ £892.40/);
 assert.match(html,/Amounts in EUR/);assert.match(html,/Amounts in GBP/);assert.doesNotMatch(html,/2,161.00/);
 const accounts=render(Accounts);
 assert.match(accounts,/€6,558.80 \+ £3,284.65/);assert.match(accounts,/£3,284.65/);assert.match(accounts,/€286.40/);
 const journal=render(Journal,{month:'2026-07',setMonth:()=>{}});
 assert.equal((journal.match(/class="sparkline"/g)||[]).length,2);
 const forecast=render(Forecast);
 assert.equal((forecast.match(/Cumulative expenses by day for/g)||[]).length,2);
 assert.match(forecast,/Forecast · EUR/);assert.match(forecast,/Forecast · GBP/);
 const atlas=render(Atlas,{month:'2026-07',setMonth:()=>{}});
 assert.match(atlas,/Atlas · EUR/);assert.match(atlas,/Atlas · GBP/);
 (await server.ssrLoadModule('/app/data.ts')).setData(jordan);
 const dollarMonth=render(Explorer,{month:'2026-10',setMonth:()=>{}});
 assert.match(dollarMonth,/Amounts in USD/);assert.doesNotMatch(dollarMonth,/Amounts in EUR/);
 assert.doesNotMatch(dollarMonth.split('class="metrics"')[1].split('class="explorer-body')[0],/€0.00/);
 assert.equal((render(Forecast).match(/Cumulative expenses by day for/g)||[]).length,1);

});

test('investment price quotes and cost bases retain currencies independently', () => {
 const prices=parsePrices('P 2026-01-01 FUND 10.0001 USD\nP 2026-01-02 FUND 11.5 USD\nP 2026-01-01 FUND 8.5 EUR\n');
 assert.equal(prices.length,2);
 assert.equal(priceOn(prices.find(p=>p.currency==='USD'),'2026-01-02').currency,'USD');
 const entries=[{id:'buy',date:'2026-01-01',sequence:0,description:'Units',postings:[{account:'Holdings:Units',currency:'FUND',amount:2,cost:20,costCurrency:'USD'}]},
 {id:'buy2',date:'2026-01-02',sequence:1,description:'Units',postings:[{account:'Holdings:Units',currency:'FUND',amount:2,cost:18,costCurrency:'EUR'}]},
 {id:'sell',date:'2026-01-03',sequence:2,description:'Units',postings:[{account:'Holdings:Units',currency:'FUND',amount:-2,cost:30,costCurrency:'USD'}]}];
 const previous=currentAccounts();
 try {
  setCurrentAccounts({'Holdings:Units':{kind:'investment',currency:'FUND'}});
  const result=accountActivity(entries,'Holdings:Units','FUND','2026-01-03').at(-1);
  assert.deepEqual(result.costs,{USD:10,EUR:9});assert.equal(result.balance,2);
  assert.equal(result.costCurrency,'USD');assert.equal(result.cost,10);
 } finally {setCurrentAccounts(previous);}
});

test('zero-unit postings preserve holdings, cost basis and valuation even before the first purchase', () => {
 const account='Holdings:Units';
 const zero={id:'check',date:'2026-01-02',sequence:1,description:'Holdings check',postings:[{account,currency:'FUND',amount:0}]};
 const buy={id:'buy',date:'2026-01-01',sequence:0,description:'Units',postings:[{account,currency:'FUND',amount:2,cost:20,costCurrency:'USD'}]};
 const previous=currentAccounts();
 try {
  setCurrentAccounts({[account]:{kind:'investment',currency:'USD'}});
  const activity=entries=>accountActivity(entries,account,'FUND','2026-01-03');
  const only=activity([zero]).at(-1);
  assert.deepEqual(only.costs,{});assert.deepEqual(only.costChanges,{});
  assert.equal(only.cost,0);assert.equal(only.balance,0);assert.equal(only.costChange,0);
  const result=activity([buy,zero]).at(-1);
  assert.deepEqual(result.costs,{USD:20});assert.equal(result.cost,20);
  assert.equal(result.balance,2);assert.equal(result.change,0);assert.equal(result.costChange,0);
  const prices={commodity:'FUND',currency:'USD',dates:['2026-01-01','2026-01-03'],navs:[10,12]};
  const values=entries=>valueSeries([{activity:activity(entries),prices}],'2026-01-01','2026-01-03');
  assert.deepEqual(values([buy,zero]),values([buy]));
  assert.deepEqual(values([zero]).map(point=>[point.value,point.cost]),[[0,0],[0,0]]);
 } finally {setCurrentAccounts(previous);}
});

test('USD income, investment funding, reimbursement and EUR forecasts stay in their own units', async t => {
 const {disposableDashboard,temporaryDirectory}=await import('./test-isolation.mjs');
 const {mkdir,writeFile,readFile,rm}=await import('node:fs/promises');
 const {join}=await import('node:path');const {execFileSync}=await import('node:child_process');
 const work=await temporaryDirectory('currency-builder-');t.after(()=>rm(work,{recursive:true,force:true}));
 const copy=await disposableDashboard(join(work,'copy')),root=join(work,'ledger');
 await mkdir(join(root,'data/expenses'),{recursive:true});await mkdir(join(root,'data/prices'));
 const write=(file,data)=>writeFile(join(root,file),JSON.stringify(data));
 const accounts={Pocket:{kind:'bank',currency:'USD'},Travel:{kind:'bank',currency:'EUR'},Units:{kind:'investment',currency:'USD'},Friend:{kind:'person',currency:'USD'},'Equity:Opening Balances':{},'Expenses:Food':{},'Income:Salary':{}};
 await write('ledger.json',{format:CURRENT_FORMAT,id:'00000000-0000-4000-8000-000000000005',name:'Currency fixture',defaultCurrency:'USD',locale:'en-US',modules:{investments:{prices:'mutual-fund-nav'}}});
 await write('data/accounts.json',{commodities:['USD','EUR','FUND'],accounts});
 const p=(account,amount,commodity,cost)=>({account,amount:String(amount),commodity,...(cost?{cost}: {})});
 let id=0;const entry=(date,description,postings)=>({id:(++id).toString(16).padStart(16,'0'),date,status:'confirmed',description,postings});
 await write('data/expenses/2026.json',[
  entry('2026-10-01','Open dollars',[p('Pocket',1000,'USD'),p('Equity:Opening Balances',-1000,'USD')]),
  entry('2026-10-01','Open euros',[p('Travel',2000,'EUR'),p('Equity:Opening Balances',-2000,'EUR')]),
  entry('2026-10-10','Salary',[p('Pocket',200,'USD'),p('Income:Salary',-200,'USD')]),
  entry('2026-10-11','Fund',[p('Units',2,'FUND',{amount:'20',commodity:'USD',per:'total'}),p('Pocket',-20,'USD')]),
  {...entry('2026-10-11','Holdings check',[p('Units',0,'FUND')]),postings:[{...p('Units',0,'FUND'),assert:{amount:'2.000',commodity:'FUND'}}]},
  entry('2026-10-11','Zero units',[p('Units',0,'FUND')]),
  {...entry('2026-10-12','Shared purchase',[p('Friend',30,'USD'),p('Pocket',-30,'USD')]),'reimbursement-group':'shared'},
  {...entry('2026-10-13','Paid back',[p('Friend',-10,'USD'),p('Pocket',10,'USD')]),'reimbursement-group':'shared'},
  entry('2026-10-14','Meal',[p('Expenses:Food',40,'EUR'),p('Travel',-40,'EUR')]),
  entry('2026-10-31','Refund',[p('Expenses:Food',-6,'USD'),p('Pocket',6,'USD')]),
 ]);
 await write('data/forecast.json',[
  {...entry('2026-11-01','Dollar commitment',[p('Expenses:Food',10,'USD'),p('Pocket',-10,'USD')]),schedule:{every:'month',from:'2026-11-01'}},
  {...entry('2026-11-01','Euro commitment',[p('Expenses:Food',12,'EUR'),p('Travel',-12,'EUR')]),schedule:{every:'month',from:'2026-11-01'}},
 ]);
 await writeFile(join(root,'data/prices/mutual-funds.prices'),'P 2026-10-01 FUND 8 EUR\n');
 const env={...copy.env,LEDGER_DIR:root};
 for(const name of ['build-dashboard-data','build-review-data'])execFileSync(process.execPath,[join(copy.dash,'scripts',name+'.mjs')],{env,stdio:'pipe'});
 const data=JSON.parse(await readFile(join(copy.dash,'app/data/dashboard.json')));
 assert.ok(!data.transactions.some(row=>row.description==='Holdings check'));
 assert.ok(data.transactions.some(row=>row.description==='Zero units'));
 const usd=data.months.find(m=>m.currency==='USD'),eur=data.months.find(m=>m.currency==='EUR');
 assert.equal(usd.total,-6);assert.equal(usd.income,200);assert.equal(usd.investments,20);assert.equal(eur.total,40);
 assert.deepEqual(data.forecasts.map(f=>[f.currency,f.months[0].committed]),[['EUR',12],['USD',10]]);
 assert.equal(data.forecast.currency,'USD');assert.equal(data.forecast.months[0].committed,10);
 assert.deepEqual(data.reimbursementGroups.map(g=>[g.currency,g.purchases,g.received,g.outstanding]),[['USD',30,10,20]]);
 (await server.ssrLoadModule('/app/data.ts')).setData({dashboard:data,review:{meta:{},transactions:[]},fuel:{meta:{},entries:[]}});
 // The fund is worth EUR 16; its USD 20 basis is not subtracted from that value.
 const {default:Accounts}=await server.ssrLoadModule('/app/account-summary.tsx');
 const html=renderToStaticMarkup(createElement(Accounts));
 assert.match(html,/€16.00/);assert.doesNotMatch(html,/−€4.00|−\$4.00/);
 assert.doesNotMatch(html,/NaN|Infinity/);
});
