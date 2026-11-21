import assert from 'node:assert/strict';
import {setImmediate} from 'node:timers/promises';
import {test} from 'node:test';
import {holdWrite} from './browser/immediate-actions.mjs';

test('held write without a request times out with the action name',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});
 const pattern='**/api/drafts/*/usual';
 const page={route:async()=>{},unroute:async()=>{}};
 const held=await holdWrite(page,pattern,{name:'usual-payment Retry'});
 let result;
 held.ready.then(()=>{result='arrived';},error=>{result=error;});
 try{
  t.mock.timers.tick(29_999);
  await setImmediate();
  assert.equal(result,undefined);
  t.mock.timers.tick(1);
  await setImmediate();
  assert.match(result?.message||'',/usual-payment Retry.*POST.*\/usual/);
 }finally{await held.release();}
});
