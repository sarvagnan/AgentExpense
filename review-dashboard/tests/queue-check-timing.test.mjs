import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {hookHarness} from './hook-harness.mjs';

test('Ledger check arrival respects a held Queue snapshot and leaves its default question detail open',()=>{
 const element=(type,props)=>({type,props}),nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 const source=readFileSync(new URL('../app/live/drafts.tsx',import.meta.url),'utf8'),dependencies={};
 for(const match of source.matchAll(/from ['"]([^'"]+)['"]/g))if(match[1]!=='react')dependencies[match[1]]={};
 let held=true,snapshot,query='';
 Object.assign(dependencies,{
  'react/jsx-runtime':{jsx:element,jsxs:element,Fragment:'fragment'},
  '../ui':{useQueryState:(name,initial)=>[name==='draft'?query:initial,()=>{}],Icon:'icon',PageHeader:'header',plural:()=>'',money:()=>''},
  '../data':{dashboard:{meta:{ledger:{defaultCurrency:'USD'},accounts:{}}}},
  './api':{isRemoved:()=>false},'./agent-status':{agentBlocked:()=>false,AgentNotice:'agent-notice'},
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
  assert.equal(detail()?.props.draft.id,draft.id);
  live.ledgerCheck={available:true,checking:false,item:{id:'ledger-check',title:'An entry doesn’t balance'}};
  h.render();
  assert.equal(detail()?.props.draft.id,draft.id,'The person’s question detail stays mounted while focus is held');
  assert.equal(nodes(h.current).filter(n=>n.type==='a'&&n.props.className==='draft-row').length,1,'No row is inserted under the held pointer');
  held=false;h.render();
  assert.ok(nodes(h.current).some(n=>n.type==='ledger-check-detail'),'The check becomes visible after interaction ends');
  assert.equal(nodes(h.current).filter(n=>n.type==='a'&&n.props.className==='draft-row').length,2);
  // Holding row insertion/selection must not freeze an already-open check's
  // progress. WebKit retains focus in the workspace after Ask the agent.
  snapshot={drafts:live.drafts,statements:live.statements,ledgerCheck:live.ledgerCheck};held=true;
  live.ledgerCheck={...live.ledgerCheck,item:{...live.ledgerCheck.item,fix:{state:'ready',draft_id:'b'.repeat(32)}}};
  h.render();
  assert.equal(nodes(h.current).find(n=>n.type==='ledger-check-detail').props.status.item.fix?.state,'ready','An open check detail follows the live proposal while its row stays held');
  // Status and drafts are separate HTTP reads: the ready link can precede the
  // draft-list transition. Focus on opening the link must not hold that old row.
  query='b'.repeat(32);
  const fix={...draft,id:query,state:'working',ledger_fix:{state:'working'},correction:{entry_id:'c'.repeat(16)}};
  live.drafts=[fix];snapshot={...snapshot,drafts:[fix]};
  h.render();assert.equal(detail().props.draft.state,'working');
  live.drafts=[{...fix,state:'question',ledger_fix:{state:'ready'}}];h.render();
  assert.equal(detail().props.draft.state,'question','An explicitly opened fix follows its live draft while rows remain held');
 }finally{h.close();}
});
