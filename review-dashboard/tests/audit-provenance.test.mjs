import { temporaryDirectory } from './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,writeFile,rm} from 'node:fs/promises';
import {auditProvenance} from '../scripts/audit-provenance.mjs';
test('provenance publishes addition words, answers and doubts without tool payloads',async()=>{
 const root=await temporaryDirectory('expense-provenance-');
 try {
  assert.equal((await auditProvenance(root)).size,0);
  await mkdir(root+'/data/additions',{recursive:true});
  const record={version:1,id:'a'.repeat(32),kind:'draft',outcome:'added',entry_id:'b'.repeat(16),text:'Lunch',time:'2026-10-30T12:00:00Z',saved_at:null,questions:[{question:'Which bank?',answer:'Savings Account',asked_at:'time'}],doubts:['No receipt'],runs:[{model:'fake',effort:'offline',lookups:[{private:'full payload'}]}]};
  await writeFile(root+'/data/additions/2026.jsonl',JSON.stringify(record)+'\n'+JSON.stringify({...record,id:'c'.repeat(32),outcome:'failed',entry_id:null})+'\n');
  const map=await auditProvenance(root), p=map.get(record.entry_id);
  assert.equal(map.size,1);assert.equal(p.text,'Lunch');assert.deepEqual(p.questions,[{question:'Which bank?',answer:'Savings Account'}]);assert.deepEqual(p.models,['fake · offline']);assert.deepEqual(p.doubts,['No receipt']);assert.ok(!JSON.stringify(p).includes('full payload'));
 }finally{await rm(root,{recursive:true,force:true});}
});
