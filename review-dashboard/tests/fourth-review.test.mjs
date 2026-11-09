import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {deferred,hookHarness} from './hook-harness.mjs';
const post=body=>({method:'POST',body:JSON.stringify(body)});
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body});
function client(fetch,stored=new Map()){
 const storage={getItem:k=>stored.get(k)??null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)};
 const d=hookHarness('../app/live/direct-writes.ts',{}, {Error,TypeError,Date,location:{pathname:'/expenses/queue/',search:''},sessionStorage:storage});
 const a=hookHarness('../app/live/api.ts',{'./direct-writes':d.exports,'../date-time':{clientTime:()=>({})}},{Error,TypeError,Date,fetch,location:{pathname:'/expenses/queue/',search:''}});
 const recover=()=>d.exports.recoverWrites('fictional',(path,options,accepted,waiting)=>a.exports.resumeWrite(path,options,120000,true,accepted,waiting));
 return {d,a,stored,recover,close(){d.close();a.close();}};
}
for(const reload of [false,true])test(`Claimed queued Hand: failed-before-server ${reload?'reload recovery':'slot Retry'} sends the exact second POST`,async()=>{
 const calls=[],held=deferred(),stored=new Map();let posts=0,accepted=false;
 const fetch=async(url,init={})=>{const path=url.replace('/expenses/api/','');calls.push({path,method:init.method||'GET',body:init.body});
  if(path==='drafts/item/hand-add'&&init.method==='POST'){
   posts++;if(posts===1){if(reload)return held.promise;throw new TypeError('Fictional network drop');}
   accepted=true;return response({id:'item',state:'queued',finished_by:{actor:'user'},claim:null},202);
  }
  return response(accepted?{id:'item',state:'added',finished_by:{actor:'user'},result:{entry_id:'kept',commit_id:'committed'}}:{id:'item',state:'queued',claim:{expires_at:'later'},finished_by:null});
 };
 const first=client(fetch,stored);first.recover();const body={claim_token:'original-claim',proposal_hash:'original-hash',approved_accounts:[]};
 const pending=first.a.exports.request('drafts/item/hand-add',post(body)).catch(e=>e);await first.a.settle();
 if(!reload)assert.match((await pending).message,/Could not reach/);
 const current=reload?client(fetch,stored):first;if(reload)current.recover();
 try{assert.equal(posts,1,'Reload never sends automatically');const id=current.d.exports.writeSnapshot()[0].id;const retry=current.d.exports.retryWrite(id);await current.a.settle();
  assert.equal(posts,2,'A queued draft with a claim is not an accepted Hand write');assert.deepEqual(calls.filter(x=>x.method==='POST').map(x=>x.body),[JSON.stringify(body),JSON.stringify(body)]);
  await current.a.advance(250);await retry;assert.equal(current.d.exports.writeSnapshot().find(x=>x.id===id).state,'saved');
 }finally{current.close();if(reload)first.close();}
});
for(const [path,value,shouldSend] of [
 ['drafts/item/hand-add',{state:'queued',finished_by:{actor:'user'},claim:null},false],
 ['drafts/item/hand-add',{state:'working',finished_by:{actor:'user'},claim:null},false],
 ['drafts/item/hand-add',{state:'working',finished_by:null,claim:{expires_at:'later'}},true],
 // The claim expired and the agent added the draft itself: that result is not this Hand write.
 ['drafts/item/hand-add',{state:'added',finished_by:null,result:{entry_id:'agent',commit_id:'agent'}},true],
 ['drafts/item/approve-accounts',{state:'queued',account_question:{proposal_hash:'original'}},true],
 ['drafts/item/approve-accounts',{state:'queued',account_question:null},false],
 ['drafts/item/approve-accounts',{state:'working',account_question:null},true],
 ['drafts/item/usual',{state:'queued'},true],['drafts/item/bill',{state:'working'},true],
 ['drafts/item/usual',{state:'queued',usual_payment:{request_hash:'original'}},true],
 ['drafts/item/bill',{state:'working',bill_request:{body:{proposal_hash:'original'}}},true],
 ['statements/item/apply',{state:'applying'},false],
])test(`Recovery acceptance is specific: ${path} ${JSON.stringify(value)}`,async()=>{
 let posts=0;const done=path.startsWith('statements/')?{state:'applied',result:{commit_id:'committed'}}:{state:'added',finished_by:{actor:'user'},result:{entry_id:'kept',commit_id:'committed'}};let reads=0;
 const g=client(async(_url,init={})=>{if(init.method==='POST'){posts++;return response(done);}return response(++reads===1?value:done);});
 try{const result=g.a.exports.resumeWrite(path,post({proposal_hash:'original'}),120000,true).catch(e=>e);await g.a.settle();assert.equal(posts,shouldSend?1:0);await g.a.advance(250);assert.ok((await result).result);}finally{g.close();}
});
test('A Cancel response lost after server cancellation still ends Apply as cancelled',async()=>{
 let cancelled=false;const g=client(async(url,init={})=>{const path=url.replace('/expenses/api/','');if(path.endsWith('/cancel')){cancelled=true;throw new TypeError('Fictional lost cancel response');}return response(path.endsWith('/apply')?{state:'applying'}:{state:cancelled?'review':'applying',error:null});});
 try{const apply=g.a.exports.request('statements/item/apply',post({})).catch(e=>e);await g.a.settle();await assert.rejects(g.a.exports.request('statements/item/cancel',post({})),/Could not reach/);await g.a.advance(250);assert.equal((await apply).applyCancelled,true);assert.equal(g.d.exports.writeSnapshot()[0].state,'cancelled');}finally{g.close();}
});
for(const path of ['statements/item/apply','drafts/item/hand-add','setup/import'])test(`404 ends an unavailable result without claiming failure or Saved: ${path}`,async()=>{
 let reads=0,later=false,savedEvents=0;const stored=new Map([['kept','newer words']]);const g=client(async(url,init={})=>{const p=url.replace('/expenses/api/','');if(init.method==='POST')return response(p==='setup/import'?{sessions:[{id:'item',state:'applying'}]}:p==='settings/name'?(later=true,{commit_id:'later'}):{state:p.startsWith('statements/')?'applying':'queued'});reads++;return response({detail:'Not found'},404);},stored);
 g.d.events.addEventListener('direct-write-saved',()=>savedEvents++);
 try{const first=g.a.exports.request(path,{...post({sessions:['item']}),clearRetained:{kept:'newer words'}}).catch(e=>e);await g.a.settle();const second=g.a.exports.request('settings/name',post({name:'later'}));await g.a.advance(250);
  const job=g.d.exports.writeSnapshot().find(x=>x.path===path);assert.equal(job.state,'interrupted','A missing result is unknown, not a failed save or a continuing poll');assert.equal(job.retryable,false);assert.match(job.reason,/may already.*saved|may already.*filed/i);assert.match(job.reason,/Queue|history|entry/);assert.equal(stored.get('kept'),'newer words');assert.equal(later,true,'A permanent missing result releases the FIFO');await first;await second;await g.a.advance(10000);assert.equal(reads,1);assert.equal(savedEvents,1,'Only the known later Settings commit gets Saved');
 }finally{g.close();}
});
test('Import keeps already committed sessions without rereading a purged saved record',async()=>{
 const d=hookHarness('../app/live/direct-writes.ts',{}, {Error,location:{pathname:'/',search:''},sessionStorage:{getItem:()=>null,setItem(){}}});const reads=[];
 try{const result=await d.exports.committedResult('setup/import',async()=>({sessions:[{id:'saved',state:'applied',result:{commit_id:'known'}},{id:'pending',state:'applying'}]}),async path=>{reads.push(path);if(path==='statements/saved'){if(reads.filter(x=>x===path).length===1)throw Object.assign(Error('gone'),{status:404});return {id:'saved',state:'applied',result:{commit_id:'known'}};}return {id:'pending',state:'applied',result:{commit_id:'later'}};},async()=>{});assert.deepEqual(reads,['statements/pending']);assert.equal(result.sessions[0].result.commit_id,'known');}finally{d.close();}
});
test('already_filed says another session filed a statement, not that an entry failed',async()=>{
 const g=client(async()=>response({state:'already_filed',error:null}));try{await assert.rejects(g.a.exports.request('statements/item/apply',post({})),e=>/statement.*another session|another session.*statement/i.test(e.message)&&/history|reconciliation/.test(e.message)&&!e.message.includes('entry was not saved'));}finally{g.close();}
});

function statementView(){
 const lists=[],applies=[],announcements=[],frames=[],stored=new Map();let selected='s'.repeat(32);
 const row={serial:'row',date:'2026-11-02',description:'Fern lunch',amount:'10.00',direction:'Debit',page:1};
 const session=(id=selected,state='review')=>({id,state,created_at:'2026-11-05',updated_at:'2026-11-05T10:00:00',original_name:id+'.pdf',error:null,statement:{kind:'card',currency:'USD',account:'Liabilities:Card',accountLabel:'Fern Card',periodStart:'2026-10-01',periodEnd:'2026-10-31',totalDue:'10',previousBalance:'0',purchasesCharges:'10',paymentsCredits:'0',rows:[row]},checks:[],plan:{items:[{row,rule:'exact',entry_id:'e'.repeat(16),needs_user:false,candidates:[]}],unmatched:[]}});
 const jsx=(type,props)=>({type,props}),nodes=n=>!n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(nodes):[n,...nodes(n.props?.children)];
 const api={list:signal=>{const d=deferred();lists.push({...d,signal});return d.promise;},apply:id=>{const d=deferred();applies.push({...d,id});return d.promise;},get:async()=>{throw Error('unexpected get');},discard:async()=>{}};
 api.cancelApply=async id=>{applies.find(x=>x.id===id).reject(Object.assign(Error('cancelled'),{applyCancelled:true}));return session(id,'review');};
 const document=Object.assign(new EventTarget(),{hidden:false,querySelector:()=>null,querySelectorAll:()=>[],getElementById:()=>null,body:{}});
 const h=hookHarness('../app/live/statement-detail.tsx',{'../date-time':{formatCalendarDate:x=>x,formatTimestamp:x=>x},'../ui':{Dialog:'dialog',Icon:'icon',PanelHeader:'header',announce:s=>announcements.push(s),evidenceHref:()=>'',money:String,plural:(n,s)=>`${n} ${s}`,useQueryState:(key,value)=>key==='session'?[selected,x=>{selected=x;}]:[value,()=>{}]},'../data':{dashboard:{meta:{accounts:{}}},reloadData:async()=>{}},'./api':{api:{},ApiError:class extends Error{},isRemoved:()=>false},'./statement-account':{StatementAccount:'account'},'./statements-api':{statementsApi:api,statementHref:()=>'',filedHref:()=>''},'./context':{LiveDraftsContext:{value:null}},'./ledger-check':{ledgerCheckBlock:()=>null},'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'}},{document,performance:{getEntriesByType:()=>[]},localStorage:{getItem:k=>stored.get(k)??null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)},requestAnimationFrame:fn=>{frames.push(fn);return frames.length;},cancelAnimationFrame(){},innerHeight:900});
 h.render(m=>m.default({initialId:selected}));
 const all=()=>nodes(h.current),button=text=>all().find(x=>x.type==='button'&&x.props.children===text);
 return {h,api,lists,applies,session,announcements,all,button,select(id){selected=id;h.render();},async ready(){lists[0].resolve({sessions:[session('s'.repeat(32)),session('t'.repeat(32))],filed:[],accounts:[]});await h.settle();},async apply(){button('Review apply').props.onClick();h.render();button('Apply statement').props.onClick();await h.settle();}};
}
for(const ends of ['failure','success'])test(`Apply ${ends} stays with its session; other statements remain usable`,async()=>{
 const g=statementView();try{await g.ready();await g.apply();g.select('t'.repeat(32));await g.h.settle();assert.equal(g.button('Discard').props.disabled,false,'S1 pending must not disable S2');assert.equal(g.button('Review apply').props.disabled,false);if(ends==='failure')g.applies[0].reject(Error('Fictional S1 refusal'));else g.applies[0].resolve({...g.session('s'.repeat(32),'applied'),result:{changed:0,converted:0,added:0,end:'2026-10-31'}});await g.h.settle();assert.equal(g.all().some(n=>n.props?.role==='alert'&&JSON.stringify(n.props.children).includes('S1 refusal')),false);assert.ok(g.all().some(n=>n.props?.className==='statement-filename'&&n.props.children==='t'.repeat(32)+'.pdf'));assert.equal(g.button('Discard').props.disabled,false);}finally{g.h.close();}
});
test('Cancel is announced once when both Cancel and the held Apply settle',async()=>{
 const g=statementView();try{await g.ready();await g.apply();await g.h.advance(1500);g.lists[1].resolve({sessions:[g.session('s'.repeat(32),'applying')],filed:[],accounts:[]});await g.h.settle();g.button('Cancel queued apply').props.onClick();await g.h.settle();assert.equal(g.announcements.filter(s=>/cancelled/i.test(s)).length,1);}finally{g.h.close();}
});

test('A late mapping failure cannot attach itself to the newly selected statement',async()=>{
 const g=statementView();try{g.lists[0].resolve({sessions:[{...g.session('s'.repeat(32)),agent_verified:true,learning:{columns:[]} },g.session('t'.repeat(32))],filed:[],accounts:[]});await g.h.settle();const mapping=g.all().find(n=>n.type?.name==='MappingChoice');assert.ok(mapping);mapping.props.onBusy(true);g.select('t'.repeat(32));await g.h.settle();mapping.props.onError('Fictional earlier mapping failure');mapping.props.onBusy(false);await g.h.settle();assert.equal(g.all().some(n=>n.props?.role==='alert'&&JSON.stringify(n.props.children).includes('earlier mapping')),false);}finally{g.h.close();}
});
test('Ending S1 Apply cannot end an independent S2 action or move its failure',async()=>{
 const g=statementView(),second=deferred();try{await g.ready();await g.apply();g.select('t'.repeat(32));await g.h.settle();g.api.discard=()=>second.promise;g.button('Discard').props.onClick();await g.h.settle();assert.equal(g.button('Discard').props.disabled,true);g.applies[0].reject(Error('Fictional S1 failure'));await g.h.settle();assert.equal(g.button('Discard').props.disabled,true,'S2 remains busy until its own action ends');second.reject(Error('Fictional S2 refusal'));await g.h.settle();assert.equal(g.button('Discard').props.disabled,false);assert.equal(g.all().some(n=>n.props?.role==='alert'&&JSON.stringify(n.props.children).includes('S2 refusal')),true);}finally{g.h.close();}
});
test('A Cancel reply lost after Apply already confirmed cancellation adds no failure or second announcement',async()=>{
 const g=statementView(),reply=deferred();try{await g.ready();await g.apply();await g.h.advance(1500);g.lists[1].resolve({sessions:[g.session('s'.repeat(32),'applying')],filed:[],accounts:[]});await g.h.settle();g.api.cancelApply=()=>reply.promise;g.button('Cancel queued apply').props.onClick();await g.h.settle();g.applies[0].reject(Object.assign(Error('cancelled'),{applyCancelled:true}));await g.h.settle();reply.reject(Error('Fictional lost Cancel response'));await g.h.settle();assert.equal(g.announcements.filter(s=>/cancelled/i.test(s)).length,1);assert.equal(g.all().some(n=>n.props?.role==='alert'&&JSON.stringify(n.props.children).includes('lost Cancel')),false);}finally{g.h.close();}
});
test('Network errors and 5xx keep Apply waiting at the controlled slow interval, then commit',async()=>{
 let reads=0,later=false;const g=client(async(url,init={})=>{const path=url.replace('/expenses/api/','');if(init.method==='POST')return response(path==='settings/name'?(later=true,{commit_id:'later'}):{state:'applying'});if(++reads===1)throw new TypeError('Fictional offline read');if(reads===2)return response({},503);return response({state:'applied',result:{commit_id:'saved'}});});
 try{const apply=g.a.exports.request('statements/item/apply',post({}));await g.a.settle();const next=g.a.exports.request('settings/name',post({name:'later'}));await g.a.advance(250);assert.equal(g.d.exports.writeSnapshot()[0].state,'waiting');assert.equal(later,false);await g.a.advance(999);assert.equal(reads,1);await g.a.advance(1);assert.equal(reads,2);assert.equal(later,false);await g.a.advance(1000);await apply;await next;assert.equal(later,true);assert.ok(g.d.exports.writeSnapshot().every(x=>x.state==='saved'));}finally{g.close();}
});
test('Reload retains the unavailable outcome and never automatically retries a purged record',async()=>{
 let posts=0;const stored=new Map(),fetch=async(_url,init={})=>init.method==='POST'?(posts++,response({state:'applying'})):response({},404);const first=client(fetch,stored);first.recover();
 try{const request=first.a.exports.request('statements/item/apply',post({})).catch(e=>e);await first.a.settle();await first.a.advance(250);await request;const reason=first.d.exports.writeSnapshot()[0].reason;const second=client(fetch,stored);try{second.recover();const job=second.d.exports.writeSnapshot()[0];assert.equal(job.state,'interrupted');assert.equal(job.reason,reason);assert.match(reason,/may already.*saved/);assert.equal(job.retryable,false);second.d.exports.retryWrite(job.id);await second.a.settle();assert.equal(posts,1);}finally{second.close();}}finally{first.close();}
});

test('A Cancel reply lost after off-screen confirmation stays quiet after returning to its statement',async()=>{
 const g=statementView(),reply=deferred();try{
  await g.ready();await g.apply();await g.h.advance(1500);g.lists[1].resolve({sessions:[g.session('s'.repeat(32),'applying'),g.session('t'.repeat(32))],filed:[],accounts:[]});await g.h.settle();
  g.api.cancelApply=()=>reply.promise;g.button('Cancel queued apply').props.onClick();await g.h.settle();
  g.select('t'.repeat(32));await g.h.settle();g.applies[0].reject(Object.assign(Error('cancelled'),{applyCancelled:true}));await g.h.settle();
  g.select('s'.repeat(32));await g.h.settle();reply.reject(Error('Fictional lost Cancel response after returning'));await g.h.settle();
  assert.equal(g.all().some(n=>n.props?.role==='alert'&&JSON.stringify(n.props.children).includes('lost Cancel response')),false,'Known cancellation remains known even when its announcement was suppressed off screen');
 }finally{g.h.close();}
});
