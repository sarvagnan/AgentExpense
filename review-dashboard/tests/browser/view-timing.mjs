import assert from 'node:assert/strict';
import {gunzipSync,gzipSync} from 'node:zlib';
import {holdResponse as hold} from './retention-timing.mjs';
import {hold as holdShared} from './held.mjs';

// A Queue poll that sees a new addition (from Add, another device or a receipt) reloads
// the dashboard while the person reads, types, navigates or saves elsewhere. Each case
// holds that reload open, acts during it, releases it and checks that nothing was lost.
function holdData(page,base,change=()=>{}){
 return holdShared(page,base+'data/dashboard.json.gz',{transform:body=>{const data=JSON.parse(gunzipSync(body));change(data);return gzipSync(JSON.stringify(data));}});
}
// The added row copies a real one, so every screen reading Queue rows can render it.
const addition=id=>rows=>{const real=rows.find(d=>d.state==='added');assert.ok(real,'The fixture has an added row');return [{...real,id:id.repeat(32),created_at:new Date().toISOString(),receipt_reading:null,result:{...real.result,entry_id:id.repeat(16)}},...rows];};
const renamed=(id,description)=>data=>{const row=data.transactions.find(t=>t.id===id||t.storeId===id);assert.ok(row,'The seeded entry is in the data');row.description=description;};

export async function runViewTiming({page,base,persona,expect,control,go,refresh,record,quiesce,resetDrafts}){
 await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});await control('gate',{state:'ready'});await control('add-pause',{enabled:false});
 const seed=await control('retention',{});
 // App shell: the first data read is unreadable; Retry's read is held, then lands.
 const dataUrl=base+'data/dashboard.json.gz';let unreadable=true;
 const broken=async route=>{if(!unreadable)return route.fallback();unreadable=false;await route.fulfill({status:200,contentType:'application/gzip',body:'not ledger data'});};
 await page.route(dataUrl,broken);
 try{
  await page.goto(base+'explorer/');const failure=page.locator('.load-failure');await expect(failure).toContainText('Ledger unavailable');
  const retry=failure.getByRole('button',{name:'Retry',exact:true}),held=await holdData(page,base);
  await retry.click();await held.ready;await expect(page.locator('.loading-state')).toContainText('Loading ledger data…');await expect(retry).toHaveCount(0);
  await held.release();await expect(page.locator('.loading-state')).toHaveCount(0);await expect(page.locator('.content h1').first()).toBeFocused();
 }finally{await page.unroute(dataUrl,broken);}
 record('views timing: a held Retry after an unreadable first load lands once and focuses the view');
 // Reload while the poll's addition is held, so each case controls when the new snapshot lands.
 async function reloadHeld(id,change){const data=await holdData(page,base,change),poll=await hold(page,base+'api/drafts?include_added=true','GET',addition(id));await refresh();await poll.ready;await poll.release();await data.ready;return data;}

 await go('explorer/?month=2026-11&entry='+seed.plain);
 const inspector=page.locator('#transaction-inspector'),edit=inspector.getByRole('button',{name:'Edit',exact:true}),title=inspector.locator('#entry-title');
 await expect(edit).toBeVisible();
 await page.evaluate(()=>{const w=window;w.__editMissing=0;w.__inspector=document.querySelector('#transaction-inspector');new MutationObserver(()=>{if(![...document.querySelectorAll('#transaction-inspector button')].some(b=>b.textContent==='Edit'))w.__editMissing++;}).observe(document.body,{subtree:true,childList:true});});
 let data=await reloadHeld('a',renamed(seed.plain,'Willow lunch, reloaded'));
 try{
  await edit.focus();await edit.hover();const box=await edit.boundingBox();
  await data.release();await expect(title).toHaveText('Willow lunch, reloaded');
  await expect(edit).toBeFocused();const next=await edit.boundingBox();for(const key of ['x','y','width','height'])assert.ok(Math.abs(next[key]-box[key])<1,`Edit moved during the reload (${key})`);
  assert.deepEqual(await page.evaluate(()=>({same:window.__inspector===document.querySelector('#transaction-inspector'),missing:window.__editMissing})),{same:true,missing:0},'The open inspector and its Edit button stay through a data reload');
 }finally{await data.release();}
 record('views timing: a held data reload keeps the open inspector, its Edit target and focus');

 await page.keyboard.press('Escape');await expect(inspector).toHaveCount(0);
 const search=page.locator('#explorer-search');await search.click();await page.keyboard.type('Wil');
 data=await reloadHeld('b',renamed(seed.plain,'Willow lunch, searched'));
 try{
  await expect(search).toBeFocused();await data.release();await expect(page.locator(`[data-row-id="${seed.plain}"]`)).toContainText('Willow lunch, searched');
  await page.keyboard.type('low');await expect(search).toHaveValue('Willow');await expect(search).toBeFocused();
 }finally{await data.release();}
 record('views timing: Explorer search keeps focus and typing through a held data reload');

 data=await reloadHeld('c',renamed(seed.plain,'Willow lunch, navigated'));
 try{
  // Back and forward during the reload: the newer view keeps its place and focus.
  await page.evaluate(path=>{history.pushState({},'',path);dispatchEvent(new PopStateEvent('popstate'));},new URL(base).pathname+'journal/?month=2026-11');
  const heading=page.locator('.content h1').first();await expect(heading).toHaveText(/Journal/);await expect(heading).toBeFocused();
  await data.release();await expect(page.locator('.content')).toContainText('Willow lunch, navigated');
  await expect(heading).toBeFocused();assert.ok(page.url().includes('/journal/'));
 }finally{await data.release();}
 record('views timing: navigating during a held data reload keeps the new view and its focus');

 await go('account-summary/?account='+encodeURIComponent('Assets:Bank:Willow everyday'));
 await page.getByRole('button',{name:'Account details',exact:true}).click();
 const details=page.getByRole('dialog',{name:'Account details',exact:true}),done=details.getByRole('button',{name:'Done',exact:true});
 await expect(done).toBeVisible();await done.focus();
 data=await reloadHeld('d',renamed(seed.plain,'Willow lunch, in Accounts'));
 try{
  await data.release();await expect(page.locator('.accounts-container')).toContainText('Willow lunch, in Accounts');
  await expect(details).toBeVisible();await expect(done).toBeFocused();
 }finally{await data.release();}
 await done.click();await expect(details).toHaveCount(0);
 record('views timing: Account details stays open and focused through a data reload');

 // Settings saves the name while an older read is still held: the screen must end on the saved name.
 await go('explorer/');const identity=page.locator('.identity:visible'),settings=page.getByRole('dialog',{name:'Settings',exact:true}),name=settings.getByLabel('Name',{exact:true});
 await identity.click();await expect(name).toBeFocused();const original=await name.inputValue();await settings.getByRole('button',{name:'Cancel',exact:true}).click();
 data=await reloadHeld('e');
 try{
  await identity.click();await name.fill(original+' Timing');
  const saved=page.waitForResponse(r=>r.url()===base+'api/settings/name'&&r.request().method()==='POST',{timeout:120000});
  await settings.getByRole('button',{name:'Save changes',exact:true}).click();assert.equal((await saved).status(),200);
  await data.release();await expect(settings).toHaveCount(0,{timeout:120000});await expect(identity).toContainText(original+' Timing');
 }finally{await data.release();}
 await identity.click();await name.fill(original);await settings.getByRole('button',{name:'Save changes',exact:true}).click();await expect(settings).toHaveCount(0,{timeout:120000});await expect(identity).toContainText(original);
 record('views timing: a Settings save during a held older read ends on the saved name');
 // The agent's status arrives from the first Queue poll while Settings is already open
 // (?settings=1 opens it with the page). Nothing below the pointer may move when it lands.
 const status=await hold(page,base+'api/agent-status');
 try{
  await page.goto(base+'explorer/?settings=1');await page.locator('.content h1').first().waitFor();await status.ready;
  await expect(settings).toContainText('Checking the agent…');
  const appearance=settings.locator('.appearance');await appearance.scrollIntoViewIfNeeded();await appearance.hover();const box=await appearance.boundingBox();
  await status.release();await expect(settings.locator('.agent-ready')).toBeVisible();
  await expect(appearance).toBeEnabled();const next=await appearance.boundingBox();for(const key of ['x','y','width','height'])assert.ok(Math.abs(next[key]-box[key])<1,`Settings moved under the pointer when the agent status arrived (${key})`);
  await settings.getByRole('button',{name:'Cancel',exact:true}).click();await expect(settings).toHaveCount(0);
 }finally{await status.release();}
 record('views timing: the agent status arriving in open Settings moves nothing under the pointer');
 await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});
}
