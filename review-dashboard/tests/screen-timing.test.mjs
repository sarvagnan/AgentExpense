import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {deferred,hookHarness} from './hook-harness.mjs';

// Review, rules, receipt and statement screens: each test holds the real hook's I/O open,
// acts, releases it late and checks that the newer state survives.
function reviewHarness(){
 const requests=[];
 const h=hookHarness('../app/review/api.ts',{'../live/api':{request:(path,options,timeout)=>{const hold=deferred();requests.push({path,options,timeout,...hold});return hold.promise;}}});
 h.render(m=>m.useReview());
 return {h,requests};
}
const snapshot=(count,ids)=>({count,important:0,items:ids.map(id=>({id,kind:'doubt',important:false,reason:'',entries:[]})),entries:{},export_entries:{},decisions:{},accounts:[],history:[]});

test('Review polls two seconds after each completed read, never while one is held',async()=>{
 const {h,requests}=reviewHarness();try{
  assert.equal(requests.length,1);assert.equal(requests[0].path,'review');assert.equal(requests[0].timeout,120000);
  await h.advance(5000);assert.equal(requests.length,1,'A held read schedules no second poll');
  requests[0].resolve(snapshot(2,['a','b']));await h.settle();assert.equal(h.current.data.count,2);
  await h.advance(1999);assert.equal(requests.length,1);await h.advance(1);assert.equal(requests.length,2);
  h.close();await h.advance(4000);assert.equal(requests.length,2,'Leaving stops the poll');assert.equal(requests[1].options.signal.aborted,true);
 }finally{h.close();}
});

test('A held Review poll cannot bring back a decided item or the old badge count after the action’s refresh',async()=>{
 const {h,requests}=reviewHarness();try{
  requests[0].resolve(snapshot(2,['a','b']));await h.settle();await h.advance(2000);assert.equal(requests.length,2);
  // The poll read the ledger before the decision committed; its answer arrives last.
  const after=h.current.refresh();requests[2].resolve(snapshot(1,['b']));assert.equal((await after).count,1);await h.settle();
  requests[1].resolve(snapshot(2,['a','b']));await h.settle();
  assert.equal(h.current.data.count,1,'The badge keeps the newer count');assert.deepEqual(h.current.data.items.map(i=>i.id),['b'],'The decided item stays gone');
  await h.advance(2000);assert.equal(requests.length,4,'Polling continues after the stale answer');
 }finally{h.close();}
});

test('A held Review poll that fails after a newer refresh shows no error',async()=>{
 const {h,requests}=reviewHarness();try{
  requests[0].resolve(snapshot(1,['a']));await h.settle();await h.advance(2000);
  const after=h.current.refresh();requests[2].resolve(snapshot(0,[]));await after;await h.settle();
  requests[1].reject(Error('The server took too long. Try again.'));await h.settle();
  assert.equal(h.current.error,'');assert.equal(h.current.data.count,0);
 }finally{h.close();}
});

test('An explicit Review refresh superseded by a newer poll returns the newest snapshot',async()=>{
 const {h,requests}=reviewHarness();try{
  requests[0].resolve(snapshot(2,['a','b']));await h.settle();
  const older=h.current.refresh();const newer=h.current.refresh();requests[2].resolve(snapshot(0,[]));await newer;
  requests[1].resolve(snapshot(2,['a','b']));assert.equal((await older).count,0);await h.settle();assert.equal(h.current.data.count,0);
 }finally{h.close();}
});

function ruleGuessHarness(instant){
 const requests=[],updates=[];
 const composer={form:{text:'Clover',declined_rules:['birch']},built:{fields:{}},draft:null,pending:[],interpretation:{busy:false,value:null},update(next){updates.push(next);composer.form=next;},save(){},setRuleDecisionNeeded(){}};
 const element=(type,props)=>({type,props});
 const rules=[{id:'birch',description:'Birch rent',postings:[]},{id:'music',description:'Clover music',postings:[]},{id:'lessons',description:'Clover lessons',postings:[]}];
 const h=hookHarness('../app/live/rule-guess.tsx',{
  '../data':{dashboard:{meta:{ledger:{locale:'en-US'},accounts:{}}}},'../date-time':{localDate:()=> '2026-11-02'},'../ui':{money:()=>''},'../rules':{},
  '../../scripts/recurring-ui.mjs':{matchRules:()=>instant,payer:()=>null,ruleAmounts:()=>({})},'./rules-api':{useRules:()=>({data:{rules},ready:true}),rulesApi:{match:(text,signal)=>{const hold=deferred();requests.push({text,signal,...hold});signal.addEventListener('abort',()=>hold.reject(new DOMException('cancelled','AbortError')));return hold.promise;}}},
  'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
 });
 const nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 const render=()=>h.render(m=>m.RuleGuess({composer,online:true}));
 return {h,requests,updates,composer,render,button:text=>nodes(h.current).find(n=>n.type==='button'&&n.props.children===text)};
}

test('Pick-list Not this stays dismissed when the held authoritative match arrives afterwards (ae-bvv)',async()=>{
 const pick={matches:[{id:'music',matched_words:['clover']},{id:'lessons',matched_words:['clover']}],pick_list:true,rule_id:null};
 for(const late of [pick,{...pick,pick_list:false,rule_id:'music'}]){
  const g=ruleGuessHarness(pick);try{
   g.render();await g.h.advance(180);assert.equal(g.requests.length,1,'The authoritative match is in flight');
   g.button('Not this').props.onClick();g.render();
   assert.deepEqual([...g.composer.form.declined_rules],['birch','music','lessons']);assert.equal(g.h.current,null);
   g.requests[0].resolve(late);await g.h.settle();g.render();assert.equal(g.h.current,null,'A late authoritative answer cannot show a dismissed rule');
  }finally{g.h.close();}
 }
});

function handHarness(){
 const calls=[],holds={},stored=new Map();
 const hold=name=>{const d=deferred();(holds[name]??=[]).push(d);return d.promise;};
 class ApiError extends Error{constructor(status,message,body){super(message);this.status=status;this.refusal=body?.detail;}}
 const api={claim:(id,token)=>{calls.push(['claim',id,token]);return hold('claim');},release:(id,token,form)=>{calls.push(['release',id,token,form?.entry?.date]);return hold('release');},
  handForm:(id,token,form)=>{calls.push(['form',id,token,form.entry.date]);return hold('form');},discard:id=>{calls.push(['discard',id]);return hold('discard');}};
 const element=(type,props)=>({type,props});
 const h=hookHarness('../app/live/hand-finish.tsx',{
  './balance-refusal':{BalanceNotice:'notice',balanceSource:()=>''},'../date-time':{formatCalendarDate:x=>x},'../data':{dashboard:{meta:{ledger:{id:'fixture',defaultCurrency:'USD'},accounts:{}}}},
  './api':{api,ApiError,apiUrl:path=>'/expenses/api/'+path,fileUrl:()=>'',upload:async()=>{}},'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
 },{sessionStorage:{getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)},fetch:async()=>({ok:true})});
 let done=0;const draft={id:'d'.repeat(32),files:[],state:'failed'};
 const nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 h.render(m=>m.default({draft,onDone:async()=>{done++;}}));
 const form={entry:{date:'2026-11-02',description:'Fictional lunch',postings:[],notes:[]},new_accounts:[],attachment_names:[]};
 return {h,calls,holds,stored,form,get done(){return done;},nodes:()=>nodes(h.current),button:text=>nodes(h.current).find(n=>n.type==='button'&&n.props.children===text)};
}

test('Leaving the hand form before its claim arrives gives the late claim back',async()=>{
 const g=handHarness();try{
  assert.deepEqual(g.calls.map(c=>c[0]),['claim']);
  g.button('Back to Queue').props.onClick();await g.h.settle();assert.equal(g.done,1);g.h.close();
  g.holds.claim[0].resolve({claim_token:'t'.repeat(64),form:g.form});await g.h.settle();
  assert.deepEqual(g.calls.at(-1),['release','d'.repeat(32),'t'.repeat(64),'2026-11-02'],'The claim is released, not held for 30 minutes');
  assert.equal(g.stored.size,0,'No claim token is kept for a form that was left');
 }finally{g.h.close();}
});

test('The hand form autosaves 500ms after a change and serializes a held save behind newer edits',async()=>{
 const g=handHarness();try{
  g.holds.claim[0].resolve({claim_token:'t'.repeat(64),form:g.form});await g.h.settle();
  const date=()=>g.nodes().find(n=>n.type==='input'&&n.props['aria-label']==='Date');
  await g.h.advance(500);assert.equal(g.calls.at(-1)[0],'form','The loaded form is saved once');g.holds.form[0].resolve({});await g.h.settle();g.calls.length=0;
  date().props.onChange({target:{value:'2026-11-03'}});g.h.render();await g.h.advance(499);assert.equal(g.calls.length,0);await g.h.advance(1);assert.deepEqual(g.calls.map(c=>c[3]),['2026-11-03']);
  date().props.onChange({target:{value:'2026-11-04'}});g.h.render();await g.h.advance(500);assert.equal(g.calls.length,1,'One save in flight at a time');
  g.holds.form[1].resolve({});await g.h.settle();assert.deepEqual(g.calls.map(c=>c[3]),['2026-11-03','2026-11-04'],'The newer edit is saved last');
 }finally{g.holds.form?.forEach(d=>d.resolve({}));g.h.close();}
});

test('Discard in the hand form’s refusal acts once while its release is held',async()=>{
 const g=handHarness();try{
  g.holds.claim[0].resolve({claim_token:'t'.repeat(64),form:g.form});await g.h.settle();
  const submit=g.nodes().find(n=>n.type==='form');submit.props.onSubmit({preventDefault(){}});await g.h.settle();
  g.holds.form.at(-1).resolve({ok:false,checks:[],new_accounts:[],proposal_hash:'x',refusal:{kind:'confirmed_balance'}});await g.h.settle();
  const discard=()=>g.nodes().find(n=>n.type==='button'&&n.props.children==='Discard');assert.ok(discard(),'The refusal offers Discard');
  discard().props.onClick();await g.h.settle();
  assert.equal(discard().props.disabled,true,'Discard is busy while it runs');assert.equal(g.button('Keep in Queue').props.disabled,true);
  discard().props.onClick();await g.h.settle();
  g.holds.release[0].resolve({});await g.h.settle();g.holds.discard?.[0]?.resolve();await g.h.settle();
  assert.equal(g.calls.filter(c=>c[0]==='release').length,1);assert.equal(g.calls.filter(c=>c[0]==='discard').length,1);assert.equal(g.done,1);
 }finally{g.h.close();}
});

function statementHarness(){
 const lists=[],applies=[],stored=new Map(),reloads={count:0,events:0};
 const row={serial:'S1',date:'2026-11-02',description:'Fern lunch',amount:'10.00',direction:'Debit',page:1,foreignAmount:null,foreignCurrency:null};
 const session=(state,updated)=>({id:'s'.repeat(32),state,created_at:'2026-11-05T10:00:00.000000+00:00',updated_at:updated,original_name:'fictional.pdf',client_time:'2026-11-05T10:00:00',
  statement:{account:'Liabilities:Card',accountLabel:'Fern Card',periodStart:'2026-10-01',periodEnd:'2026-10-31',totalDue:'10.00',previousBalance:'0',purchasesCharges:'10.00',paymentsCredits:'0',rows:[row],kind:'card'},checks:[],
  plan:{items:[{row,rule:'exact',why:'Exact match',entry_id:'e'.repeat(16),entry_description:'Fern lunch',postings:null,needs_user:false,decision:null,candidates:[]}],unmatched:[]}});
 const element=(type,props)=>({type,props});
 const statementsApi={list:signal=>{const d=deferred();lists.push({signal,...d});return d.promise;},apply:id=>{const d=deferred();applies.push({id,...d});return d.promise;},get:async()=>{throw Error('not used');}};
 const document=Object.assign(new EventTarget(),{hidden:false,querySelector:()=>null,querySelectorAll:()=>[],getElementById:()=>null});
 const h=hookHarness('../app/live/statement-detail.tsx',{
  '../date-time':{formatCalendarDate:x=>x,formatTimestamp:x=>x},'../ui':{Dialog:'dialog',Icon:'icon',PanelHeader:'header',announce(){},evidenceHref:()=>'',money:v=>String(v),useQueryState:(name,initial)=>[name==='session'?'s'.repeat(32):initial,()=>{}]},
  '../data':{dashboard:{meta:{accounts:{}}},reloadData:async()=>{reloads.count++;}},'./api':{api:{},ApiError:class extends Error{},isRemoved:()=>false},'./statement-upload':{StatementAccount:'statement-account'},
  './statements-api':{statementsApi,statementHref:()=>'',filedHref:()=>''},'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
 },{document,performance:{getEntriesByType:()=>[]},localStorage:{getItem:k=>stored.get(k)??null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)},requestAnimationFrame:()=>0,cancelAnimationFrame(){},innerHeight:900});
 const nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 h.render(m=>m.default({initialId:'s'.repeat(32)}));
 const list=s=>({sessions:[s],filed:[],accounts:[]});
 h.events.addEventListener('drafts-changed',()=>reloads.events++);
 return {h,lists,applies,session,list,reloads,button:text=>nodes(h.current).find(n=>n.type==='button'&&n.props.children===text)};
}

test('Statements poll every 1.5 seconds, skip while a read is held, and stop on pagehide',async()=>{
 const g=statementHarness();try{
  assert.equal(g.lists.length,1,'One read on open; the selection refresh joins it');
  await g.h.advance(3000);assert.equal(g.lists.length,1,'No second read while one is held');
  g.lists[0].resolve(g.list(g.session('review','2026-11-05T10:00:01.000000+00:00')));await g.h.settle();
  await g.h.advance(1499);assert.equal(g.lists.length,1);await g.h.advance(1);assert.equal(g.lists.length,2);
  g.h.events.dispatchEvent(new Event('pagehide'));assert.equal(g.lists[1].signal.aborted,true);g.lists[1].reject(new DOMException('cancelled','AbortError'));await g.h.settle();
  await g.h.advance(3000);assert.equal(g.lists.length,2,'Hidden pages do not poll');
  g.h.events.dispatchEvent(new Event('pageshow'));await g.h.settle();assert.equal(g.lists.length,3);g.lists[2].resolve(g.list(g.session('review','2026-11-05T10:00:01.000000+00:00')));await g.h.settle();
 }finally{g.h.close();}
});

test('A statement poll held across Apply cannot bring back the checklist or re-enable Apply',async()=>{
 const g=statementHarness();try{
  g.lists[0].resolve(g.list(g.session('review','2026-11-05T10:00:01.000000+00:00')));await g.h.settle();
  assert.equal(g.button('Review apply').props.disabled,false);
  await g.h.advance(1500);assert.equal(g.lists.length,2,'This poll read the session before Apply');
  g.button('Review apply').props.onClick();g.h.render();g.button('Apply statement').props.onClick();await g.h.settle();
  assert.equal(g.button('Apply statement').props.disabled,true,'One Apply at a time');
  g.applies[0].resolve(g.session('applying','2026-11-05T10:00:02.000000+00:00'));await g.h.settle();
  g.lists[1].resolve(g.list(g.session('review','2026-11-05T10:00:01.000000+00:00')));await g.h.settle();
  assert.equal(g.applies.length,1);assert.equal(g.button('Review apply')?.props.disabled??true,true,'The stale checklist cannot offer Apply again');
 }finally{g.h.close();}
});

test('An applied statement reloads the dashboard once, however many polls see it',async()=>{
 const g=statementHarness();try{
  g.lists[0].resolve(g.list(g.session('applying','2026-11-05T10:00:02.000000+00:00')));await g.h.settle();assert.equal(g.reloads.count,0);
  await g.h.advance(1500);g.lists[1].resolve(g.list({...g.session('applied','2026-11-05T10:00:03.000000+00:00'),result:{changed:1,converted:0,added:0}}));await g.h.settle();
  assert.equal(g.reloads.count,1);const events=g.reloads.events;
  await g.h.advance(1500);g.lists[2].resolve(g.list({...g.session('applied','2026-11-05T10:00:03.000000+00:00'),result:{changed:1,converted:0,added:0}}));await g.h.settle();
  assert.equal(g.reloads.count,1,'One data reload per applied statement');assert.equal(g.reloads.events,events,'Badges refresh once');
 }finally{g.h.close();}
});

test('Queue polls every 3 seconds while a statement is read, waits for the agent or applies, otherwise every 30',async()=>{
 const h=hookHarness('../app/live/use-drafts.ts',{'../data':{reloadData:async()=>{}},'./api':{api:{},request:async()=>({}),isRemoved:()=>false,rememberRemoved(){}},'./statements-api':{statementsApi:{}}});
 const {pollDelay}=h.exports;
 for(const state of ['reading','applying','agent_waiting'])assert.equal(pollDelay([],[{state}]),3000,state);
 for(const state of ['review','found','applied','failed'])assert.equal(pollDelay([],[{state}]),30000,state);
 assert.equal(pollDelay([{state:'added',receipt_reading:{state:'reading'}}],[]),3000,'A receipt being read after a save keeps the fast poll');
 assert.equal(pollDelay([{state:'added',receipt_reading:{state:'question'}}],[]),30000);
});

test('A held correction form that arrives after Back is not shown',async()=>{
 const held=deferred();let opened=0;const element=(type,props)=>({type,props});
 const h=hookHarness('../app/live/correction.tsx',{'../data':{reloadData:async()=>{}},'../ui':{dayLabel:x=>x},'./api':{api:{correctionForm:()=>{opened++;return held.promise;}}},'./hand-finish':{default:'hand-finish'},'./use-drafts':{draftsChanged(){}},'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'}});
 let back=0;h.render(m=>m.default({entryId:'e'.repeat(16),onBack:()=>{back++;}}));
 try{
  assert.equal(opened,1);const nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
  nodes(h.current).find(n=>n.type==='button').props.onClick();assert.equal(back,1);h.close();
  held.resolve({id:'c'.repeat(32),files:[],state:'question'});await h.settle();
  assert.equal(nodes(h.current).some(n=>n.type==='hand-finish'),false,'No form opens after leaving');
 }finally{h.close();}
});
