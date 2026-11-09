import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {hookHarness} from './hook-harness.mjs';
const jsx=(type,props)=>({type,props});
const nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
test('A shelf/recovered opening completion clears its own earlier refusal and keeps newer rows',async()=>{
 let submitted,done=0;let rows;const row={key:'one',kind:'bank',label:'Willow',currency:'USD',balance:'100.00'};
 const h=hookHarness('../app/first-run.tsx',{
  './styles/first-run.css':{},'./data':{dashboard:{meta:{ledger:{id:'fictional',defaultCurrency:'USD',locale:'en-US'},accounts:{}}},reloadData:async()=>{}},'./ui':{Icon:'icon',dayLabel:x=>x,money:v=>String(v)},'./settings-dialog':{CurrencyOptions:'currencies'},
  './live/api':{request:async(_path,options)=>{submitted=JSON.parse(options.body);throw Error('Fictional opening refusal');}},'./live/statements-api':{},'./live/use-drafts':{},'./live/ledger-check':{},'./date-time':{today:()=>'2026-11-09'},'./formatting':{minorUnits:()=>2},'./live/interaction':{useActionGuard:()=>({begin:()=>0,unchanged:()=>true,interaction:{}})},'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'},
 });
 try{
  rows=[row];h.render(m=>m.Today({title:null,main:'USD',rows,setRows(){},onBack(){},onDone(){done++;}}));await h.settle();nodes(h.current).find(n=>n.type==='button'&&n.props.children?.props?.children==='Finish setup').props.onClick();await h.settle();assert.ok(nodes(h.current).some(n=>n.props?.role==='alert'));
  rows=[{...row,label:'Willow newer row'}];h.render();
  h.events.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{id:'opening-job',path:'setup/opening',body:JSON.stringify(submitted),previousReason:'Fictional opening refusal',result:{commit_id:'a'.repeat(40)}}}));await h.settle();
  assert.equal(nodes(h.current).some(n=>n.props?.role==='alert'),false,'Opening acknowledgement must not retain the old refusal');assert.equal(done,0,'Replay leaves Continue explicit');assert.equal(nodes(h.current).find(n=>n.props?.className==='field account-name').props.value,'Willow newer row');
 }finally{h.close();}
});
test('Committed page handlers are scoped, current, once per job and removed on unmount',async()=>{
 const h=hookHarness('../app/live/write-completion.ts');let calls=[];let scope='first';const render=m=>m.useWriteCompletion(w=>w.path===scope,w=>calls.push(scope+':'+w.id));h.render(render);
 const send=(path,id)=>h.events.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{id,path,body:'{}',result:{}}}));
 send('other','x');assert.equal(calls.length,0);send('first','one');send('first','one');assert.deepEqual(calls,['first:one']);scope='next';h.render();send('first','two');send('next','three');assert.deepEqual(calls,['first:one','next:three']);h.close();send('next','four');assert.equal(calls.length,2);
});
test('Committed error cleanup preserves an unrelated read or newer validation reason',()=>{
 const h=hookHarness('../app/live/write-completion.ts'),clear=h.exports.clearCommittedError,write={path:'settings/name',body:'{}',previousReason:'Old refused save'};
 assert.equal(clear('Could not save. Old refused save',write),'');assert.equal(clear('Newer validation reason',write),'Newer validation reason');assert.equal(clear('Read failed',{...write,previousReason:''}),'Read failed');h.close();
});

test('An older commit cannot clear a newer failed job with the same refusal text',async()=>{
 let snapshot=[];const h=hookHarness('../app/live/write-completion.ts',{'./direct-writes':{writeSnapshot:()=>snapshot}});
 h.render(m=>m.useCommittedError(write=>write.path==='settings/name'));
 snapshot=[{id:'first',path:'settings/name',body:'{}',state:'failed',reason:'Same refusal'}];h.current[1]('Same refusal');await h.settle();
 snapshot.push({id:'second',path:'settings/name',body:'{}',state:'failed',reason:'Same refusal'});h.current[1]('Same refusal');await h.settle();
 h.events.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{id:'first',path:'settings/name',body:'{}',previousReason:'Same refusal',result:{}}}));await h.settle();assert.equal(h.current[0],'Same refusal');
 h.events.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{id:'second',path:'settings/name',body:'{}',previousReason:'Same refusal',result:{}}}));await h.settle();assert.equal(h.current[0],'');h.close();
});


test('Coalesced declarations complete for their secondary mounted owner',()=>{
 const job={id:'shared',owner:{id:'first'},owners:[{id:'second'}],path:'accounts',body:'{}'};const h=hookHarness('../app/live/write-completion.ts',{'./direct-writes':{writeSnapshot:()=>[job]}});
 assert.ok(h.exports.writeOwnedBy({id:'shared',path:'accounts',body:'{}',owner:job.owner},{id:'second'}));assert.equal(h.exports.writeOwnedBy(job,{id:'unrelated'}),false);h.close();
});
