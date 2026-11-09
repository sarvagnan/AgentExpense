import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {deferred,hookHarness} from './hook-harness.mjs';

const options=body=>({method:'POST',body:JSON.stringify(body)});
function writes(){const h=hookHarness('../app/live/direct-writes.ts',{}, {Error,location:{pathname:'/expenses/add/',search:''},sessionStorage:{getItem:()=>null,setItem(){}}});return {h,api:h.exports};}
test('EUR, USD, EUR keeps the last choice after an intervening queued choice',async()=>{
 const {h,api}=writes(),held=deferred(),posted=[];
 const save=currency=>api.enqueueWrite('accounts/currency',options({account:'Assets:Bank:Willow',currency}),async()=>{posted.push(currency);if(posted.length===1)await held.promise;return {currency};});
 const first=save('EUR'),second=save('USD'),last=save('EUR');await h.settle();held.resolve();await Promise.all([first,second,last]);
 assert.deepEqual(posted,['EUR','USD','EUR']);
});
for(const [label,path,result] of [
 ['statement review','statements/item/apply',{state:'review',error:'The queued apply was cancelled.'}],
 ['statement already filed','statements/item/apply',{state:'already_filed',error:'This period is already filed.'}],
 ['import already filed','setup/import',{sessions:[{id:'item',state:'already_filed',error:'Already filed.'}]}],
 ['import could not read','setup/import',{sessions:[{id:'item',state:'couldnt_read',error:'The file could not be read.'}]}],
 ['draft editing','drafts/item/hand-add',{state:'editing',error:'Check the changed entry again.'}],
 ['trashed draft','drafts/item/hand-add',{state:'added',result:{entry_id:'entry'},trashed_at:'2026-11-01',error:'This item moved to Trash.'}],
 ['receipt without its committed result','drafts/item/receipt-choice',{state:'added',result:{entry_id:'entry'},receipt_reading:{state:'done'}}],
 ['receipt refusal','drafts/item/receipt-choice',{state:'added',result:{entry_id:'entry'},receipt_reading:{state:'question',refusal:{kind:'confirmed_balance',message:'A confirmed balance would change.'}}}],
])test(`A ${label} ends the committed wait without another poll`,async()=>{
 const {api}=writes();let pauses=0;
 await assert.rejects(api.committedResult(path,async()=>result,async()=>result,async()=>{if(++pauses>1)throw Error('Still polling a terminal result');}),error=>!error.message.includes('Still polling'));
 assert.equal(pauses,0);
});
test('A decision slot cannot show a stranger’s saved result or Retry',()=>{
 for(const state of ['saved','failed']){
  const items=[{id:'a',owner:'owner-a',scope:'review',path:'review/actions',body:'{}',label:'Review decision',state,reason:'A failed'}];
  const h=hookHarness('../app/live/pending-actions.tsx',{'./add-visit':{},'../data':{},'./api':{},'./direct-writes':{subscribeWrites:()=>()=>{},writeSnapshot:()=>items},'react/jsx-runtime':{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})}});
  const view=h.render(m=>m.ActionFeedback({scope:'review',owner:{id:'owner-b',key:'review:b'}}));
  assert.equal(view.props.children,undefined,`B must not show A’s ${state}`);h.close();
 }
});
test('A worker releases the page FIFO at acceptance, stays pending after the polling window, and settles itself',async()=>{
 const {h,api}=writes(),held=deferred(),calls=[];let polls=0;
 const first=api.enqueueWrite('drafts/item/hand-add',options({}),(_retry,accepted,waiting)=>api.committedResult('drafts/item/hand-add',async()=>{calls.push('accepted');return {state:'queued'};},async()=>{if(++polls>480)return held.promise;return {state:'working'};},async()=>{},undefined,accepted,waiting));
 const second=api.enqueueWrite('settings/name',options({name:'next'}),async()=>{calls.push('setting');return {};});
 await h.settle();await second;await h.settle(2000);
 assert.deepEqual(calls,['accepted','setting']);assert.equal(api.writeSnapshot()[0].state,'waiting');
 held.resolve({state:'added',result:{entry_id:'entry'}});await first;assert.equal(api.writeSnapshot()[0].state,'saved');
});
test('A newly mounted slot hides acknowledgements left by an earlier mount',()=>{
 const items=[{id:'a',scope:'settings',state:'saved',label:'Settings',path:'settings/name',body:'{}'}];
 const h=hookHarness('../app/live/pending-actions.tsx',{'./add-visit':{},'../data':{},'./api':{},'./direct-writes':{subscribeWrites:()=>()=>{},writeSnapshot:()=>items},'react/jsx-runtime':{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})}});
 const view=h.render(m=>m.ActionFeedback({scope:'settings'}));assert.equal(view.props.children,undefined);h.close();
});
test('Discard stays visible until committed, including a held DELETE; retry uses the same completion effects',async()=>{
 const held=deferred();let deletes=0;const direct=writes().api;
 const h=hookHarness('../app/live/api.ts',{'./direct-writes':direct,'../date-time':{clientTime:()=>({})}},{Error,Date,fetch:async(_url,init)=>{if(init.method==='DELETE'){if(++deletes===1){await held.promise;throw Error('Lost reply');}return new Response(null,{status:204});}return new Response('{}');}});
 const changed=[];h.events.addEventListener('item-removed',()=>changed.push('removed'));h.events.addEventListener('item-restored',()=>changed.push('restored'));
 const discarded=h.exports.api.discard('item');await h.settle();assert.equal(h.exports.isRemoved('item'),false,'Pending is not committed removal');
 held.resolve();await assert.rejects(discarded,/Lost reply/);assert.equal(h.exports.isRemoved('item'),false);
 await direct.retryWrite(direct.writeSnapshot()[0].id);await h.settle();assert.equal(h.exports.isRemoved('item'),true);assert.deepEqual(changed,['removed']);
 await h.exports.resumeWrite('drafts/item/restore',{method:'POST'},12000,true);assert.equal(h.exports.isRemoved('item'),false);assert.deepEqual(changed,['removed','restored']);h.close();
});
test('Recovery keeps a refusal reason and never offers an expired request as a fresh retry',async()=>{
 const stored=new Map(),ledger='fictional',now=Date.now();
 stored.set('ledger-direct-writes-'+ledger,JSON.stringify([
 {id:'refused',path:'retention/operations/refused',method:'POST',body:'{"action":"restore"}',state:'failed',reason:'A statement covers this entry.',retryable:false,createdAt:now},
 {id:'old',path:'settings/name',method:'POST',body:'{"name":"Old"}',state:'saving',createdAt:now-86400001}
 ]));
 const h=hookHarness('../app/live/direct-writes.ts',{}, {Error,Date,sessionStorage:{getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value)}});h.exports.recoverWrites(ledger,async()=>({}));
 assert.equal(h.exports.writeSnapshot().find(item=>item.id==='refused').reason,'A statement covers this entry.');assert.equal(h.exports.writeSnapshot().find(item=>item.id==='refused').retryable,false);assert.equal(h.exports.writeSnapshot().find(item=>item.id==='old'),undefined);h.close();
});
test('Committed saves clear exactly the submitted recovery values after unmount, retaining newer edits',async()=>{
 const stored=new Map([['form-a','"submitted"'],['form-b','"newer"']]),held=deferred();
 const h=hookHarness('../app/live/direct-writes.ts',{}, {Error,location:{pathname:'/',search:''},sessionStorage:{getItem:key=>stored.get(key),setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)}});
 const save=h.exports.enqueueWrite('settings/name',{...options({name:'submitted'}),clearRetained:{'form-a':'"submitted"','form-b':'"submitted"'}},()=>held.promise);await h.settle();held.resolve({});await save;assert.equal(stored.get('form-a'),'"submitted"');assert.equal(stored.get('form-b'),'"newer"');h.close();
});
test('The shelf hides only the job with a local slot and keeps an unrelated failed job reachable',()=>{
 const items=[{id:'a',scope:'settings',state:'failed',label:'A',path:'settings/name',body:'{}'},{id:'b',scope:'review',state:'failed',label:'B',path:'review/actions',body:'{}'}];
 const document=Object.assign(new EventTarget(),{querySelectorAll:()=>[{dataset:{actionId:'a'},getClientRects:()=>[{}]}],body:{}});
 const h=hookHarness('../app/live/pending-actions.tsx',{'./add-visit':{},'../data':{dashboard:{meta:{ledger:{id:'fictional'}}}},'./api':{},'./direct-writes':{subscribeWrites:()=>()=>{},writeSnapshot:()=>items,recoverWrites(){}},'react/jsx-runtime':{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})}},{document,MutationObserver:class{observe(){}disconnect(){}}});
 h.render(m=>m.PendingActions());h.render();const rows=h.current.props.children.props.children[1].props.children;assert.equal(rows.length,1);assert.equal(rows[0].props.children[0].props.item.id,'b');h.close();
});

test('A new checked hand attempt replaces the failed request for that same draft, leaving no stranger Retry behind',async()=>{const {api}=writes();await assert.rejects(api.enqueueWrite('drafts/item/hand-add',options({claim_token:'old'}),async()=>{throw Error('No response');}));await api.enqueueWrite('drafts/item/hand-add',options({claim_token:'renewed'}),async()=>({}));assert.equal(api.writeSnapshot().filter(item=>item.state==='failed').length,0);});
for(const path of ['accounts','setup/accounts'])test(`An uncertain ${path} declaration recognises committed metadata before another POST`,async()=>{
 const methods=[],account='Assets:Bank:Willow';
 const h=hookHarness('../app/live/api.ts',{'../date-time':{clientTime:()=>({})}},{Error,fetch:async(url,init)=>{methods.push(init.method||'GET');return Response.json(url.endsWith('/setup')?{accounts:[account]}:{account_currency:'USD',commit_id:'committed'});}});
 const result=await h.exports.resumeWrite(path,options({kind:'bank',name:'Willow',label:'Willow',currency:'USD'}),12000,true);assert.equal(result.account,account);assert.equal(result.replayed,true);assert.equal(result.commit_id,'committed');assert.deepEqual(methods,['GET','GET']);h.close();
});
test('An unmounted Settings save clears its submitted dirty flag after the stored value was acknowledged',()=>{
 const prefix='ledger-form-fictional-settings-',stored=new Map([[prefix+'dirty','{"name":true,"currency":false,"limits":false}']]);
 const h=hookHarness('../app/live/pending-actions.tsx',{'./add-visit':{},'../data':{dashboard:{meta:{ledger:{id:'fictional'}}},reloadData:async()=>{}},'./api':{},'./direct-writes':{subscribeWrites:()=>()=>{},writeSnapshot:()=>[],recoverWrites(){}},'react/jsx-runtime':{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})}},{sessionStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)},MutationObserver:class{observe(){}disconnect(){}}});
 h.render(m=>m.PendingActions());h.events.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{path:'settings/name',body:'{"name":"Saved"}',result:{},clearedRetained:[prefix+'name']}}));assert.equal(stored.has(prefix+'dirty'),false);h.close();
});
test('An in-flight DELETE keeps its old row through missing/trashed polls until the committed response',async()=>{
 const direct=writes().api,held=deferred(),record={id:'item',state:'editing',files:[],text:'Old words'};let deleting=false;
 const h=hookHarness('../app/live/api.ts',{'./direct-writes':direct,'../date-time':{clientTime:()=>({})}},{Error,Date,fetch:async(url,init)=>{if(init.method==='DELETE'){deleting=true;await held.promise;return new Response(null,{status:204});}return Response.json(url.includes('include_added')?(deleting?[]:[record]):{...record,trashed_at:deleting?'temporary':null});}});
 await h.exports.api.list();const removing=h.exports.api.discard('item');await h.settle();const listed=await h.exports.api.list();assert.equal(listed[0]?.id,'item');assert.equal((await h.exports.api.get('item')).trashed_at||null,null);assert.equal(h.exports.isRemoved('item'),false);held.resolve();await removing;assert.equal(h.exports.isRemoved('item'),true);assert.equal((await h.exports.api.list()).length,0);h.close();
});
test('The shared write completion path never emits mutation notifications for a result GET',async()=>{const h=hookHarness('../app/live/api.ts',{'../date-time':{clientTime:()=>({})}},{Error,fetch:async()=>Response.json({id:'item',state:'editing',files:[]})});let changes=0;h.events.addEventListener('drafts-changed',()=>changes++);await h.exports.api.get('item');assert.equal(changes,0);h.close();});
test('Draft autosaves do not run direct-write completion side effects',async()=>{const h=hookHarness('../app/live/api.ts',{'../date-time':{clientTime:()=>({})}},{Error,fetch:async()=>Response.json({id:'item',state:'editing',files:[]})});let changes=0;h.events.addEventListener('drafts-changed',()=>changes++);await h.exports.api.edit('item','new words',null);assert.equal(changes,0);h.close();});
test('A fresh new-rule form cannot adopt an earlier cancelled form’s pending owner',()=>{const items=[{id:'job',owner:{id:'old-mount',key:'rule:new'},state:'saving'}];const h=hookHarness('../app/live/pending-actions.tsx',{'./add-visit':{},'../data':{},'./api':{},'./direct-writes':{writeSnapshot:()=>items},'react/jsx-runtime':{}});const owner=h.render(m=>m.useWriteOwner('rule:new',false));assert.notEqual(owner.id,'old-mount');h.close();});
test('Coalesced requests retain both initiating owners instead of giving one a stranger’s job',async()=>{const {h,api}=writes(),held=deferred(),a={id:'a',key:'file:a'},b={id:'b',key:'file:b'};const first=api.enqueueWrite('setup/accounts',{...options({label:'Willow',kind:'bank'}),writeOwner:a},()=>held.promise),second=api.enqueueWrite('setup/accounts',{...options({label:'Willow',kind:'bank'}),writeOwner:b},()=>held.promise);assert.equal(first,second);assert.equal(api.writeSnapshot()[0].owner.id,'a');assert.ok(api.writeSnapshot()[0].owners?.some(owner=>owner.id==='b'));held.resolve({});await first;h.close();});
