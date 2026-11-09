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
 const composer={form:{text:'Clover',declined_rules:['birch']},built:{fields:{}},draft:null,pending:[],interpretation:{busy:false,value:null},update(next){updates.push(next);composer.form=next;},save(){},setUsualCard(){}};
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

test('A one-word suggestion has no primary button, reports no card, and opens its card only when chosen (B-2 N1, N2)',async()=>{
 const suggestion={matches:[],suggestions:[{id:'music',description:'Clover music',matched_words:['clover']}],pick_list:false,rule_id:null};
 const g=ruleGuessHarness(suggestion),cards=[];g.composer.setUsualCard=value=>cards.push(value);
 const nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 try{
  g.render();await g.h.advance(180);g.requests[0].resolve(suggestion);await g.h.settle();g.render();
  const aside=g.h.current;assert.equal(aside.props.className,'rule-guess rule-suggestion');
  assert.equal(nodes(aside).some(n=>/\bprimary\b/.test(n.props?.className||'')),false,'A suggestion has no primary button');
  assert.equal(nodes(aside).some(n=>n.type?.name==='RuleGuessChoice'),false,'A suggestion never opens the card by itself');
  assert.ok(!cards.includes('fixed'),'No usual card is reported while only a suggestion shows');
  g.button('Show usual payment').props.onClick();g.render();await g.h.settle();
  assert.ok(nodes(g.h.current).some(n=>n.type?.name==='RuleGuessChoice'),'Choosing the suggestion opens its card');
  assert.equal(cards.at(-1),'fixed','The open fixed card is reported to the composer, which steps Continue back');
  g.composer.form={...g.composer.form,text:'Clover again'};g.render();await g.h.advance(180);g.requests.at(-1).resolve(suggestion);await g.h.settle();g.render();
  assert.equal(g.h.current.props.className,'rule-guess rule-suggestion','Changed words return to the quiet suggestion');assert.equal(cards.at(-1),'');
  g.button('Not this').props.onClick();g.render();assert.deepEqual([...g.composer.form.declined_rules],['birch','music']);assert.equal(g.h.current,null);
 }finally{g.h.close();}
});

function handHarness(directWrites={subscribeWrites:()=>()=>{},writeSnapshot:()=>[]},correction=false){
 const calls=[],holds={},stored=new Map();
 const hold=name=>{const d=deferred();(holds[name]??=[]).push(d);return d.promise;};
 class ApiError extends Error{constructor(status,message,body){super(message);this.status=status;this.refusal=body?.detail;}}
 const api={claim:(id,token)=>{calls.push(['claim',id,token]);return hold('claim');},release:(id,token,form)=>{calls.push(['release',id,token,form?.entry?.date]);return hold('release');},
  handForm:(id,token,form)=>{calls.push(['form',id,token,form.entry.date]);return hold('form');},discard:id=>{calls.push(['discard',id]);return hold('discard');}};
 const element=(type,props)=>({type,props});
 const h=hookHarness('../app/live/hand-finish.tsx',{
  './direct-writes':directWrites,
  './balance-refusal':{BalanceNotice:'notice',balanceSource:()=>''},'../date-time':{formatCalendarDate:x=>x},'../ui':{Icon:'icon',plural:(n,one)=>`${n} ${n===1?one:one+'s'}`},'../data':{dashboard:{meta:{ledger:{id:'fixture',defaultCurrency:'USD'},accounts:{}},transactions:[]}},'../account-labels':{accountFieldLabel:account=>account},'../extensions':{ExtensionFields:'extension-fields'},
  './api':{api,ApiError,apiUrl:path=>'/expenses/api/'+path,fileUrl:()=>'',upload:async()=>{}},'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
 },{sessionStorage:{getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)},fetch:async()=>({ok:true})});
 let done=0;const draft={id:'d'.repeat(32),files:[],state:'failed',...(correction?{correction:{entry_id:'entry',version:'original',sources:[],before:{description:'Saved entry',postings:[]}}}:{})};
 const nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 h.render(m=>m.default({draft,onDone:async()=>{done++;}}));
 const form={entry:{date:'2026-11-02',description:'Fictional lunch',postings:[],notes:[]},'new-accounts':[],'attachment-names':[]};
 return {h,api,calls,holds,stored,form,get done(){return done;},nodes:()=>nodes(h.current),button:text=>nodes(h.current).find(n=>n.type==='button'&&n.props.children===text)};
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
 const lists=[],applies=[],stored=new Map(),reloads={count:0,events:0},frames=[];let selectedSession='s'.repeat(32);
 const row={serial:'S1',date:'2026-11-02',description:'Fern lunch',amount:'10.00',direction:'Debit',page:1,foreignAmount:null,foreignCurrency:null};
 const session=(state,updated)=>({id:'s'.repeat(32),state,created_at:'2026-11-05T10:00:00.000000+00:00',updated_at:updated,original_name:'fictional.pdf',client_time:'2026-11-05T10:00:00',
  statement:{account:'Liabilities:Card',accountLabel:'Fern Card',periodStart:'2026-10-01',periodEnd:'2026-10-31',totalDue:'10.00',previousBalance:'0',purchasesCharges:'10.00',paymentsCredits:'0',rows:[row],kind:'card'},checks:[],
  plan:{items:[{row,rule:'exact',why:'Exact match',entry_id:'e'.repeat(16),entry_description:'Fern lunch',postings:null,needs_user:false,decision:null,candidates:[]}],unmatched:[]}});
 const element=(type,props)=>({type,props});
 const statementsApi={list:signal=>{const d=deferred();lists.push({signal,...d});return d.promise;},apply:id=>{const d=deferred();applies.push({id,...d});return d.promise;},discard:async()=>{throw Error('Fictional refused discard');},get:async()=>{throw Error('not used');}};
 const document=Object.assign(new EventTarget(),{hidden:false,querySelector:()=>null,querySelectorAll:()=>[],getElementById:()=>null});
 const h=hookHarness('../app/live/statement-detail.tsx',{
  '../date-time':{formatCalendarDate:x=>x,formatTimestamp:x=>x},'../ui':{Dialog:'dialog',Icon:'icon',PanelHeader:'header',announce(){},evidenceHref:()=>'',money:v=>String(v),plural:(n,one,many=one+'s')=>`${n} ${n===1?one:many}`,useQueryState:(name,initial)=>name==='session'?[selectedSession,value=>{selectedSession=value;}]:[initial,()=>{}]},
  '../data':{dashboard:{meta:{accounts:{}}},reloadData:async()=>{reloads.count++;}},'./api':{api:{},ApiError:class extends Error{},isRemoved:()=>false},'./statement-account':{StatementAccount:'statement-account'},
  './statements-api':{statementsApi,statementHref:()=>'',filedHref:()=>''},'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
  './context':{LiveDraftsContext:{value:null}},'./ledger-check':{LEDGER_CHECK_HREF:'/expenses/queue/?check=ledger',ledgerCheckBlock:()=>null},
 },{document,performance:{getEntriesByType:()=>[]},localStorage:{getItem:k=>stored.get(k)??null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)},requestAnimationFrame:fn=>{frames.push(fn);return frames.length;},cancelAnimationFrame(){},innerHeight:900});
 const nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 h.render(m=>m.default({initialId:'s'.repeat(32)}));
 const list=s=>({sessions:[s],filed:[],accounts:[]});
 h.events.addEventListener('drafts-changed',()=>reloads.events++);
 return {h,lists,applies,session,list,reloads,frames,document,nodes:()=>nodes(h.current),button:text=>nodes(h.current).find(n=>n.type==='button'&&n.props.children===text)};
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

test('A committed action cannot close its form after a newer focus, key, pointer action or unmount',()=>{
 const h=hookHarness('../app/live/interaction.ts');const guard=h.render(m=>m.useActionGuard());
 for(const event of ['onFocusCapture','onKeyDownCapture','onPointerDownCapture']){const ticket=guard.begin();assert.equal(guard.unchanged(ticket),true);guard.interaction[event]();assert.equal(guard.unchanged(ticket),false,event);}
 const ticket=guard.begin();h.close();assert.equal(guard.unchanged(ticket),false);
});


test('Leaving a hand form during a shelf retry keeps its claim and shows the shared pending state',async()=>{
 let pending=false,notify=()=>{};
 const g=handHarness({writeSnapshot:()=>pending?[{path:`drafts/${'d'.repeat(32)}/hand-add`,state:'saving'}]:[],subscribeWrites:fn=>{notify=fn;return()=>{};}});
 try{
  g.holds.claim[0].resolve({claim_token:'t'.repeat(64),form:g.form});await g.h.settle();pending=true;notify();await g.h.settle();
  assert.ok(g.button('Saving…'));assert.equal(g.button('Saving…').props.disabled,true);assert.equal(g.button('Back to Queue').props.disabled,false);
  g.button('Back to Queue').props.onClick();await g.h.settle();assert.equal(g.done,1);g.h.close();await g.h.settle();assert.equal(g.calls.some(call=>call[0]==='release'),false,'Navigation must not release a claim belonging to the queued retry');
 }finally{g.h.close();}
});

test('Rule editor passes its initiating interaction to the saved snapshot callback, even after Save loses focus',async()=>{
 for(const newer of [false,true]){
  const held=deferred();let revision=0,accepted,closed=0;
  const element=(type,props)=>({type,props}),nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
  const h=hookHarness('../app/rule-editor.tsx',{
   './live/interaction':{useInteractionSnapshot:value=>[value,{},()=>{}],useActionGuard:()=>({begin:()=>revision,unchanged:ticket=>ticket===revision,interaction:{}})},
   './data':{dashboard:{meta:{accounts:{'Assets:Bank:Willow':{kind:'bank',currency:'USD'},'Expenses:Classes':{}},ledger:{defaultCurrency:'USD'}}}},
   './date-time':{localDate:()=>'2026-11-01'},'./ui':{Dialog:'dialog',money:()=>'',dayLabel:x=>x,monthLong:x=>x,useDocumentScrollLock(){}},
   '../scripts/recurring-ui.mjs':{complexRule:()=>false,mainPostings:()=>[],payer:()=>null,varyingDetails:()=>null},'./account-labels':{accountFieldLabel:x=>x},
   './live/rules-api':{useRuleOperation:onSaved=>({busy:false,error:'',operate:async()=>{await held.promise;await onSaved();return true;}})},
   'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
  });
  try{
   h.render(m=>m.RuleEditor({onClose:()=>closed++,onSaved:unchanged=>{accepted=unchanged?.();}}));
   const change=(test,value)=>{nodes(h.current).find(test).props.onChange({target:{value}});h.render();};
   change(n=>n.type==='input'&&n.props.id==='rule-description','Fictional rule');change(n=>n.type==='input'&&n.props.inputMode==='decimal','12.50');change(n=>n.type==='select'&&n.props['aria-label']==='Paying account','Assets:Bank:Willow');change(n=>n.type==='select'&&n.props['aria-label']==='Category','Expenses:Classes');
   nodes(h.current).find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await h.settle();if(newer)revision++;held.resolve({});await h.settle();assert.equal(accepted,!newer);assert.equal(closed,newer?0:1);
  }finally{held.resolve({});h.close();}
 }
});

test('An inert disabled control cannot invalidate its own in-flight completion',()=>{
 const h=hookHarness('../app/live/interaction.ts');try{h.render(m=>m.useActionGuard());const ticket=h.current.begin();h.current.interaction.onPointerDownCapture({target:{closest:selector=>selector===':disabled'?{}:null}});assert.equal(h.current.unchanged(ticket),true);h.current.interaction.onPointerDownCapture({target:{closest:()=>null}});assert.equal(h.current.unchanged(ticket),false);}finally{h.close();}
});

test('Background bar or refresh-notice measurements never scroll an older focused input; focus and viewport changes reveal on a controlled frame',()=>{
 const frames=new Map(),scrolls=[];let sequence=0,mutated,resized;
 const nav={getBoundingClientRect:()=>({height:64,top:836})},bar={getBoundingClientRect:()=>({height:80,top:756})},notice={getBoundingClientRect:()=>({height:40,top:700})};let bars=[nav,bar];
 const h=hookHarness('../app/live/focus.ts',{}, {innerHeight:900,getComputedStyle:()=>({display:'block',position:'fixed'}),requestAnimationFrame:fn=>{frames.set(++sequence,fn);return sequence;},cancelAnimationFrame:id=>frames.delete(id),ResizeObserver:class{constructor(fn){resized=fn;}observe(){}unobserve(){}disconnect(){}},MutationObserver:class{constructor(fn){mutated=fn;}observe(){}disconnect(){}}});
 const flush=()=>{for(const [id,fn] of [...frames]){frames.delete(id);fn();}};
 Object.assign(h.document,{body:{},documentElement:{style:{setProperty(){},removeProperty(){}}},querySelector:selector=>selector==='.phone-live-nav'?nav:selector.startsWith('.add-savebar')?bar:null,querySelectorAll:()=>bars,activeElement:null});h.events.scrollBy=value=>scrolls.push(value);
 try{
  h.render(m=>m.useLiveFocus('add'));flush();h.document.activeElement={closest:selector=>selector==='.live-page'?{}:null,getBoundingClientRect:()=>({top:-53,bottom:47})};
  bars=[nav,bar,notice];mutated();resized();flush();assert.equal(scrolls.length,0,'A background publication must not reveal the old caret while the person is pointing elsewhere');
  h.document.dispatchEvent(new Event('focusin'));assert.equal(scrolls.length,0);flush();assert.equal(scrolls.length,1);assert.equal(scrolls[0].top,-65);
  h.events.dispatchEvent(new Event('resize'));assert.equal(scrolls.length,1);flush();assert.equal(scrolls.length,2);
 }finally{h.close();}
});


test('A late first-run account creation keeps newer name and kind; an unchanged creation closes its form',async()=>{
 for(const newer of [false,true]){
  const held=deferred(),sent=[];let revision=0;
  const element=(type,props)=>({type,props}),nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
  const h=hookHarness('../app/first-run.tsx',{
   './styles/first-run.css':{},'./data':{dashboard:{meta:{ledger:{id:'fictional',defaultCurrency:'USD'},accounts:{}}}},'./ui':{Icon:'icon',dayLabel:x=>x,money:()=>'',useAppearance:()=>({})},'./settings-dialog':{CurrencyOptions:'currencies'},'./live/api':{},'./live/statements-api':{},'./live/use-drafts':{statementNeedsAttention:()=>false},'./live/ledger-check':{},'./date-time':{},'./formatting':{},
   './live/interaction':{useInteractionSnapshot:value=>[value,{},()=>{}],useActionGuard:()=>({touch:()=>revision++,begin:()=>revision,unchanged:ticket=>ticket===revision,interaction:{}})},
   'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
  });
  const row={key:'file',id:'a'.repeat(32),name:'Fictional.csv',session:{state:'account_needed',file_currency:'USD'}};
  const render=()=>h.render(m=>m.FileItem({row,finishing:false,onChoose(){},onCreate:spec=>{sent.push(spec);return held.promise;},onRemove(){},onUnlock(){}}));
  const find=predicate=>nodes(h.current).find(predicate);
  try{
   render();find(n=>n.type==='button'&&String(n.props.children).startsWith('Add an account')).props.onClick();render();find(n=>n.type==='input'&&n.props.maxLength===100).props.onChange({target:{value:'Willow original account'}});render();find(n=>n.type==='button'&&n.props.className==='button primary').props.onClick();await h.settle();
   if(newer){find(n=>n.type==='input'&&n.props.maxLength===100).props.onChange({target:{value:'Willow next account'}});render();find(n=>n.type==='select'&&n.props.value==='bank').props.onChange({target:{value:'card'}});render();row.session.state='reading';render();assert.ok(find(n=>n.props?.['aria-label']==='New account'),'Reading the assigned file must not remount its open account fields');row.session.state='found';render();}
   held.resolve();await h.settle();assert.equal(sent[0].label,'Willow original account');assert.equal(sent[0].kind,'bank');
   if(newer){assert.ok(find(n=>n.props?.['aria-label']==='New account'),'Newer account fields must remain open after the old save');assert.equal(find(n=>n.type==='input'&&n.props.maxLength===100).props.value,'Willow next account');assert.equal(find(n=>n.type==='select'&&n.props.value==='card').props.value,'card');}else assert.equal(find(n=>n.props?.['aria-label']==='New account'),undefined);
  }finally{held.resolve();h.close();}
 }
});


test('An unmounted first-run account creator cannot overwrite a newer recovered form',async()=>{
 const held=deferred();let local,saved,writes=0;
 const element=(type,props)=>({type,props}),nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 const h=hookHarness('../app/first-run.tsx',{
  './styles/first-run.css':{},'./data':{dashboard:{meta:{ledger:{id:'fictional',defaultCurrency:'USD'},accounts:{}}}},'./ui':{Icon:'icon',dayLabel:x=>x,money:()=>'',useAppearance:()=>({})},'./settings-dialog':{CurrencyOptions:'currencies'},'./live/api':{},'./live/statements-api':{},'./live/use-drafts':{statementNeedsAttention:()=>false},'./live/ledger-check':{},'./date-time':{},'./formatting':{},
  './live/interaction':{useInteractionSnapshot:value=>[value,{},()=>{}],useActionGuard:()=>({touch(){},begin:()=>0,unchanged:()=>true,interaction:{}})},
  './live/retained-state':{useRetainedState:(_key,initial)=>{local??=typeof initial==='function'?initial():initial;saved??=local;return [local,next=>{local=typeof next==='function'?next(local):next;saved=local;writes++;},next=>{local=typeof next==='function'?next(local):next;}];}},
  'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
 });
 const render=()=>h.render(m=>m.FileItem({row:{key:'file',id:'a'.repeat(32),name:'Fictional.csv',session:{state:'account_needed',file_currency:'USD'}},finishing:false,onChoose(){},onCreate:()=>held.promise,onRemove(){},onUnlock(){}}));
 const find=predicate=>nodes(h.current).find(predicate);
 try{
  render();find(n=>n.type==='button'&&String(n.props.children).startsWith('Add an account')).props.onClick();render();find(n=>n.type==='input'&&n.props.maxLength===100).props.onChange({target:{value:'Willow original'}});render();find(n=>n.type==='button'&&n.props.className==='button primary').props.onClick();await h.settle();
  h.close();saved={...saved,label:'Willow recovered next'};const before=writes;held.resolve();await h.settle();assert.equal(saved.label,'Willow recovered next');assert.equal(writes,before,'No late setter may replace the next mounted form’s recovery');
 }finally{held.resolve();h.close();}
});

test('A late first-run account declaration assigns only an unchanged file in the mounted import',async()=>{
 const outcomes=[];
 for(const mode of ['unchanged','new-choice','unmounted']){
  const held=deferred(),assignments=[],events=new EventTarget();events.location={search:'?setup=import'};
  const element=(type,props)=>({type,props}),nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
  const id='a'.repeat(32),session={id,original_name:'Fictional.csv',created_at:'2026-11-01',state:'account_needed',file_currency:'USD'};
  const h=hookHarness('../app/first-run.tsx',{
   './styles/first-run.css':{},'./data':{dashboard:{meta:{ledger:{id:'fictional',defaultCurrency:'USD'},accounts:{}}},dataVersion:()=>0,subscribeData:()=>()=>{},reloadData:async()=>{}},'./ui':{Icon:'icon',dayLabel:x=>x,money:()=>'',useAppearance:()=>['light',()=>{}]},'./settings-dialog':{CurrencyOptions:'currencies'},
   './live/api':{request:async(path,options)=>{if(path==='setup/accounts')return held.promise;assignments.push(JSON.parse(options.body).account);return {...session,assigned_account:JSON.parse(options.body).account};}},
   './live/statements-api':{statementsApi:{list:async()=>({sessions:[session]})}},'./live/use-drafts':{statementNeedsAttention:()=>false},'./live/ledger-check':{useLedgerCheckStatus:()=>[null,()=>{}],ledgerCheckBlock:()=>null},'./date-time':{},'./formatting':{},
   './live/interaction':{useInteractionSnapshot:value=>[value,{},()=>{}],useActionGuard:()=>({touch(){},begin:()=>0,unchanged:()=>true,interaction:{}})},'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
  },{window:events,URLSearchParams});
  const render=()=>h.render(m=>{const root=m.default({onDone(){}}),child=nodes(root).find(n=>n.type?.name==='Import');assert.ok(child);return child.type(child.props);});
  const item=()=>nodes(h.current).find(n=>n.type?.name==='FileItem');
  try{
   render();await h.settle();const creating=item().props.onCreate({kind:'bank',label:'Willow original',currency:'USD'});await h.settle();
   if(mode==='new-choice'){item().props.onChoose('Assets:Bank:Cedar choice');await h.settle();}
   if(mode==='unmounted')h.close();
   held.resolve({account:'Assets:Bank:Willow original'});await creating;await h.settle();outcomes.push({mode,assignments});
  }finally{held.resolve({account:'Assets:Bank:Willow original'});h.close();}
 }
 assert.deepEqual(outcomes,[{mode:'unchanged',assignments:['Assets:Bank:Willow original']},{mode:'new-choice',assignments:['Assets:Bank:Cedar choice']},{mode:'unmounted',assignments:[]}]);
});
test('A hand worker refusal reclaims and rechecks before Retry, preserving its original reason',async()=>{
 const g=handHarness();try{
  g.holds.claim[0].resolve({claim_token:'original',form:g.form});await g.h.settle();
  g.nodes().find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await g.h.settle();g.holds.form[0].resolve({ok:true,checks:[],new_accounts:[],proposal_hash:'checked'});await g.h.settle();
  g.api.handAdd=async()=>{throw Object.assign(Error('Worker refused this entry'),{terminal:true});};g.button('Save and add').props.onClick();await g.h.settle();
  assert.equal(g.calls.filter(call=>call[0]==='claim').length,2,'The worker consumed the original claim');
  g.holds.claim[1].resolve({claim_token:'renewed',form:g.form});await g.h.settle();assert.equal(g.calls.at(-1)[2],'renewed');
  g.holds.form.at(-1).resolve({ok:true,checks:[],new_accounts:[],proposal_hash:'rechecked'});await g.h.settle();assert.equal(g.button('Retry').props.disabled,false);assert.ok(JSON.stringify(g.h.current).includes('Worker refused this entry'));
  const date=g.nodes().find(n=>n.type==='input'&&n.props['aria-label']==='Date');date.props.onChange({target:{value:'2026-11-03'}});g.h.render();await g.h.advance(500);assert.equal(g.calls.at(-1)[2],'renewed','Autosave uses the renewed claim too');
 }finally{g.holds.form?.forEach(hold=>hold.resolve({}));g.h.close();}
});

test('An uncertain Hand transport failure never reclaims an accepted worker draft',async()=>{
 const g=handHarness();try{
  g.holds.claim[0].resolve({claim_token:'original',form:g.form});await g.h.settle();
  g.nodes().find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await g.h.settle();g.holds.form[0].resolve({ok:true,checks:[],new_accounts:[],proposal_hash:'checked'});await g.h.settle();
  g.api.handAdd=async()=>{throw Error('Fictional network interruption');};g.button('Save and add').props.onClick();await g.h.settle();
  assert.equal(g.calls.filter(call=>call[0]==='claim').length,1,'An unknown result cannot pull queued work into a new claim');
 }finally{g.holds.form?.forEach(hold=>hold.resolve({}));g.h.close();}
});

test('Newer edits after an uncertain correction get plain recovery words and no impossible Retry',async()=>{const g=handHarness(undefined,true);try{g.holds.claim[0].resolve({claim_token:'token',form:g.form});await g.h.settle();g.nodes().find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await g.h.settle();g.holds.form[0].resolve({ok:true,checks:[],new_accounts:[],proposal_hash:'checked'});await g.h.settle();g.api.correct=async()=>{throw Error('Reply lost');};g.button('Save correction').props.onClick();await g.h.settle();const description=g.nodes().find(n=>n.type==='input'&&n.props.value==='Fictional lunch');description.props.onChange({target:{value:'Newer description'}});g.h.render();g.nodes().find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await g.h.settle();g.holds.form.at(-1).resolve({ok:true,checks:[],new_accounts:[],proposal_hash:'new'});await g.h.settle();assert.ok(JSON.stringify(g.h.current).includes('earlier correction may already be saved'));assert.equal(g.button('Save correction').props.disabled,true);}finally{g.holds.form?.forEach(hold=>hold.resolve({}));g.h.close();}});


test('A committed statement discard replay clears its old refusal and shows its Trash destination',async()=>{
 const g=statementHarness();try{
  g.lists[0].resolve(g.list(g.session('saved','2026-11-05T10:00:01.000000+00:00')));await g.h.settle();
  g.button('Discard').props.onClick();await g.h.settle();assert.ok(g.nodes().some(node=>node.props?.role==='alert'),'The original refusal is visible');
  g.h.events.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{path:'statements/'+ 's'.repeat(32),body:'',result:undefined}}));await g.h.settle();
  assert.equal(g.nodes().some(node=>node.props?.role==='alert'),false,'An exact-job Retry must clear the old refusal');
  assert.ok(g.nodes().some(node=>node.props?.id==='statement-discarded'),'The committed discard has its own visible Trash destination');
  assert.equal(g.button('Discard'),undefined);assert.ok(g.reloads.events);
 }finally{g.h.close();}
});


test('Statement discard completion keeps focus on a connected outside control',async()=>{
 const g=statementHarness();try{
  const chosen={isConnected:true},notice={focus(){g.document.activeElement=notice;},scrollIntoView(){}};g.document.body={};g.document.querySelector=selector=>selector==='#statement-discarded'?notice:null;
  g.lists[0].resolve(g.list(g.session('saved','2026-11-05T10:00:01.000000+00:00')));await g.h.settle();while(g.frames.length)g.frames.shift()();
  g.document.activeElement=chosen;
  g.h.events.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{path:'statements/'+ 's'.repeat(32),body:'',result:undefined}}));await g.h.settle();while(g.frames.length)g.frames.shift()();
  assert.equal(g.document.activeElement,chosen,'A connected control chosen during the save keeps focus');
 }finally{g.h.close();}
});
