import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

test('late reads and autosave writes stop on pagehide; in-flight requests and pageshow recovery survive',async()=>{
  const source=ts.transpileModule(readFileSync(new URL('../app/live/api.ts',import.meta.url),'utf8').replace('import.meta.env.BASE_URL',"'/expenses/'"),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  const window=new EventTarget(),exports={};let calls=0,release,signal;
  const fetch=async(_url,options)=>{calls++;if(calls>1)return new Response('{}');signal=options.signal;return new Promise(resolve=>{release=()=>resolve(new Response('{}'));});};
  vm.runInNewContext(source,{exports,window,fetch,DOMException,AbortController,setTimeout,clearTimeout,require:name=>name==='./direct-writes'?{writeSnapshot:()=>[],enqueueWrite:(_path,_options,send)=>send(),committedResult:(_path,send)=>send()}:{clientTime:()=>({})}});
  const {request}=exports;
  const inflight=request('drafts',{method:'POST'});
  window.dispatchEvent(new Event('pagehide'));
  for(const method of ['GET','POST','PATCH'])await assert.rejects(request('drafts',{method}),error=>error.name==='AbortError');
  assert.equal(calls,1,'No transport may begin during teardown');
  assert.equal(signal.aborted,false,'Departure does not cancel an in-flight write');
  release();await inflight;
  window.dispatchEvent(new Event('pageshow'));
  const restored=request('drafts');assert.equal(calls,2,'Restored documents can fetch again');release();await restored;
});

test('small JSON autosaves use keepalive, while large text and explicit transport choices remain supported',async()=>{
  const source=ts.transpileModule(readFileSync(new URL('../app/live/api.ts',import.meta.url),'utf8').replace('import.meta.env.BASE_URL',"'/expenses/'"),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  const options=[],exports={},window=new EventTarget();
  vm.runInNewContext(source,{exports,window,fetch:async(_url,init)=>{options.push(init);return new Response('{}');},DOMException,AbortController,setTimeout,clearTimeout,require:name=>name==='./direct-writes'?{writeSnapshot:()=>[],enqueueWrite:(_path,_options,send)=>send(),committedResult:(_path,send)=>send()}:{clientTime:()=>({})}});
  await exports.api.create('Fictional expense, 12, paid with cash',null);
  await exports.api.edit('fictional','Updated words',null);
  assert.deepEqual(options.map(init=>init.keepalive),[true,true],'Ordinary autosave writes must finish across document departure');
  await exports.api.create('x'.repeat(100_000),null);
  assert.equal(options[2].keepalive,false,'Large supported text must not exceed the browser keepalive budget');
  assert.equal(JSON.parse(options[2].body).text.length,100_000,'Large input is preserved');
  await exports.request('drafts',{method:'PATCH',body:'{}',keepalive:false});
  assert.equal(options[3].keepalive,false,'An explicit transport choice is preserved');
});
