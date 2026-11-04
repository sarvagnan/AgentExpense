import {bank, card, wallet, metadata} from './metadata-fixture.mjs';
import './test-isolation.mjs';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createTestServer as createServer } from './test-isolation.mjs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { buildPatterns, patternOf } from '../scripts/add-patterns.mjs';
import { loadExpenseStore } from '../scripts/expense-store.mjs';
import { gzipSync } from 'node:zlib';
import { buildSentence, draftPieces, PATTERN_DEFS, payLabel } from '../app/live/builder.ts';
const PAYS = {savings:bank, card, cash:'Pocket', wallet};
const data={modules:{fuel:true},patterns:{fuel:{count:19,usualPay:PAYS.savings,usualShare:15,usualExpense:'Expenses:Transport:Fuel',last:[]}},people:['Casey'],accounts:Object.keys(metadata),payments:metadata};
const build=(text,edits={},files=false)=>buildSentence(text,data,edits,files,'2026-10-30');
test('runtime data decoding survives a retired document blob origin',async()=>{
 const server=await createServer({optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
 const originalFetch=globalThis.fetch,originalStream=Blob.prototype.stream;
 try{
  const {fetchGzipJson}=await server.ssrLoadModule('/app/data.ts');
  const expected={description:'Fictional data',amount:'51.48'};
  Blob.prototype.stream=()=>{throw new Error('The previous document can no longer load its blob URL');};
  for(const bytes of [Buffer.from(JSON.stringify(expected)),gzipSync(JSON.stringify(expected))]){
   globalThis.fetch=async()=>new Response(bytes);
   assert.deepEqual(await fetchGzipJson('dashboard'),expected);
  }
 }finally{globalThis.fetch=originalFetch;Blob.prototype.stream=originalStream;await server.close();}
});
test('prototype examples identify all seven familiar patterns and something new',()=>{
 for(const [text,pattern] of [['petrol 875.25 8.5l 190km','fuel'],['cab 96 to office','ride'],['paper lantern renewed $5','sub'],['casey paid back 430 coffee','payback'],['bike chain lube 450 fablemart card','new'],['dinner 600 moss','dining'],['card bill 2300','cardbill'],['cashback 798','rewards']]) assert.equal(build(text).pattern,pattern,text);
 assert.equal(PATTERN_DEFS.length,7); assert.equal(build('').empty,true); assert.equal(build('',{},true).empty,false);
});
test('fuel quantities, history, edits and source provenance survive structured serialization',()=>{
 const b=build('petrol 875.25 8.5l 190km');
 assert.equal(b.fields.amount.value,'875.25');assert.equal(b.fields.litres.value,'8.5');assert.equal(b.fields.km.value,'190');assert.equal(b.fields.pay.source,'missing');assert.equal(b.fields.pay.value,'');
 assert.equal(b.fields.outlet.source,'missing');assert.equal(build('petrol',{},true).fields.outlet.source,'agent');assert.equal(b.fields.date.value,'2026-10-30');
 const edited=build('petrol 875.25 8.5l 190km',{outlet:'Fable Fuels',pay:PAYS.cash});
 assert.equal(edited.fields.outlet.source,'typed');assert.equal(edited.fields.pay.source,'typed');assert.equal(draftPieces(edited).fields.outlet.value,'Fable Fuels');assert.ok(!('shown' in draftPieces(edited).fields.outlet));
 for(const key of Object.keys(b.fields)) assert.equal(build('petrol',{[key]:'manual'}).fields[key].source,'typed',key);
 assert.equal(build('petrol',{outlet:''},true).fields.outlet.source,'agent');
});
test('Add shows the inherited display category while retaining the suggested posting account',()=>{
 const account = 'Expenses:Market:Dining:Corner Store';
 const accounts = {...metadata, 'Expenses:Market':{category:'Groceries'}, 'Expenses:Market:Dining':{category:'Meals'}, [account]:{}};
 const patterns = buildPatterns([{date:'2026-01-02', description:'Corner Store meal', postings:[{account,amount:20},{account:bank,amount:-20}]}], accounts);
 const built = buildSentence('Corner Store dinner 20', patterns, {}, false, '2026-01-02');
 assert.equal(patterns.patterns.dining.usualCategory, 'Meals');
 assert.equal(built.fields.category.shown, 'Meals');
 assert.equal(built.fields.category.value, account);
 assert.equal(draftPieces(built).fields.category.value, account);
 assert.equal(buildSentence('Corner Store dinner 20', patterns, {category:'manual'}, false, '2026-01-02').fields.category.shown, 'manual');
});
test('dates, payment hints, merchant text and word boundaries handle edge cases',()=>{
 for(const text of ['petrol ₹875.25 8.5 litres 251 km','petrol rs. 875.25 8.5ltr 190km']) assert.equal(build(text).fields.amount.value,'875.25');
 assert.equal(build('petrol 27/10/2026 200').fields.date.value,'2026-10-27');assert.equal(build('petrol 200 yesterday').fields.date.value,'2026-10-29');assert.equal(build('petrol 200 today').fields.date.value,'2026-10-30');
 assert.equal(build('petrol 2026-09-03 200').fields.amount.value,'200');assert.equal(build('petrol 5 November 200').fields.date.value,'2026-11-05');assert.equal(build('petrol 2026-02-30 200').fields.date.value,'2026-10-30');
 for(const [hint,pay] of [['card',PAYS.card],['checking',bank],['wallet',wallet],['Lunch balance',wallet],['Travel card',card],['cash',PAYS.cash]]){ const sentence=build(`petrol 300 ${hint}`);assert.equal(sentence.pattern,'fuel',hint);assert.equal(sentence.fields.pay.value,pay,hint); }
 assert.equal(build('chocolate 40').pattern,'new');assert.equal(build('fuel 100 at Fable Fuels').fields.outlet.value,'fable fuels');
 assert.equal(build('cab 96 to office').fields.route.value,'to office');assert.equal(build('Liora paid me back 430 coffee').fields.person.value,'liora');
 assert.equal(build('paper lantern renewed $5').fields.currency.value,'USD');assert.equal(build('lube ₹450 Fablemart card').fields.what.value,'lube fablemart');
});
test('pattern statistics use the selected store and are deterministic for fixtures',async()=>{
 const tx=(id,date,amount,pay=bank)=>({id,date,description:'Petrol',postings:[{account:'Expenses:Transport:Fuel',amount},{account:pay,amount:-amount}]});
 const items=[tx('a','2026-01-02',20),tx('b','2026-01-03',25),tx('c','2026-01-04',30),tx('d','2026-01-05',35,'Pocket')];
 const b=buildPatterns(items,metadata,{modules:{fuel:true}});assert.equal(b.patterns.fuel.count,4);assert.equal(b.patterns.fuel.usualPay,bank);assert.equal(b.patterns.fuel.usualShare,3);assert.deepEqual(b.patterns.fuel.last.map(x=>x.amount),['35','30','25']);assert.equal(b.patterns.fuel.usualExpense,'Expenses:Transport:Fuel');assert.deepEqual(buildPatterns([]),{patterns:{},people:[],accounts:[],payments:{},merchants:{},rules:[]});
 const {transactions,accounts}=await loadExpenseStore();const actual=buildPatterns(transactions,accounts,{modules:{fuel:true}});for(const [id,st]of Object.entries(actual.patterns))assert.equal(st.count,transactions.filter(x=>patternOf(x,accounts)===id).length);
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
  let finishRead,readSignal;
  globalThis.fetch=async(_url,init)=>{assert.equal(init.keepalive,true);readSignal=init.signal;return new Promise(resolve=>{finishRead=()=>resolve(new Response('{}'));});};
  const cancel=new AbortController();const pending=request('health',{signal:cancel.signal});cancel.abort();await assert.rejects(pending,e=>e.name==='AbortError');assert.equal(readSignal.aborted,false,'Read cancellation must not abort WebKit transport during departure');finishRead();await new Promise(resolve=>setImmediate(resolve));
  let writeSignal;globalThis.fetch=async(_url,init)=>new Promise((_,reject)=>{assert.equal(init.keepalive,true);writeSignal=init.signal;init.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')));});
  const stopWrite=new AbortController();const writing=request('drafts',{method:'POST',body:'{}',signal:stopWrite.signal});stopWrite.abort();await assert.rejects(writing,e=>e.name==='AbortError');assert.equal(writeSignal.aborted,true,'Writes retain transport cancellation');
  const m=await server.ssrLoadModule('/app/data.ts');let notified=0;const off=m.subscribeData(()=>notified++);let calls=0;
  globalThis.fetch=async(url,init)=>{calls++;assert.equal(init.cache,'no-store');return new Response(JSON.stringify({tag:'new',url,meta:{ledger:{modules:{fuel:true}}}}));};
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
 assert.match(render({state:'question',questions:[{question:'Which account did you pay from?',answer:null}]}),/<select/);
 const consent=render({state:'question',account_question:{proposal_hash:'a'.repeat(64),accounts:['Shared:Accounts Receivable:Finch'],postings:[{account:'Shared:Accounts Receivable:Finch',amount:'46.80',commodity:'EUR',cost:{amount:'51.48',commodity:'USD'}}]}});assert.match(consent,/total cost/);assert.match(consent,/51.48/);
 const categoryQuestion=render({state:'question',questions:[{question:'Which expense account should I use for this payment?',answer:null}]});assert.match(categoryQuestion,/<textarea/);assert.doesNotMatch(categoryQuestion,/<select/);
 assert.match(render({state:'failed',error:'Upload unreadable'}),/Upload unreadable/);assert.match(render({state:'failed'}),/Retry/);assert.match(render({state:'failed'}),/Discard/);
 assert.equal(needsAttention({...base,state:'failed'}),true);assert.equal(needsAttention({...base,state:'question'}),true);assert.equal(needsAttention(base),false);assert.equal(pollDelay([base]),3000);assert.equal(pollDelay([]),30000);
 assert.equal(pollDelay([{...base,state:'added',result:{audit_id:'receipt'}}]),30000);
 assert.equal(pollDelay([{...base,state:'added',result:{audit_id:'receipt',can_undo:false,undo_until:null}}]),30000);
 const input=[{...base,id:'old',created_at:'2026-01-01'},{...base,id:'new',created_at:'2026-10-30'}];assert.equal(newestFirst(input)[0].id,'new');assert.equal(input[0].id,'old');
 }finally{await server.close();}
});

test('metadata labels, history names and ambiguity determine composer suggestions', () => {
 assert.equal(payLabel(bank, metadata), 'Day account');
 assert.equal(build('cashback 100 Travel card').pattern, 'rewards');
 assert.equal(build('lube 100 Travel card').pattern, 'new');
 const payments = {...metadata, 'Other:Plastic':{kind:'card', label:'Second card', aliases:['card']}, 'Other:Bank':{kind:'bank', label:'Second bank'}};
 for (const text of ['petrol 100 card','petrol 100 bank transfer']) {
   const result = buildSentence(text,{...data,payments});
   assert.equal(result.fields.pay.value, ''); assert.equal(result.fields.pay.source, 'missing');
 }
 assert.equal(buildSentence('purchase 100 travel',{...data,payments}).fields.pay.value,card);
 assert.equal(build('fuel 100').fields.vehicle.source,'missing');
 assert.equal(build('Casey paid me back 100').fields.person.value,'Casey');
 const shared = {...data,payments:{...metadata,'Other:Checking':{kind:'bank',label:'Other',aliases:['checking']}}};
 assert.equal(buildSentence('fuel 100 checking',shared).fields.pay.value,'');
 assert.equal(buildSentence('purchase 100 日常',{...data,payments:{[bank]:{kind:'bank',label:'日常'}}}).fields.pay.value,bank);
 const history = buildPatterns([{description:'Fern Station', date:'2026-01-01',postings:[{account:'Expenses:Fuel',amount:100},{account:bank,amount:-100}]}],metadata,{modules:{fuel:true}});
 const result = buildSentence('Fern Station 100',{...history,modules:{fuel:true}});
 assert.equal(result.pattern,'fuel'); assert.equal(result.fields.outlet.value,'Fern Station');
 assert.equal(patternOf({description:'Rewards', postings:[{account:'Income:Cashback', amount:-10}, {account:card, amount:10}]},metadata), 'rewards');
});

test('merchant defaults use latest actual entry, forecast fallback and no pattern-wide account',()=>{
 const expense='Expenses:Subscriptions:Paper Lantern',other='Expenses:Subscriptions:Cloud Nest';
 const tx=(date,description,amount,pay,category=expense)=>({date,description,postings:[{account:category,amount},{account:pay,amount:-amount,currency:metadata[pay].currency}]});
 const actual=[tx('2026-01-01','Paper Lantern',10,bank),tx('2026-05-01','Paper Lantern',12,card)];
 const forecasts=[tx('2027-01-01','Paper Lantern',99,bank),tx('2026-06-01','Cloud Nest',8,bank,other)];
 const data=buildPatterns(actual,{...metadata,[expense]:{},[other]:{}},{},forecasts);
 const latest=buildSentence('Paper Lantern renewed',data,{},false,'2026-11-02');assert.equal(latest.fields.pay.value,card);assert.equal(latest.fields.amount.value,'12');assert.equal(latest.fields.currency.value,metadata[card].currency);assert.equal(latest.fields.category.value,expense);
 const fallback=buildSentence('Cloud Nest subscription',data,{},false,'2026-11-02');assert.equal(fallback.fields.pay.value,bank);assert.equal(fallback.fields.amount.value,'8');assert.equal(fallback.fields.category.value,other);
 const unknown=buildSentence('Unknown Workshop subscription',data,{},false,'2026-11-02');assert.equal(unknown.fields.pay.value,'');assert.equal(unknown.fields.category.value,'');
 const fuel=buildPatterns([tx('2026-01-01','Fable Fuels',20,bank,'Expenses:Transport:Fuel')],metadata,{modules:{fuel:true}});const unknownFuel=buildSentence('Fuel at Unknown Workshop', {...fuel,modules:{fuel:true}}, {}, false, '2026-11-02');assert.equal(unknownFuel.fields.pay.value,'');assert.equal(unknownFuel.fields.category.value,'');assert.equal(buildSentence('Fable Fuels', {...fuel,modules:{fuel:true}}, {}, false, '2026-11-02').fields.pay.value,bank);
 for(const phrase of ['payment from','repayment','paid me','my share'])assert.equal(buildSentence(`${phrase} Casey coffee 20`,data,{},false,'2026-11-02').pattern,'payback');
});
