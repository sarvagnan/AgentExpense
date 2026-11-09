import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {hookHarness} from './hook-harness.mjs';

test('Retained setters stay stable across typing so dependent setup effects do not restart',()=>{
 const storage=new Map();
 const h=hookHarness('../app/live/retained-state.ts',{'../data':{dashboard:{meta:{ledger:{id:'fictional'}}}}},{sessionStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)}});
 try{
  h.render(m=>m.useRetainedState('setup-rows',()=>[{balance:''}]));
  const update=h.current[1];
  update(rows=>rows.map(row=>({...row,balance:'1840.25'})));h.render();
  assert.equal(h.current[1],update,'Typing must not retrigger an effect depending on the state setter');
  assert.equal(h.current[0][0].balance,'1840.25');
  update(rows=>[...rows,{balance:'4650.00'}]);h.render();
  assert.equal(h.current[1],update);
  assert.deepEqual(Array.from(h.current[0],row=>row.balance),['1840.25','4650.00']);
  assert.deepEqual(JSON.parse(storage.get('ledger-form-fictional-setup-rows')).map(row=>row.balance),['1840.25','4650.00'],'Recovery is persisted before leaving');
 }finally{h.close();}
});
test('Server initialization does not retain untouched values; a submitted snapshot clears after the form is left, preserving newer typing',async()=>{
 const storage=new Map(),window=new EventTarget();
 const h=hookHarness('../app/live/retained-state.ts',{'../data':{dashboard:{meta:{ledger:{id:'fictional'}}}}},{sessionStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},window});
 h.render(m=>m.useRetainedState('hand-form',null));assert.equal(storage.size,0);assert.equal(typeof h.current[2],'function');h.current[2]({description:'Server initial'});h.render();assert.equal(storage.size,0);
 h.current[1]({description:'Edited'});h.render();assert.equal(storage.size,1);h.close();
});
