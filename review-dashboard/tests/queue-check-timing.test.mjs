import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {hookHarness} from './hook-harness.mjs';

test('Queue selects nothing until requested and a held snapshot keeps the chosen detail open',()=>{
 const element=(type,props)=>({type,props}),nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 const source=readFileSync(new URL('../app/live/drafts.tsx',import.meta.url),'utf8'),dependencies={};
 for(const match of source.matchAll(/from ['"]([^'"]+)['"]/g))if(match[1]!=='react')dependencies[match[1]]={};
 let held=true,snapshot,query='',item='',check='';
 Object.assign(dependencies,{
  'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
  '../ui':{useQueryState:(name,initial)=>[name==='draft'?query:name==='item'?item:name==='check'?check:initial,(value)=>{if(name==='draft')query=value;if(name==='item')item=value;}],Icon:'icon',PageHeader:'header',plural:()=>'',money:()=>''},
  '../data':{dashboard:{meta:{ledger:{defaultCurrency:'USD'},accounts:{}}}},
  './statements-api':{statementHref:id=>'/expenses/queue/?session='+id},'./api':{isRemoved:()=>false},'./agent-status':{agentBlocked:()=>false,AgentNotice:'agent-notice'},
  './interaction':{useInteractionSnapshot:value=>{snapshot??=value;return [held?snapshot:value,{},()=>{held=false;}];}},
  './ledger-check':{LedgerCheckDetail:'ledger-check-detail',LEDGER_CHECK_HREF:'/expenses/queue/?check=ledger'},
  './use-drafts':{isRunning:()=>false},'../date-time':{formatTimestamp:x=>x},
 });
 const h=hookHarness('../app/live/drafts.tsx',dependencies);
 const draft={id:'a'.repeat(32),state:'question',text:'Question waiting',created_at:'2026-11-01',files:[],questions:[],result:null};
 const live={drafts:[draft],statements:[],ledgerCheck:{available:true,checking:true,item:null},online:true,agent:{state:'ready'},attention:1,refresh:async()=>{}};
 try{
  h.render(m=>m.default({live}));
  const detail=()=>nodes(h.current).find(n=>n.type?.name==='DraftDetail');
  assert.equal(detail(),undefined,'Opening Queue selects no draft');
  query=draft.id;h.render();
  assert.equal(detail()?.props.draft.id,draft.id);
  live.ledgerCheck={available:true,checking:false,item:{id:'ledger-check',title:'An entry doesn’t balance'}};
  h.render();
  assert.equal(detail()?.props.draft.id,draft.id,'The person’s question detail stays mounted while focus is held');
  assert.equal(nodes(h.current).filter(n=>n.type==='a'&&n.props.className==='draft-row').length,1,'No row is inserted under the held pointer');
  held=false;h.render();
  assert.equal(detail()?.props.draft.id,draft.id,'Arrival never replaces the selected draft');
  query='';h.render();assert.equal(detail(),undefined);
  assert.ok(!nodes(h.current).some(n=>n.type==='ledger-check-detail'),'An unselected ledger check never opens');
  check='ledger';h.render();
  assert.ok(nodes(h.current).some(n=>n.type==='ledger-check-detail'),'An explicit link opens the check');
  assert.equal(nodes(h.current).filter(n=>n.type==='a'&&n.props.className==='draft-row').length,2);
  // Holding row insertion/selection must not freeze an already-open check's
  // progress. WebKit retains focus in the workspace after Ask the agent.
  snapshot={drafts:live.drafts,statements:live.statements,ledgerCheck:live.ledgerCheck};held=true;
  live.ledgerCheck={...live.ledgerCheck,item:{...live.ledgerCheck.item,fix:{state:'ready',draft_id:'b'.repeat(32)}}};
  h.render();
  assert.equal(nodes(h.current).find(n=>n.type==='ledger-check-detail').props.status.item.fix?.state,'ready','An open check detail follows the live proposal while its row stays held');
  // Status and drafts are separate HTTP reads: the ready link can precede the
  // draft-list transition. Focus on opening the link must not hold that old row.
  check='';query='b'.repeat(32);
  const fix={...draft,id:query,state:'working',ledger_fix:{state:'working'},correction:{entry_id:'c'.repeat(16)}};
  live.drafts=[fix];snapshot={...snapshot,drafts:[fix]};
  h.render();assert.equal(detail().props.draft.state,'working');
  live.drafts=[{...fix,state:'question',ledger_fix:{state:'ready'}}];h.render();
  assert.equal(detail().props.draft.state,'question','An explicitly opened fix follows its live draft while rows remain held');
  query='';held=false;live.drafts=[draft];item=draft.id;h.render();live.drafts=[];h.render();assert.equal(item,'','A removed ?item= selection returns the phone to its list');
  live.statements=[{id:'statement',state:'saved',original_name:'Fictional statement',created_at:'2026-11-01'}];h.render();
  assert.equal(detail(),undefined);assert.ok(!nodes(h.current).some(n=>n.type?.name==='StatementDetail'),'A statement is never selected as a fallback');
 }finally{h.close();}
});

test('A failed receipt reading returns its saved payment to Needs you without pretending the payment failed',()=>{
 const source=readFileSync(new URL('../app/live/drafts.tsx',import.meta.url),'utf8'),dependencies={};
 for(const match of source.matchAll(/from ['"]([^'"]+)['"]/g))if(match[1]!=='react')dependencies[match[1]]={};
 dependencies['react/jsx-runtime']={jsx:()=>null,jsxs:()=>null,Fragment:'fragment'};
 const h=hookHarness('../app/live/drafts.tsx',dependencies);
 const saved={id:'a'.repeat(32),state:'added',text:'Fictional saved payment',created_at:'2026-11-01',files:[],receipt_reading:{state:'failed',message:'No receipt details changed.'}};
 try{
  let groups;h.render(module=>{groups=module.queueGroups([saved]);return null;});
  assert.deepEqual(JSON.parse(JSON.stringify(groups.map(group=>[group.name,group.items.map(item=>item.id)]))),[['Needs you',[saved.id]],['In progress',[]],['Saved for later',[]]]);
  assert.equal(groups[0].items[0].state,'added');
  h.render(module=>{groups=module.queueGroups([{...saved,receipt_reading:{state:'done'}}]);return null;});
  assert.equal(groups.flatMap(group=>group.items).length,0);
 }finally{h.close();}
});

test('Needs you total excludes an already saved payment with a failed receipt',()=>{
 const element=(type,props)=>({type,props}),nodes=n=>!n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(nodes):[n,...nodes(n.props?.children)];
 const source=readFileSync(new URL('../app/live/drafts.tsx',import.meta.url),'utf8'),dependencies={};
 for(const match of source.matchAll(/from ['"]([^'"]+)['"]/g))if(match[1]!=='react')dependencies[match[1]]={};
 Object.assign(dependencies,{'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},'../ui':{useQueryState:(_n,initial)=>[initial,()=>{}],Icon:'icon',PageHeader:'header',plural:()=>'',money:(v,c)=>`${v} ${c}`},'../data':{dashboard:{meta:{ledger:{defaultCurrency:'USD'},accounts:{}}}},'./statements-api':{statementHref:id=>'/expenses/queue/?session='+id},'./api':{isRemoved:()=>false},'./agent-status':{agentBlocked:()=>false},'./interaction':{useInteractionSnapshot:v=>[v,{},()=>{}]},'./ledger-check':{LedgerCheckDetail:'ledger-check-detail'},'./use-drafts':{isRunning:()=>false},'../date-time':{formatTimestamp:x=>x}});
 const h=hookHarness('../app/live/drafts.tsx',dependencies),base={created_at:'2026-11-01',files:[],questions:[],pieces:{fields:{amount:{source:'typed',value:'100'},currency:{source:'typed',value:'USD'}}}};
 const live={drafts:[{...base,id:'a'.repeat(32),state:'added',text:'Saved payment',receipt_reading:{state:'failed'}},{...base,id:'b'.repeat(32),state:'failed',text:'Unfinished addition'}],statements:[],ledgerCheck:null,online:true,agent:{state:'ready'},attention:2,refresh:async()=>{}};
 try{h.render(m=>m.default({live}));const heading=nodes(h.current).find(n=>n.type==='h2'&&n.props.className==='queue-heading');assert.match(JSON.stringify(heading),/100 USD/);assert.doesNotMatch(JSON.stringify(heading),/200 USD/);}finally{h.close();}
});

test('A resolved fix has a Resolved Queue label even in an older failed snapshot',()=>{
 const source=readFileSync(new URL('../app/live/drafts.tsx',import.meta.url),'utf8'),dependencies={};
 for(const match of source.matchAll(/from ['"]([^'"]+)['"]/g))if(match[1]!=='react')dependencies[match[1]]={};
 dependencies['react/jsx-runtime']={jsx:()=>null,jsxs:()=>null,Fragment:'fragment'};
 const h=hookHarness('../app/live/drafts.tsx',dependencies);
 try{for(const state of ['failed','question','working','queued'])assert.equal(h.exports.draftStatus({state,ledger_fix:{state:'resolved'}}),'Resolved');}finally{h.close();}
});
