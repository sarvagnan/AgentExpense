import assert from 'node:assert/strict';
import {hold} from './held.mjs';
import {expectInjectedHttp} from './expected-errors.mjs';
export async function runDataDeparture({page,base,expect,control,go,record,snap=async()=>{}}){
 await page.addInitScript(()=>{if(!['http:','https:'].includes(location.protocol))return;for(const type of ['pagehide','pageshow'])window.addEventListener(type,event=>{const key='review-data-lifecycle',events=JSON.parse(sessionStorage.getItem(key)||'[]');events.push({type,persisted:event.persisted,path:location.pathname});sessionStorage.setItem(key,JSON.stringify(events));});});
 await go('add/');await control('data-refresh',{mode:'hold'});const held=await hold(page,base+'api/data-status',{limit:30000});
 try{
  await control('data-refresh',{mode:'retry'});await page.evaluate(()=>window.dispatchEvent(new Event('drafts-changed')));await held.ready;
  await control('data-refresh',{mode:'release'});await control('data-refresh',{mode:'wait'});
  await page.goto(base+'queue/',{waitUntil:'domcontentloaded'});await expect(page.locator('.content h1').first()).toHaveText('Queue');
  await held.release();await page.goBack({waitUntil:'domcontentloaded'});await expect(page.locator('.content h1').first()).toHaveText('Add');await page.goForward({waitUntil:'domcontentloaded'});await expect(page.locator('.content h1').first()).toHaveText('Queue');
  // Force the persisted lifecycle as well: routing can prevent a real browser
  // from caching a document, while its pagehide/pageshow handlers must still work.
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})));
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));
  const events=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('review-data-lifecycle')||'[]'));assert.ok(events.some(event=>event.type==='pagehide'));assert.ok(events.some(event=>event.type==='pageshow'&&event.persisted));console.log('DATA_LIFECYCLE '+JSON.stringify(events));
  await expect(page.locator('.content h1').first()).toHaveText('Queue');
  const gzip=await hold(page,url=>url.origin===new URL(base).origin&&url.pathname.startsWith(new URL(base).pathname+'api/data/')&&url.pathname.endsWith('/dashboard.json.gz'),{limit:30000});
  try{await page.goto(base+'add/',{waitUntil:'domcontentloaded'});await gzip.ready;await page.goto(base+'queue/',{waitUntil:'domcontentloaded'});await gzip.release();await expect(page.locator('.content h1').first()).toHaveText('Queue');record('held generation gzip read retires on full-document departure and the next document loads');}finally{await gzip.release();}
  const bad=async route=>{await page.unroute(base+'api/data-status',bad);expectInjectedHttp(page,route.request().url(),500);await route.fulfill({status:500,contentType:'application/json',body:'{}'});};await page.route(base+'api/data-status',bad);await page.reload({waitUntil:'domcontentloaded'});await expect(page.getByRole('heading',{name:'Ledger unavailable',exact:true})).toBeVisible();await snap('immediate-real-data-load-failure');await page.getByRole('button',{name:'Retry',exact:true}).click();await expect(page.locator('.content h1').first()).toHaveText('Queue');
  record('data lifecycle: held read across full-document navigation; back/forward and forced persisted lifecycle resume; a new document loads and a real HTTP failure remains visible/retryable');
 }finally{await held.release();await control('data-refresh',{mode:'release'});await control('data-refresh',{mode:'wait'});}
}
