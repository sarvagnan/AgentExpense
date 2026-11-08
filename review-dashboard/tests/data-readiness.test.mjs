import './test-isolation.mjs';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source=ts.transpileModule(readFileSync(new URL('../app/data.ts',import.meta.url),'utf8').replaceAll('import.meta.env.BASE_URL',"'/expenses/'"),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const timers=new Map(),window=new EventTarget(),document=new EventTarget();document.hidden=false;
let timerId=0,status={version:1,epoch:'fixture',generation:'old',data_stale:false,updating:false},hold=null,reads=0,failStatus=false,statusHold=null;
const fileUrls=[];
const value=name=>name==='dashboard'?{meta:{ledger:{name:'New fixture',extensions:{}},accounts:{}} ,transactions:[]}:{transactions:[]};
const exports={};
vm.runInNewContext(source,{exports,window,document,Event,Response,ReadableStream,Uint8Array,DecompressionStream,
 setTimeout:(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),
 require:name=>name.includes('ledger-modules')?{hasExtension:()=>false}:name.includes('account-metadata')?{setCurrentAccounts(){}}:name.includes('formatting')?{setMoneySettings(){}}:{setDateLocale(){}},
 fetch:async url=>{if(url.endsWith('data-status')){if(failStatus)throw new TypeError('fixture connection failure');const captured=status;if(statusHold)await statusHold;return Response.json(captured);}reads++;fileUrls.push(url);if(hold)await hold;return Response.json(value(url.includes('dashboard.json')?'dashboard':'review'));}});
const settle=async()=>{for(let n=0;n<12;n++)await new Promise(r=>setImmediate(r));};
const fire=()=>{const [id,t]=[...timers][0];timers.delete(id);t.fn();};
status={...status,version:0,data_stale:true,updating:true};let booted=false;
const boot=exports.loadData().then(()=>{booted=true;});await settle();
assert.equal(booted,false,'Initial navigation waits for the active rebuild before making setup/entry decisions');assert.equal(reads,0);
assert.equal([...timers.values()][0].delay,250);
window.dispatchEvent(new Event('pagehide'));assert.equal(timers.size,0);
status={...status,version:1,data_stale:false,updating:false};await settle();assert.equal(booted,false);
window.dispatchEvent(new Event('pageshow'));await boot;assert.equal(exports.dashboard.meta.ledger.name,'New fixture');
exports.setData({dashboard:{meta:{ledger:{name:'Previous fixture'},accounts:{}},transactions:[]},review:{},fuel:{}});
status={...status,version:2,data_stale:true,updating:true};
await exports.reloadData();assert.equal(exports.dashboard.meta.ledger.name,'Previous fixture');assert.equal(timers.size,1);
assert.equal([...timers.values()][0].delay,250,'Readiness poll uses the controlled 250 ms interval');
fire();await settle();assert.equal(timers.size,1);assert.equal([...timers.values()][0].delay,250);
// Readiness does not clear the stale label until every immutable file has loaded.
let release;hold=new Promise(r=>release=r);status={...status,version:3,generation:'new',data_stale:false,updating:false};fire();await settle();
assert.equal(exports.dataStatus.data_stale,true);assert.equal(exports.dashboard.meta.ledger.name,'Previous fixture');
const patterns=exports.fetchGzipJson('add-patterns');
assert.ok(fileUrls.at(-1).endsWith('/api/data/old/add-patterns.json.gz'),'Auxiliary reads stay pinned to the published view while newer files are held');
// An older health response cannot overwrite the newer readiness response.
exports.observeDataStatus({...status,version:2,generation:'old',data_stale:true});assert.equal(exports.dataStatus.generation,'new');
// A newer save arriving during the reads invalidates the old publication.
status={...status,version:4,data_stale:true,updating:true};exports.observeDataStatus(status);release();hold=null;await patterns;await settle();
assert.equal(exports.dashboard.meta.ledger.name,'Previous fixture');assert.equal(exports.dataStatus.data_stale,true);
status={...status,version:5,generation:'latest',data_stale:false,updating:false};fire();await settle();
assert.equal(exports.dashboard.meta.ledger.name,'New fixture');assert.equal(exports.dataStatus.data_stale,false);assert.equal(timers.size,0);
exports.observeDataStatus({...status,version:6,data_stale:true,updating:true});assert.equal(timers.size,1);
failStatus=true;fire();await settle();assert.equal([...timers.values()][0].delay,2000,'A failed readiness read retries after two seconds');assert.equal(exports.dataStatus.data_stale,true);
failStatus=false;
status={...status,version:7,data_stale:true,updating:true};exports.observeDataStatus(status);
let releaseStatus;statusHold=new Promise(resolve=>releaseStatus=resolve);fire();await settle();
assert.equal(timers.size,0,'A held readiness read never overlaps another poll');
exports.observeDataStatus({...status,version:8});assert.equal(timers.size,0);
releaseStatus();statusHold=null;await settle();assert.equal(exports.dataStatus.version,8);assert.equal(timers.size,1,'A superseded read still schedules the next readiness check');
// An old server's ready reply must not relabel the previous totals as current
// while a restarted server's replacement files are still held open.
status={...status,version:9,data_stale:false,updating:false};
statusHold=new Promise(resolve=>releaseStatus=resolve);fire();await settle();statusHold=null;
hold=new Promise(resolve=>release=resolve);
status={version:1,epoch:'restarted',generation:'restart-views',data_stale:false,updating:false};exports.observeDataStatus(status);await settle();
assert.equal(exports.dataStatus.data_stale,true);
releaseStatus();await settle();
assert.equal(exports.dataStatus.epoch,'restarted','A retired server response cannot replace the new epoch');
assert.equal(exports.dataStatus.data_stale,true,'Old totals stay labelled stale until the new generation loads');
release();hold=null;await settle();assert.equal(exports.dataStatus.data_stale,false);assert.equal(exports.dataStatus.generation,'restart-views');
status={...status,version:2,data_stale:true,updating:true};exports.observeDataStatus(status);
document.hidden=true;document.dispatchEvent(new Event('visibilitychange'));assert.equal(timers.size,0);
const retained=exports.dashboard;
hold=new Promise(resolve=>release=resolve);status={...status,version:9,generation:'after-leaving',data_stale:false,updating:false};
const departing=exports.reloadData();await settle();window.dispatchEvent(new Event('pagehide'));release();hold=null;await departing;
assert.equal(exports.dashboard,retained,'A data read finishing after departure never publishes');assert.equal(timers.size,0);
console.log('Data readiness: controlled polling, held whole-generation reads, stale label, superseded saves and hidden-page cleanup passed.');
