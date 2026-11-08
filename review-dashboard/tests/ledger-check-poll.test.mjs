import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {deferred,hookHarness} from './hook-harness.mjs';

test('Import follows late failures and passes; errors retain the refusal, held reads never overlap, leaving cancels',async()=>{
 const reads=[];
 const h=hookHarness('../app/live/ledger-check.tsx',{
  '../ui':{},'../date-time':{},'../data':{},'../account-labels':{},'../rules':{},'react/jsx-runtime':{},
  './api':{request:(_path,{signal})=>{const wait=deferred();reads.push({...wait,signal});return wait.promise;}},
 });
 const pass={available:true,checking:false,item:null},fail={...pass,item:{block:{accounts:['Assets:Cash'],reason:'Wait for the ledger check'}}};
 try{
  h.render(m=>m.useLedgerCheckStatus());assert.equal(reads.length,1);
  await h.advance(9000);assert.equal(reads.length,1,'A held read starts no overlapping poll');
  reads[0].resolve(pass);await h.settle();assert.equal(h.current[0].item,null);
  await h.advance(2999);assert.equal(reads.length,1);await h.advance(1);assert.equal(reads.length,2);
  reads[1].resolve(fail);await h.settle();assert.equal(h.current[0].item.block.reason,'Wait for the ledger check');
  await h.advance(3000);reads[2].reject(Error('Fictional read failure'));await h.settle();assert.equal(h.current[0].item.block.reason,'Wait for the ledger check');
  await h.advance(3000);reads[3].resolve(pass);await h.settle();assert.equal(h.current[0].item,null,'A later pass clears the reason without Finish');
  await h.advance(3000);h.current[1](fail);reads[4].resolve(pass);await h.settle();assert.equal(h.current[0].item.block.reason,'Wait for the ledger check','An older poll cannot overwrite an explicit result');
  await h.advance(3000);h.close();assert.equal(reads[5].signal.aborted,true);reads[5].resolve(pass);await h.settle();await h.advance(9000);assert.equal(reads.length,6);
 }finally{h.close();}
});
