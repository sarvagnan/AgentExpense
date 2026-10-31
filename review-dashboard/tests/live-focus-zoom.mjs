import { temporaryDirectory } from './test-isolation.mjs';
// Native 200% browser zoom, never CSS zoom or a reduced viewport substitute.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,rm,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
process.env.TMPDIR=process.env.EXPENSES_TEST_TMP;
const require=createRequire(process.env.HOME+'/.cache/expense-playwright/'),{chromium}=require('playwright'),{expect}=require('playwright/test');
const base=process.argv[2]||'http://127.0.0.1:15173/',stage=process.env.STAGE;
assert.ok(stage,'Set STAGE for the screenshot and measurement output');
await mkdir(join(stage,'logs-r2'),{recursive:true});
const profile=await temporaryDirectory('expense-live-focus-zoom-'),results=[];let context;
try{
 context=await chromium.launchPersistentContext(profile,{headless:true,channel:'chromium',viewport:null,downloadsPath:join(profile,'downloads')});
 const page=context.pages()[0],settings=await context.newPage();
 await settings.goto('chrome://settings/appearance');await settings.waitForFunction(()=>typeof chrome.settingsPrivate?.setDefaultZoom==='function');await settings.evaluate(()=>chrome.settingsPrivate.setDefaultZoom(2));
 const cdp=await context.newCDPSession(page);
 let draft={id:'f'.repeat(32),state:'editing',text:'',pieces:null,files:[],created_at:'2026-10-30T12:00:00Z',runs:[],questions:[]};
 await context.route('**/api/**',route=>{const path=new URL(route.request().url()).pathname,method=route.request().method();if(path.endsWith('/health'))return route.fulfill({json:{ok:true}});if(method==='PATCH'||method==='POST')draft={...draft,...JSON.parse(route.request().postData()||'{}')};return route.fulfill({json:path.endsWith('/drafts')&&method==='GET'?[draft]:draft});});
 for(const appearance of ['light','dark'])for(const physical of [640,780,824,1440]){
  await page.emulateMedia({colorScheme:appearance,reducedMotion:'reduce'});
  await cdp.send('Emulation.setDeviceMetricsOverride',{width:physical,height:physical===1440?900:1688,deviceScaleFactor:1,mobile:false});
  await page.goto(base+'add/');await expect(page.locator('#expense-words')).toBeEnabled();await page.locator('#expense-words').fill('petrol 875.25 8.5l 190km');await expect.poll(()=>page.evaluate(()=>innerWidth)).toBe(physical/2);await page.locator('#expense-words').focus();
  let controls=0;
  for(const key of ['Tab','Shift+Tab'])for(let n=0;n<14;n++){
   await page.keyboard.press(key);await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
   const box=await page.evaluate(()=>{const active=document.activeElement;if(!active?.closest('.live-page')||active.closest('.add-savebar'))return null;const b=active.getBoundingClientRect();const bottom=Math.min(innerHeight,...[...document.querySelectorAll('.add-savebar,.phone-live-nav')].filter(e=>getComputedStyle(e).display!=='none').map(e=>e.getBoundingClientRect().top));return {top:b.top,bottom:b.bottom,limit:bottom,name:active.getAttribute('aria-label')||active.textContent};});
   if(box){controls++;assert.ok(box.top>=-1&&box.bottom<=box.limit-5,`${physical}px ${appearance}: ${JSON.stringify(box)}`);}
  }
  assert.ok(controls>=12,'Both directions must exercise the composer controls');
  await page.getByRole('button',{name:'Edit Category',exact:true}).focus();await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  const folder=join(stage,'shots-r2',`${physical===1440?'desktop':'phone'}-${appearance}`);await mkdir(folder,{recursive:true});
  const png=await cdp.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await writeFile(join(folder,`add-focus-200percent-${physical/2}.png`),Buffer.from(png.data,'base64'));
  results.push({appearance,physical,effective:physical/2,controls,status:'PASS'});console.log(`PASS ${appearance}: 200% zoom, ${physical/2} CSS px, ${controls} focused controls visible`);
 }
 await settings.evaluate(()=>chrome.settingsPrivate.setDefaultZoom(1));await writeFile(join(stage,'logs-r2/live-focus-zoom.json'),JSON.stringify(results,null,2)+'\n');
}finally{await context?.close();await rm(profile,{recursive:true,force:true});}
