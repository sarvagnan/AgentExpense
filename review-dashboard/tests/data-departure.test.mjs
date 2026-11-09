import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {hookHarness,deferred} from './hook-harness.mjs';
test('Data reads use keepalive and a retired document never decodes a late response; BFCache and new reads still load',async()=>{
 const held=deferred(),calls=[];let bytes=0,first=true,fail=false;
 const h=hookHarness('../app/data.ts',{'../scripts/ledger-modules.mjs':{hasExtension:()=>false},'../scripts/account-metadata.mjs':{setCurrentAccounts(){}},'./formatting':{setMoneySettings(){}},'./date-time':{setDateLocale(){}}},{Response,ReadableStream,Uint8Array,DecompressionStream,fetch:async(url,options)=>{calls.push({url,options});if(fail)throw Error('Real load failure');if(first){first=false;return held.promise;}return Response.json({meta:{ledger:{},accounts:{}},transactions:[]});}});
 const read=h.exports.fetchGzipJson('dashboard');h.events.dispatchEvent(new Event('pagehide'));held.resolve({ok:true,arrayBuffer:async()=>{bytes++;return new TextEncoder().encode('{}').buffer;}});
 await assert.rejects(read,error=>error.name==='AbortError');assert.equal(bytes,0,'Departure fences the continuation immediately after fetch');assert.equal(calls[0].options.keepalive,true);
 h.events.dispatchEvent(new Event('pageshow'));assert.ok((await h.exports.fetchGzipJson('dashboard')).meta);fail=true;await assert.rejects(h.exports.fetchGzipJson('dashboard'),/Real load failure/);h.close();
});
