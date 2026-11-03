import './test-isolation.mjs';
import {createTestServer} from './test-isolation.mjs';
import {loadPublishedData} from './load-data.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';

test('Add visit recovery tolerates disabled/malformed storage, caps results and removes Undo rows',async()=>{
 const server=await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
 const old={storage:globalThis.sessionStorage,window:globalThis.window};
 try{
  const data=await loadPublishedData(server);(await server.ssrLoadModule('/app/data.ts')).setData(data);
  let raw='broken',events=0;
  globalThis.sessionStorage={getItem:()=>raw,setItem:(_key,value)=>{raw=value;}};
  globalThis.window={dispatchEvent:()=>{events++;}};
  const {readVisit,rememberResults,forgetResult}=await server.ssrLoadModule('/app/live/add-visit.ts');
  assert.deepEqual(readVisit(),[]);
  const rows=Array.from({length:5},(_,i)=>({id:String(i).padStart(32,'0'),type:i%2?'statement':'draft'}));
  rememberResults(rows);assert.deepEqual(readVisit(),rows.slice(0,3));
  rememberResults([rows[1]]);assert.deepEqual(readVisit(),[rows[1],rows[0],rows[2]]);
  for(const row of rows.slice(0,3))forgetResult(row.id);assert.deepEqual(readVisit(),[]);
  raw='[{"id":"../escape","type":"draft"}]';assert.deepEqual(readVisit(),[]);
  globalThis.sessionStorage={getItem:()=>{throw Error('disabled');},setItem:()=>{throw Error('disabled');}};
  rememberResults([rows[0]]);assert.deepEqual(readVisit(),[rows[0]]);forgetResult(rows[0].id);assert.deepEqual(readVisit(),[]);assert.equal(events,7);
 }finally{globalThis.sessionStorage=old.storage;globalThis.window=old.window;await server.close();}
});

test('Add result labels derive only from expense, receipt and statement states',async()=>{
 const server=await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
 try{
  const {draftStatus,statementStatus}=await server.ssrLoadModule('/app/live/add-results.tsx');
  for(const [state,label] of [['queued','Working'],['working','Working'],['question','Needs an answer'],['failed','Failed'],['added','Added']])assert.equal(draftStatus({state}),label);
  assert.equal(draftStatus({state:'added',result:{outcome:'not_expense'}}),'Not an expense');assert.equal(draftStatus({state:'added',receipt_reading:{state:'question'}}),'Needs an answer');
  for(const [state,label] of [['reading','Reading'],['found','Needs decisions'],['review','Needs decisions'],['applied','Applied'],['couldnt_read','Failed'],['locked','Needs an answer']])assert.equal(statementStatus({state}),label);
 }finally{await server.close();}
});
