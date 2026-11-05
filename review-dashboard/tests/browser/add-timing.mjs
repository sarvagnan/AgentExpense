import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';

export async function holdAddResponse(page,pattern,method='GET'){
 let ready,release,taken=false;const entered=new Promise(r=>ready=r),gate=new Promise(r=>release=r),work=[];
 const handler=async route=>{if(taken||route.request().method()!==method)return route.fallback();taken=true;const task=(async()=>{const response=await route.fetch();ready();await gate;await route.fulfill({response});})();work.push(task);await task;};
 await page.route(pattern,handler);return {ready:entered,async release(){release();await Promise.all(work);await page.unroute(pattern,handler);}};
}
async function stable(locator,before,expect){await expect(locator).toBeEnabled();const after=await locator.boundingBox();for(const key of ['x','y','width','height'])assert.ok(Math.abs(before[key]-after[key])<1,`${key}: Add's control moved during held background work`);}
export async function runAddTiming({page,base,stage,persona,expect,control,go,refresh,record,quiesce,resetDrafts}){
 const reset=async()=>{await quiesce();await control('reset-workflows',{});await resetDrafts({persona,empty:true});await control('gate',{state:'ready'});await go('add/');};
 const words=page.locator('#expense-words'),upload=page.getByRole('button',{name:'Upload',exact:true}),button=page.getByRole('button',{name:'Continue',exact:true});
 await reset();const creation=await holdAddResponse(page,base+'api/drafts','POST');
 try{
  await words.fill('First meal 12');await creation.ready;await words.fill('Newer meal 14');await words.focus();const caret=await words.evaluate(n=>n.selectionStart);await upload.hover();const box=await upload.boundingBox();await creation.release();await expect(words).toHaveValue('Newer meal 14');await expect(words).toBeFocused();assert.equal(await words.evaluate(n=>n.selectionStart),caret);await stable(upload,box,expect);
 }finally{await creation.release();}
 record('Add held creation/autosave retains newer words, caret, focus and Upload pointer target');
 await reset();await words.fill('Next meal 14');await expect(button).toBeEnabled();const health=await holdAddResponse(page,base+'api/health');
 try{await refresh();await health.ready;await words.fill('Words during health check 16');await upload.hover();const box=await upload.boundingBox();await health.release();await stable(upload,box,expect);await expect(words).toHaveValue('Words during health check 16');await expect(page.locator('.add-blocking-notice')).toHaveCount(0);}finally{await health.release();}
 record('Add held health check keeps words and Upload target available');
 await runOfflineRetry({page,base,expect,record});
 await reset();const seeded=await control('add-result',{state:'working'}),manifest=JSON.parse(await readFile(join(stage.ledger,'ledger.json'),'utf8'));
 const result=await holdAddResponse(page,base+'api/drafts/'+seeded.id);
 try{
  await page.evaluate(({key,seeded})=>{sessionStorage.setItem(key,JSON.stringify([seeded]));window.dispatchEvent(new Event('add-results-changed'));},{key:`ledger-add-visit-${manifest.id}`,seeded});await result.ready;
  await words.fill('Meal during result lookup 18');await upload.hover();const box=await upload.boundingBox();await result.release();await stable(upload,box,expect);await expect(words).toHaveValue('Meal during result lookup 18');await expect(page.locator('.just-added')).toContainText('Working');
 }finally{await result.release();}
 const poll=await holdAddResponse(page,base+'api/drafts?include_added=true');
 try{await refresh();await poll.ready;await control('add-result',{id:seeded.id,state:'question'});await words.fill('Meal during poll 20');await upload.hover();const box=await upload.boundingBox();await refresh();await poll.release();await expect(page.locator('.just-added')).toContainText('Needs an answer');await stable(upload,box,expect);await expect(words).toHaveValue('Meal during poll 20');}finally{await poll.release();}
 record('Add held result lookup and superseded draft poll preserve input and pointer geometry');
 await reset();await control('scenario',{mode:'question'});await words.fill('Fictional payment');await expect(button).toBeEnabled();const save=await holdAddResponse(page,base+'api/drafts/*/save','POST');
 try{await button.click();await save.ready;await expect(words).toBeDisabled();await expect(button).toBeDisabled();await expect(page.locator('.add-savebar')).toContainText('Saving');await save.release();await expect(words).toBeEnabled();await expect(words).toBeFocused();await words.fill('Next expense 22');await refresh();await expect(words).toHaveValue('Next expense 22');await expect(page.locator('.add-savebar .live-error')).toHaveCount(0);}finally{await save.release();}
 record('Add held save locks deliberate input, prevents resubmission, and restores next-entry focus');
 await reset();
}

// One health check that times out is not "offline": a follow-up check 2 s later decides,
// and nothing on Add moves or becomes disabled in between. Two failures show the notice.
// A controlled clock fires the 5 s request timeout and the 2 s retry; health reads are held.
export async function runOfflineRetry({page,base,expect,record}){
 const context=await page.context().browser().newContext({viewport:page.viewportSize(),locale:'en-US',reducedMotion:'reduce'});
 try{
  const clock=await context.newPage();
  await clock.route('**/*',route=>{const url=route.request().url();return url.startsWith(base)||url.startsWith('blob:')||url.startsWith('data:')?route.continue():route.abort('blockedbyclient');});
  await clock.clock.install();await clock.goto(base+'add/');
  const words=clock.locator('#expense-words'),upload=clock.getByRole('button',{name:'Upload',exact:true}),notice=clock.locator('.add-blocking-notice');
  await expect(upload).toBeEnabled({timeout:30000});
  // Timers now run only when the test advances them; a few seconds ahead so a running clock cannot pass first.
  await clock.clock.pauseAt(await clock.evaluate(()=>Date.now()+5000));
  // The first words are saved (a new draft refreshes the page) before any health read is held.
  const saved=clock.waitForResponse(r=>/\/api\/drafts(\/[0-9a-f]+)?$/.test(new URL(r.url()).pathname)&&['POST','PATCH'].includes(r.request().method()));
  await words.fill('Meal during a slow check');await clock.clock.runFor(400);await saved;await clock.waitForLoadState('networkidle');
  let held=[],checks=0;const hold=route=>{checks++;held.push(route);};
  await clock.route('**/api/health',hold);
  await upload.hover();const box=await upload.boundingBox(),wordsBox=await words.boundingBox();
  const unmoved=async()=>{await expect(upload).toBeEnabled();await expect(words).toBeEnabled();for(const [locator,before] of [[upload,box],[words,wordsBox]]){const after=await locator.boundingBox();for(const key of ['x','y','width','height'])assert.ok(Math.abs(before[key]-after[key])<1,`${key}: Add moved while a health check was retried`);}};
  await clock.evaluate(()=>window.dispatchEvent(new Event('drafts-changed')));await expect.poll(()=>checks).toBe(1);
  // The clock is paused, so the retry is due 2 s after the failure whenever the page gets to it;
  // wait until the aborted read has reached the page before advancing again (under load it is late).
  const settled=async()=>{await expect.poll(()=>clock.evaluate(()=>new Promise(resolve=>{const channel=new MessageChannel();channel.port1.onmessage=()=>resolve(true);channel.port2.postMessage(0);}))).toBe(true);await clock.waitForTimeout(250);};
  const timedOut=clock.waitForEvent('requestfailed',{predicate:request=>request.url().endsWith('/api/health'),timeout:15000}).catch(()=>null);
  await clock.clock.runFor(5000);await timedOut;await settled();// the held read timed out: first failure
  // Typing while the follow-up check is pending is kept.
  await words.fill('Meal during a slow check 9');await clock.clock.runFor(1999);await clock.waitForTimeout(50);
  assert.equal(checks,1,'No follow-up check before OFFLINE_RETRY_MS');await expect(notice).toHaveCount(0);await unmoved();
  await clock.unroute('**/api/health',hold);for(const route of held.splice(0))await route.abort().catch(()=>{});
  const retried=clock.waitForRequest(request=>request.url().endsWith('/api/health'));await clock.clock.runFor(1);await retried;
  await expect(clock.locator('.add-savebar strong')).not.toHaveText('You’re offline');await clock.waitForTimeout(100);await expect(notice).toHaveCount(0);await unmoved();
  await expect(words).toHaveValue('Meal during a slow check 9');
  record('Add: one timed-out health check retries after 2 s and shows nothing; Add stays put and usable');
  // Two consecutive failures: offline after the follow-up check, not before. Here the
  // server answers that it is unavailable, so each failure arrives with its response.
  // Start from a quiet page: the follow-up pass of the previous check has finished.
  await clock.waitForLoadState('networkidle');await settled();
  let answered=()=>{};const answer=()=>new Promise(resolve=>answered=resolve);
  checks=0;const down=async route=>{checks++;await route.fulfill({status:200,contentType:'application/json',body:'{"ok":false}'});answered();};await clock.route('**/api/health',down);
  let next=answer();await clock.evaluate(()=>window.dispatchEvent(new Event('drafts-changed')));await next;await settled();
  await clock.clock.runFor(1999);await clock.waitForTimeout(50);assert.equal(checks,1,'No follow-up check before OFFLINE_RETRY_MS');await expect(notice).toHaveCount(0);await unmoved();
  next=answer();await clock.clock.runFor(1);await next;
  await expect(notice).toContainText('You’re offline');await expect(upload).toBeDisabled();assert.equal(checks,2);
  await expect(clock.locator('.add-savebar strong')).toHaveText('You’re offline');
  await clock.unroute('**/api/health',down);
  await notice.getByRole('button',{name:'Check connection',exact:true}).click();await expect(notice).toHaveCount(0);await expect(upload).toBeEnabled();await expect(words).toHaveValue('Meal during a slow check 9');
  await clock.evaluate(()=>localStorage.removeItem('ledger-add-v1'));
  record('Add: two consecutive failed health checks show offline; Check connection recovers');
 }finally{await context.close();}
}
