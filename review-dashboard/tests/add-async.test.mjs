import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {deferred,hookHarness} from './hook-harness.mjs';

const id='a'.repeat(32),file={id:'receipt',original_name:'Fictional-receipt.png',media_type:'image/png',size:20};
class ApiError extends Error{constructor(status,message,body){super(message);this.status=status;this.refusal=body?.detail;}}
function composerFixture(overrides={}){
 let saved=overrides.initial||null,record={id,text:'',state:'editing',files:[],declined_rules:[]};const calls=[],removed=new Set();
 const api={list:async()=>[],get:async()=>({...record}),create:async text=>{record={...record,text};return {...record};},edit:async(_id,text)=>{calls.push(text);record={...record,text};return {...record};},save:async()=>({...record,state:'queued'}),remove:async()=>{record={...record,files:[]};},cancelUpload:async()=>({...record}),...overrides.api};
 const dependencies={
  './add-intake':{useAddInterpretation:()=>({value:null,busy:false,error:''})},'./add-visit':{rememberResults(){}},'../date-time':{clientTime:()=>({})},
  '../data':{dashboard:{meta:{ledger:{modules:{}},accounts:{}}},fetchGzipJson:async()=>({patterns:{},people:[],accounts:[]})},
  '../../scripts/account-metadata.mjs':{paymentAccounts:()=>({})},'../ui':{announce(){}},
  './api':{api,ApiError,isRemoved:value=>removed.has(value),request:async()=>({}),upload:async()=>{record={...record,files:[file]};},...overrides.transport},
  './builder':{buildSentence:text=>({fields:{what:{value:text}}}),draftPieces:built=>built},
  './persistence':{readStored:()=>saved,writeStored:(_key,next)=>{saved=next;return true;},findRecoveryDraft:()=>null,restorePieceEdits:()=>({})},'./use-drafts':{draftsChanged(){}},...overrides.dependencies,
 };
 const h=hookHarness('../app/live/composer.ts',dependencies);let active=true;h.render(m=>m.useComposer(active));
 return {h,api,calls,removed,get saved(){return saved;},get record(){return record;},set record(next){record=next;},type(text){h.current.update({...h.current.form,text});h.render();},active(value){active=value;h.render();}};
}

test('Add autosave waits 350ms, serializes held edits, and preserves newer words across a late response',async()=>{
 const f=composerFixture(),{h}=f;try{
  await h.settle();f.type('First meal');await h.advance(349);assert.equal(f.calls.length,0);await h.advance(1);assert.equal(f.calls.at(-1),'First meal');
  const held=deferred();let entered=false;f.api.edit=async(_id,text)=>{f.calls.push(text);if(!entered){entered=true;return held.promise;}return {...f.record,text};};
  f.type('Second meal');await h.advance(350);assert.ok(entered);f.type('Third meal');await h.advance(350);assert.equal(f.calls.at(-1),'Second meal','No competing autosave');
  held.resolve({...f.record,text:'Second meal'});await h.settle();assert.equal(f.calls.at(-1),'Third meal');assert.equal(h.current.form.text,'Third meal');assert.equal(h.current.draft.text,'Third meal');assert.equal(f.saved.text,'Third meal');assert.equal(h.current.syncError,'');
 }finally{h.close();}
});

test('Discard during a held autosave ignores its late failure and keeps the next expense',async()=>{
 const f=composerFixture(),{h}=f;try{
  await h.settle();f.type('First meal');await h.advance(350);
  const held=deferred();f.api.edit=()=>held.promise;f.type('Old words');await h.advance(350);
  f.removed.add(id);h.current.forgetDiscarded(id);h.render();f.type('Next meal');held.reject(Error('Item moved to Trash'));await h.settle();
  assert.equal(h.current.form.text,'Next meal');assert.equal(h.current.syncError,'','A gone item must not show a late autosave error');assert.equal(h.current.draft,null);
 }finally{h.close();}
});

test('Add waits for a held autosave before saving and refuses duplicate clicks; route changes retain the next input',async()=>{
 const f=composerFixture(),{h}=f;try{
  await h.settle();f.type('First meal');await h.advance(350);
  const held=deferred(),saving=deferred();let submits=0;f.api.edit=()=>held.promise;f.type('Final words');await h.advance(350);
  f.api.save=()=>{submits++;return saving.promise;};const run=h.current.save();await h.settle();assert.equal(h.current.saving,true);assert.equal(submits,0);assert.equal(await h.current.save(),undefined);
  held.resolve({...f.record,text:'Final words'});await h.settle();assert.equal(submits,1);f.active(false);saving.resolve({...f.record,state:'queued'});assert.equal(await run,true);await h.settle();f.active(true);f.type('Next meal');await h.settle();assert.equal(h.current.form.text,'Next meal');assert.equal(h.current.saving,false);
 }finally{h.close();}
});

test('Confirmed balance refusal has no save-bar error; ordinary save failures retain theirs',async()=>{
 const f=composerFixture(),{h}=f;try{
  await h.settle();f.type('Fictional meal');await h.advance(350);
  f.api.save=async()=>{throw new ApiError(409,'A confirmed balance would change.',{detail:{kind:'confirmed_balance'}});};assert.equal(await h.current.save(),false);await h.settle();assert.equal(h.current.error,'');assert.equal(h.current.form.text,'Fictional meal');
  f.api.save=async()=>{throw Error('Try again later');};assert.equal(await h.current.save(),false);await h.settle();assert.match(h.current.error,/Try again later/);
 }finally{h.close();}
});

test('Held uploads survive Add navigation; cancelling one leaves newer words and the other file intact',async()=>{
 const held=deferred(),started=deferred();let cancelled=0;
 const f=composerFixture({transport:{upload:async(_id,selected,progress,signal)=>{progress(50);if(selected.name==='Held.png'){started.resolve();await held.promise;}else f.record={...f.record,files:[file]};if(signal.aborted)throw new DOMException('cancelled','AbortError');}},api:{cancelUpload:async()=>{cancelled++;return {...f.record};}}}),{h}=f;
 try{
  await h.settle();f.type('First words');h.current.pick([new File(['x'],'Held.png'),new File(['y'],'Ready.png')]);await started.promise;await h.settle();assert.equal(h.current.pending.length,1);const pending=h.current.pending[0];assert.equal(pending.progress,50);
  f.type('New words');f.active(false);await h.current.cancel(pending);await h.settle();f.active(true);held.resolve();await h.settle();assert.equal(cancelled,1);assert.equal(h.current.pending.length,0);assert.equal(h.current.form.text,'New words');assert.equal(h.current.draft.files.length,1);assert.equal(h.current.error,'');
 }finally{h.close();}
});

test('Late file refreshes cannot restore a removed receipt or overwrite newer words',async()=>{
 const f=composerFixture(),{h}=f;try{
  await h.settle();f.type('Meal');await h.advance(350);f.record={...f.record,files:[file]};
  const old=deferred();let reads=0;f.api.get=()=>++reads===1?old.promise:Promise.resolve({...f.record});
  const removing=h.current.remove(file);await h.settle();f.type('New words');const newer=h.current.remove(file);await newer;await h.settle();old.resolve({...f.record,files:[file]});await removing;await h.settle();assert.equal(h.current.draft.files.length,0);assert.equal(h.current.form.text,'New words');
 }finally{h.close();}
});

test('Interpretation waits 250ms and ignores held stale words, receipt, password and removed-file responses',async()=>{
 const requests=[],h=hookHarness('../app/live/add-intake.ts',{'../data':{},'./api':{request:(path,options)=>{const hold=deferred();requests.push({path,options,...hold});return hold.promise;}}});
 const args=[null,'Meal','',{}, {},true];const render=()=>h.render(m=>m.useAddInterpretation(...args));
 try{
  render();await h.settle();await h.advance(249);assert.equal(requests.length,0);await h.advance(1);assert.equal(requests.length,1);
  args[1]='Next meal';render();await h.advance(250);requests[1].resolve({kind:'expense'});await h.settle();requests[0].resolve({kind:'statement'});await h.settle();assert.equal(h.current.value.words.kind,'expense');
  args[0]=id;args[2]='receipt';render();await h.advance(250);args[4]={receipt:'new password'};render();await h.advance(250);
  requests[3].resolve({words:{kind:'expense'},files:[{id:'receipt',kind:'receipt'}]});await h.settle();requests[2].reject(Error('Old password'));await h.settle();assert.equal(h.current.error,'');assert.equal(h.current.value.files[0].kind,'receipt');
  args[2]='';render();await h.advance(250);args[5]=false;render();requests[4].resolve({kind:'statement'});await h.settle();args[5]=true;render();await h.advance(250);requests[5].resolve({kind:'expense'});await h.settle();assert.equal(h.current.value.files.length,0);assert.equal(h.current.error,'');
 }finally{h.close();}
});

test('Live rule refresh waits 30s and rejects an older held snapshot after a newer refresh',async()=>{
 const requests=[],h=hookHarness('../app/live/rules-api.ts',{
  '../data':{dashboard:{},fetchGzipJson:async()=>({rules:[]})},'../date-time':{},'./use-drafts':{},'./api':{request:()=>{const hold=deferred();requests.push(hold);return hold.promise;}},
 });
 try{
  h.render(m=>m.useRules(true));requests[0].resolve({rules:[{id:'first'}]});await h.settle();await h.advance(29999);assert.equal(requests.length,1);await h.advance(1);assert.equal(requests.length,2);
  const next=h.current.refresh();requests[2].resolve({rules:[{id:'new'}]});await next;await h.settle();requests[1].resolve({rules:[{id:'old'}]});await h.settle();assert.equal(h.current.data.rules[0].id,'new','A poll cannot undo a newer rule action');
 }finally{h.close();}
});

test('Add health/draft polling uses 30s idle and 3s active, coalesces held reads, and reloads additions',async()=>{
 let drafts=[],healthCount=0,reloads=0,healthHold=null,listHold=null;
 const h=hookHarness('../app/live/use-drafts.ts',{'../data':{reloadData:async()=>{reloads++;}},'./api':{
  api:{health:()=>{healthCount++;return healthHold?.promise||Promise.resolve({ok:true});},list:()=>listHold?.promise||Promise.resolve(drafts)},isRemoved:()=>false,rememberRemoved(){},request:async path=>path==='trash'?{items:[]}:{state:'ready'},
 },'./statements-api':{statementsApi:{list:async()=>({sessions:[]})}}});
 try{
  h.render(m=>m.useDrafts());await h.settle();assert.equal(h.current.online,true);await h.advance(29999);assert.equal(healthCount,1);await h.advance(1);assert.equal(healthCount,2);
  healthHold=deferred();const check=h.current.refresh();await h.settle();assert.equal(h.current.online,true,'Held health checks do not disable Continue');healthHold.resolve({ok:false});await check;await h.settle();assert.equal(h.current.online,false);healthHold=null;
  drafts=[{id,state:'working',created_at:'2026-11-01'}];await h.current.refresh();await h.settle();const before=healthCount;await h.advance(2999);assert.equal(healthCount,before);await h.advance(1);assert.equal(healthCount,before+1);
  listHold=deferred();const pending=h.current.refresh();await h.settle();drafts=[{id,state:'added',created_at:'2026-11-01',result:{entry_id:'entry'}}];void h.current.refresh();const old=listHold;listHold=null;old.resolve([{id,state:'question',created_at:'2026-11-01'}]);await pending;await h.settle();assert.equal(h.current.drafts[0].state,'added');assert.equal(reloads,1);assert.equal(h.current.online,true);
  h.events.dispatchEvent(new Event('pagehide'));const hiddenCount=healthCount;await h.advance(30000);assert.equal(healthCount,hiddenCount);h.events.dispatchEvent(new Event('pageshow'));await h.settle();assert.ok(healthCount>hiddenCount);
 }finally{h.close();}
});

test('History suggestions arriving after navigation cannot replace the current Add snapshot',async()=>{
 const requests=[],f=composerFixture({dependencies:{'../data':{dashboard:{meta:{ledger:{modules:{}},accounts:{}}},fetchGzipJson:()=>{const hold=deferred();requests.push(hold);return hold.promise;}}}}),{h}=f;
 try{
  f.type('New meal');f.active(false);f.active(true);requests[1].resolve({patterns:{new:{count:2}}});await h.settle();requests[0].resolve({patterns:{old:{count:1}}});await h.settle();assert.equal(h.current.patterns.patterns.new.count,2);assert.equal(h.current.patterns.patterns.old,undefined);assert.equal(h.current.form.text,'New meal');
 }finally{h.close();}
});

test('Held recovery is invalidated by discard and cannot replace a new input',async()=>{
 const held=deferred(),f=composerFixture({initial:{id,text:'Original words',edits:{}},api:{get:()=>held.promise}}),{h}=f;
 try{
  const run=h.current.restore(id);await h.settle();h.current.forgetDiscarded(id);h.render();f.type('Next meal');held.resolve({id,state:'editing',files:[file],text:'Old recovered words',pieces:null});await run;await h.settle();assert.equal(h.current.form.text,'Next meal');assert.equal(h.current.draft,null);assert.equal(h.current.recovering,false);
 }finally{h.close();}
});

test('Rule guesses wait 180ms, cancel obsolete matches, and retain authoritative choices during a refresh',async()=>{
 const rule={id:'rent',description:'Birch rent',amount:'fixed'},result={matches:[{id:'rent'}],rule_id:'rent',pick_list:false},requests=[];
 const composer={form:{text:'Birch rent',declined_rules:[]},built:{fields:{}},draft:null,pending:[],interpretation:{busy:false,value:null},update(){},save(){},setRuleDecisionNeeded(){}};
 const element=(type,props)=>({type,props});
 const h=hookHarness('../app/live/rule-guess.tsx',{
  '../data':{dashboard:{meta:{ledger:{locale:'en-US'},accounts:{}}}},'../date-time':{localDate:()=> '2026-11-02'},'../ui':{},'../rules':{},
  '../../scripts/recurring-ui.mjs':{matchRules:()=>result},'./rules-api':{useRules:()=>({data:{rules:[rule]},ready:true}),rulesApi:{match:(text,signal)=>{const hold=deferred();requests.push({text,signal,...hold});signal.addEventListener('abort',()=>hold.reject(new DOMException('cancelled','AbortError')));return hold.promise;}}},
  'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
 });
 function choice(node){if(!node||typeof node!=='object')return;if(node.type?.name==='RuleGuessChoice')return node;for(const child of [node.props?.children].flat()){const found=choice(child);if(found)return found;}}
 const render=()=>h.render(m=>m.RuleGuess({composer,online:true}));
 try{
  render();await h.advance(179);assert.equal(requests.length,0);await h.advance(1);assert.equal(requests.length,1);assert.equal(choice(h.current).props.disabled,true);
  composer.form.text='Birch rent today';render();assert.equal(requests[0].signal.aborted,true);await h.advance(180);requests[1].resolve(result);await h.settle();assert.equal(choice(h.current).props.disabled,false);
  composer.form.declined_rules=['rent'];render();assert.equal(h.current,null,'Not this stays dismissed after an authoritative response');
 }finally{h.close();}
});

// A read's answer can settle before its abort while its continuation runs after it;
// a match that ignores the signal stands in for that order (carry-over from terra.patch).
test('A rule match answered after its request was aborted cannot replace the newer match',async()=>{
 const rule={id:'rent',description:'Birch rent',amount:'fixed'},result={matches:[{id:'rent'}],rule_id:'rent',pick_list:false},requests=[];
 let composer={form:{text:'Birch rent',declined_rules:[]},built:{fields:{}},draft:null,pending:[],interpretation:{busy:false,value:null},update(){},save(){},setRuleDecisionNeeded(){}};
 const element=(type,props)=>({type,props});
 const h=hookHarness('../app/live/rule-guess.tsx',{
  '../data':{dashboard:{meta:{ledger:{locale:'en-US'},accounts:{}}}},'../date-time':{localDate:()=> '2026-11-02'},'../ui':{},'../rules':{},
  '../../scripts/recurring-ui.mjs':{matchRules:()=>result},'./rules-api':{useRules:()=>({data:{rules:[rule]},ready:true}),rulesApi:{match:(text,signal)=>{const hold=deferred();requests.push({text,signal,...hold});return hold.promise;}}},
  'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
 });
 function choice(node){if(!node||typeof node!=='object')return;if(node.type?.name==='RuleGuessChoice')return node;for(const child of [node.props?.children].flat()){const found=choice(child);if(found)return found;}}
 const render=()=>h.render(m=>m.RuleGuess({composer,online:true}));
 try{
  render();await h.advance(180);assert.equal(requests.length,1);
  // Each edit is a new form, as the composer makes it.
  composer={...composer,form:{...composer.form,text:'Birch rent today'}};render();assert.equal(requests[0].signal.aborted,true);await h.advance(180);assert.equal(requests.length,2);
  requests[1].resolve(result);await h.settle();assert.equal(choice(h.current).props.disabled,false);assert.equal(choice(h.current).props.text,'Birch rent today');
  requests[0].resolve(result);await h.settle();
  assert.equal(choice(h.current).props.disabled,false,'The aborted answer for older words cannot take the newer match’s place');
 }finally{h.close();}
});

test('Just added ignores held lookups after a newer visit or removal',async()=>{
 let ids=[{id,type:'draft'}],removed=false;const requests=[];
 const h=hookHarness('../app/live/add-results.tsx',{
  '../data':{dashboard:{transactions:[]}},'../ui':{announce(){}},'./api':{isRemoved:()=>removed,api:{get:()=>{const hold=deferred();requests.push(hold);return hold.promise;}}},'./statements-api':{},
  './add-visit':{readVisit:()=>ids},'./rule-saved':{},'./receipt-reading':{},'../dashboard':{},'react/jsx-runtime':{},
 });
 const live={drafts:[],statements:[]};const render=()=>h.render(m=>m.useAddResults(live));
 try{
  render();ids=[];h.events.dispatchEvent(new Event('add-results-changed'));await h.settle();requests[0].resolve({id,state:'working'});await h.settle();assert.equal(h.current.length,0);
  ids=[{id,type:'draft'}];h.events.dispatchEvent(new Event('add-results-changed'));await h.settle();removed=true;requests[1].resolve({id,state:'question'});await h.settle();render();assert.equal(h.current.length,0,'Removed rows cannot reappear from a late get');
 }finally{h.close();}
});

test('Just added rule actions keep operation identity while held and reject duplicate submission',async()=>{
 const requests=[],h=hookHarness('../app/live/rules-api.ts',{'../data':{reloadData:async()=>{}},'../date-time':{clientTime:()=>({})},'./use-drafts':{draftsChanged(){}},'./api':{request:(path,options)=>{const hold=deferred();requests.push({path,options,...hold});return hold.promise;}}});
 let refreshed=0;try{
  h.render(m=>m.useRuleOperation(async()=>{refreshed++;}));const first=h.current.operate({action:'kept',rule_id:'rent'});await h.settle();assert.equal(h.current.busy,true);assert.equal(await h.current.operate({action:'kept',rule_id:'rent'}),false);assert.equal(requests.length,1);
  requests[0].reject(Error('Uncertain response'));assert.equal(await first,false);await h.settle();const retry=h.current.operate({action:'kept',rule_id:'rent'});assert.equal(requests[1].path,requests[0].path);assert.equal(requests[1].options.body,requests[0].options.body);requests[1].resolve({});assert.equal(await retry,true);await h.settle();assert.equal(refreshed,1);assert.equal(h.current.error,'');
 }finally{h.close();}
});

test('Health, autosave and long request deadlines fire at 5s, 12s and 120s on a controlled clock',async()=>{
 const signals=[],h=hookHarness('../app/live/api.ts',{'../date-time':{clientTime:()=>({})}},{Error,fetch:async(_url,options)=>{signals.push(options.signal);return new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError'))));}});
 try{
  const health=h.exports.api.health().then(()=>null,error=>error.message);await h.advance(4999);assert.equal(signals[0].aborted,false);await h.advance(1);assert.match(await health,/took too long/);
  const saving=h.exports.api.edit(id,'Fictional words',null).then(()=>null,error=>error.message);await h.advance(11999);assert.equal(signals[1].aborted,false);await h.advance(1);assert.match(await saving,/took too long/);
  const continuing=h.exports.request(`drafts/${id}/continue`,{method:'POST'},120000).then(()=>null,error=>error.message);await h.advance(119999);assert.equal(signals[2].aborted,false);await h.advance(1);assert.match(await continuing,/took too long/);assert.equal(h.timers.size,0);
 }finally{h.close();}
});
