import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import {deferred,hookHarness} from './hook-harness.mjs';

// The app shell, its data reloads and the shared notices: each test holds the real
// module's I/O open, acts, releases it late and checks that the newer state survives.
const compile=file=>ts.transpileModule(readFileSync(new URL(file,import.meta.url),'utf8').replaceAll('import.meta.env.BASE_URL','"/"'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const snapshot=name=>new Response(JSON.stringify({meta:{ledger:{name}},transactions:[]}));
function dataModule(){
 const reads=[],exports={};
 vm.runInNewContext(compile('../app/data.ts'),{exports,Response,ReadableStream,Uint8Array,DecompressionStream,
  fetch:url=>{if(!url.includes('dashboard'))return Promise.resolve(new Response('{}'));const hold=deferred();reads.push(hold);return hold.promise;},
  require:name=>name.includes('ledger-modules')?{moduleSettings:()=>({}),hasExtension:()=>false}:name.includes('account-metadata')?{setCurrentAccounts(){}}:name.includes('formatting')?{setMoneySettings(){}}:{setDateLocale(){}}});
 const published=[];exports.subscribeData(()=>published.push(exports.dashboard.meta.ledger.name));
 return {exports,reads,published};
}
const turn=()=>new Promise(resolve=>setImmediate(resolve));

test('A reload asked for during a held older read publishes a snapshot read after the request',async()=>{
 const {exports,reads,published}=dataModule();
 const poll=exports.reloadData();await turn();assert.equal(reads.length,1);
 // A save commits while the poll's read is held, then asks for fresh data.
 let saved=false;const save=exports.reloadData().then(()=>{saved=true;});await turn();
 assert.equal(reads.length,1,'One read at a time');assert.equal(saved,false,'The save cannot reuse the read begun before it');
 reads[0].resolve(snapshot('Before the save'));await poll;await turn();
 assert.equal(reads.length,2,'The save gets a read that starts after it');assert.equal(saved,false);
 reads[1].resolve(snapshot('After the save'));await save;
 assert.deepEqual(published,['Before the save','After the save'],'Snapshots publish in the order they were read');
 assert.equal(exports.dashboard.meta.ledger.name,'After the save');
});

test('Reloads asked for during one held read share one follow-up, and an older failure does not fail them',async()=>{
 const {exports,reads,published}=dataModule();
 const first=exports.reloadData();const later=[exports.reloadData(),exports.reloadData(),exports.reloadData()];await turn();
 reads[0].reject(new TypeError('connection reset'));await assert.rejects(first);await turn();
 assert.equal(reads.length,2,'Three callers share one follow-up read');
 reads[1].resolve(snapshot('Newest'));await Promise.all(later);
 assert.deepEqual(published,['Newest']);
 const idle=exports.reloadData();await turn();assert.equal(reads.length,3,'An idle reload starts at once');reads[2].resolve(snapshot('Idle'));await idle;
});
