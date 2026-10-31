import { temporaryDirectory } from './test-isolation.mjs';
// Capture transient pressed and tooltip states without mutating generated data.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
process.env.TMPDIR=process.env.EXPENSES_TEST_TMP;
const require=createRequire(process.env.HOME+'/.cache/expense-playwright/'),{chromium}=require('playwright'),{expect}=require('playwright/test');
const base=process.argv[2]||'http://127.0.0.1:15177/',stage=process.env.STAGE,work=await temporaryDirectory('expense-live-polish-'),results=[];
assert.ok(stage);
await mkdir(join(stage,'logs-r2'),{recursive:true});
const browser=await chromium.launch({headless:true,downloadsPath:join(work,'downloads')});
try{
 for(const device of ['phone','desktop'])for(const appearance of ['light','dark']){
  const context=await browser.newContext({viewport:device==='phone'?{width:390,height:844}:{width:1440,height:900},hasTouch:device==='phone',colorScheme:appearance,reducedMotion:'reduce'}),id='e'.repeat(32);
  const draft={id,state:'editing',text:'petrol 875.25 8.5l 190km',pieces:null,files:[{id:'d'.repeat(32),original_name:'Receipt.pdf',media_type:'application/pdf',size:1024}],created_at:'2026-10-30T00:00:00Z',questions:[],runs:[]};
  await context.addInitScript(({id,text})=>localStorage.setItem('ledger-add-v1',JSON.stringify({id,text,edits:{}})),draft);
  await context.route('**/api/**',route=>route.fulfill({json:route.request().url().endsWith('/health')?{ok:true}:route.request().url().includes('include_added')?[draft]:draft}));
  await mkdir(join(stage,'shots-r2',`${device}-${appearance}`),{recursive:true});
  const page=await context.newPage();await page.goto(base+'add/');const remove=page.getByRole('button',{name:'Remove Receipt.pdf',exact:true});await expect(remove).toBeVisible();await remove.focus();await expect(remove).toBeFocused();assert.equal(await page.locator('.file-remove').evaluate(e=>getComputedStyle(e,'::after').opacity),'1');await page.screenshot({path:join(stage,'shots-r2',`${device}-${appearance}`,'add-remove-tooltip.png'),fullPage:true});
  if(device==='phone'){const views=page.getByRole('button',{name:'Views',exact:true});await views.click();await page.locator('#expense-words').click();await expect(views).toBeFocused();await expect(views).toHaveAttribute('aria-expanded','false');}
  const piece=page.getByRole('button',{name:/^Edit Amount:/});await piece.focus();await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));const rect=await piece.boundingBox();await page.mouse.move(rect.x+10,rect.y+10);const idle=await piece.evaluate(e=>getComputedStyle(e).backgroundColor);await page.mouse.down();assert.notEqual(await piece.evaluate(e=>getComputedStyle(e).backgroundColor),idle);await page.screenshot({path:join(stage,'shots-r2',`${device}-${appearance}`,'add-piece-pressed.png'),fullPage:true});await page.mouse.up();await page.keyboard.press('Escape');
  results.push(`PASS ${device}/${appearance}: focus tooltip and pressed-piece captures`);console.log(results.at(-1));await context.close();
 }
}finally{await browser.close();await writeFile(join(stage,'logs-r2/live-polish.log'),results.join('\n')+'\n');}
