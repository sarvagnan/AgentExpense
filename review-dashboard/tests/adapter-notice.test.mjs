import './test-isolation.mjs';
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createTestServer} from './test-isolation.mjs';

test('shared agent notices show selected login, identity, API setup and isolation opt-in',async()=>{
  const server=await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
  try {
    const {AgentNotice,agentBlocked}=await server.ssrLoadModule('/app/live/agent-status.tsx');
    const render=(status,settings=false)=>renderToStaticMarkup(createElement(AgentNotice,{status,settings}));
    for(const id of ['codex','claude','pi','opencode']){
      const status={state:'signed out',reason:null,adapter:{id,display_name:id,pinned_version:'fixture',isolation:id!=='opencode'},model:'fixture-model',effort:'high',login_command:'make agent-login'+(id==='codex'?'':' ADAPTER='+id)};
      const html=render(status);assert.ok(html.includes(status.login_command),html);assert.equal(agentBlocked(status),true);
      const ready=render({...status,state:'ready',isolation_warning:id==='opencode'?'Full agent isolation is not enforced; machine opt-in is enabled.':null},true);
      assert.ok(ready.includes('fixture-model'));assert.ok(ready.includes('high'));
      if(id==='opencode')assert.ok(ready.includes('machine opt-in is enabled'));
    }
    for(const id of ['openai','anthropic']){
      const html=render({state:'signed out',reason:null,adapter:{id,display_name:id}});
      assert.ok(html.includes('API key environment variable or key file'));assert.ok(!html.includes('<code>make agent-login'));
    }
  } finally {await server.close();}
});
