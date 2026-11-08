import './test-isolation.mjs';
// Exercise the actual polling hook with deterministic React/effect and timer doubles.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const source=ts.transpileModule(readFileSync(new URL('../app/live/use-drafts.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const slots=[],effects=[],timers=new Map();let index=0,timerId=0,records=[],statements=[],ledgerCheck={available:false,checking:false,item:null},healthWait=null,listWait=[],calls=0,reloads=0,dirty=false,current;
const window=new EventTarget(),document=new EventTarget();document.hidden=false;
const changed=(a,b)=>!a||a.length!==b.length||a.some((x,i)=>!Object.is(x,b[i]));
const hooks={
 useState(initial){const i=index++;slots[i]??={value:typeof initial==='function'?initial():initial};return [slots[i].value,value=>{slots[i].value=typeof value==='function'?value(slots[i].value):value;dirty=true;}];},
 useRef(initial){const i=index++;return slots[i]??=( {current:initial} );},
 useCallback(fn,deps){const i=index++;if(!slots[i]||changed(slots[i].deps,deps))slots[i]={value:fn,deps};return slots[i].value;},
 useEffect(fn,deps){const i=index++;if(!slots[i]||changed(slots[i].deps,deps)){const old=slots[i];slots[i]={deps,cleanup:null};effects.push(()=>{old?.cleanup?.();slots[i].cleanup=fn();});}},
};
const exports={};
vm.runInNewContext(source,{exports,window,document,Event,AbortController,setTimeout:(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),require:name=>name==='react'?hooks:name==='../data'?{reloadData:async()=>{reloads++;}}:name==='./statements-api'?{statementsApi:{list:async()=>({sessions:statements})}}:{isRemoved:()=>false,rememberRemoved(){},request:async path=>path==='trash'?{items:[]}:path==='ledger-check'?(ledgerCheck instanceof Error?Promise.reject(ledgerCheck):ledgerCheck):({state:'ready',reason:null}),api:{health:async()=>healthWait || ({ok:true}),list:async()=>{calls++;return listWait.length?listWait.shift():records;}}}});
const render=()=>{index=0;dirty=false;current=exports.useDrafts();while(effects.length)effects.shift()();};
async function settle(){for(let n=0;n<8;n++){await new Promise(r=>setImmediate(r));if(dirty)render();}}
function delay(){assert.equal(timers.size,1,'Exactly one poll timer must exist');return [...timers.values()][0].delay;}
function fire(){const [id,t]=[...timers][0];timers.delete(id);t.fn();}
const draft=(state,result=null)=>({id:'one',state,created_at:'2026-10-30',result});
render();await settle();assert.equal(delay(),30000);assert.equal(current.online,true);
records=[draft('queued')];window.dispatchEvent(new Event('drafts-changed'));await settle();assert.equal(delay(),3000,'First queued draft starts three-second polling');
records=[draft('question')];fire();await settle();assert.equal(delay(),30000);assert.equal(current.attention,1);
statements=[{state:'reading'},{state:'review'},{state:'found',duplicate_of:'one'},{state:'applied'}];await current.refresh();await settle();assert.equal(current.attention,2,'One question and one statement need attention; working and duplicate sessions do not');
assert.equal(delay(),3000,'Reading statements must poll promptly even without an active expense draft');
statements=[{state:'applying'}];await current.refresh();await settle();assert.equal(delay(),3000,'Applying statements keep the active polling interval');
statements=[{state:'currency_mismatch'}];fire();await settle();assert.equal(delay(),30000,'A settled statement restores the idle polling interval');
records=[{...draft('added'),statement_file:'receipt-file'}];statements=[];await current.refresh();await settle();assert.equal(current.attention,1,'A recognized statement waiting for handover needs attention');records[0].handover_session='session';await current.refresh();await settle();assert.equal(current.attention,0,'The handed-over statement is counted by its session only');
// hledger's background check: while it runs the poll comes back in three seconds; its open item counts in Needs you.
statements=[];records=[];ledgerCheck={available:true,checking:true,item:null};await current.refresh();await settle();assert.equal(delay(),3000,'A running ledger check keeps the active polling interval');assert.equal(current.attention,0);
ledgerCheck={available:true,checking:false,item:{id:'ledger-check',title:'An entry doesn’t balance'}};fire();await settle();assert.equal(delay(),30000,'A finished check restores the idle interval');assert.equal(current.attention,1,'The open Ledger check item needs attention');assert.equal(current.ledgerCheck.item.title,'An entry doesn’t balance');
// A failing status read (a 500) keeps the last status and never stops the Queue's own updates.
ledgerCheck=new Error('Fictional server error');records=[draft('question')];fire();await settle();assert.equal(current.online,true);assert.equal(current.drafts[0].state,'question','Drafts still update');assert.equal(current.ledgerCheck.item.title,'An entry doesn’t balance','The last status stays');
ledgerCheck={available:true,checking:false,item:null};records=[];fire();await settle();assert.equal(current.attention,0,'The item leaves Needs you when a check passes');
statements=[];records=[draft('working')];await current.refresh();await settle();assert.equal(delay(),3000,'Answer/retry transition resets the previous idle timer');
const before=calls;document.hidden=true;document.dispatchEvent(new Event('visibilitychange'));await settle();assert.equal(timers.size,0);assert.equal(calls,before,'Hidden pages do not fetch');
document.hidden=false;document.dispatchEvent(new Event('visibilitychange'));await settle();assert.equal(delay(),3000);
records=[draft('added',{entry_id:'entry'})];fire();await settle();assert.equal(delay(),30000);assert.equal(reloads,1);
window.dispatchEvent(new Event('focus'));await settle();assert.equal(reloads,1,'Retained additions do not repeatedly reload ledger data');
window.dispatchEvent(new Event('pagehide'));await settle();assert.equal(timers.size,0);
window.dispatchEvent(new Event('pageshow'));await settle();assert.equal(delay(),30000);
let releaseHealth;healthWait=new Promise(resolve=>{releaseHealth=resolve;});window.dispatchEvent(new Event('focus'));await settle();const beforeLeaving=calls;window.dispatchEvent(new Event('pagehide'));releaseHealth({ok:true});await settle();assert.equal(calls,beforeLeaving,'A late health response must not start draft requests after departure');assert.equal(timers.size,0);healthWait=null;window.dispatchEvent(new Event('pageshow'));await settle();assert.equal(delay(),30000);
// A mutation requests a fresh pass while an older poll is held open.
let releaseOld,releaseNew;listWait=[new Promise(r=>releaseOld=r),new Promise(r=>releaseNew=r)];
const pending=current.refresh();await settle();void current.refresh();releaseOld([{...draft('question'),id:'obsolete'}]);await settle();
assert.equal(current.drafts.some(d=>d.id==='obsolete'),false,'A poll superseded by an action cannot publish its older rows');
releaseNew([draft('editing')]);await pending;await settle();assert.equal(current.drafts[0].state,'editing');
for(const slot of slots)slot?.cleanup?.();assert.equal(timers.size,0,'Unmount removes polling');
console.log('Draft polling: initial active draft, answer/retry interval transitions, one timer, visibility pause, focus refresh, one reload per addition cleanup and held poll superseded by an action passed.');
