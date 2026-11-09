import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import {hookHarness} from './hook-harness.mjs';

const deferred=()=>{let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});return {promise,resolve,reject};};
const compile=file=>ts.transpileModule(readFileSync(new URL(file,import.meta.url),'utf8').replaceAll('import.meta.env.BASE_URL','"/"'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
function component(file,name,dependencies={}){
 const slots=[],effects=[];let index=0,dirty=false,tree,props={};const timers=new Map();let timer=0;
 const different=(a,b)=>!a||!b||a.length!==b.length||a.some((v,i)=>!Object.is(v,b[i]));
 const react={
  useState(initial){const i=index++;slots[i]??={value:typeof initial==='function'?initial():initial};return [slots[i].value,v=>{slots[i].value=typeof v==='function'?v(slots[i].value):v;dirty=true;}];},
  useRef(initial){return slots[index++]??={current:initial};},
  useCallback(fn,deps){const i=index++;if(!slots[i]||different(slots[i].deps,deps))slots[i]={deps,value:fn};return slots[i].value;},
  useEffect(fn,deps){const i=index++;if(!slots[i]||different(slots[i].deps,deps)){const old=slots[i];slots[i]={deps};effects.push(()=>{old?.cleanup?.();slots[i].cleanup=fn();});}},
 };
 const window=new EventTarget(),exports={};
 const jsx=(type,props)=>({type,props});
 const require=key=>{if(key==='react')return react;if(key.endsWith('/write-completion')){const result={};vm.runInNewContext(compile('../app/live/write-completion.ts'),{exports:result,require:key=>key==='react'?react:Object.entries(dependencies).find(([key])=>key.endsWith('/direct-writes'))?.[1]||{writeSnapshot:()=>[]},window});return result;}if(key==='react/jsx-runtime')return {jsx,jsxs:jsx,Fragment:'fragment'};if(key.endsWith('/retained-state')&&dependencies[key])return dependencies[key];if(key.endsWith('/retained-state'))return {useRetainedState:(_name,initial)=>{const [value,set]=react.useState(initial);return [value,set,set];},clearRetained(){},retainedSnapshot:()=>({}),hasRetained:()=>false};if(key.endsWith('/pending-actions'))return {ActionFeedback:()=>null,useWriteOwner:key=>({id:key,key})};if(key.endsWith('/direct-writes'))return dependencies[key]||{writeSnapshot:()=>[]};if(key.endsWith('/interaction')){const result={};vm.runInNewContext(compile('../app/live/interaction.ts'),{exports:result,require,document:window});return result;}return dependencies[key]||{};};
 vm.runInNewContext(compile(file),{exports,require,window,Event,CustomEvent,AbortController,location:{search:''},crypto,URLSearchParams,console,setInterval:(fn,ms)=>{const id=++timer;timers.set(id,{fn,ms});return id;},clearInterval:id=>timers.delete(id)});
 const render=()=>{index=0;dirty=false;tree=exports[name](props);while(effects.length)effects.shift()();return tree;};
 const walk=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(n=>walk(n)):[node,...walk(node.props?.children)];
 const all=()=>walk(tree);
 return {window,react,exports,timers,render(value=props){props=value;return render();},all,async settle(){for(let n=0;n<12;n++){await new Promise(r=>setImmediate(r));if(dirty)render();}},unmount(){slots.forEach(s=>s?.cleanup?.());}};
}
const walkText=node=>node==null||node===false?'':typeof node!=='object'?String(node):Array.isArray(node)?node.map(walkText).join(''):walkText(node.props?.children);
const item={id:'a'.repeat(32),kind:'entry',description:'Willow lunch',removed_at:'2026-11-04',expires_at:'2026-11-11',files:[]};
const ui={PageHeader:'header',Icon:'icon',Dialog:'dialog',plural:(n,one,many=one+'s')=>`${n} ${n===1?one:many}`};
test('Failed Settings, Cancel, reopen and slot Retry sends the exact failed job, never the current form',async()=>{
 const direct=hookHarness('../app/live/direct-writes.ts',{}, {Error,location:{pathname:'/',search:''},sessionStorage:{getItem:()=>null,setItem(){}}}).exports;
 const sent=[],announcements=[];let fail=true;
 const dependencies={
  './data':{dashboard:{meta:{ledger:{id:'fictional',name:'Original',defaultCurrency:'INR'},accounts:{}}},reloadData:async()=>{}},
  './live/direct-writes':direct,
  './live/api':{request:async(path,options={})=>path==='settings/retention'?{delete_days:7,trash_days:7,saved_hours:24}:direct.enqueueWrite(path,options,async()=>{sent.push(options.body);if(fail)throw Error('Fictional refused save');return {commit_id:'committed'};})},
  './ui':{Dialog:'dialog',Icon:'icon',useDocumentScrollLock(){},useAppearance:()=>['light',()=>{}],announce:text=>announcements.push(text),plural:(n,word)=>`${n} ${word}`},
  './live/retention':{},'./live/agent-status':{AgentNotice:'notice',agentIdentity:()=>''},'./live/update-notice':{UpdateNotice:'update',knownUpdateStatus:()=>({}),refreshUpdateStatus:async()=>{}},
 };
 let h=component('../app/settings-dialog.tsx','SettingsDialog',dependencies);h.render({onClose(){},agent:{state:'ready'}});await h.settle();h.all().find(n=>n.props?.id==='settings-name').props.onChange({target:{value:'Failed original name'}});await h.settle();h.all().find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await h.settle();assert.equal(direct.writeSnapshot()[0].state,'failed');button(h,'Cancel').props.onClick();h.unmount();
 h=component('../app/settings-dialog.tsx','SettingsDialog',dependencies);h.render({onClose(){},agent:{state:'ready'}});await h.settle();h.all().find(n=>n.props?.id==='settings-name').props.onChange({target:{value:'Separate newer edit'}});await h.settle();
 const slot=h.all().find(n=>n.type?.name==='ActionFeedback'),job=direct.writeSnapshot()[0];fail=false;await (slot.props.onRetry?slot.props.onRetry(job):direct.retryWrite(job.id));await h.settle();assert.equal(sent.length,2);assert.equal(sent[1],sent[0]);assert.equal(JSON.parse(sent[1]).name,'Failed original name');assert.equal(h.all().find(n=>n.props?.id==='settings-name').props.value,'Separate newer edit');assert.equal(direct.writeSnapshot().find(x=>x.id===job.id).state,'saved');h.unmount();
});
const buttons=h=>h.all().filter(n=>n.type==='button');
const button=(h,text)=>buttons(h).find(n=>n.props.children===text);
test('Rule slot Retry replays the failed job and keeps a separate newer description open',async()=>{
 let closed=0,retried=0,rebuilt=0;const job={id:'failed-rule',state:'failed',body:JSON.stringify({action:'edited',rule:{description:'Failed original'}})};
 const h=component('../app/rule-editor.tsx','RuleEditor',{
  './data':{dashboard:{meta:{ledger:{defaultCurrency:'EUR'},accounts:{'Assets:Bank:Willow':{kind:'bank',currency:'EUR'},'Expenses:Classes':{}}},transactions:[]}},
  './ui':{Dialog:'dialog',useDocumentScrollLock(){},money:()=>'',dayLabel:x=>x,monthLong:x=>x},'./date-time':{localDate:()=>'2026-11-01'},'./account-labels':{accountFieldLabel:x=>x},
  '../scripts/recurring-ui.mjs':{complexRule:()=>false,mainPostings:()=>[{amount:'20.00',account:'Expenses:Classes'}],payer:()=>({account:'Assets:Bank:Willow'}),varyingDetails:()=>({history:[]})},
  './live/rules-api':{useRuleOperation:()=>({owner:{id:'owner'},busy:false,error:'Failed',operate:async()=>{rebuilt++;return true;}})},
  './live/direct-writes':{writeSnapshot:()=>[job],retryWrite:async id=>{assert.equal(id,job.id);retried++;job.state='saved';return {}; }},
 });
 h.render({rule:{id:'rule',version:'old-version',description:'Original',postings:[],schedule:{every:'month',interval:1,'anchor-day':15},date:'2026-11-15'},onClose:()=>closed++,onSaved(){}});await h.settle();
 h.all().find(n=>n.props?.id==='rule-description').props.onChange({target:{value:'Separate newer description'}});await h.settle();await h.all().find(n=>n.type?.name==='ActionFeedback').props.onRetry(job);await h.settle();
 assert.equal(retried,1);assert.equal(rebuilt,0);assert.equal(closed,0);assert.equal(h.all().find(n=>n.props?.id==='rule-description').props.value,'Separate newer description');h.unmount();
});
test('Trash initial loading under the pointer cannot freeze an empty list',async()=>{
 const first=deferred();
 const h=component('../app/live/retention.tsx','default',{'./api':{request:async()=>first.promise},'../date-time':{formatTimestamp:x=>x},'../ui':ui});
 h.render();h.all()[0].props.onPointerEnter();await h.settle();first.resolve({items:[item]});await h.settle();
 assert.ok(button(h,'Restore'),'The first loaded rows must appear even if the pointer arrived before the response');h.unmount();
});
test('Queue initial session loading under the pointer cannot freeze empty rows',async()=>{
 const session={id:'b'.repeat(32),state:'saved',original_name:'Willow saved statement.pdf',created_at:'2026-11-04'};
 const h=component('../app/live/drafts.tsx','default',{'./api':{isRemoved:()=>false},'../ui':{...ui,useQueryState:key=>[key==='session'?session.id:'',()=>{}]},'../date-time':{formatTimestamp:x=>x},'./statements-api':{statementHref:()=>'/queue/'},'./agent-status':{agentBlocked:()=>false}});
 const live={drafts:[],statements:[],online:null,attention:0,agent:{state:'ready'}};h.render({live});
 h.all().find(n=>n.props?.className==='draft-workspace').props.onPointerEnter();await h.settle();
 h.render({live:{...live,online:true,statements:[session]}});await h.settle();
 assert.ok(h.all().some(n=>n.props?.className==='draft-row'),'Initial Queue rows must arrive while a session detail is already open');h.unmount();
});
test('Trash Restore invalidates a held pre-action list error',async()=>{
 let read=0;const old=deferred();
 const h=component('../app/live/retention.tsx','default',{'./api':{request:async path=>path==='trash'?(++read===2?old.promise:{items:read===1?[item]:[]}):({})},'../date-time':{clientTime:()=>({}),formatTimestamp:x=>x},'../data':{reloadData:async()=>{}},'../ui':ui});
 h.render();await h.settle();h.window.dispatchEvent(new Event('drafts-changed'));await h.settle();
 assert.equal(button(h,'Restore').props.disabled,false,'Background reads cannot disable the action');
 button(h,'Restore').props.onClick();await h.settle();old.reject(Error('Old item disappeared'));await h.settle();
 assert.equal(button(h,'Restore'),undefined);assert.equal(h.all().some(n=>n.props?.role==='alert'),false,'An obsolete read must not show an error for an item already gone');h.unmount();
});
test('Trash late list cannot resurrect a restored item',async()=>{
 let read=0;const old=deferred();
 const h=component('../app/live/retention.tsx','default',{'./api':{request:async path=>path==='trash'?(++read===2?old.promise:{items:read===1?[item]:[]}):({})},'../date-time':{clientTime:()=>({}),formatTimestamp:x=>x},'../data':{reloadData:async()=>{}},'../ui':ui});
 h.render();await h.settle();h.window.dispatchEvent(new Event('drafts-changed'));await h.settle();
 button(h,'Restore').props.onClick();await h.settle();assert.equal(button(h,'Restore'),undefined);
 old.resolve({items:[item]});await h.settle();assert.equal(button(h,'Restore'),undefined,'A stale list must not overwrite the completed Restore');h.unmount();
});
test('Trash Restore retries retain operation identity after a lost committed response',async()=>{
 let listed=[item];const operations=[];
 const h=component('../app/live/retention.tsx','default',{'./api':{request:async path=>{if(path==='trash')return {items:listed};operations.push(path);listed=[];if(operations.length===1)throw Error('Fictional response lost after commit');return {};}},'../date-time':{clientTime:()=>({}),formatTimestamp:x=>x},'../data':{reloadData:async()=>{}},'../ui':ui});
 h.render();await h.settle();button(h,'Restore').props.onClick();await h.settle();assert.ok(button(h,'Restore'));
 button(h,'Restore').props.onClick();await h.settle();assert.equal(operations.length,2);assert.equal(operations[1],operations[0],'The retry must replay its committed operation rather than trying to restore an item already gone');assert.equal(button(h,'Restore'),undefined);h.unmount();
});
test('Trash thirty-second expiry poll holds rows under the pointer until departure',async()=>{
 let read=0;const poll=deferred();
 const h=component('../app/live/retention.tsx','default',{'./api':{request:async()=>++read===1?{items:[item]}:poll.promise},'../date-time':{formatTimestamp:x=>x},'../ui':ui});
 h.render();await h.settle();assert.equal(h.timers.size,1);const timer=[...h.timers.values()][0];assert.equal(timer.ms,30000);
 h.all()[0].props.onPointerEnter();await h.settle();timer.fn();await h.settle();
 button(h,'Delete now').props.onClick();await h.settle();poll.resolve({items:[]});await h.settle();
 assert.ok(button(h,'Restore'),'Expiry cannot move an active row while its controls are in use');
 assert.equal(button(h,'Restore').props.disabled,false);assert.ok(h.all().some(n=>n.type==='dialog'));
 h.all()[0].props.onPointerLeave();await h.settle();assert.equal(button(h,'Restore'),undefined);
 assert.ok(h.all().some(n=>n.type==='dialog'),'The confirmation and its named files survive the poll');
 h.unmount();assert.equal(h.timers.size,0);
});
test('Delete and Restore wait for a new dashboard snapshot after a held pre-write reload',async()=>{
 const old=deferred(),exports={};let reads=0;
 vm.runInNewContext(compile('../app/data.ts'),{exports,Response,ReadableStream,Uint8Array,DecompressionStream,fetch:async url=>url.includes('dashboard')?(++reads===1?old.promise:new Response(JSON.stringify({meta:{ledger:{}},transactions:[]}))):new Response('{}'),require:name=>name.includes('ledger-modules')?{moduleSettings:()=>({}),hasExtension:()=>false}:name.includes('account-metadata')?{setCurrentAccounts(){}}:name.includes('formatting')?{setMoneySettings(){}}:{setDateLocale(){}}});
 const before=exports.reloadData();let completed=false;const after=exports.reloadData().then(()=>{completed=true;});
 await new Promise(r=>setImmediate(r));assert.equal(reads,1);assert.equal(completed,false,'A write cannot reuse the pending old snapshot');
 old.resolve(new Response(JSON.stringify({meta:{ledger:{}},transactions:[{id:'removed'}]})));await before;await after;
 assert.equal(reads,2);assert.equal(exports.dashboard.transactions.length,0,'The late pre-Delete snapshot cannot be the final screen');
});
test('A committed retention result with stale dashboard data waits for an idempotent retry',async()=>{
 let stale=true,reads=0,changed=0;
 const h=component('../app/live/retention.tsx','default',{'./api':{request:async()=>stale?{data_stale:true}:{}},'../date-time':{clientTime:()=>({})},'../data':{reloadData:async()=>{reads++;}}});
 h.window.addEventListener('drafts-changed',()=>changed++);
 const body={action:'delete',entry_id:'c'.repeat(16)},id='d'.repeat(32);
 await assert.rejects(h.exports.retentionOperation(body,id),/saved.*dashboard.*refresh/i);
 assert.equal(reads,0,'An explicitly stale public snapshot must never be published as the completed action');
 assert.equal(changed,0);stale=false;await h.exports.retentionOperation(body,id);assert.equal(reads,1);assert.equal(changed,0,'The shared API completion path owns notifications; a wrapper must not duplicate them');
});
test('Retention preview ignores older replies while edits and the save remain intact',async()=>{
 const previews=[],saved=deferred(),limits={delete_days:7,trash_days:7,saved_hours:24};let closed=0,body;
 const h=component('../app/settings-dialog.tsx','SettingsDialog',{'../../schema/currencies.json':{default:[]},'../scripts/formatting.mjs':{isCurrency:()=>true},'./data':{dashboard:{meta:{ledger:{name:'Willow',defaultCurrency:'EUR'},accounts:{}}},reloadData:async()=>{}},'./ui':{...ui,useDocumentScrollLock(){},useAppearance:()=>['light',()=>{}],announce(){},plural:(n,word)=>`${n} ${word}`},'./live/agent-status':{AgentNotice:'agent',agentIdentity:()=>''},'./live/update-notice':{UpdateNotice:'update-notice',knownUpdateStatus:()=>null,refreshUpdateStatus:async()=>{}},'./live/api':{request:async path=>{if(path==='settings/retention')return limits;const p=deferred();previews.push(p);return p.promise;}},'./live/retention':{retentionOperation:async value=>{body=value;return saved.promise;}}});
 h.render({onClose:()=>closed++,agent:{state:'ready'}});await h.settle();
 const select=label=>h.all().find(n=>n.type==='select'&&n.props['aria-label']===label);
 select('Delete an entry').props.onChange({target:{value:'14'}});await h.settle();select('Keep in Trash').props.onChange({target:{value:'3'}});await h.settle();
 previews[1].resolve({remove_now:[],move_to_trash:[],deletable_again:[{description:'Current preview'}]});await h.settle();previews[0].resolve({remove_now:[],move_to_trash:[],deletable_again:[{description:'OBSOLETE'}]});await h.settle();
 assert.equal(JSON.stringify(h.all()).includes('OBSOLETE'),false);assert.equal(select('Delete an entry').props.value,14);
 // Each changed limit says what it does directly under its own selector, in the singular for one item.
 const row=label=>h.all().find(n=>n.props?.className==='time-limit'&&walkText(n).includes(label)),effect=label=>row(label).props.children.find(c=>c&&c.props?.className==='limit-effect live-hint');
 assert.match(walkText(effect('Delete an entry')),/^1 older entry becomes deletable again\./);assert.match(walkText(effect('Keep in Trash')),/^No Trash items would be removed for good\./);assert.ok(!effect('Keep saved for later'),'An unchanged limit shows no effect');assert.equal(select('Keep in Trash').props.value,3);
 h.all().find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await h.settle();assert.equal(closed,0);assert.equal(body.limits.trash_days,3);saved.resolve({});await h.settle();assert.equal(closed,1);h.unmount();
});

test('Settings sends an explicit edit back to the published value while an earlier commit is not published',async()=>{
 const first=deferred(),sent=[],limits={delete_days:7,trash_days:7,saved_hours:24};let closed=0;
 const h=component('../app/settings-dialog.tsx','SettingsDialog',{
  './data':{dashboard:{meta:{ledger:{id:'fictional',name:'Published name',defaultCurrency:'INR'},accounts:{}}},reloadData:async()=>{}},
  './live/api':{request:async(path,options)=>{if(path==='settings/retention')return limits;if(path==='settings/name'){sent.push(JSON.parse(options.body).name);if(sent.length===1)return first.promise;return {};}return {};}},
  './ui':{Dialog:'dialog',Icon:'icon',useDocumentScrollLock(){},useAppearance:()=>['light',()=>{}],announce(){},plural:(n,word)=>`${n} ${word}`},
  './live/retention':{},'./live/agent-status':{AgentNotice:'notice',agentIdentity:()=>''},'./live/update-notice':{UpdateNotice:'update',knownUpdateStatus:()=>({}),refreshUpdateStatus:async()=>{}},
 });
 const name=()=>h.all().find(node=>node.type==='input'&&node.props.id==='settings-name'),form=()=>h.all().find(node=>node.type==='form');
 h.render({onClose:()=>closed++,agent:{state:'ready'}});await h.settle();name().props.onChange({target:{value:'First committed name'}});await h.settle();form().props.onSubmit({preventDefault(){}});await h.settle();name().props.onChange({target:{value:'Later words'}});await h.settle();first.resolve({});await h.settle();assert.equal(closed,0);
 name().props.onChange({target:{value:'Published name'}});await h.settle();form().props.onSubmit({preventDefault(){}});await h.settle();assert.deepEqual(sent,['First committed name','Published name'],'An explicit edit must reach the server even when it equals old published data');h.unmount();
});

test('A Settings shelf retry clears only the acknowledged edit and keeps newer words dirty',async()=>{
 const sent=[],limits={delete_days:7,trash_days:7,saved_hours:24};let fail=true;
 const h=component('../app/settings-dialog.tsx','SettingsDialog',{
  './data':{dashboard:{meta:{ledger:{id:'fictional',name:'Original',defaultCurrency:'INR'},accounts:{}}},reloadData:async()=>{}},
  './live/api':{request:async(path,options)=>{if(path==='settings/retention')return limits;if(path==='settings/name'){sent.push(JSON.parse(options.body).name);if(fail)throw Error('Fictional lost reply');}return {};}},
  './ui':{Dialog:'dialog',Icon:'icon',useDocumentScrollLock(){},useAppearance:()=>['light',()=>{}],announce(){},plural:(n,word)=>`${n} ${word}`},
  './live/retention':{},'./live/agent-status':{AgentNotice:'notice',agentIdentity:()=>''},'./live/update-notice':{UpdateNotice:'update',knownUpdateStatus:()=>({}),refreshUpdateStatus:async()=>{}},
 });
 const name=()=>h.all().find(node=>node.type==='input'&&node.props.id==='settings-name'),form=()=>h.all().find(node=>node.type==='form');
 const acknowledge=value=>h.window.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{path:'settings/name',body:JSON.stringify({name:value}),previousReason:'Fictional lost reply'}}));
 try{
  h.render({onClose(){},agent:{state:'ready'}});await h.settle();name().props.onChange({target:{value:'Acknowledged'}});await h.settle();form().props.onSubmit({preventDefault(){}});await h.settle();assert.match(walkText(h.all()),/Fictional lost reply/);
  acknowledge('Acknowledged');await h.settle();assert.doesNotMatch(walkText(h.all()),/Fictional lost reply/);form().props.onSubmit({preventDefault(){}});await h.settle();assert.deepEqual(sent,['Acknowledged'],'A shelf retry must not leave an already saved edit dirty');
  name().props.onChange({target:{value:'Older attempt'}});await h.settle();form().props.onSubmit({preventDefault(){}});await h.settle();name().props.onChange({target:{value:'Newer words'}});await h.settle();acknowledge('Older attempt');await h.settle();fail=false;form().props.onSubmit({preventDefault(){}});await h.settle();assert.equal(sent.at(-1),'Newer words');
 }finally{h.unmount();}
});

test('Settings submits every edited field into the FIFO before a later action can overtake it',async()=>{
 const held=deferred(),sent=[],limits={delete_days:7,trash_days:7,saved_hours:24};
 const h=component('../app/settings-dialog.tsx','SettingsDialog',{
  './data':{dashboard:{meta:{ledger:{id:'fictional',name:'Original',defaultCurrency:'INR'},accounts:{}}},reloadData:async()=>{}},
  './live/api':{request:async(path)=>{if(path==='settings/retention')return limits;sent.push(path);if(path==='settings/name')return held.promise;return {};}},
  './ui':{Dialog:'dialog',Icon:'icon',useDocumentScrollLock(){},useAppearance:()=>['light',()=>{}],announce(){},plural:(n,word)=>`${n} ${word}`},
  './live/retention':{},'./live/agent-status':{AgentNotice:'notice',agentIdentity:()=>''},'./live/update-notice':{UpdateNotice:'update',knownUpdateStatus:()=>({}),refreshUpdateStatus:async()=>{}},
 });
 try{
  h.render({onClose(){},agent:{state:'ready'}});await h.settle();h.all().find(n=>n.type==='input'&&n.props.id==='settings-name').props.onChange({target:{value:'First name'}});h.all().find(n=>n.type==='select'&&n.props['aria-labelledby']==='settings-currency-label').props.onChange({target:{value:'EUR'}});await h.settle();h.all().find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await h.settle();assert.deepEqual(sent,['settings/name','settings/default-currency'],'Both requests must enter the shared FIFO while the first reply is held');
 }finally{held.resolve({});await h.settle();h.unmount();}
});

test('Account currency keeps a newer selection and can save back before publication catches up',async()=>{
 const held=deferred(),sent=[];let closed=0;
 const h=component('../app/settings-dialog.tsx','AccountDialog',{
  './data':{dashboard:{meta:{ledger:{id:'fictional',defaultCurrency:'INR'},accounts:{}}},reloadData:async()=>{}},
  './live/api':{request:async(path,options)=>{if(path.startsWith('accounts/details'))return {account:'Assets:Bank:Willow',kind:'bank',currency:'INR',currencyEditable:true,openingBalance:'12.00'};sent.push(JSON.parse(options.body).currency);return sent.length===1?held.promise:{};}},
  './ui':{Dialog:'dialog',Icon:'icon',useDocumentScrollLock(){},useQueryState:()=>['',()=>{}],announce(){},money:()=>''},
 });
 const select=()=>h.all().find(n=>n.type==='select'),submit=()=>h.all().find(n=>n.type==='form').props.onSubmit({preventDefault(){}});
 try{h.render({account:'Assets:Bank:Willow',onClose:()=>closed++});await h.settle();select().props.onChange({target:{value:'EUR'}});await h.settle();submit();await h.settle();assert.notEqual(select().props.disabled,true);select().props.onChange({target:{value:'INR'}});await h.settle();held.resolve({});await h.settle();assert.equal(closed,0);assert.equal(select().props.value,'INR');const save=button(h,'Save account');assert.equal(save.props.disabled,false);submit();await h.settle();assert.deepEqual(sent,['EUR','INR']);}finally{held.resolve({});h.unmount();}
});

test('Account slot replay acknowledges its submitted currency without replacing a newer choice',async()=>{
 const account='Assets:Bank:Willow',updates=[],job={id:'currency-job',owner:{id:'account:'+account},state:'saved'};
 const h=component('../app/settings-dialog.tsx','AccountDialog',{
  './data':{dashboard:{meta:{ledger:{id:'fictional',defaultCurrency:'INR'},accounts:{}}},reloadData:async()=>{}},
  './live/direct-writes':{writeSnapshot:()=>[job]},
  './live/api':{request:async path=>{if(path.startsWith('accounts/details'))return {account,kind:'bank',currency:'INR',currencyEditable:true,openingBalance:'12.00'};throw Error('Fictional currency refusal');}},
  './ui':{Dialog:'dialog',Icon:'icon',useDocumentScrollLock(){},useQueryState:()=>[account,value=>updates.push(value)],announce(){},money:()=>''},
 });
 h.render({account,onClose(){}});await h.settle();let select=()=>h.all().find(n=>n.type==='select');select().props.onChange({target:{value:'USD'}});await h.settle();h.all().find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await h.settle();select().props.onChange({target:{value:'EUR'}});await h.settle();
 h.window.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{id:job.id,path:'accounts/currency',body:JSON.stringify({account,currency:'USD'}),previousReason:'Fictional currency refusal'}}));await h.settle();
 assert.equal(select().props.value,'EUR');assert.doesNotMatch(walkText(h.all()),/Fictional currency refusal/);assert.equal(updates.at(-1),account+'|USD');select().props.onChange({target:{value:'USD'}});await h.settle();assert.equal(button(h,'Save account').props.disabled,true);h.unmount();
});
test('Kept rule edits retain their original version, refuse a newer saved rule and can be discarded or cancelled',async()=>{
 const cleared=[];let closed=0;
 const rule={id:'r',version:'current',description:'Saved rule',date:'2026-11-01',schedule:{every:'month',from:'2026-11-01'},postings:[{account:'Expenses:Classes',amount:'12.00',commodity:'USD'},{account:'Assets:Bank:Willow',amount:'-12.00',commodity:'USD'}]};
 const h=component('../app/rule-editor.tsx','RuleEditor',{
  './live/retained-state':{useRetainedState:(name,initial)=>{const [value,set]=h.react.useState(name.endsWith('-version')?'old':name.endsWith('-description')?'Kept description':initial);return [value,set,set];},clearRetained:names=>cleared.push(...names),retainedSnapshot:()=>({}),hasRetained:()=>true},
  './data':{dashboard:{meta:{ledger:{defaultCurrency:'USD'},accounts:{'Assets:Bank:Willow':{kind:'bank',currency:'USD'},'Expenses:Classes':{}}},transactions:[]}},
  './date-time':{localDate:()=>'2026-11-01'},'./ui':{...ui,useDocumentScrollLock(){},money:()=>'',dayLabel:x=>x,monthLong:x=>x},
  '../scripts/recurring-ui.mjs':{complexRule:()=>false,mainPostings:r=>[r.postings[0]],payer:r=>r.postings[1],varyingDetails:()=>null},'./account-labels':{accountFieldLabel:x=>x},
  './live/rules-api':{useRuleOperation:()=>({busy:false,error:'',operate:async()=>{throw Error('A stale form must not send');}})},
 });
 try{h.render({rule,onClose:()=>closed++,onSaved(){}});await h.settle();assert.ok(walkText(h.all()).includes('The saved rule changed'));assert.equal(button(h,'Save rule').props.disabled,true);assert.ok(button(h,'Discard kept edits'));button(h,'Cancel').props.onClick();await h.settle();assert.equal(closed,1);assert.ok(cleared.includes('rule-r-version'));}finally{h.unmount();}
});

test('Reopening an account with its own failed currency save restores that submitted choice',async()=>{
 const account='Assets:Bank:Willow';
 const h=component('../app/settings-dialog.tsx','AccountDialog',{
  './live/direct-writes':{writeSnapshot:()=>[{path:'accounts/currency',state:'failed',owner:{id:'account:'+account,key:'account:'+account},body:JSON.stringify({account,currency:'EUR'})}]},
  './data':{dashboard:{meta:{ledger:{id:'fictional',defaultCurrency:'USD'},accounts:{}}},reloadData:async()=>{}},
  './live/api':{request:async()=>({account,kind:'bank',currency:'USD',currencyEditable:true,openingBalance:'12.00'})},
  './ui':{Dialog:'dialog',Icon:'icon',useDocumentScrollLock(){},useQueryState:()=>['',()=>{}],announce(){},money:()=>''},
 });
 try{h.render({account,onClose(){}});await h.settle();assert.equal(h.all().find(n=>n.type==='select').props.value,'EUR');}finally{h.unmount();}
});
