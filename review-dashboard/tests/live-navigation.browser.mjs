import { temporaryDirectory, disposableDashboard, stopChildren } from './test-isolation.mjs';
// Tight regression for a slow live-health request during whole-document navigation.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
process.env.TMPDIR=process.env.EXPENSES_TEST_TMP;
const fixture=await disposableDashboard(),dash=fixture.dash,work=await temporaryDirectory('expense-live-navigation-');
const stage=process.env.STAGE || work;
const pw=createRequire(process.env.HOME+'/.cache/expense-playwright/')('playwright');
const {expect}=createRequire(process.env.HOME+'/.cache/expense-playwright/')('playwright/test');
const vite=spawn('npm',['run','dev','--','--port','15175','--strictPort'],{cwd:dash,env:{...fixture.env,EXPENSES_DEV:'0',npm_config_cache:join(stage,'.npm-cache')},stdio:['ignore','pipe','pipe']});
let log='';vite.stdout.on('data',b=>log+=b);vite.stderr.on('data',b=>log+=b);
const base='http://127.0.0.1:15175/';let browser;
const errors=[];
try{
 for(let n=0;n<100;n++){try{if((await fetch(base)).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
 browser=await pw.webkit.launch({headless:true,downloadsPath:join(work,'downloads')});
 const context=await browser.newContext({viewport:{width:1440,height:900}});
 const page=await context.newPage();page.on('pageerror',e=>errors.push({message:e.message,stack:e.stack}));
 await page.route('**/api/health',async r=>{await new Promise(resolve=>setTimeout(resolve,150));await r.continue().catch(()=>{});});
 for(let n=0;n<20;n++){await page.goto(base+(n%2?'explorer/':'account-summary/'));await page.locator('.content h1').waitFor();}
 await page.waitForTimeout(300);
 assert.deepEqual(errors,[]);await context.close();console.log('PASS WebKit: 20 full navigations with delayed health; no uncaught page errors.');
}catch(e){console.error(e);process.exitCode=1;}
finally{await browser?.close();await stopChildren([vite]);await mkdir(join(stage,'logs'),{recursive:true});await writeFile(join(stage,'logs/live-navigation.json'),JSON.stringify({errors,log},null,2)+'\n');}
