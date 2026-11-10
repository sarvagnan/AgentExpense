import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {expectInjected422,expectInjectedHttp,consumeInjected422} from './browser/expected-errors.mjs';
import {observeDirectWrites} from './browser/direct-write-observer.mjs';
test('Injected console exclusions require the exact held URL and exact 422 text',()=>{
 const page={},message=(url,text='Failed to load resource: the server responded with a status of 422 (Unprocessable Entity)')=>({text:()=>text,location:()=>({url})});expectInjected422(page,'http://fixture/api/settings/name');
 assert.equal(consumeInjected422(page,message('http://fixture/api/settings/default-currency')),false);assert.equal(consumeInjected422(page,message('http://fixture/api/settings/name','Unrelated 422')),false);assert.equal(consumeInjected422(page,message('http://fixture/api/settings/name')),true);assert.equal(consumeInjected422(page,message('http://fixture/api/settings/name')),false);
});
test('An expected obsolete-claim refusal allows one exact 409 console response',()=>{
 const page={},url='http://fixture/api/drafts/obsolete/release';
 const message=(source,text='Failed to load resource: the server responded with a status of 409 (Conflict)')=>({text:()=>text,location:()=>({url:source})});
 expectInjectedHttp(page,url,409);
 assert.equal(consumeInjected422(page,message('http://fixture/api/drafts/current/release')),false);
 assert.equal(consumeInjected422(page,message(url,'Unrelated 409')),false);
 assert.equal(consumeInjected422(page,message(url)),true);
 assert.equal(consumeInjected422(page,message(url)),false,'The expected response is consumed only once');
});
test('The direct observer includes Queue DELETE and draft/statement Restore',async()=>{
 for(const [method,path] of [['DELETE','drafts/id'],['DELETE','statements/id'],['POST','drafts/id/restore'],['POST','statements/id/restore']]){
  let handler,forwarded=false,observed=0;
  const page={route:async(_path,fn)=>handler=fn,unroute:async()=>{},waitForFunction:async()=>{observed++;},evaluate:async()=>({controls:1}),isClosed:()=>false};
  const observer=await observeDirectWrites({page,base:'http://fixture/',snap:async()=>{},record(){}});
  await handler({request:()=>({method:()=>method,url:()=>`http://fixture/api/${path}`,postDataJSON:()=>({})}),fallback:async()=>{},continue:async()=>{forwarded=true;},abort:async()=>{}});await observer.close();assert.ok(observed&&forwarded,`${method} ${path} must be observed`);
 }
});

test('The complete browser case enforces the declared action table',async()=>{const source=await (await import('node:fs/promises')).readFile(new URL('./browser/suites.mjs',import.meta.url),'utf8');assert.match(source,/assertActionCoverage\(page\)/);});

test('Action coverage rejects a missing action or a failure without a saved outcome',async()=>{const {actionCoverage,coveredAction,assertActionCoverage}=await import('./browser/action-coverage.mjs');const page={};assert.throws(()=>assertActionCoverage(page),/Every action/);for(const [,names] of actionCoverage)for(const name of names)coveredAction(page,name,{failed:true,saved:true,visibleSaved:'Saved.',outcomeChecked:true,ownAlertsCleared:true,usable:'Relevant control',before:[],after:[]});assert.doesNotThrow(()=>assertActionCoverage(page));coveredAction(page,'hand-add',{failed:true,saved:false,usable:'Notes',before:[],after:[]});assert.throws(()=>assertActionCoverage(page),/complete evidence/);});

test('The action table rejects event-only success without a visible result and checked outcome',async()=>{
 const {actionCoverage,coveredAction,assertActionCoverage}=await import('./browser/action-coverage.mjs');const page={};
 for(const [,names] of actionCoverage)for(const name of names)coveredAction(page,name,{failed:true,saved:true,usable:'Notes',before:[],after:[]});
 assert.throws(()=>assertActionCoverage(page),/visible.*outcome/);
});

test('Import outcomes compare each session ID/state/commit independent of response order',async()=>{
 const {committedOutcome}=await import('./browser/action-coverage.mjs');
 const first={id:'a'.repeat(32),state:'applied',result:{commit_id:'a'.repeat(40)}},second={id:'b'.repeat(32),state:'applied',result:{commit_id:'b'.repeat(40)}};
 assert.doesNotThrow(()=>committedOutcome('setup-import','setup/import',{}, {sessions:[second,first]},{sessions:[first,second]},200));
 assert.throws(()=>committedOutcome('setup-import','setup/import',{}, {sessions:[second,first]},{sessions:[first,{...second,result:{commit_id:'c'.repeat(40)}}]},200),/exact committed result/);
});

test('Every action rejects Saved beside its own visible alert',async()=>{
 const {actionCoverage,coveredAction,assertActionCoverage}=await import('./browser/action-coverage.mjs');const page={};
 for(const [,names] of actionCoverage)for(const name of names)coveredAction(page,name,{failed:true,saved:true,visibleSaved:'Saved.',outcomeChecked:true,ownAlertsCleared:true,usable:'Relevant control',before:[],after:[]});
 coveredAction(page,'receipt-use',{failed:true,saved:true,visibleSaved:'Saved.',outcomeChecked:true,ownAlertsCleared:false,usable:'Composer',before:[],after:[]});
 assert.throws(()=>assertActionCoverage(page),/alert/);
});

test('A forced missing result excludes only its exact URL and 404 console text',()=>{
 const page={},text='Failed to load resource: the server responded with a status of 404 (Not Found)',message=(url,value=text)=>({text:()=>value,location:()=>({url})});
 expectInjectedHttp(page,'http://fixture/api/statements/held',404);
 assert.equal(consumeInjected422(page,message('http://fixture/api/statements/other')),false);
 assert.equal(consumeInjected422(page,message('http://fixture/api/statements/held','Unrelated 404')),false);
 assert.equal(consumeInjected422(page,message('http://fixture/api/statements/held')),true);
 assert.equal(consumeInjected422(page,message('http://fixture/api/statements/held')),false);
});
