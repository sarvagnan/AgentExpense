import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

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
 const require=key=>{if(key==='react')return react;if(key==='react/jsx-runtime')return {jsx,jsxs:jsx,Fragment:'fragment'};if(key==='./interaction'){const result={};vm.runInNewContext(compile('../app/live/interaction.ts'),{exports:result,require});return result;}return dependencies[key]||{};};
 vm.runInNewContext(compile(file),{exports,require,window,Event,CustomEvent,location:{search:''},crypto,URLSearchParams,console,setInterval:(fn,ms)=>{const id=++timer;timers.set(id,{fn,ms});return id;},clearInterval:id=>timers.delete(id)});
 const render=()=>{index=0;dirty=false;tree=exports[name](props);while(effects.length)effects.shift()();return tree;};
 const walk=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(n=>walk(n)):[node,...walk(node.props?.children)];
 const all=()=>walk(tree);
 return {window,react,exports,timers,render(value=props){props=value;return render();},all,async settle(){for(let n=0;n<12;n++){await new Promise(r=>setImmediate(r));if(dirty)render();}},unmount(){slots.forEach(s=>s?.cleanup?.());}};
}
const item={id:'a'.repeat(32),kind:'entry',description:'Willow lunch',removed_at:'2026-11-04',expires_at:'2026-11-11',files:[]};
const ui={PageHeader:'header',Icon:'icon',Dialog:'dialog'};
const buttons=h=>h.all().filter(n=>n.type==='button');
const button=(h,text)=>buttons(h).find(n=>n.props.children===text);
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
 vm.runInNewContext(compile('../app/data.ts'),{exports,Response,ReadableStream,Uint8Array,DecompressionStream,fetch:async url=>url.includes('dashboard')?(++reads===1?old.promise:new Response(JSON.stringify({meta:{ledger:{}},transactions:[]}))):new Response('{}'),require:name=>name.includes('ledger-modules')?{moduleSettings:()=>({fuel:false})}:name.includes('account-metadata')?{setCurrentAccounts(){}}:name.includes('formatting')?{setMoneySettings(){}}:{setDateLocale(){}}});
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
 assert.equal(changed,0);stale=false;await h.exports.retentionOperation(body,id);assert.equal(reads,1);assert.equal(changed,1);
});
test('Retention preview ignores older replies while edits and the save remain intact',async()=>{
 const previews=[],saved=deferred(),limits={delete_days:7,trash_days:7,saved_hours:24};let closed=0,body;
 const h=component('../app/settings-dialog.tsx','SettingsDialog',{'../../schema/currencies.json':{default:[]},'../scripts/formatting.mjs':{isCurrency:()=>true},'./data':{dashboard:{meta:{ledger:{name:'Willow',defaultCurrency:'EUR'},accounts:{}}},reloadData:async()=>{}},'./ui':{...ui,useDocumentScrollLock(){},useAppearance:()=>['light',()=>{}],announce(){}},'./live/agent-status':{AgentNotice:'agent'},'./live/update-notice':{UpdateNotice:'update-notice',knownUpdateStatus:()=>null,refreshUpdateStatus:async()=>{}},'./live/api':{request:async path=>{if(path==='settings/retention')return limits;const p=deferred();previews.push(p);return p.promise;}},'./live/retention':{retentionOperation:async value=>{body=value;return saved.promise;}}});
 h.render({onClose:()=>closed++,agent:{state:'ready'}});await h.settle();
 const select=label=>h.all().find(n=>n.type==='select'&&n.props['aria-label']===label);
 select('Delete an entry').props.onChange({target:{value:'14'}});await h.settle();select('Keep in Trash').props.onChange({target:{value:'3'}});await h.settle();
 previews[1].resolve({remove_now:[],move_to_trash:[],deletable_again:[{description:'Current preview'}]});await h.settle();previews[0].resolve({remove_now:[],move_to_trash:[],deletable_again:[{description:'OBSOLETE'}]});await h.settle();
 assert.equal(JSON.stringify(h.all()).includes('OBSOLETE'),false);assert.equal(select('Delete an entry').props.value,14);assert.equal(select('Keep in Trash').props.value,3);
 h.all().find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await h.settle();assert.equal(closed,0);assert.equal(body.limits.trash_days,3);saved.resolve({});await h.settle();assert.equal(closed,1);h.unmount();
});
