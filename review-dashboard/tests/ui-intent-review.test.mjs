import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {hookHarness} from './hook-harness.mjs';
import * as navigation from '../scripts/edit-navigation.mjs';
const nodes=n=>!n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(nodes):[n,...nodes(n.props?.children)];
test('A back-forward-cache editor return reloads Queue instead of reviving a released form',()=>{
 const target=new EventTarget(),replaced=[];
 const cleanup=navigation.watchEditRestores(target,{search:'?draft=kept',replace:value=>replaced.push(value)},{state:{agentExpenseEditing:true}},'/expenses/queue/');
 const event=new Event('pageshow');Object.defineProperty(event,'persisted',{value:true});
 target.dispatchEvent(new Event('pageshow'));assert.deepEqual(replaced,[]);target.dispatchEvent(event);assert.deepEqual(replaced,['/expenses/queue/']);cleanup();target.dispatchEvent(event);assert.equal(replaced.length,1);
});
test('The changed-entry notice belongs only to the month of its current entry',()=>{
 assert.equal(navigation.showEntryChangedNotice('?edit_changed=2026-11','2026-11'),true);
 assert.equal(navigation.showEntryChangedNotice('?edit_changed=2026-11','2026-10'),false);
});
test('Rules sorting replaces the URL entry for both labelled select controls',()=>{
 const source=readFileSync(new URL('../app/rules.tsx',import.meta.url),'utf8'),deps={},writes=[];
 for(const m of source.matchAll(/from ['"]([^'"]+)['"]/g))if(m[1]!=='react')deps[m[1]]={};
 Object.assign(deps,{'react/jsx-runtime':{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})},'../scripts/rule-sort.mjs':{sortRules:r=>r},'./live/interaction':{useInteractionSnapshot:d=>[d,{},()=>{}],useActionGuard:()=>({})},'./data':{dashboard:{meta:{accounts:{}}}},'./ui':{useQueryState:(name,value)=>[value,(...args)=>writes.push([name,...args])]},'./live/rules-api':{useRules:()=>({data:{rules:[],suggestions:[]},ready:true}),useRuleOperation:()=>({})}});
 const h=hookHarness('../app/rules.tsx',deps,{location:{hash:''}});
 try{h.render(m=>m.default({online:true}));const controls=nodes(h.current).filter(n=>n.type==='select');controls[0].props.onChange({target:{value:'amount'}});controls[1].props.onChange({target:{value:'desc'}});assert.deepEqual(writes,[['sort','amount','replace'],['direction','desc','replace']]);}finally{h.close();}
});

test('Queue Discard keeps its own draft form and never imports another kept edit for the same entry',async()=>{
 const stored=new Map(),sent=[],base={entry:{description:'Draft A server words'}},ledger={meta:{ledger:{id:'fixture'}}};
 for(const [name,value] of Object.entries({form:{entry:{description:'Draft B words'}},sources:['Bills/b.png'],reason:'B reason',version:'v'})){
  stored.set('ledger-form-fixture-hand-B-'+name,JSON.stringify(value));
  stored.set('ledger-form-fixture-hand-entry-'+name,JSON.stringify(value));
 }
 const api={claim:async(_id,token)=>({claim_token:token,form:base}),handForm:async(...args)=>sent.push(args),release:async()=>{},discard:async()=>{}};
 const h=hookHarness('../app/live/edit-recovery.ts',{'./api':{api},'../data':{dashboard:ledger},'./retained-state':'actual'},{sessionStorage:{getItem:k=>stored.get(k)??null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)}});
 await h.exports.discardEdit({id:'A',state:'question',correction:{entry_id:'entry',sources:['Bills/a.png'],reason:'A reason'}});
 assert.equal(sent[0][2].entry.description,'Draft A server words');assert.deepEqual(sent[0].slice(3),[['Bills/a.png'],'A reason']);
 assert.equal(stored.get('ledger-form-fixture-hand-B-reason'),JSON.stringify('B reason'));
});

function detailHarness(items=[]){
 const source=readFileSync(new URL('../app/live/drafts.tsx',import.meta.url),'utf8'),deps={},calls=[],checks=[],retries=[],listeners=new Set();let writes=items;
 for(const m of source.matchAll(/from ['"]([^'"]+)['"]/g))if(m[1]!=='react')deps[m[1]]={};
 const api={retryCheck:()=>{let resolve;const promise=new Promise(r=>resolve=r);checks.push({resolve});return promise;},retry:(...args)=>{calls.push(args);return Promise.resolve({state:'queued'});}};
 Object.assign(deps,{'react/jsx-runtime':{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})},'../data':{dashboard:{meta:{ledger:{id:'fixture'},accounts:{}}}},'../ui':{Dialog:'dialog',Icon:'icon',formatTimestamp:x=>x},'../date-time':{formatTimestamp:x=>x},'./api':{api},'./answers':{useAnswer:()=>({flush:async()=>{},clear(){}})},'./write-completion':{useCommittedError:()=>['',()=>{}]},'./direct-writes':{writeSnapshot:()=>writes,subscribeWrites:fn=>{listeners.add(fn);return()=>listeners.delete(fn);}},'./edit-recovery':{discardEdit:async()=>{},prepareFixRetry:d=>api.retryCheck(d.id),runFixRetry:(d,p)=>api.retry(d.id,p.replace_edits?p.version:undefined),editSaveReason:d=>d.correction_save?'Save is in progress.':writes.some(w=>w.path===`entries/entry/corrections/${d.id}`&&['queued','saving','waiting','failed','interrupted'].includes(w.state))?'Save’s result is unknown.':''}});
 const draft={id:'d'.repeat(32),state:'failed',text:'Fictional fix',created_at:'2026-11-10',files:[],questions:[],correction:{entry_id:'entry',version:'version',sources:[],before:{description:'Saved entry',postings:[]}},ledger_fix:{state:'failed',title:'Fictional fix'},hand_form:{entry:{description:'Kept owner edits'}}};
 const h=hookHarness('../app/live/drafts.tsx',deps);h.render(m=>m.DraftDetail({draft,onAction:async()=>{},onDiscard:async()=>{}}));
 return {h,draft,calls,checks,button:label=>nodes(h.current).find(n=>n.type==='button'&&n.props.children===label),writes:next=>{writes=next;listeners.forEach(fn=>fn());}};
}
test('Fix Retry asks before replacing kept changes and Keep my changes never queues it',async()=>{
 const g=detailHarness();try{
  g.button('Retry').props.onClick();await g.h.settle();assert.equal(g.calls.length,0);assert.equal(g.checks.length,1);
  g.checks[0].resolve({replace_edits:true,version:'kept-version'});await g.h.settle();
  assert.ok(nodes(g.h.current).some(n=>n.type==='dialog'));g.button('Keep my changes').props.onClick();await g.h.settle();assert.equal(g.calls.length,0);
  g.button('Retry').props.onClick();await g.h.settle();g.checks[1].resolve({replace_edits:true,version:'kept-version'});await g.h.settle();g.button('Ask the agent again').props.onClick();await g.h.settle();
  assert.deepEqual(g.calls,[[g.draft.id,'kept-version']]);
  g.button('Retry').props.onClick();await g.h.settle();g.checks[2].resolve({replace_edits:false,version:'unchanged'});await g.h.settle();assert.equal(g.calls.length,2);assert.equal(g.calls[1][1],undefined);assert.ok(!nodes(g.h.current).some(n=>n.type==='dialog'));
 }finally{g.h.close();}
});
test('Queue Discard stays disabled with a reason while this edit Save is pending or unknown',async()=>{
 const path='entries/entry/corrections/'+'d'.repeat(32),g=detailHarness([{path,state:'saving'}]);try{
  assert.equal(g.button('Discard proposal').props.disabled,true);assert.match(JSON.stringify(g.h.current),/Save/);
  g.writes([{path,state:'failed',outcomeUnknown:true}]);await g.h.settle();assert.equal(g.button('Discard proposal').props.disabled,true);
  g.writes([]);await g.h.settle();assert.equal(g.button('Discard proposal').props.disabled,false);
 }finally{g.h.close();}
});
test('Queued fix Discard without recovery goes straight to the atomic discard endpoint',async()=>{
 const calls=[],stored=new Map(),h=hookHarness('../app/live/edit-recovery.ts',{'./api':{api:{claim:async()=>{throw Error('A queued discard must not claim then release');},discard:async(...args)=>calls.push(args)}},'../data':{dashboard:{meta:{ledger:{id:'fixture'}}}},'./retained-state':'actual'},{sessionStorage:{getItem:k=>stored.get(k)??null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)}});
 await h.exports.discardEdit({id:'queued',state:'queued',files:[],correction:{entry_id:'entry',sources:[]},ledger_fix:{state:'queued'}});assert.deepEqual(calls,[['queued',undefined]]);
});

test('Retry includes browser-kept edits in its question and clears only the confirmed recovery',async()=>{
 const stored=new Map(),calls=[],draft={id:'retry',state:'failed',correction:{entry_id:'entry',sources:[],reason:''}},base={entry:{description:'Agent baseline'}};
 for(const [field,value] of Object.entries({form:{entry:{description:'Browser kept edit'}},sources:['Bills/kept.png'],reason:'Browser reason',version:'v'}))stored.set('ledger-form-fixture-hand-retry-'+field,JSON.stringify(value));
 const api={claim:async(_id,token)=>{calls.push(['claim']);return {claim_token:token,form:base,draft};},handForm:async(...args)=>calls.push(['form',...args]),release:async(...args)=>calls.push(['release',...args]),retryCheck:async()=>{calls.push(['check']);return {replace_edits:true,version:'confirmed'};},retry:async(...args)=>calls.push(['retry',...args])};
 const h=hookHarness('../app/live/edit-recovery.ts',{'./api':{api},'../data':{dashboard:{meta:{ledger:{id:'fixture'}}}},'./retained-state':'actual'},{sessionStorage:{getItem:k=>stored.get(k)??null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)}});
 const prepared=await h.exports.prepareFixRetry(draft);assert.deepEqual(calls.map(c=>c[0]),['claim','form','release','check']);assert.equal(calls[1][3].entry.description,'Browser kept edit');assert.equal(calls[1][5],'Browser reason');
 assert.equal([...stored.keys()].filter(k=>k.startsWith('ledger-form')).length,4);await h.exports.runFixRetry(draft,prepared);
 assert.deepEqual(calls.at(-1),['retry','retry','confirmed']);assert.equal([...stored.keys()].filter(k=>k.startsWith('ledger-form')).length,0);
});
test('Save-aware discard guard distinguishes this draft from other writes and known refusals',()=>{
 const h=hookHarness('../app/live/edit-recovery.ts',{'./api':{api:{}},'../data':{dashboard:{meta:{ledger:{id:'fixture'}}}},'./retained-state':'actual'},{sessionStorage:{getItem:()=>null}}),d={id:'draft',correction:{entry_id:'entry'}},path='entries/entry/corrections/draft';
 for(const state of ['queued','saving','waiting','interrupted'])assert.match(h.exports.editSaveReason(d,[{path,state}]),/Save/);
 assert.match(h.exports.editSaveReason(d,[{path,state:'failed',outcomeUnknown:true}]),/unknown/);
 assert.equal(h.exports.editSaveReason(d,[{path,state:'failed',outcomeUnknown:false}]),'');assert.equal(h.exports.editSaveReason(d,[{path:'entries/entry/corrections/other',state:'saving'}]),'');
});

test('Retry acknowledgement preserves a successor claim and identical recovery bytes',async()=>{
 const stored=new Map(),draft={id:'retry-owner',state:'failed',correction:{entry_id:'entry',sources:[],reason:''}};let resolve;
 for(const [field,value] of Object.entries({form:{entry:{description:'Kept'}},sources:[],reason:'',version:'v'}))stored.set('ledger-form-fixture-hand-retry-owner-'+field,JSON.stringify(value));
 const api={claim:async(_id,token)=>({claim_token:token,form:{entry:{}},draft}),handForm:async()=>{},release:async()=>{},retryCheck:async()=>({replace_edits:true,version:'hash'}),retry:()=>new Promise(r=>resolve=r)};
 const h=hookHarness('../app/live/edit-recovery.ts',{'./api':{api},'../data':{dashboard:{meta:{ledger:{id:'fixture'}}}},'./retained-state':'actual'},{sessionStorage:{getItem:k=>stored.get(k)??null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)}});
 const prepared=await h.exports.prepareFixRetry(draft),saving=h.exports.runFixRetry(draft,prepared);
 stored.set('hand-claim-fixture-retry-owner','successor');resolve({state:'queued'});await saving;
 assert.equal([...stored.keys()].filter(k=>k.startsWith('ledger-form')).length,4);assert.equal(stored.get('hand-claim-fixture-retry-owner'),'successor');
});


test('Resolved notice Discard does not wait for an edit claim when there are no local edits',async()=>{
 const calls=[],stored=new Map();let release;const held=new Promise(resolve=>release=resolve);
 const api={claim:async(_id,token)=>{calls.push('claim');await held;return {claim_token:token,form:{entry:{description:'Server proposal'}}};},handForm:async()=>calls.push('form'),discard:async()=>calls.push('discard')};
 const h=hookHarness('../app/live/edit-recovery.ts',{'./api':{api},'../data':{dashboard:{meta:{ledger:{id:'fixture'}}}},'./retained-state':'actual'},{sessionStorage:{getItem:k=>stored.get(k)??null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)}});
 const work=h.exports.discardEdit({id:'resolved',state:'question',files:[],correction:{entry_id:'entry',sources:[]},ledger_fix:{state:'resolved'}});
 try{await Promise.resolve();assert.deepEqual(calls,['discard'],'Discarding a resolved notice has no edit claim to wait for');await work;}
 finally{release();await work;h.close();}
});


test('Resolved notice Discard still keeps browser edits in Trash',async()=>{
 const stored=new Map(),sent=[],draft={id:'resolved-kept',state:'question',files:[],correction:{entry_id:'entry',sources:[],reason:''},ledger_fix:{state:'resolved'}};
 for(const [field,value] of Object.entries({form:{entry:{description:'Kept owner edit'}},sources:['Bills/kept.png'],reason:'Kept reason',version:'v'}))stored.set('ledger-form-fixture-hand-resolved-kept-'+field,JSON.stringify(value));
 const api={claim:async(_id,token)=>({claim_token:token,form:{entry:{description:'Server proposal'}}}),handForm:async(...args)=>sent.push(args),discard:async()=>{assert.equal(sent[0][2].entry.description,'Kept owner edit');assert.deepEqual(JSON.parse(JSON.stringify(sent[0].slice(3))),[['Bills/kept.png'],'Kept reason']);}};
 const h=hookHarness('../app/live/edit-recovery.ts',{'./api':{api},'../data':{dashboard:{meta:{ledger:{id:'fixture'}}}},'./retained-state':'actual'},{sessionStorage:{getItem:k=>stored.get(k)??null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)}});
 try{await h.exports.discardEdit(draft);assert.equal([...stored.keys()].filter(k=>k.startsWith('ledger-form')).length,0);}finally{h.close();}
});
