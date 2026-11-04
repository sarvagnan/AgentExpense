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
