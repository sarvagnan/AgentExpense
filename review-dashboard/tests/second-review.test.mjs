import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {deferred,hookHarness} from './hook-harness.mjs';

const post=body=>({method:'POST',body:JSON.stringify(body)});
function writes(stored=new Map()){
 const storage={getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)};
 const h=hookHarness('../app/live/direct-writes.ts',{}, {Error,Date,location:{pathname:'/',search:''},sessionStorage:storage});
 return {h,api:h.exports,stored};
}
test('Recovered worker Retry forwards acceptance and waiting, releasing later writes',async()=>{
 const {h,api,stored}=writes(),held=deferred();let polls=0,sent=false;
 stored.set('ledger-direct-writes-fictional',JSON.stringify([{id:'recovered',path:'drafts/item/hand-add',method:'POST',body:'{}',state:'saving',attempts:1,createdAt:Date.now()}]));
 api.recoverWrites('fictional',(path,_options,accepted,waiting)=>api.committedResult(path,async()=>({state:'queued'}),async()=>++polls>480?held.promise:{state:'working'},async()=>{},undefined,accepted,waiting));
 const first=api.retryWrite('recovered');const second=api.enqueueWrite('settings/name',post({name:'later'}),async()=>{sent=true;return {};});
 await h.settle();assert.equal(sent,true);assert.equal(api.writeSnapshot().find(x=>x.id==='recovered').state,'waiting');
 held.resolve({state:'added',result:{commit_id:'committed'}});await Promise.all([first,second]);h.close();
});
for(const path of ['drafts/item/hand-add','setup/import'])test(`A failed result poll stays waiting and eventually commits: ${path}`,async()=>{
 const {api}=writes();let polls=0,waiting=0;const pauses=[];const queued={state:'queued',id:'item'},added={state:path==='setup/import'?'applied':'added',id:'item',result:{commit_id:'committed'}};
 const result=await api.committedResult(path,async()=>path==='setup/import'?{sessions:[queued]}:queued,async()=>{if(++polls===1)throw Error('Fictional offline poll');return added;},async slow=>{pauses.push(slow);},undefined,()=>{},()=>waiting++);
 assert.deepEqual([...pauses],[false,true],'Read retries switch from 250 ms to one second after a failure');assert.ok(waiting);assert.equal(path==='setup/import'?result.sessions[0].state:result.state,added.state);
});
test('Only a known terminal worker refusal authorizes Hand reclaim',async()=>{
 const {api}=writes();await assert.rejects(api.committedResult('drafts/item/hand-add',async()=>({state:'editing',error:'Check this again.'}),async()=>{},async()=>{}),error=>error.terminal===true);
});
test('Statement Apply holds later metadata writes but cancellation bypasses it and ends quietly',async()=>{
 const {h,api}=writes(),held=deferred();let sent=false,polls=0;
 const apply=api.enqueueWrite('statements/item/apply',post({}),(_retry,accepted,waiting)=>api.committedResult('statements/item/apply',async()=>({state:'applying'}),async()=>++polls>480?held.promise:{state:'applying'},async()=>{},undefined,accepted,waiting)).catch(error=>error);
 const later=api.enqueueWrite('settings/name',post({name:'later'}),async()=>{sent=true;return {};});await h.settle();assert.equal(sent,false);assert.equal(api.writeSnapshot()[0].state,'waiting');
 await api.enqueueWrite('statements/item/cancel',post({}),async()=>{api.markApplyCancellation('statements/item');held.resolve({state:'review',error:null});return {state:'review',error:null};});
 const result=await apply;assert.equal(result.applyCancelled,true);assert.equal(api.writeSnapshot()[0].state,'cancelled');assert.match(api.writeSnapshot()[0].reason,/Apply cancelled.*checklist is kept/);await later;assert.equal(sent,true);h.close();
});
test('A review result with an error stays a terminal refusal even after cancellation',async()=>{
 const {api}=writes();api.markApplyCancellation?.('statements/item');await assert.rejects(api.committedResult('statements/item/apply',async()=>({state:'review',error:'The printed total changed.'}),async()=>{},async()=>{}),/printed total changed/);
});
test('Kept form fields and their version clear as one group, never partially',async()=>{
 const {api,stored}=writes(new Map([['form','newer'],['version','old-version']]));
 await api.enqueueWrite('settings/name',{...post({name:'submitted'}),clearRetained:{form:'submitted',version:'old-version'}},async()=>({}));
 assert.equal(stored.get('form'),'newer');assert.equal(stored.get('version'),'old-version');
});
test('A recovery snapshot includes absent fields so later typing cannot lose its old version',()=>{
 const h=hookHarness('../app/live/retained-state.ts',{'../data':{dashboard:{meta:{ledger:{id:'fictional'}}}}},{sessionStorage:{getItem:key=>key.endsWith('version')?'old-version':null}});
 const snapshot=h.exports.retainedSnapshot(['form','version']);assert.equal(snapshot['ledger-form-fictional-form'],null);assert.equal(snapshot['ledger-form-fictional-version'],'old-version');h.close();
});
test('A form mounted before recovery adopts its recovered owner on publication',()=>{
 const items=[];const h=hookHarness('../app/live/pending-actions.tsx',{'./add-visit':{},'../data':{},'./api':{},'./direct-writes':{subscribeWrites:()=>()=>{},writeSnapshot:()=>items},'react/jsx-runtime':{}});
 h.render(m=>m.useWriteOwner('settings'));items.push({state:'interrupted',recovered:true,owner:{id:'recovered-owner',key:'settings'}});assert.equal(h.render().id,'recovered-owner');h.close();
});
test('A declaration slot replays its job without assigning over a newer statement account choice',async()=>{
 const assigned=[],account='Assets:Bank:Newer',session={id:'session',state:'currency_mismatch',file_currency:'EUR'};
 const job={id:'declaration',state:'saved',body:JSON.stringify({name:'Older declaration',kind:'card',currency:'EUR'}),clearRetained:{'ledger-form-fictional-statement-account-session-account':null}};
 const jsx=(type,props)=>({type,props}),nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 const h=hookHarness('../app/live/statement-account.tsx',{'../data':{dashboard:{meta:{ledger:{id:'fictional'},accounts:{[account]:{kind:'bank',currency:'EUR'}}}},reloadData:async()=>{}},'./api':{},'./direct-writes':{writeSnapshot:()=>[job],retryWrite:async()=>({account:'Liabilities:Credit Cards:Older declaration'})},'./statements-api':{statementsApi:{assign:async(id,target)=>{assigned.push(target);return session;}}},'react/jsx-runtime':{jsx,jsxs:jsx}});
 h.render(m=>m.StatementAccount({session,onSaved(){}}));nodes(h.current).find(node=>node.type==='button'&&String(node.props.children).startsWith('Add an account in')).props.onClick();await h.settle();nodes(h.current).find(node=>node.type==='input'&&node.props.maxLength===100).props.onChange({target:{value:'Older declaration'}});nodes(h.current).find(node=>node.props?.id==='statement-account-session').props.onChange({target:{value:account}});await h.settle();await nodes(h.current).find(node=>node.type?.name==='ActionFeedback').props.onRetry(job);await h.settle();assert.deepEqual(assigned,[]);assert.equal(nodes(h.current).find(node=>node.props?.id==='statement-account-session').props.value,account);h.close();
});
