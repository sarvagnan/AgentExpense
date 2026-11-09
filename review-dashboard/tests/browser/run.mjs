import {useProjectNode} from '../../../scripts/node-toolchain.mjs';
useProjectNode();
import {ensureTestRoot} from "../../../scripts/test-run.mjs";
ensureTestRoot();
console.log(`Browser Node ${process.version}`);
// All browsers are headless; all writable runtime state and artifacts stay under /tmp.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {homedir} from 'node:os';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {cp,mkdir,mkdtemp,readFile,writeFile,rename,rm,open} from 'node:fs/promises';
import {spawn,execFileSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {createServer} from 'node:net';
import {capture,contactSheet,shots} from './capture.mjs';
import {runSuite} from './suites.mjs';

const source=resolve(dirname(fileURLToPath(import.meta.url)),'../../..');
const cache=join(homedir(),'.cache/expense-playwright');
const require=createRequire(join(cache,'package.json'));
const pw=require('playwright'),{expect}=require('playwright/test');
assert.equal(require('playwright/package.json').version,'1.64.1','Use the pinned headless browser package');
const engines=(process.env.ENGINES || 'chromium,firefox,webkit').split(',');
const themes=(process.env.THEMES || 'light,dark').split(',');
for(const theme of themes)assert.ok(['light','dark'].includes(theme),'THEMES must be light and/or dark');
const personas=(process.env.PERSONA || 'asha,empty,jordan,sam').split(',');
for(const engine of engines)assert.ok(['chromium','firefox','webkit'].includes(engine));
for(const persona of personas)assert.ok(['asha','empty','jordan','sam'].includes(persona));
const artifacts=await mkdtemp(join(process.env.AGENTEXPENSE_TEST_ROOT,'agentexpense-5-browser-'));
process.env.TMPDIR=join(artifacts,'tmp');await mkdir(process.env.TMPDIR);
const venv=join(source,'review-dashboard/server/.venv'),python=join(venv,'bin/python');
if(process.env.CAPTURE!=='0' && !process.env.ENGINES && !process.env.PERSONA)await rm(shots,{recursive:true,force:true});
const manifest=[],results=[];let activeServer,activeBrowser,stage;
async function checkpoint() {
  const destination=join(artifacts,'results.json');
  await writeFile(destination+'.next',JSON.stringify({engines,personas,results,captures:manifest.length,manifest},null,2)+'\n');
  await rename(destination+'.next',destination);
}
const filter=path=>!['.git','.jj','.venv','node_modules','dist','__pycache__','.source-ledger.json'].includes(path.split('/').at(-1));
async function run(command,args,options) {
  const child=spawn(command,args,{...options,stdio:['ignore','pipe','pipe']});
  let log='';child.stdout.on('data',data=>log+=data);child.stderr.on('data',data=>log+=data);
  const code=await new Promise((done,reject)=>{child.on('exit',done);child.on('error',reject);});
  if(options.log)await writeFile(options.log,log);
  assert.equal(code,0,`${command} ${args.join(' ')} failed:\n${log.slice(-12000)}`);
  return log;
}
async function stop(child) {
  if(!child || child.exitCode!==null || child.signalCode!==null)return;
  child.kill('SIGTERM');
  await new Promise(done=>{const timer=setTimeout(()=>child.kill('SIGKILL'),5000);child.once('exit',()=>{clearTimeout(timer);done();});});
}
async function port() {
  const server=createServer();await new Promise((done,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',done);});
  const value=server.address().port;await new Promise(done=>server.close(done));return value;
}
async function staging(persona) {
  const root=join(artifacts,persona),app=join(root,'application'),dash=join(app,'review-dashboard'),ledger=join(app,'ledger');
  await mkdir(app,{recursive:true});
  for(const path of ['scripts','schema','extensions','migrations','policy','templates','readers','Makefile','.node-version','VERSION'])await cp(join(source,path),join(app,path),{recursive:true,filter,preserveTimestamps:true});
  for(const path of ['app','public','scripts','server','tests','index.html','package.json','package-lock.json','tsconfig.json','vite.config.ts'])await cp(join(source,'review-dashboard',path),join(dash,path),{recursive:true,filter,preserveTimestamps:true});
  // Private dependency directory; package links are read-only inputs. Vite caches stay local.
  const {readdir,symlink}=await import('node:fs/promises');
  await mkdir(join(dash,'node_modules'));
  for(const name of await readdir(join(source,'review-dashboard/node_modules')))if(!name.startsWith('.vite'))await symlink(join(source,'review-dashboard/node_modules',name),join(dash,'node_modules',name));
  if(process.env.BROWSER_LARGE==='1')await run(python,[join(source,'scripts/fixtures/generate_large.py'),'--dir',ledger,'--no-repository'],{cwd:app,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},log:join(root,'large.log')});
  else await cp(join(source,'fixtures/ledgers',persona),ledger,{recursive:true,filter,preserveTimestamps:true});
  await readFile(join(ledger,'AGENTS.md'),'utf8');
  await writeFile(join(ledger,'.gitignore'),'/drafts/\n/.lock\n/expense_ledger.ledger\n');
  const jjConfig=join(root,'jj.toml');await writeFile(jjConfig,'[user]\nname = "Browser Fixture"\nemail = "browser@example.invalid"\n');
  const fake=join(root,'fake-agent');await writeFile(fake,`#!${python}\n`+(await readFile(join(source,'review-dashboard/server/tests/fake_agent.py'),'utf8')).split('\n').slice(1).join('\n'),{mode:0o755});
  const config=join(root,'fake.json');await writeFile(config,JSON.stringify({steps:[]}));
  const env={...process.env,DASHBOARD_DATA_DIR:dash,npm_config_update_notifier:'false',DASHBOARD_TODAY:process.env.DASHBOARD_TODAY || '2026-11-01',PYTHONDONTWRITEBYTECODE:'1',VIRTUAL_ENV:venv,LEDGER_PYTHON:join(venv,'bin/python'),PATH:join(venv,'bin')+':'+process.env.PATH,UV_OFFLINE:'1',UV_NO_SYNC:'1',UV_CACHE_DIR:join(root,'uv-cache'),npm_config_cache:join(root,'npm-cache'),NODE_COMPILE_CACHE:join(root,'node-cache'),EXPENSES_TEST_ORACLE_CACHE:join(root,'hledger-oracles'),JJ_CONFIG:jjConfig,LEDGER_DIR:ledger,EXPENSES_STATE_DIR:join(root,'state'),EXPENSES_CODEX_HOME:join(root,'agent-home'),EXPENSES_CODEX:fake,EXPENSES_FAKE_CONFIG:config,EXPENSES_DEV:'1',EXPENSES_BASE_PATH:'/expenses/',EXPENSES_AGENT_TIMEOUT:'10',BROWSER_PERSONA:persona};
  await run('jj',['git','init','--colocate',ledger],{cwd:app,env});
  await run('jj',['-R',ledger,'commit','-m','Fictional browser baseline'],{cwd:app,env});
  await run('jj',['-R',ledger,'bookmark','set','main','-r','@-'],{cwd:app,env});
  await run(python,[join(app,'scripts/ledger/generate_ledger.py')],{cwd:app,env});
  await run('npm',['--prefix','review-dashboard','run','data'],{cwd:app,env,log:join(root,'data.log')});
  await run('npm',['--prefix','review-dashboard','run','build'],{cwd:app,env,log:join(root,'build.log')});
  env.EXPENSES_PORT=String(await port());
  const log=await open(join(root,'server.log'),'w');
  // Unbuffered, so a traceback is whole in the log even if the server is killed after it.
  activeServer=spawn(python,[join(dash,'tests/browser/serve.py')],{cwd:app,env:{...env,PYTHONUNBUFFERED:'1'},stdio:['ignore',log.fd,log.fd]});await log.close();
  const base=`http://127.0.0.1:${env.EXPENSES_PORT}/expenses/`;
  const deadline=Date.now()+20000;let started=false;
  while(Date.now()<deadline){try{if((await fetch(base+'api/health')).ok){started=true;break;}}catch{}if(activeServer.exitCode!==null)break;await new Promise(done=>setTimeout(done,100));}
  assert.ok(started,await readFile(join(root,'server.log'),'utf8'));
  return {root,app,dash,ledger,env,base,persona};
}
async function launch(engine,root) {
  const downloadsPath=join(root,'downloads',engine);await mkdir(downloadsPath,{recursive:true});
  const options={headless:true,downloadsPath,tracesDir:join(root,'traces',engine)};
  if(engine==='firefox' && process.platform==='darwin') {
    // Firefox 157 otherwise looks up the real macOS app-data directory before its profile.
    // https://github.com/microsoft/playwright/issues/42768
    const appData=join(root,'firefox-app-data');await mkdir(appData,{recursive:true});
    options.env={...process.env,CFFIXED_USER_HOME:appData};
  }
  if(engine==='chromium') {
    const data=JSON.parse(await readFile(join(cache,'node_modules/playwright-core/browsers.json'),'utf8'));
    const revision=data.browsers.find(browser=>browser.name==='chromium-headless-shell').revision;
    const browserRoot=process.env.PLAYWRIGHT_BROWSERS_PATH || (process.platform==='darwin'?join(homedir(),'Library/Caches/ms-playwright'):join(homedir(),'.cache/ms-playwright'));
    options.executablePath=join(browserRoot,`chromium_headless_shell-${revision}`,process.platform==='darwin'?`chrome-headless-shell-mac-${process.arch==='arm64'?'arm64':'x64'}`:'chrome-headless-shell-linux64','chrome-headless-shell');
    assert.ok(!options.executablePath.includes('.app/'));
  }
  return pw[engine].launch(options);
}
async function clearPrivateHomeACLs(path) {
  // CoreFoundation creates protected Documents/Downloads folders even in the
  // redirected disposable home. Remove their macOS deny-delete ACLs only inside
  // this run's private home; never follow a symlink into a real home.
  const {lstat,readdir}=await import('node:fs/promises');
  let info;try{info=await lstat(path);}catch(error){if(error.code==='ENOENT')return;throw error;}
  if(info.isSymbolicLink())return;
  if(info.isDirectory())for(const name of await readdir(path))await clearPrivateHomeACLs(join(path,name));
  execFileSync('chmod',['-N',path]);
}
async function cleanupArtifacts(passed) {
  if(process.env.KEEP_ARTIFACTS!=='1' && process.platform==='darwin')for(const persona of personas)await clearPrivateHomeACLs(join(artifacts,persona,'firefox-app-data'));
  if(process.env.KEEP_ARTIFACTS==='1')console.log(`Browser results: ${join(artifacts,'results.json')}`);
  else if(passed){await rm(artifacts,{recursive:true,force:true});console.log('Passing browser artifacts removed');}
  else {
    const {readdir,lstat}=await import('node:fs/promises');
    // A failed server request is otherwise only a status code: print each server's tail
    // (fictional fixture data only) into this run's own output before the root is removed.
    // The last traceback is printed whole, even when later lines would push it out of the tail.
    for(const persona of personas){const log=await readFile(join(artifacts,persona,'server.log'),'utf8').catch(()=>null);if(!log)continue;
      const lines=log.split('\n'),tail=lines.slice(-120),start=lines.findLastIndex(line=>line.startsWith('Traceback ')||line.startsWith('ERROR:'));
      const traceback=start>=0&&start<lines.length-120?lines.slice(start,start+200):[];
      console.log(`SERVER_LOG_TAIL ${persona}\n${traceback.length?traceback.join('\n')+'\n[…]\n':''}${tail.join('\n')}\nEND_SERVER_LOG_TAIL ${persona}`);}
    const failures=[];
    async function retain(folder){for(const name of await readdir(folder)){const path=join(folder,name);if((await lstat(path)).isDirectory())await retain(path);else if(name.startsWith('failure-')&&name.endsWith('.zip'))failures.push(path);}}
    await retain(artifacts);
    const saved=[];for(const [i,path] of failures.entries()){const bytes=await readFile(path);saved.push([`failure-${i}-${path.split('/').at(-1)}`,bytes]);}
    await rm(artifacts,{recursive:true,force:true});
    if(saved.length){await mkdir(artifacts);for(const [name,bytes] of saved)await writeFile(join(artifacts,name),bytes);console.log('Failing case traces retained for run cleanup.');}
  }
}
for(const signal of ['SIGINT','SIGTERM','SIGHUP'])process.once(signal,async()=>{try{if(stage?.activeContext)await stage.activeContext.tracing.stop({path:join(stage.root,`failure-${stage.activeCase}-interrupted.zip`)});}catch{}await activeBrowser?.close();await stop(activeServer);await cleanupArtifacts(false);process.exit(130);});
try {
  for(const persona of personas) {
    stage=await staging(persona);
    for(const engine of engines) {
      activeBrowser=await launch(engine,stage.root);
      // The approved mockups live in the ignored tmp/ folder, so a published checkout (CI) has none.
      const mockups=join(source,'tmp/mockups/index.html');
      if(persona===personas[0] && engine===engines[0] && !existsSync(mockups))console.log('Mockup packaging check not run: tmp/mockups is not in this checkout');
      if(persona===personas[0] && engine===engines[0] && existsSync(mockups)) {
        const context=await activeBrowser.newContext(),page=await context.newPage();
        await page.route('**/*',route=>route.request().url().startsWith('file:')?route.continue():route.abort());
        await page.goto('file://'+mockups);
        assert.ok((await page.title()).includes('mockup'));
        await context.close();
      }
      await runSuite({browser:activeBrowser,engine,stage,expect,results,checkpoint,themes,capture:process.env.CAPTURE==='0'?async()=>{}:async(page,info)=>{if(!process.env.BROWSER_CAPTURE_PREFIX||info.state.startsWith(process.env.BROWSER_CAPTURE_PREFIX))await capture(page,manifest,{...info,engine,persona});}});
      await activeBrowser.close();activeBrowser=null;
      console.log(`PASS ${persona} ${engine}`);
    }
    await stop(activeServer);activeServer=null;
    assert.equal(execFileSync('jj',['-R',stage.ledger,'diff','--summary'],{env:stage.env,encoding:'utf8'}).trim(),'','Fixture working copy remains clean');
  }
} catch(error) {
  console.error(error);process.exitCode=1;
} finally {
  await activeBrowser?.close();await stop(activeServer);
  if(process.env.CAPTURE!=='0')await contactSheet(manifest);
  await checkpoint();
  console.log('BROWSER_RESULTS '+JSON.stringify({engines,personas,themes,results,captures:manifest.length}));
  await cleanupArtifacts(!process.exitCode);
  console.log(`Screenshot contact sheet: ${shots}/index.html`);
}
