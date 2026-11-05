import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync,openSync,readSync,closeSync,fstatSync,readFileSync,readdirSync,writeFileSync,mkdirSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

// The live server streams public/data while a save rebuilds it. A reader that opened the
// previous file must keep getting that whole version, never a truncated or growing one.
test('Rebuilding dashboard data replaces each published file whole, even under an open reader',()=>{
 const out=mkdtempSync(join(process.env.EXPENSES_TEST_TMP||'/tmp','expense-publish-'));
 try{
  mkdirSync(join(out,'app/data'),{recursive:true});
  for(const name of ['dashboard','pending-transactions','fuel-log','add-patterns'])writeFileSync(join(out,'app/data',name+'.json'),JSON.stringify({fictional:name}));
  mkdirSync(join(out,'public/data'),{recursive:true});
  const published=join(out,'public/data/dashboard.json.gz'),previous=Buffer.from('previous complete version');writeFileSync(published,previous);
  const reader=openSync(published,'r');
  try{
   const script=fileURLToPath(new URL('../scripts/compress-dashboard-data.mjs',import.meta.url));
   const run=spawnSync(process.execPath,[script],{env:{...process.env,DASHBOARD_DATA_DIR:out},encoding:'utf8'});
   assert.equal(run.status,0,run.stderr);
   const held=Buffer.alloc(fstatSync(reader).size);readSync(reader,held,0,held.length,0);
   assert.deepEqual(held,previous,'The open reader still sees the whole previous file');
   assert.notDeepEqual(readFileSync(published),previous,'New readers get the rebuilt file');
   assert.deepEqual(readdirSync(join(out,'public/data')).filter(name=>!name.endsWith('.json.gz')),[],'No temporary files are left behind');
  }finally{closeSync(reader);}
 }finally{rmSync(out,{recursive:true,force:true});}
});
