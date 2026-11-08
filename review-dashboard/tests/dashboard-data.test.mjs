import assert from 'node:assert/strict';
import {test} from 'node:test';
import {execFileSync,spawnSync} from 'node:child_process';
import {cp,readFile,writeFile,mkdir,symlink,readdir,realpath} from 'node:fs/promises';
import {join,relative} from 'node:path';
import {disposableDashboard,temporaryDirectory,sourceApplication,snapshotData} from './test-isolation.mjs';

async function bytes(root) {
 const result={};
 async function visit(folder){for(const item of await readdir(folder,{withFileTypes:true})){const file=join(folder,item.name);if(item.isDirectory())await visit(file);else result[relative(root,file)]=(await readFile(file)).toString('base64');}}
 await visit(root);return result;
}
const scripts=['copy-fuel-log','build-dashboard-data','build-review-data','build-add-patterns','check-dashboard-data','compress-dashboard-data'];
async function fixture(){
 const copy=await disposableDashboard(),ledger=join(copy.application,'ledger');
 await cp(join(sourceApplication,'fixtures/ledgers/empty'),ledger,{recursive:true});
 const out=await temporaryDirectory('provenance-output-');
 const env={...copy.env,LEDGER_DIR:ledger,DASHBOARD_DATA_DIR:out};
 const run=(name,overrides={})=>spawnSync(process.execPath,[join(copy.dash,'scripts',name+'.mjs')],{env:{...env,...overrides},encoding:'utf8'});
 const build=(overrides={})=>{for(const name of scripts){const r=run(name,overrides);assert.equal(r.status,0,r.stderr);}};
 return {...copy,ledger,out,env,run,build};
}

test('each standalone data script refuses a different ledger before changing any bytes',async()=>{
 const f=await fixture();f.build();
 const other=join(f.application,'other');await cp(f.ledger,other,{recursive:true});
 const before=await bytes(f.out);
 for(const script of scripts){const r=f.run(script,{LEDGER_DIR:other});assert.notEqual(r.status,0,script);assert.match(r.stderr,/DASHBOARD_DATA_DIR.*DASHBOARD_DATA_REPLACE=1/);assert.deepEqual(await bytes(f.out),before,script);}
});
test('redirected outputs leave defaults identical; replacement removes old data and updates provenance',async()=>{
 const f=await fixture(),before=snapshotData(f.application);f.build();assert.deepEqual(snapshotData(f.application),before);
 assert.ok((await bytes(f.out))['public/data/dashboard.json.gz']);
 const built=JSON.parse(await readFile(join(f.out,'app/data/dashboard.json')));
 assert.deepEqual(built.meta.currencyMinorUnits,JSON.parse(await readFile(join(sourceApplication,'schema/currency-minor-units.json'))));
 const other=join(f.application,'other');await cp(f.ledger,other,{recursive:true});
 await writeFile(join(f.out,'public/data/old-ledger.json'),'old');
 f.build({LEDGER_DIR:other,DASHBOARD_DATA_REPLACE:'1'});
 const files=await bytes(f.out);assert.ok(!files['public/data/old-ledger.json']);
 assert.deepEqual(JSON.parse(await readFile(join(f.out,'app/data/.source-ledger.json'))),{format:1,ledger:await realpath(other)});
});
test('default folders work without a stamp and with a matching stamp; symlink ledger is the same source',async()=>{
 const f=await fixture();f.build({DASHBOARD_DATA_DIR:''});f.build({DASHBOARD_DATA_DIR:''});
 const alias=join(f.application,'ledger-alias');await symlink(f.ledger,alias);
 const before=await readFile(join(f.dash,'app/data/.source-ledger.json'));
 f.build({DASHBOARD_DATA_DIR:'',LEDGER_DIR:alias});
 assert.deepEqual(await readFile(join(f.dash,'app/data/.source-ledger.json')),before);
});
test('npm prefix preserves relative output paths from its caller',async()=>{
 const f=await fixture();
 execFileSync('npm',['--prefix',f.dash,'run','data'],{cwd:f.application,env:{...f.env,DASHBOARD_DATA_DIR:'relative-output'},stdio:'pipe'});
 assert.ok((await bytes(join(f.application,'relative-output')))['public/data/dashboard.json.gz']);
});
test('npm environments disable update notifications in the Makefile and every test/browser launcher',async()=>{
 assert.equal(process.env.npm_config_update_notifier,'false');
 const make=await readFile(join(sourceApplication,'Makefile'),'utf8');assert.match(make,/export npm_config_update_notifier = false/);
 const isolation=await readFile(new URL('./test-isolation.mjs',import.meta.url),'utf8');assert.match(isolation,/npm_config_update_notifier: 'false'/);
 const browser=await readFile(new URL('./browser/run.mjs',import.meta.url),'utf8');assert.match(browser,/npm_config_update_notifier:'false'/);
 for(const name of (await readdir(new URL('.',import.meta.url))).filter(n=>n.endsWith('.browser.mjs'))){const source=await readFile(new URL(name,import.meta.url),'utf8');assert.match(source,/useProjectNode\(\)/,name);}
});

test('unreadable provenance fails closed and explicit replacement recovers it',async()=>{
 const f=await fixture();f.build();
 const stamp=join(f.out,'app/data/.source-ledger.json');await writeFile(stamp,'{');
 const before=await bytes(f.out),failed=f.run('copy-fuel-log');
 assert.notEqual(failed.status,0);assert.match(failed.stderr,/DASHBOARD_DATA_REPLACE=1/);assert.deepEqual(await bytes(f.out),before);
 f.build({DASHBOARD_DATA_REPLACE:'1'});assert.equal(JSON.parse(await readFile(stamp)).format,1);
});

test('initial provenance publication never exposes a partial stamp to another reader',async()=>{
 const f=await fixture();
 const stamp=join(f.out,'app/data/.source-ledger.json');
 const code=`import fs from 'node:fs';import assert from 'node:assert/strict';import {syncBuiltinESMExports} from 'node:module';
 const stamp=${JSON.stringify(stamp)},write=fs.writeFileSync;
 fs.writeFileSync=(file,value,options)=>{
  if(String(file).includes('.source-ledger')){
   write(file,'{',options);
   if(fs.existsSync(stamp))assert.doesNotThrow(()=>JSON.parse(fs.readFileSync(stamp,'utf8')),'A concurrent reader saw partial provenance');
   return write(file,value,{...options,flag:'w'});
  }
  return write(file,value,options);
 };syncBuiltinESMExports();
 const {guardDashboardData}=await import(${JSON.stringify(join(f.application,'scripts/dashboard-data.mjs'))});
 guardDashboardData();assert.equal(JSON.parse(fs.readFileSync(stamp,'utf8')).format,1);`;
 const result=spawnSync(process.execPath,['--input-type=module','-e',code],{env:f.env,encoding:'utf8'});
 assert.equal(result.status,0,result.stderr);
});
