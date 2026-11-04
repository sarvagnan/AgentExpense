import './test-isolation.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {writeFile,mkdir,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createTestServer,temporaryDirectory} from './test-isolation.mjs';
import {loadPublishedData} from './load-data.mjs';
import {expandForecast,toTransaction,loadExpenseStore} from '../scripts/expense-store.mjs';
import {varyingDetails,varyingAmountCheck,ruleStatus,matchRules} from '../scripts/recurring-ui.mjs';
import {buildPatterns} from '../scripts/add-patterns.mjs';
import {CURRENT_FORMAT} from '../scripts/ledger-manifest.mjs';
const accounts={'Assets:Purse':{kind:'cash',currency:'GBP',label:'Purse'},'Expenses:Utilities':{}};
const rule=()=>({id:'f000000000000001',date:'2026-11-12',status:'confirmed',description:'Willow Energy',amount:'varies',schedule:{every:'month',from:'2026-11-12'},postings:[{account:'Expenses:Utilities',amount:'48.00',commodity:'GBP'},{account:'Assets:Purse',amount:'-48.00',commodity:'GBP'}]});
const history=()=>['40.00','180.00','48.00'].map((amount,i)=>({...rule(),amount:undefined,schedule:undefined,id:String(i+1).padStart(16,'0'),date:`2026-${String(i+8).padStart(2,'0')}-12`,postings:rule().postings.map((p,index)=>({...p,amount:(index?'-':'')+amount}))}));
test('varying history includes outliers, honors links and excludes other payees and currencies',()=>{
 const r=rule(),actual=history(),unrelated={...actual[0],id:'0000000000000004',description:'Other household'};
 const details=varyingDetails(r,[...actual,unrelated],accounts);assert.deepEqual(details.history.map(e=>Number(e.amount)),[48,180,40]);assert.deepEqual([Number(details.min),Number(details.max)],[40,180]);
 const normalized=actual.map(e=>toTransaction(e,accounts));assert.deepEqual(varyingDetails(r,normalized,accounts),{...details,history:details.history.map(e=>({...e,amount:String(Number(e.amount))}))});
 const built=buildPatterns(normalized,accounts,{defaultCurrency:'EUR'},[],[r]);assert.deepEqual(built.rules[0].varying.history.map(e=>Number(e.amount)),[48,180,40]);
 assert.equal(varyingAmountCheck({...r,varying:details},360),null);assert.ok(varyingAmountCheck({...r,varying:details},360.01));assert.equal(varyingAmountCheck({...r,varying:details},20),null);assert.ok(varyingAmountCheck({...r,varying:details},19.99));
 const other={...actual[0],postings:actual[0].postings.map(p=>({...p,commodity:'EUR'}))};assert.equal(varyingDetails(r,[other],accounts).history.length,0);
});
test('forecast bill is exact once at pay-by and all following occurrences keep the estimate',()=>{
 const r=toTransaction(rule(),accounts);assert.deepEqual(expandForecast([r],'2026-11-01','2027-02-01').map(e=>[e.date,e.postings[0].amount]),[['2026-11-12',48],['2026-12-12',48],['2027-01-12',48]]);
 r.bill={amount:'77.00',received:'2026-11-01',pay_by:'2026-11-20',occurrence:r.date,sources:['Bills/2026-11-01/bill.txt']};
 const rows=expandForecast([r],'2026-11-01','2027-02-01');assert.deepEqual(rows.map(e=>[e.date,e.postings[0].amount,e.billOccurrence]),[['2026-11-20',77,true],['2026-12-12',48,false],['2027-01-12',48,false]]);assert.deepEqual(rows[0].sources,r.bill.sources);assert.equal(ruleStatus(r,'2026-11-02'),'bill-received');
 delete r.bill.pay_by;assert.equal(expandForecast([r],'2026-11-01','2026-12-01')[0].date,r.date);
 r.bill.pay_by='2026-12-20';assert.deepEqual(expandForecast([r],'2026-11-01','2027-01-01').map(e=>[e.date,e.postings[0].amount]),[['2026-12-12',48],['2026-12-20',77]]);
});
test('Node reader checks varying shape and closed bill dates/occurrence/currency',async t=>{
 const root=await temporaryDirectory('varying-reader-');t.after(()=>rm(root,{recursive:true,force:true}));await mkdir(join(root,'data/expenses'),{recursive:true});
 const write=(p,value)=>writeFile(join(root,p),JSON.stringify(value));
 await write('ledger.json',{format:CURRENT_FORMAT,id:'00000000-0000-4000-8000-000000000001',name:'Fictional bills',defaultCurrency:'GBP',modules:{investments:false}});await write('data/accounts.json',{commodities:['GBP','EUR'],accounts});await write('data/expenses/2026.json',history());
 const r=rule();await write('data/forecast.json',[r]);assert.equal((await loadExpenseStore(root)).rawRules[0].amount,'varies');
 for(const change of [{postings:[...r.postings,{account:'Expenses:Fees',amount:'1.00',commodity:'GBP'}]},{postings:r.postings.map((p,i)=>i?{...p,commodity:'EUR'}:p)},{amount:'sometimes'},{bill:{amount:'1.00',received:'2026-02-30',occurrence:r.date,sources:[]}},{bill:{amount:'1.00',received:'2026-11-01',occurrence:'2026-12-12',sources:[]}},{bill:{amount:'1.00',received:'2026-11-01',occurrence:r.date,sources:[],extra:'invalid'}}]){
  await write('data/forecast.json',[{...r,...change}]);await assert.rejects(loadExpenseStore(root));
 }
});
test('varying Add, Rules and editor render distinct estimate, unpaid choice, bill and chart states',async()=>{
 const server=await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
 try{
  const data=await loadPublishedData(server);Object.assign(data.dashboard.meta.ledger,{locale:'en-GB',defaultCurrency:'EUR'});data.dashboard.meta.accounts=accounts;(await server.ssrLoadModule('/app/data.ts')).setData(data);
  const r={...rule(),varying:varyingDetails(rule(),history(),accounts)},match=matchRules([r],'Willow Energy 49 GBP',accounts,'2026-11-02','EUR').matches[0];
  const {RuleGuessChoice}=await server.ssrLoadModule('/app/live/rule-guess.tsx'),{RuleRow}=await server.ssrLoadModule('/app/rules.tsx'),{PaymentBars}=await server.ssrLoadModule('/app/rule-editor.tsx');
  const render=(component,props)=>renderToStaticMarkup(createElement(component,props));
  const props={rule:r,match,text:'Willow Energy 49 GBP',initialDate:'2026-11-02',disabled:false,onAdd:()=>{},onDecline:()=>{}};
  const typed=render(RuleGuessChoice,props);assert.match(typed,/about £48.00/);assert.match(typed,/Have you paid it/);assert.match(typed,/Bill only. Not paid yet/);assert.doesNotMatch(typed,/checked=""|This payment only|Update the rule too/);
  const noAmount=render(RuleGuessChoice,{...props,text:'Willow Energy'});assert.match(noAmount,/≈ £48.00 estimate/);assert.match(noAmount,/Needs an amount/);
  const bill={...r,bill:{amount:'77.00',received:'2026-11-01',pay_by:'2026-11-20',occurrence:r.date,sources:[]}};
  const saved=render(RuleGuessChoice,{...props,rule:bill});assert.match(saved,/November 2026 bill/);assert.match(saved,/Add £77.00/);assert.doesNotMatch(saved,/Have you paid it|Undo/);
  const row=render(RuleRow,{rule:bill,readonly:false,busy:false,onEdit:()=>{},operate:async()=>true});assert.match(row,/Bill received/);assert.match(row,/varies · £40.00–£180.00 last 3/);
  const chart=render(PaymentBars,{rule:bill});assert.match(chart,/estimate-payment/);assert.equal((chart.match(/class="estimate-payment"/g)||[]).length,3);assert.match(chart,/Latest 3 highlighted/);
  const f=structuredClone(data.dashboard.forecast),month=f.currentMonth;f.currency='GBP';f.items=[{currency:'GBP',date:month+'-20',month,description:'Exact fictional bill',rule:'monthly',kind:'Expense',category:'Utilities',account:'Expenses:Utilities',amount:77.25,signedExpense:77.25,paymentLabel:'Purse',sources:[],varying:{bill:true,min:'40.50',max:'180.75'}},{currency:'GBP',date:month+'-25',month,description:'Varying fictional estimate',rule:'monthly',kind:'Expense',category:'Utilities',account:'Expenses:Utilities',amount:48.25,signedExpense:48.25,paymentLabel:'Purse',sources:[],varying:{bill:false,min:'40.50',max:'180.75'}}];data.dashboard.forecasts=[f];data.dashboard.forecast=f;(await server.ssrLoadModule('/app/data.ts')).setData(data);
  const Forecast=(await server.ssrLoadModule('/app/forecast.tsx')).default,forecast=render(Forecast,{online:false});assert.match(forecast,/£77.25/);assert.match(forecast,/≈ £48.25/);assert.match(forecast,/£40.50–£180.75/);

 }finally{await server.close();}
});

test('same-day estimate history uses ledger order and keeps explicitly linked pending payments',()=>{
 const r=rule(),rows=history().map(e=>({...e,date:'2026-10-12'}));rows.at(-1).status='pending';rows.at(-1).rule={id:r.id,occurrence:'2026-10-12'};
 assert.deepEqual(varyingDetails(r,rows,accounts).history.map(e=>e.id),rows.toReversed().map(e=>e.id));
});
