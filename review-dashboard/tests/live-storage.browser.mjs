import { temporaryDirectory, disposableDashboard, stopChildren } from './test-isolation.mjs';
// Denied local and server persistence must be honest and protect departure.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {writeFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
process.env.TMPDIR=process.env.EXPENSES_TEST_TMP;
const require=createRequire(process.env.HOME+'/.cache/expense-playwright/'),{chromium}=require('playwright'),{expect}=require('playwright/test');
const fixture=await disposableDashboard(),dash=fixture.dash,stage=process.env.STAGE,work=await temporaryDirectory('expense-live-storage-'),base='http://127.0.0.1:15178/';
const vite=spawn('npm',['run','dev','--','--port','15178','--strictPort'],{cwd:dash,env:{...fixture.env,EXPENSES_DEV:'0'},stdio:['ignore','pipe','pipe']});let log='';vite.stdout.on('data',b=>log+=b);vite.stderr.on('data',b=>log+=b);let browser;const output=[];
try{
 await mkdir(join(stage,'logs-r2'),{recursive:true});
 for(let n=0;;n++){try{if((await fetch(base)).ok)break;}catch{}if(n===100||vite.exitCode!==null)throw Error(log);await new Promise(r=>setTimeout(r,100));}
 browser=await chromium.launch({headless:true,downloadsPath:join(work,'downloads')});
 for(const appearance of ['light','dark']){
  const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,colorScheme:appearance});await context.grantPermissions(['clipboard-read','clipboard-write']);
  await context.addInitScript(()=>{const original=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){if(key.startsWith('ledger-add')||key.startsWith('ledger-answer'))throw new DOMException('denied','QuotaExceededError');return original.call(this,key,value);};});
  let allow=false;const id='a'.repeat(32),draft={id,state:'question',text:'Coffee',pieces:null,created_at:'2026-10-30T00:00:00Z',files:[],runs:[],questions:[{question:'Which bank paid?',answer:null}],result:null};
  await context.route('**/api/**',route=>{const url=new URL(route.request().url()),method=route.request().method();if(url.pathname.endsWith('/health'))return route.fulfill({json:{ok:true}});if(method==='POST'&&url.pathname.endsWith('/drafts')&&allow)return route.fulfill({json:{...draft,id:'b'.repeat(32),state:'editing',text:'Words with nowhere to save'}});if(method==='PATCH'&&url.pathname.includes('b'.repeat(32))&&allow)return route.fulfill({json:{...draft,id:'b'.repeat(32),state:'editing',text:'Words with nowhere to save'}});if(method==='GET'&&url.pathname.includes('b'.repeat(32)))return route.fulfill({json:{...draft,id:'b'.repeat(32),state:'editing'}});if(method==='GET')return route.fulfill({json:url.pathname.endsWith('/drafts')?[draft]:draft});if(!allow)return route.fulfill({status:503,json:{detail:'Server storage unavailable'}});return route.fulfill({status:200,json:draft});});
  const page=await context.newPage();
  const protectedUnload=()=>page.evaluate(()=>{const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented;});
  await page.goto(base+`drafts/?draft=${id}`);await page.getByLabel('Which bank paid?').fill('Unsent Savings Account');await expect(page.getByText(/Could not save this answer to Drafts/)).toBeVisible();assert.equal(await protectedUnload(),true);
  await page.getByRole('button',{name:'Copy answer'}).click();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'Unsent Savings Account');
  await page.getByRole('navigation',{name:'Quick navigation'}).getByRole('link',{name:'Add',exact:true}).click();assert.equal(await protectedUnload(),true,'Answer protection survives leaving its route');
  allow=true;await page.getByRole('navigation',{name:'Quick navigation'}).getByRole('link',{name:/Drafts/}).click();await page.locator('a.draft-row').click();await page.getByRole('button',{name:'Retry saving answer'}).click();await expect(page.getByText('Answer saved to Drafts',{exact:true})).toBeVisible();
  await page.getByRole('navigation',{name:'Quick navigation'}).getByRole('link',{name:'Add',exact:true}).click();assert.equal(await protectedUnload(),false,'A durable answer no longer guards departure');allow=false;
  await page.locator('#expense-words').fill('Words with nowhere to save');await expect(page.getByText(/Could not save to Drafts/)).toBeVisible();await page.getByRole('button',{name:'Copy words'}).click();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'Words with nowhere to save');assert.equal(await protectedUnload(),true);
  allow=true;await page.getByRole('button',{name:'Retry saving'}).click();await expect(page.locator('.add-savebar strong')).toHaveText('Saved to Drafts');assert.equal(await protectedUnload(),false,'Durable words no longer guard departure');
  await page.getByRole('navigation',{name:'Quick navigation'}).getByRole('link',{name:/Drafts/}).click();await page.locator('a.draft-row').click();await expect(page.getByLabel('Which bank paid?')).toHaveValue('Unsent Savings Account');await expect(page.getByText('Answer saved to Drafts',{exact:true})).toBeVisible();
  output.push(`PASS ${appearance}: denied storage and server writes expose copy/retry, preserve route state and guard departure`);console.log(output.at(-1));await context.close();
 }
}finally{await browser?.close();await stopChildren([vite]);await writeFile(join(stage,'logs-r2/live-storage.log'),output.join('\n')+'\n');}
