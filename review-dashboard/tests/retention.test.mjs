import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const compile=file=>ts.transpileModule(readFileSync(new URL(file,import.meta.url),'utf8').replaceAll('import.meta.env.BASE_URL','"/"'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
test('Discard suppresses later result GETs and stale list rows',async()=>{
 const exports={};let fetched=0;
 const d={id:'a'.repeat(32),text:'Fictional item',state:'editing',questions:[],trashed_at:'2026-11-04T00:00:00Z'};
 const run=removed=>{
  vm.runInNewContext(compile('../app/live/add-results.tsx'),{exports,window:{addEventListener(){},removeEventListener(){}},require:name=>name==='react'?{useState:init=>[typeof init==='function'?init():init,()=>{}],useRef:v=>({current:v}),useEffect:fn=>fn()}:name==='./api'?{isRemoved:()=>removed,api:{get:async()=>{fetched++;throw Error('GET 404 after Discard');}}}:name==='./add-visit'?{readVisit:()=>[{id:d.id,type:'draft'}],forgetResult(){}}:{} });
  return exports.useAddResults({drafts:[removed?{...d,trashed_at:null}:d],statements:[]});
 };
 assert.equal(run(false).length,0);assert.equal(run(true).length,0);
 await new Promise(resolve=>setImmediate(resolve));assert.equal(fetched,0,'Discard must stop all subsequent result polling, including a stale list response');
});
test('Queue groups omit added items, order worker before FIFO, and preserve Saved for later',()=>{
 const exports={};vm.runInNewContext(compile('../app/live/drafts.tsx'),{exports,require:name=>name==='./direct-writes'?{writeSnapshot:()=>[],enqueueWrite:(_path,_options,send)=>send(),committedResult:(_path,send)=>send()}:{}});
 const base={created_at:'2026-11-01T00:00:00Z',queued_at:'2026-11-01T00:00:00Z',files:[],text:'Fictional item'};
 const groups=exports.queueGroups([{...base,id:'a',state:'added'},{...base,id:'b',state:'editing'},{...base,id:'c',state:'question'},{...base,id:'d',state:'queued'},{...base,id:'e',state:'working'},{...base,id:'f',state:'queued',queued_at:'2026-11-01T01:00:00Z'}]);
 assert.equal(JSON.stringify(groups.map(g=>[g.name,g.items.map(d=>d.id)])),JSON.stringify([['Needs you',['c']],['In progress',['e','d','f']],['Saved for later',['b']]]));
});
test('Discard fences late reads and autosaves at the API boundary',async()=>{
 const exports={},window=new EventTarget();let reads=0,writes=0,release;
 vm.runInNewContext(compile('../app/live/api.ts'),{exports,window,Event,Date,DOMException,AbortController,setTimeout,clearTimeout,require:name=>name==='./direct-writes'?{writeSnapshot:()=>[],enqueueWrite:(_path,_options,send)=>send(),committedResult:(_path,send)=>send()}:{clientTime:()=>({})},fetch:async(_path,options)=>{
  if(options.method==='DELETE'){writes++;return new Response(null,{status:204});}
  reads++;return new Promise(resolve=>{release=()=>resolve(new Response(JSON.stringify({id:'f'.repeat(32),state:'editing',text:'Stale words',files:[]})));});
 }});
 const id='f'.repeat(32),pending=exports.api.get(id);await exports.api.discard(id);
 await assert.rejects(exports.api.edit(id,'Late words',null),error=>error.name==='AbortError');
 assert.ok((await exports.api.get(id)).trashed_at);release();assert.ok((await pending).trashed_at);
 assert.equal(reads,1,'No later result read reaches the removed record');assert.equal(writes,1,'No later autosave reaches the removed record');
});
test('Just added follows the actual ledger after Delete and Restore',()=>{
 const run=transactions=>{
  const exports={},id='e'.repeat(32),entryId='a'.repeat(16),d={id,state:'added',text:'Fictional payment',questions:[],result:{outcome:'ready',entry_id:entryId}};
  vm.runInNewContext(compile('../app/live/add-results.tsx'),{exports,window:{addEventListener(){},removeEventListener(){}},require:name=>name==='react'?{useState:init=>[typeof init==='function'?init():init,()=>{}],useRef:v=>({current:v}),useEffect:fn=>fn()}:name==='../data'?{dashboard:{transactions}}:name==='./api'?{isRemoved:()=>false,api:{get:async()=>d}}:name==='./add-visit'?{readVisit:()=>[{id,type:'draft'}]}:{} });
  return exports.useAddResults({drafts:[d],statements:[]});
 };
 assert.equal(run([]).length,0,'A retained completion cannot display a removed ledger entry');
 assert.equal(run([{storeId:'a'.repeat(16)}]).length,1,'Restoring the same entry makes its result available again');
});
test('Queue totals keep an explicitly typed foreign currency without conversion',()=>{
 const exports={};vm.runInNewContext(compile('../app/live/drafts.tsx'),{exports,require:name=>name==='../data'?{dashboard:{meta:{ledger:{defaultCurrency:'EUR'},accounts:{'Assets:Bank:Willow':{currency:'EUR'}}}}}:{} });
 const pieces={fields:{amount:{value:'12.34',source:'typed'},currency:{value:'GBP',source:'typed'},pay:{value:'Assets:Bank:Willow',source:'typed'}}};
 assert.equal(JSON.stringify(exports.typedAmount({pieces})),JSON.stringify({amount:12.34,currency:'GBP'}));
 pieces.fields.currency={value:'EUR',source:'usual'};assert.equal(exports.typedAmount({pieces}).currency,'EUR');
});
