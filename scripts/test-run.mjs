// Direct Node test launchers share the same ownership contract as make/Python.
import {mkdtempSync,mkdirSync,existsSync,readdirSync,lstatSync,copyFileSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import {syncBuiltinESMExports} from 'node:module';
export function ensureTestRoot(){
 const owned=!process.env.AGENTEXPENSE_TEST_ROOT;
 const root=process.env.AGENTEXPENSE_TEST_ROOT||mkdtempSync('/tmp/agentexpense-run-');
 for(const name of ['tmp','npm-cache','uv-cache','node-cache','oracles'])mkdirSync(join(root,name),{recursive:true});
 Object.assign(process.env,{AGENTEXPENSE_TEST_ROOT:root,TMPDIR:join(root,'tmp'),TMP:join(root,'tmp'),TEMP:join(root,'tmp'),EXPENSES_TEST_TMP:join(root,'tmp'),EXPENSES_TEST_ORACLE_CACHE:join(root,'oracles'),npm_config_cache:join(root,'npm-cache'),UV_CACHE_DIR:join(root,'uv-cache'),NODE_COMPILE_CACHE:join(root,'node-cache'),npm_config_update_notifier:'false',npm_config_offline:'true',UV_OFFLINE:'1',UV_NO_SYNC:'1',PYTHONDONTWRITEBYTECODE:'1'});
 process.env.DASHBOARD_DATA_DIR ||= join(root,'dashboard-data');
 if(!fs.agentExpenseArchiveInstalled){
  const originalSync=fs.rmSync,originalAsync=fsp.rm;
  const archive=path=>{
   if(process.env.KEEP_ARTIFACTS!=='1')return;
   const target=String(path),base=process.env.AGENTEXPENSE_TEST_ROOT,kept=join(base,'kept');
   if(!target.startsWith(base+'/')||target.startsWith(kept+'/')||!existsSync(target)||!lstatSync(target).isDirectory())return;
   mkdirSync(kept,{recursive:true});const destination=fs.mkdtempSync(join(kept,'node-'));
   fs.cpSync(target,join(destination,target.split('/').at(-1)),{recursive:true,dereference:false,verbatimSymlinks:true});
  };
  fs.rmSync=(path,options)=>{archive(path);return originalSync(path,options);};
  fsp.rm=async(path,options)=>{archive(path);return originalAsync(path,options);};
  fs.agentExpenseArchiveInstalled=true;syncBuiltinESMExports();
 }
 if(owned){
  process.once('exit',code=>{
   if(process.env.KEEP_ARTIFACTS==='1'){console.log(`Test artifacts kept: ${root}`);return;}
   const traces=[];
   function visit(folder){if(!existsSync(folder))return;for(const name of readdirSync(folder)){const p=join(folder,name),stat=lstatSync(p);if(stat.isSymbolicLink())continue;if(stat.isDirectory())visit(p);else if(/^failure-.*\.zip$/.test(name))traces.push(p);}}
   if(code)visit(root);
   if(traces.length){const dest=`/tmp/agentexpense-failures-${Date.now()}-${process.pid}`;mkdirSync(dest);traces.forEach((p,i)=>copyFileSync(p,join(dest,`${i}-${p.split('/').at(-1)}`)));console.log(`Failing traces: ${dest}`);}
   if(process.platform==='darwin')execFileSync('chmod',['-RN',root],{stdio:'ignore'});
   rmSync(root,{recursive:true,force:true});
  });
  // A browser launcher installs an asynchronous trace/close handler of its own.
  for(const [name,code] of [['SIGINT',130],['SIGTERM',143],['SIGHUP',129]])process.once(name,()=>{if(process.listenerCount(name)===0)process.exit(code);});
 }
 return root;
}

// Initialize before other imported test helpers choose their scratch paths.
export const testRoot=ensureTestRoot();
