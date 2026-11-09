import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {hookHarness} from './hook-harness.mjs';
for(const newer of [false,true])test(`An import replay publishes its real sessions without another POST${newer?' and keeps newer files for Continue':''}`,async()=>{
 let posts=0,done=0,completion;const session={id:'a'.repeat(32),state:'found',created_at:'2026-11-01',original_name:'Fictional.csv',plan:{items:[]},statement:{account:'Assets:Bank:Willow',currency:'USD',rows:[]}},extra={...session,id:'b'.repeat(32),original_name:'Newer.csv'};
 const jsx=(type,props)=>({type,props}),nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 const h=hookHarness('../app/first-run.tsx',{
  './styles/first-run.css':{},'./data':{dashboard:{meta:{ledger:{id:'fictional',defaultCurrency:'USD'},accounts:{}}},subscribeData:()=>()=>{},dataVersion:()=>0,reloadData:async()=>{}},'./ui':{Icon:'icon',dayLabel:x=>x,money:()=>'',useAppearance:()=>({})},'./settings-dialog':{CurrencyOptions:'currencies'},
  './live/api':{request:async()=>{posts++;return {};}},'./live/statements-api':{statementsApi:{list:async()=>({sessions:newer?[session,extra]:[session]})}},'./live/use-drafts':{statementNeedsAttention:()=>true},'./live/ledger-check':{useLedgerCheckStatus:()=>[{item:null},()=>{}],ledgerCheckBlock:()=>null},'./date-time':{},'./formatting':{},'./live/interaction':{useInteractionSnapshot:value=>[value,{},()=>{}]},'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'},
 },{localStorage:{getItem:()=>null,setItem(){}}});
 try{h.render(m=>m.Import({title:null,onBack(){},onDone(result){completion=result;done++;}}));await h.settle();h.events.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{path:'setup/import',body:JSON.stringify({sessions:[session.id]}),result:{sessions:[{...session,state:'applied',result:{commit_id:'committed'}}]}}}));await h.settle();
  if(newer){assert.equal(done,0);const continuation=nodes(h.current).find(node=>node.type==='button'&&node.props.children?.props?.text==='Continue');assert.ok(continuation);continuation.props.onClick();await h.settle();}
  assert.equal(done,1);assert.equal(posts,0);assert.equal(completion.unsaved,newer?1:0);
 }finally{h.close();}
});
test('A terminal import after setup has committed offers Continue without another write',async()=>{
 let posts=0,done=0;
 const session={id:'a'.repeat(32),state:'found',created_at:'2026-11-01',original_name:'Fictional.csv',plan:{items:[]},statement:{account:'Assets:Bank:Willow',currency:'USD',rows:[]}};
 const jsx=(type,props)=>({type,props}),nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 const h=hookHarness('../app/first-run.tsx',{
  './styles/first-run.css':{},'./data':{dashboard:{meta:{ledger:{id:'fictional',defaultCurrency:'USD'},accounts:{}}},subscribeData:()=>()=>{},dataVersion:()=>0,reloadData:async()=>{}},'./ui':{Icon:'icon',dayLabel:x=>x,money:()=>'',useAppearance:()=>({})},'./settings-dialog':{CurrencyOptions:'currencies'},
  './live/api':{request:async path=>{if(path==='setup/import'){posts++;throw Error('A statement could not be saved.');}if(path==='setup')return {fresh:false};return {};}},
  './live/statements-api':{statementsApi:{list:async()=>({sessions:[session]}),get:async()=>({...session,state:'failed',error:'A statement could not be saved.'})}},'./live/use-drafts':{statementNeedsAttention:()=>true},'./live/ledger-check':{useLedgerCheckStatus:()=>[{item:null},()=>{}],ledgerCheckBlock:()=>null,ledgerCheckApi:{status:async()=>({item:null})}},'./date-time':{},'./formatting':{},
  './live/interaction':{useInteractionSnapshot:value=>[value,{},()=>{}]},'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'},
 },{localStorage:{getItem:()=>null,setItem(){}}});
 try{h.render(m=>m.Import({title:null,onBack(){},onDone(){done++;}}));await h.settle();nodes(h.current).find(node=>node.type==='button'&&node.props.children?.props?.children==='Finish with 1 read file').props.onClick();await h.settle();assert.ok(JSON.stringify(h.current).includes('A statement could not be saved.'));const continuation=nodes(h.current).find(node=>node.type==='button'&&node.props.children?.props?.text==='Continue');assert.ok(continuation,'Continue must not submit setup/import again');continuation.props.onClick();await h.settle();assert.equal(posts,1);assert.equal(done,1);}finally{h.close();}
});
test('An opening form restored on another day reads its earlier committed operation before submitting newer rows',async()=>{
 let posts=0,reads=0,done=0;const row={key:'one',kind:'bank',label:'Willow',currency:'USD',balance:'100.00'},attempt={id:'a'.repeat(32),signature:JSON.stringify({start:'2026-11-08',accounts:[{kind:'bank',label:'Willow',currency:'USD',balance:'100.00'}]})};
 const jsx=(type,props)=>({type,props}),nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 const h=hookHarness('../app/first-run.tsx',{
  './styles/first-run.css':{},'./data':{dashboard:{meta:{ledger:{id:'fictional',defaultCurrency:'USD',locale:'en-US'},accounts:{}}},reloadData:async()=>{}},'./ui':{Icon:'icon',dayLabel:x=>x,money:()=>''},'./settings-dialog':{CurrencyOptions:'currencies'},'./live/api':{request:async(path,options)=>{if(options?.method==='POST'){posts++;return {};}reads++;return {commit_id:'committed',start:'2026-11-08'};}},'./live/statements-api':{},'./live/use-drafts':{},'./live/ledger-check':{},'./date-time':{today:()=>'2026-11-09'},'./formatting':{minorUnits:()=>2},
  './live/interaction':{useActionGuard:()=>({begin:()=>0,unchanged:()=>true,interaction:{}})},'./live/retained-state':{useRetainedState:()=>[attempt,()=>{}],clearRetainedPrefix(){}},'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'},
 });
 try{h.render(m=>m.Today({title:null,main:'USD',rows:[row],setRows(){},onBack(){},onDone(){done++;}}));await h.settle();nodes(h.current).find(node=>node.type==='button'&&node.props.children?.props?.children==='Finish setup').props.onClick();await h.settle();assert.equal(posts,0);assert.equal(reads,1);assert.equal(done,0,'Different restored values need an explicit Continue');const continuation=nodes(h.current).find(node=>node.type==='button'&&node.props.children?.props?.text==='Continue');assert.ok(continuation);continuation.props.onClick();await h.settle();assert.equal(done,1);assert.equal(posts,0);}finally{h.close();}
});

test('An untouched opening row follows the main currency without creating a recovery copy',async()=>{
 let edits=0,initializations=0;const row={key:'one',kind:'bank',label:'',currency:'USD',balance:''};const jsx=(type,props)=>({type,props});
 const h=hookHarness('../app/first-run.tsx',{
  './styles/first-run.css':{},'./data':{dashboard:{meta:{ledger:{id:'fictional',defaultCurrency:'USD',locale:'en-US'},accounts:{}}},reloadData:async()=>{}},'./ui':{Icon:'icon',dayLabel:x=>x,money:()=>''},'./settings-dialog':{CurrencyOptions:'currencies'},'./live/api':{request:async(path,options)=>{if(options?.method==='POST'){posts++;return {};}reads++;return {commit_id:'committed',start:'2026-11-08'};}},'./live/statements-api':{},'./live/use-drafts':{},'./live/ledger-check':{},'./date-time':{today:()=>'2026-11-09'},'./formatting':{minorUnits:()=>2},
  './live/interaction':{useActionGuard:()=>({begin:()=>0,unchanged:()=>true,interaction:{}})},'./live/retained-state':{useRetainedState:()=>[null,()=>{}],clearRetainedPrefix(){}},'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'},
 });
 try{h.render(m=>m.Today({title:null,main:'EUR',rows:[row],setRows(){edits++;},initializeRows(update){initializations++;assert.equal(update([row])[0].currency,'EUR');},onBack(){},onDone(){}}));await h.settle();assert.equal(edits,0);assert.equal(initializations,1);}finally{h.close();}
});
