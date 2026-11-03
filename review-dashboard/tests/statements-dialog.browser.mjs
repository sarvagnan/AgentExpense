import {useProjectNode} from '../../scripts/node-toolchain.mjs';
useProjectNode();
import { temporaryDirectory } from './test-isolation.mjs';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
process.env.TMPDIR=process.env.EXPENSES_TEST_TMP;
const {chromium}=createRequire(process.env.HOME+'/.cache/expense-playwright/')('playwright');
const {expect}=createRequire(process.env.HOME+'/.cache/expense-playwright/')('playwright/test');
const work=await temporaryDirectory('expense-statement-dialog-');
const browser=await chromium.launch({headless:true,downloadsPath:work});
try{for(const appearance of ['light','dark'])for(const width of [320,390]){
 const context=await browser.newContext({viewport:{width,height:844},hasTouch:true,isMobile:true,colorScheme:appearance,reducedMotion:'reduce'});
 await context.route('**/*',r=>['127.0.0.1','localhost'].includes(new URL(r.request().url()).hostname)?r.continue():r.abort());
 const s={id:'a'.repeat(32),state:'review',original_name:'fixture.pdf',created_at:new Date().toISOString(),statement:{periodStart:'2026-09-29',periodEnd:'2026-10-28',previousBalance:'0',totalDue:'0',purchasesCharges:'0',paymentsCredits:'0',rows:[]},plan:{items:[],unmatched:[],counts:{}},checks:[]};
 await context.route('**/api/statements',r=>r.fulfill({json:{sessions:[s],filed:[],accounts:[]}}));
 const page=await context.newPage();await page.goto((process.argv[2]||'http://127.0.0.1:5173/')+'statements/?session='+s.id);await page.getByRole('button',{name:'Review apply',exact:true}).click();
 const dialog=page.getByRole('dialog');await expect(dialog).toBeVisible();const box=await dialog.boundingBox();console.log(await dialog.evaluate(e=>{const layer=e.parentElement,c=getComputedStyle(e),l=getComputedStyle(layer);return JSON.stringify({dialog:{width:c.width,minWidth:c.minWidth,position:c.position,transform:c.transform},layer:{rect:layer.getBoundingClientRect().toJSON(),width:l.width,grid:l.gridTemplateColumns,padding:l.padding},viewport:{innerWidth,scrollX,width:visualViewport.width,scale:visualViewport.scale}});}));assert.ok(box.x>=0&&box.x+box.width<=width+1,JSON.stringify({width,box}));
 for(const control of await dialog.getByRole('button').all()){const b=await control.boundingBox();assert.ok(b.width>=44&&b.height>=44);}
 console.log(`PASS ${appearance}/${width}: dialog fits the physical viewport; every target ≥44×44`);await context.close();
}}finally{await browser.close();}
// Repeat the consequential portal at native 200% Chromium zoom.
const {mkdir,writeFile,rm}=await import('node:fs/promises');
const {join}=await import('node:path');
const profile=await temporaryDirectory('expense-statement-dialog-zoom-');let zoom;
try{
 zoom=await chromium.launchPersistentContext(profile,{headless:true,channel:'chromium',viewport:null,downloadsPath:join(profile,'downloads')});
 const page=zoom.pages()[0],settings=await zoom.newPage();await settings.goto('chrome://settings/appearance');await settings.waitForFunction(()=>typeof chrome.settingsPrivate?.setDefaultZoom==='function');await settings.evaluate(()=>chrome.settingsPrivate.setDefaultZoom(2));
 await zoom.route('**/api/health',r=>r.fulfill({json:{ok:true}}));
 const s={id:'a'.repeat(32),state:'review',original_name:'fixture.pdf',created_at:new Date().toISOString(),statement:{periodStart:'2026-09-29',periodEnd:'2026-10-28',previousBalance:'0',totalDue:'0',purchasesCharges:'0',paymentsCredits:'0',rows:[]},plan:{items:[],unmatched:[],counts:{}},checks:[]};
 await zoom.route('**/api/statements',r=>r.fulfill({json:{sessions:[s],filed:[],accounts:[]}}));
 const cdp=await zoom.newCDPSession(page);
 for(const appearance of ['light','dark'])for(const physical of [640,780]){
  await page.emulateMedia({colorScheme:appearance,reducedMotion:'reduce'});await cdp.send('Emulation.setDeviceMetricsOverride',{width:physical,height:1688,deviceScaleFactor:1,mobile:false});
  await page.goto((process.argv[2]||'http://127.0.0.1:5173/')+'statements/?session='+s.id);await expect.poll(()=>page.evaluate(()=>innerWidth)).toBe(physical/2);await page.getByRole('button',{name:'Review apply',exact:true}).click();
  const dialog=page.getByRole('dialog'),box=await dialog.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=physical/2+1,JSON.stringify(box));
  for(const control of await dialog.getByRole('button').all()){const b=await control.boundingBox();assert.ok(b.width>=44&&b.height>=44);}
  if(process.env.STAGE){const folder=join(process.env.STAGE,'shots-r2',`phone-${appearance}`);await mkdir(folder,{recursive:true});const png=await cdp.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await writeFile(join(folder,`statement-apply-200percent-${physical/2}.png`),Buffer.from(png.data,'base64'));}
  console.log(`PASS ${appearance}/${physical/2}: native 200% zoom, contained dialog and ≥44×44 controls`);
 }
 await settings.evaluate(()=>chrome.settingsPrivate.setDefaultZoom(1));
}finally{await zoom?.close();await rm(profile,{recursive:true,force:true});}
