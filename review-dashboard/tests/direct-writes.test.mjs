import assert from 'node:assert/strict';
import {test} from 'node:test';
import {deferred,hookHarness} from './hook-harness.mjs';
function fixture(stored=new Map()){
 const h=hookHarness('../app/live/direct-writes.ts',{}, {Error,location:{pathname:'/expenses/add/',search:''},sessionStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value)}});
 return {h,api:h.exports,stored};
}
const options=body=>({method:'POST',body:JSON.stringify(body)});
test('Direct writes immediately publish pending, serialize saves, keep reads free and acknowledge only a real result',async()=>{
 const {h,api}=fixture(),first=deferred(),calls=[];
 const a=api.enqueueWrite('settings/name',options({name:'Fictional'}),()=>{calls.push('a');return first.promise;});
 assert.equal(api.writeSnapshot()[0].state,'queued');
 const b=api.enqueueWrite('settings/default-currency',options({currency:'USD'}),async()=>{calls.push('b');return {};});
 const read=await api.enqueueWrite('health',{},async()=>{calls.push('read');return true;});assert.equal(read,true);
 await h.settle();assert.deepEqual(calls,['read','a']);assert.equal(api.writeSnapshot()[0].state,'saving');assert.equal(api.writeSnapshot()[1].state,'queued');
 first.resolve({});await Promise.all([a,b]);assert.deepEqual(calls,['read','a','b']);assert.ok(api.writeSnapshot().every(item=>item.state==='saved'));
});
test('A failed direct write keeps its exact request, exposes its reason, retries once, and does not block the next action',async()=>{
 const {h,api}=fixture();let attempts=0;
 const send=async()=>{if(++attempts===1)throw Error('Fictional refusal');return {};};
 await assert.rejects(api.enqueueWrite('rules/operations/op',options({action:'paused'}),send),/Fictional refusal/);
 const failed=api.writeSnapshot()[0];assert.equal(failed.state,'failed');assert.equal(failed.reason,'Fictional refusal');
 await api.enqueueWrite('settings/name',options({name:'Other'}),async()=>({}));
 api.retryWrite(failed.id);api.retryWrite(failed.id);await h.settle();assert.equal(attempts,2);assert.equal(api.writeSnapshot().at(-1).state,'saved');assert.equal(api.writeSnapshot().at(-1).body,failed.body);
});
test('Identical in-flight requests coalesce, including a second save on the same entry',async()=>{
 const {h,api}=fixture(),hold=deferred();let calls=0;const send=()=>{calls++;return hold.promise;};
 const first=api.enqueueWrite('entries/entry/corrections/op',options({version:'old'}),send),second=api.enqueueWrite('entries/entry/corrections/op',options({version:'old'}),send);
 assert.equal(first,second);await h.settle();assert.equal(calls,1);hold.resolve({});await first;
});
test('Reload preserves the request without sending; an explicit Retry uses the original identity and bytes',async()=>{
 const stored=new Map(),first=fixture(stored);first.api.recoverWrites('fictional',async()=>({}));const hold=deferred();
 void first.api.enqueueWrite('retention/operations/original',options({action:'restore',item_id:'item'}),()=>hold.promise);
 const second=fixture(stored),calls=[];second.api.recoverWrites('fictional',async(path,init)=>{calls.push({path,init});return {};});
 assert.equal(calls.length,0);assert.equal(second.api.writeSnapshot()[0].state,'interrupted');second.api.retryWrite(second.api.writeSnapshot()[0].id);await second.h.settle();assert.equal(calls.length,1);assert.equal(calls[0].path,'retention/operations/original');assert.equal(calls[0].init.body,JSON.stringify({action:'restore',item_id:'item'}));hold.resolve({});await first.h.settle();
});
test('An asynchronous hand save stays pending through held polls and checks the 250ms interval with a controlled clock',async()=>{
 const {h,api}=fixture(),held=deferred();let polls=0,done=false;
 const operation=api.committedResult('drafts/item/hand-add',async()=>({state:'queued'}),async()=>{polls++;return held.promise;},()=>new Promise(resolve=>h.events.addEventListener('tick',resolve,{once:true}))).then(value=>{done=true;return value;});
 await h.settle();assert.equal(done,false);assert.equal(polls,0);h.events.dispatchEvent(new Event('tick'));await h.settle();assert.equal(polls,1);assert.equal(done,false);held.resolve({state:'added',result:{entry_id:'entry'}});assert.equal((await operation).state,'added');
 // The actual browser/API scheduler uses 250ms, without relying on real engine speed.
 const apiHook=hookHarness('../app/live/api.ts',{'./direct-writes':api,'../date-time':{clientTime:()=>({})}}, {Error,fetch:async(_url,init)=>new Response(JSON.stringify(init.method?{state:'applying'}:{state:'applied',result:{}}))});
 const applying=apiHook.exports.request('statements/statement/apply',{method:'POST'});await apiHook.settle();assert.equal(apiHook.timers.size,1);await apiHook.advance(249);assert.equal(api.writeSnapshot().at(-1).state,'saving');await apiHook.advance(1);assert.equal((await applying).state,'applied');assert.equal(api.writeSnapshot().at(-1).state,'saved');apiHook.close();
});
test('Every direct-action family uses the same pending pattern; structured refusals never become saved',async()=>{
 const {api}=fixture();
 for(const path of ['entries/id/corrections/op','drafts/id/hand-add','drafts/id/usual','drafts/id/bill','retention/operations/id','rules/operations/id','statements/id/apply','review/actions','setup/about','setup/opening','setup/accounts','setup/import','accounts','drafts/id/approve-accounts','drafts/id/receipt-choice','settings/name','settings/default-currency','accounts/currency'])assert.ok(api.writeDescription(path,'POST','{}'),path);
 assert.equal((await api.enqueueWrite('retention/operations/id',options({action:'restore'}),async()=>({refused:true,reason:'A statement now covers this entry.'}))).refused,true);assert.equal(api.writeSnapshot()[0].state,'failed');
});

test('First-run statement import stays pending until every committed session, with the same controlled poll interval',async()=>{
 const {h,api}=fixture();let polls=0;
 const pending=api.committedResult('setup/import',async()=>({sessions:[{id:'first',state:'applying'},{id:'second',state:'applied'}]}),async path=>{polls++;return {id:path.split('/').at(-1),state:'applied'};},()=>new Promise(resolve=>h.events.addEventListener('tick',resolve,{once:true})));
 await h.settle();assert.equal(polls,0);h.events.dispatchEvent(new Event('tick'));const result=await pending;assert.equal(polls,1,'Only the unfinished session is read; the committed session stays known');assert.ok(result.sessions.every(item=>item.state==='applied'));
});

test('An uncertain worker response retries by reading its existing result before sending another write',async()=>{
 const {h,api}=fixture(),flags=[];
 await assert.rejects(api.enqueueWrite('drafts/item/hand-add',options({proposal_hash:'same'}),async retry=>{flags.push(retry);throw Error('Reply lost');}),/Reply lost/);
 const failed=api.writeSnapshot()[0];api.retryWrite(failed.id);await h.settle();assert.deepEqual(flags,[false,true]);
 const methods=[],apiHook=hookHarness('../app/live/api.ts',{'./direct-writes':api,'../date-time':{clientTime:()=>({})}}, {Error,fetch:async(_url,init)=>{methods.push(init.method||'GET');return new Response(JSON.stringify({state:'added',finished_by:{actor:'user'},result:{entry_id:'already-committed'}}));}});
 const result=await apiHook.exports.resumeWrite('drafts/item/hand-add',options({proposal_hash:'same'}),12000,true);assert.equal(result.result.entry_id,'already-committed');assert.deepEqual(methods,['GET'],'Never repeat hand-add after its claim was consumed');apiHook.close();
});

test('An uncertain Settings response confirms matching committed metadata without a second write',async()=>{
 const methods=[],h=hookHarness('../app/live/api.ts',{'../date-time':{clientTime:()=>({})}}, {Error,fetch:async(_url,init)=>{methods.push(init.method||'GET');return new Response(JSON.stringify({name:'Fictional committed name',currency:'INR',commit_id:'committed'}));}});
 const result=await h.exports.resumeWrite('settings/name',options({name:'Fictional committed name'}),12000,true);assert.equal(result.replayed,true);assert.equal(result.commit_id,'committed');assert.deepEqual(methods,['GET']);h.close();
});

test('Committed-result polling preserves a confirmed-balance refusal for the existing recovery UI',async()=>{
 const refusal={kind:'confirmed_balance',message:'A confirmed balance would change.',account:'Assets:Bank:Willow'};
 const h=hookHarness('../app/live/api.ts',{'./direct-writes':fixture().api,'../date-time':{clientTime:()=>({})}},{Error,fetch:async()=>new Response(JSON.stringify({state:'failed',error:refusal.message,refusal}))});
 try{await assert.rejects(h.exports.resumeWrite('drafts/item/usual',options({rule_id:'rule'})),error=>error instanceof h.exports.ApiError&&JSON.stringify(error.refusal)===JSON.stringify(refusal));}finally{h.close();}
});


test('New account declarations and decisions keep unrelated failed requests available for exact Retry',async()=>{
 for(const path of ['setup/accounts','accounts','review/actions']){
  const {api}=fixture();const original={label:'Willow original',name:'Willow original',entry_id:'first'};
  await assert.rejects(api.enqueueWrite(path,options(original),async()=>{throw Error('Fictional uncertain reply');}));const first=api.writeSnapshot()[0];
  await api.enqueueWrite(path,options({label:'Willow next',name:'Willow next',entry_id:'second'}),async()=>({}));
  assert.equal(api.writeSnapshot().find(item=>item.id===first.id)?.body,JSON.stringify(original),path);assert.equal(api.writeSnapshot().find(item=>item.id===first.id)?.state,'failed',path);
 }
});
