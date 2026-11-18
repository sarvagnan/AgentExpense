import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {deferred,hookHarness} from './hook-harness.mjs';

test('Queue progress follows the current item and cursor, preserves ordered steps, and has no timer',async()=>{
 const reads=[],node=(type,props)=>({type,props}),nodes=tree=>!tree||typeof tree!=='object'?[]:Array.isArray(tree)?tree.flatMap(nodes):[tree,...nodes(tree.props?.children)];
 const h=hookHarness('../app/live/agent-progress.tsx',{
  'react/jsx-runtime':{jsx:node,jsxs:node},
  './api':{request:(url,{signal})=>{const read=deferred();reads.push({...read,url,signal});return read.promise;}},
 });
 let path='drafts/first',poll=[];
 const status=()=>nodes(h.current).find(n=>n.props.role==='status');
 const list=()=>nodes(h.current).filter(n=>n.type==='li');
 try{
  h.render(m=>m.AgentProgress({path,poll}));
  assert.equal(reads[0].url,'drafts/first/events?after=0');
  reads[0].resolve({after:100,events:[{type:'start',adapter:'codex',model:'fixture'},{type:'prompt'},
   {type:'lookup',tool:'entries_near',arguments:'{}',result_bytes:20}]});await h.settle();
  assert.equal(status().props.children.join(''),'1 lookup so far · Lookup · entries_near');
  assert.equal(nodes(h.current).find(n=>n.type==='summary').props.children,'Show steps');
  assert.deepEqual(list().map(n=>n.props.children[0]),['Run started','Prompt sent','Lookup · entries_near']);
  await h.advance(30000);assert.equal(reads.length,1,'No poll is scheduled by the step view');
  poll=[];h.render();assert.equal(reads[1].url,'drafts/first/events?after=100');
  poll=[];h.render();assert.equal(reads.length,2,'A new Queue snapshot does not overlap a held read');
  reads[1].resolve({after:200,events:[{type:'reconnecting'}]});await h.settle();
  assert.equal(status().props['data-reconnecting'],true);
  assert.equal(list().at(-1).props.children[0],'Provider reconnecting');
  poll=[];h.render();reads[2].resolve({after:300,events:[{type:'progress',text:'Connection resumed'}]});await h.settle();
  assert.equal(status().props['data-reconnecting'],false);
  poll=[];h.render();
  path='statements/second';h.render();await h.settle();
  assert.equal(reads[3].signal.aborted,true);
  assert.equal(reads[4].url,'statements/second/events?after=0');
  reads[3].resolve({after:400,events:[{type:'progress',text:'Obsolete step'}]});await h.settle();
  assert.equal(h.current,null,'A late response cannot appear on the next item');
  reads[4].resolve({after:40,events:[{type:'start'},{type:'progress',text:'Reading rows'}]});await h.settle();
  assert.equal(status().props.children.join(''),'0 lookups so far · Reading rows');
 }finally{h.close();}
});
