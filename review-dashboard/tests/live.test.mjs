import './test-isolation.mjs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createTestServer as createServer } from './test-isolation.mjs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { buildPatterns, patternOf } from '../scripts/add-patterns.mjs';
import { loadExpenseStore } from '../scripts/expense-store.mjs';
import { buildSentence, draftPieces, PATTERN_DEFS, PAYS, payLabel, todayInIndia } from '../app/live/builder.ts';
const data={patterns:{fuel:{count:19,usualPay:PAYS.savings,usualShare:15,usualExpense:'Expenses:Transport:Fuel',last:[]}},people:['Nila'],accounts:[]};
const build=(text,edits={},files=false)=>buildSentence(text,data,edits,files,'2026-10-30');
test('prototype examples identify all nine familiar patterns and something new',()=>{
 for(const [text,pattern] of [['petrol 875.25 8.5l 190km','fuel'],['zipcab 96 to office','ride'],['paper lantern renewed $5','sub'],['nila paid back 430 coffee','payback'],['bike chain lube 450 fablemart card','new'],['mealcart 320','mealcart'],['basketdemo 500','basketdemo'],['dinner 600 moss','dining'],['card bill 2300','cardbill'],['cashback 798','rewards']]) assert.equal(build(text).pattern,pattern,text);
 assert.equal(PATTERN_DEFS.length,9); assert.equal(build('').empty,true); assert.equal(build('',{},true).empty,false);
});
test('fuel quantities, history, edits and source provenance survive structured serialization',()=>{
 const b=build('petrol 875.25 8.5l 190km');
 assert.equal(b.fields.amount.value,'875.25');assert.equal(b.fields.litres.value,'8.5');assert.equal(b.fields.km.value,'190');assert.equal(b.fields.pay.value,PAYS.savings);
 assert.equal(b.fields.outlet.source,'missing');assert.equal(build('petrol',{},true).fields.outlet.source,'agent');assert.equal(b.fields.date.value,'2026-10-30');
 const edited=build('petrol 875.25 8.5l 190km',{outlet:'Fable Fuels',pay:PAYS.cash});
 assert.equal(edited.fields.outlet.source,'typed');assert.equal(edited.fields.pay.source,'typed');assert.equal(draftPieces(edited).fields.outlet.value,'Fable Fuels');assert.ok(!('shown' in draftPieces(edited).fields.outlet));
 for(const key of Object.keys(b.fields)) assert.equal(build('petrol',{[key]:'manual'}).fields[key].source,'typed',key);
 assert.equal(build('petrol',{outlet:''},true).fields.outlet.source,'agent');
});
test('dates, payment hints, merchant text and word boundaries handle edge cases',()=>{
 for(const text of ['petrol ₹875.25 8.5 litres 251 km','petrol rs. 875.25 8.5ltr 190km']) assert.equal(build(text).fields.amount.value,'875.25');
 assert.equal(build('petrol 27/10/2026 200').fields.date.value,'2026-10-27');assert.equal(build('petrol 200 yesterday').fields.date.value,'2026-10-29');assert.equal(build('petrol 200 today').fields.date.value,'2026-10-30');
 assert.equal(build('petrol 2026-09-03 200').fields.amount.value,'200');assert.equal(build('petrol 5 November 200').fields.date.value,'2026-11-05');assert.equal(build('petrol 2026-02-30 200').fields.date.value,'2026-10-30');
 for(const [hint,pay] of [['card',PAYS.card],['Savings Account UPI',PAYS.savings],['wallet',PAYS.wallet],['Shopping wallet',PAYS.wallet],['Rewards card',PAYS.card],['salary',PAYS.salary],['Salary account',PAYS.salary],['appstore',PAYS.appstore],['App store balance',PAYS.appstore],['savings',PAYS.savings],['cash',PAYS.cash]]){ const sentence=build(`petrol 300 ${hint}`);assert.equal(sentence.pattern,'fuel',hint);assert.equal(sentence.fields.pay.value,pay,hint); }
 assert.match(build('petrol 300 upi').fields.pay.value,/bank to confirm/);assert.equal(build('chocolate 40').pattern,'new');assert.equal(build('fuel 100 at Fable Fuels').fields.outlet.value,'Fable Fuels');
 assert.equal(build('zipcab 96 to office').fields.route.value,'to office');assert.equal(build('Liora paid me back 430 coffee').fields.person.value,'liora');
 assert.equal(build('paper lantern renewed $5').fields.currency.value,'USD');assert.equal(build('lube ₹450 Fablemart card').fields.what.value,'lube fablemart');
 assert.equal(todayInIndia(new Date('2026-10-29T19:00:00Z')),'2026-10-30');
});
test('pattern statistics use the selected store and are deterministic for fixtures',async()=>{
 const tx=(id,date,amount,pay='Assets:Bank:Savings Account')=>({id,date,description:'Petrol',postings:[{account:'Expenses:Transport:Fuel',amount},{account:pay,amount:-amount}]});
 const items=[tx('a','2026-01-02',20),tx('b','2026-01-03',25),tx('c','2026-01-04',30),tx('d','2026-01-05',35,'Assets:Cash')];
 const b=buildPatterns(items);assert.equal(b.patterns.fuel.count,4);assert.equal(b.patterns.fuel.usualPay,'Assets:Bank:Savings Account');assert.equal(b.patterns.fuel.usualShare,3);assert.deepEqual(b.patterns.fuel.last.map(x=>x.amount),['35','30','25']);assert.equal(b.patterns.fuel.usualExpense,'Expenses:Transport:Fuel');assert.deepEqual(buildPatterns([]),{patterns:{},people:[],accounts:[]});
 const {transactions}=await loadExpenseStore();const actual=buildPatterns(transactions);for(const [id,st]of Object.entries(actual.patterns))assert.equal(st.count,transactions.filter(x=>patternOf(x)===id).length);
});
test('API URLs, action errors, timeouts and atomic concurrent data reload',async()=>{
 const server=await createServer({optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});const original=globalThis.fetch;
 try{
  const {apiUrl,request,errorMessage}=await server.ssrLoadModule('/app/live/api.ts');
  assert.equal(apiUrl('drafts','/'),'/api/drafts');assert.equal(apiUrl('/health','/expenses/'),'/expenses/api/health');assert.equal(apiUrl('health','/expenses'),'/expenses/api/health');
  assert.equal(errorMessage(413,{detail:'Too large'}),'Too large');assert.match(errorMessage(500,null),/HTTP 500/);
  globalThis.fetch=async(url,init)=>{assert.equal(Object.keys(init.headers||{}).some(x=>/tailscale|identity/i.test(x)),false);return new Response(JSON.stringify({detail:'Cannot discard a working draft'}),{status:409});};
  await assert.rejects(request('drafts'),e=>e.status===409 && /Cannot discard/.test(e.message));
  globalThis.fetch=async()=>new Response('not JSON',{status:502});await assert.rejects(request('health'),/HTTP 502/);
  globalThis.fetch=async()=>{throw new TypeError('offline');};await assert.rejects(request('health'),/Could not reach/);
  globalThis.fetch=async(_url,init)=>new Promise((_,reject)=>init.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError'))));await assert.rejects(request('health',{},1),/took too long/);
  const cancel=new AbortController();const pending=request('health',{signal:cancel.signal});cancel.abort();await assert.rejects(pending,e=>e.name==='AbortError');
  const m=await server.ssrLoadModule('/app/data.ts');let notified=0;const off=m.subscribeData(()=>notified++);let calls=0;
  globalThis.fetch=async(url,init)=>{calls++;assert.equal(init.cache,'no-store');return new Response(JSON.stringify({tag:'new',url}));};
  const a=m.reloadData(),b=m.reloadData();assert.equal(a,b);await a;assert.equal(calls,3);assert.equal(notified,1);const prior=m.dashboard;
  globalThis.fetch=async()=>new Response('error',{status:500});await assert.rejects(m.reloadData(),/HTTP 500/);assert.equal(m.dashboard,prior);assert.equal(notified,1);off();
 }finally{globalThis.fetch=original;await server.close();}
});
test('each draft state renders its content and permitted actions',async()=>{
 const server=await createServer({optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
 try{
 (await server.ssrLoadModule('/app/data.ts')).setData({dashboard:{transactions:[{id:'abc',month:'2026-09'}]},review:{},fuel:{}});
 const {DraftDetail,draftStatus}=await server.ssrLoadModule('/app/live/drafts.tsx');const {needsAttention,pollDelay,newestFirst}=await server.ssrLoadModule('/app/live/use-drafts.ts');
 const base={id:'a',state:'working',text:'petrol 100',created_at:'2026-10-30T10:00:00Z',queued_at:'2026-10-30T10:00:00Z',files:[],runs:[{start:'2026-10-30T10:00:00Z',doubts:['No reference supplied']}],questions:[],result:null};
 const render=d=>renderToStaticMarkup(createElement(DraftDetail,{draft:{...base,...d},onAction:async()=>{}}));
 assert.match(render({}),/The agent is reading it/);assert.doesNotMatch(render({}),/Discard/);assert.match(render({state:'queued'}),/In the queue/);assert.match(render({waiting:'Waiting for a clean ledger'}),/Waiting for a clean ledger/);
 assert.match(render({state:'question',questions:[{question:'Which bank?',answer:null}]}),/Which bank\?/);assert.match(render({state:'question'}),/Send answer/);
 assert.match(render({state:'failed',error:'Upload unreadable'}),/Upload unreadable/);assert.match(render({state:'failed'}),/Retry/);assert.match(render({state:'failed'}),/Discard/);
 const result={outcome:'ready',description:'Fuel',entry_id:'abc',total:'100 INR',payment_account:'Assets:Cash',new_accounts:['Expenses:New'],sources:['Bills/2026-10-30/fuel.png']};
 const added=render({state:'added',result});assert.match(added,/month=2026-09/);for(const text of ['Fuel','100 INR','Assets:Cash','Expenses:New','Open in Explorer','fuel.png','No reference supplied'])assert.ok(added.includes(text),text);
 const not={state:'added',result:{outcome:'not_expense',explanation:'A card statement'}};assert.equal(draftStatus({...base,...not}),'Not an expense');assert.match(render(not),/A card statement/);
 assert.equal(needsAttention({...base,state:'failed'}),true);assert.equal(needsAttention({...base,state:'question'}),true);assert.equal(needsAttention(base),false);assert.equal(pollDelay([base]),3000);assert.equal(pollDelay([]),30000);
 const input=[{...base,id:'old',created_at:'2026-01-01'},{...base,id:'new',created_at:'2026-10-30'}];assert.equal(newestFirst(input)[0].id,'new');assert.equal(input[0].id,'old');
 }finally{await server.close();}
});

test('generic shortcuts have product labels and card rewards retain their pattern', () => {
 for (const [key,label] of [['card','Rewards card'],['savings','Savings account'],['salary','Salary account'],['wallet','Shopping wallet'],['appstore','App store balance']]) {
  assert.equal(build(`purchase 100 ${key}`).fields.pay.value, PAYS[key]);
  assert.equal(payLabel(PAYS[key]), label);
 }
 assert.equal(build('cashback 100 Rewards card').pattern,'rewards');
 assert.equal(build('lube 100 Rewards card').pattern,'new');
 assert.equal(patternOf({description:'Mealcart pantrydemo', postings:[]}), null);
 assert.equal(patternOf({description:'Rewards', postings:[{account:'Income:Card Rewards', amount:-10}, {account:PAYS.card, amount:10}]}), 'rewards');
});
