import './test-isolation.mjs';
// One failed or slow live check is retried after OFFLINE_RETRY_MS; only a second
// consecutive failure shows "offline". Real hook, deterministic effect and timer doubles.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const source=ts.transpileModule(readFileSync(new URL('../app/live/use-drafts.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const slots=[],effects=[],timers=new Map();let index=0,timerId=0,dirty=false,current,health='ok',healthCalls=0,listHold=null;
const window=new EventTarget(),document=new EventTarget();document.hidden=false;
const changed=(a,b)=>!a||a.length!==b.length||a.some((x,i)=>!Object.is(x,b[i]));
const hooks={
 useState(initial){const i=index++;slots[i]??={value:typeof initial==='function'?initial():initial};return [slots[i].value,value=>{slots[i].value=typeof value==='function'?value(slots[i].value):value;dirty=true;}];},
 useRef(initial){const i=index++;return slots[i]??=( {current:initial} );},
 useCallback(fn,deps){const i=index++;if(!slots[i]||changed(slots[i].deps,deps))slots[i]={value:fn,deps};return slots[i].value;},
 useEffect(fn,deps){const i=index++;if(!slots[i]||changed(slots[i].deps,deps)){const old=slots[i];slots[i]={deps,cleanup:null};effects.push(()=>{old?.cleanup?.();slots[i].cleanup=fn();});}},
};
const exports={};
const api={health:async()=>{healthCalls++;if(health==='timeout')throw new DOMException('The operation was aborted.','AbortError');if(health==='down')throw new Error('Failed to fetch');return {ok:true};},list:async()=>listHold?listHold.promise:[]};
vm.runInNewContext(source,{exports,window,document,Event,DOMException,AbortController,setTimeout:(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),require:name=>name==='react'?hooks:name==='../data'?{reloadData:async()=>{}}:name==='./statements-api'?{statementsApi:{list:async()=>({sessions:[]})}}:{isRemoved:()=>false,rememberRemoved(){},request:async path=>path==='trash'?{items:[]}:({state:'ready',reason:null}),api}});
const render=()=>{index=0;dirty=false;current=exports.useDrafts();while(effects.length)effects.shift()();};
async function settle(){for(let n=0;n<8;n++){await new Promise(r=>setImmediate(r));if(dirty)render();}}
const delays=()=>[...timers.values()].map(t=>t.delay).sort((a,b)=>a-b);
function fire(delay){const entry=[...timers].find(([,t])=>t.delay===delay);assert.ok(entry,`a ${delay} ms timer is pending`);timers.delete(entry[0]);entry[1].fn();}
assert.equal(exports.OFFLINE_RETRY_MS,2000,'The follow-up check comes quickly, not with the next idle poll');
render();await settle();assert.equal(current.online,true);assert.deepEqual(delays(),[30000]);
// A single timed-out health read: still online, one quick retry pending, nothing shown.
health='timeout';window.dispatchEvent(new Event('focus'));await settle();
assert.equal(current.online,true,'One slow check must not declare the page offline');assert.equal(current.error,'');
assert.deepEqual(delays(),[2000,30000],'A failed check schedules one retry at OFFLINE_RETRY_MS beside the poll');
// The retry succeeds: still online, and the retry timer is gone.
health='ok';let before=healthCalls;fire(2000);await settle();
assert.equal(healthCalls,before+1,'The retry runs one health check');assert.equal(current.online,true);assert.deepEqual(delays(),[30000]);
// Two consecutive failures: offline after the retry, with no further quick retry.
health='down';window.dispatchEvent(new Event('focus'));await settle();assert.equal(current.online,true);assert.deepEqual(delays(),[2000,30000]);
before=healthCalls;fire(2000);await settle();assert.equal(healthCalls,before+1);
assert.equal(current.online,false,'The second consecutive failure means offline');assert.match(current.error,/Failed to fetch/);
assert.deepEqual(delays(),[30000],'Offline waits for the ordinary poll or Check connection');
// Still offline on later failures; back online on the first success.
await current.refresh();await settle();assert.equal(current.online,false);assert.deepEqual(delays(),[30000]);
health='ok';await current.refresh();await settle();assert.equal(current.online,true);assert.equal(current.error,'');
// A success resets the count: the next single failure is not offline, and a
// failure from any later refresh (here a focus) is the second one.
health='down';await current.refresh();await settle();assert.equal(current.online,true);assert.deepEqual(delays(),[2000,30000]);
window.dispatchEvent(new Event('focus'));await settle();assert.equal(current.online,false,'A second failure from any refresh counts');
assert.deepEqual(delays(),[30000],'Declaring offline cancels the pending quick retry');
health='ok';await current.refresh();await settle();health='down';await current.refresh();await settle();assert.deepEqual(delays(),[2000,30000]);
// An answered check ends the run of failures even when a newer refresh supersedes its pass:
// failure, then a check that succeeds while its reads are held and a newer refresh is queued,
// then one failure. That is one failure since the last answer, not two.
health='ok';await current.refresh();await settle();health='down';await current.refresh();await settle();assert.equal(current.online,true);
health='ok';let releaseList;listHold={promise:new Promise(r=>releaseList=r)};const pass=current.refresh();await settle();
health='down';void current.refresh();releaseList([]);listHold=null;await pass;await settle();
assert.equal(current.online,true,'A failure after a superseded successful check is the first one');assert.deepEqual(delays(),[2000,30000]);
for(const slot of slots)slot?.cleanup?.();assert.equal(timers.size,0,'Unmount removes the poll and the pending retry');
console.log('Offline retry: one failed check retries after 2 s without changing the screen; two consecutive failures show offline; success resets; unmount clears the retry.');
