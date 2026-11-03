import {useProjectNode} from '../../scripts/node-toolchain.mjs';
useProjectNode();
import { temporaryDirectory, disposableDashboard, stopChildren } from './test-isolation.mjs';
// A denied write can leave a readable ID for an older, already submitted draft.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
process.env.TMPDIR=process.env.EXPENSES_TEST_TMP;
const require=createRequire(process.env.HOME+'/.cache/expense-playwright/'),{chromium}=require('playwright'),{expect}=require('playwright/test');
const fixture=await disposableDashboard(),dash=fixture.dash,stage=process.env.STAGE,work=await temporaryDirectory('expense-stale-recovery-'),base='http://127.0.0.1:15179/';
const vite=spawn('npm',['run','dev','--','--port','15179','--strictPort'],{cwd:dash,env:{...fixture.env,EXPENSES_DEV:'0'},stdio:['ignore','pipe','pipe']});let log='';vite.stdout.on('data',b=>log+=b);vite.stderr.on('data',b=>log+=b);let browser;const output=[];
try{
 await mkdir(join(stage,'logs-r2'),{recursive:true});
 for(let n=0;;n++){try{if((await fetch(base)).ok)break;}catch{}if(n===100||vite.exitCode!==null)throw Error(log);await new Promise(r=>setTimeout(r,100));}
 browser=await chromium.launch({headless:true,downloadsPath:join(work,'downloads')});
 for(const appearance of ['light','dark'])for(const previous of ['submitted','missing','no-id']){
  const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,colorScheme:appearance});
  const old='a'.repeat(32),id='b'.repeat(32),stored={id:previous==='no-id'?null:old,text:'Older saved words',edits:{},updatedAt:10};
  const server={id,state:'editing',text:'petrol 300 from the newer server copy',client_updated_at:80,pieces:{pattern:'fuel',edits:{amount:'',outlet:'Recovered outlet'},fields:{amount:{value:'',source:'agent'},outlet:{value:'Recovered outlet',source:'typed'}}},created_at:'2026-10-30T01:00:00Z',files:[{id:'c'.repeat(32),original_name:'Recovered.pdf',media_type:'application/pdf',size:123}],runs:[],questions:[]};
  await context.addInitScript(stored=>{localStorage.setItem('ledger-add-v1',JSON.stringify(stored));const original=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k==='ledger-add-v1')throw new DOMException('quota','QuotaExceededError');return original.call(this,k,v);};},stored);
  let release;const gate=new Promise(r=>release=r);let creates=0;
  await context.route('**/api/**',async route=>{const path=new URL(route.request().url()).pathname,method=route.request().method();if(path.endsWith('/health'))return route.fulfill({json:{ok:true}});if(method==='PATCH'){Object.assign(server,route.request().postDataJSON());return route.fulfill({json:server});}if(method==='POST'){creates++;return route.fulfill({json:server});}if(path==='/api/drafts'){await gate;return route.fulfill({json:[server]});}if(path.endsWith(old)){if(previous==='missing')return route.fulfill({status:404,json:{detail:'Unknown draft'}});return route.fulfill({json:{...server,id:old,state:'added',text:'Older saved words',files:[],client_updated_at:10}});}return route.fulfill({json:server});});
  const page=await context.newPage();await page.goto(base+'add/');await page.locator('#expense-words').waitFor();await page.waitForTimeout(700);assert.equal(creates,0,'Recovery must finish before an editing draft can be duplicated');release();
  await expect(page.locator('#expense-words')).toHaveValue(server.text);await expect(page.getByRole('button',{name:'Remove Recovered.pdf'})).toBeVisible();await expect(page.getByRole('button',{name:/^Edit Outlet: Recovered outlet/})).toBeVisible();await expect(page.getByText(/Not saved on this device/)).toBeVisible();
  await expect(page.getByRole('button',{name:/^Edit Amount:/})).not.toContainText('300');await page.locator('#expense-words').fill('petrol 999 12.5l 222km now');await expect(page.getByRole('button',{name:/^Edit Trip:/})).toContainText('222 km');await expect(page.getByRole('button',{name:/^Edit Amount:/})).not.toContainText('999');await expect.poll(()=>server.text).toBe('petrol 999 12.5l 222km now');
  await page.reload();await expect(page.locator('#expense-words')).toHaveValue(server.text);await expect(page.getByRole('button',{name:/^Edit Amount:/})).not.toContainText('999');assert.equal(creates,0);
  output.push(`PASS ${appearance}/${previous}: newer server words, pieces and files beat stale local recovery without duplicate creation`);console.log(output.at(-1));await context.close();
 }
}finally{await browser?.close();await stopChildren([vite]);await writeFile(join(stage,'logs-r2/live-stale-recovery.log'),output.join('\n')+'\n');}
