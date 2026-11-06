import assert from 'node:assert/strict';

// Hold real responses, rather than depending on one engine being slower.
async function hold(page,pattern,method='GET',transform=null,count=1){
 let resolveReady,release;const ready=new Promise(r=>resolveReady=r),gate=new Promise(r=>release=r);let seen=0,completed=0;
 const work=[];
 const handler=async route=>{
  if(route.request().method()!==method||seen>=count)return route.fallback();
  seen++;const task=(async()=>{const response=await route.fetch();let body=await response.body();if(transform)body=Buffer.from(JSON.stringify(transform(JSON.parse(body))));if(++completed===count)resolveReady();await gate;await route.fulfill({response,body});})();work.push(task);await task;
 };
 await page.route(pattern,handler);
 return {ready,async release(){release();await Promise.all(work);await page.unroute(pattern,handler);}};
}
async function unchanged(locator,box,expect){
 await expect(locator).toBeEnabled();const next=await locator.boundingBox();assert.ok(next&&box);for(const key of ['x','y','width','height'])assert.ok(Math.abs(next[key]-box[key])<1,`${key}: control moved during a background response`);
}
export async function runRetentionTiming({page,base,persona,expect,control,go,refresh,snap=async()=>{},record,quiesce,resetDrafts}){
 const get=async path=>{const r=await fetch(base+'api/'+path);assert.ok(r.ok,await r.clone().text());return r.json();};
 await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});await control('gate',{state:'ready'});await control('add-pause',{enabled:true});
 const queue=await control('retention-queue',{});await go('queue/?draft='+queue.editing);
 const detail=page.getByRole('article',{name:'Item details'}),words=detail.getByRole('textbox',{name:'Your words',exact:true}),discard=detail.getByRole('button',{name:'Discard',exact:true});
 const poll=await hold(page,base+'api/drafts?include_added=true'),autosave=await hold(page,base+'api/drafts/'+queue.editing,'PATCH');
 try{
  await refresh();await poll.ready;await words.fill('Willow words kept during held autosave 37');await autosave.ready;
  await discard.hover();const box=await discard.boundingBox();await poll.release();await unchanged(discard,box,expect);await expect(words).toHaveValue('Willow words kept during held autosave 37');
  await discard.click();await autosave.release();await expect(page.locator('.drafts-page > .live-notice')).toContainText('Item moved to Trash.',{timeout:180000});
  const gone=await get('drafts/'+queue.editing);assert.ok(gone.trashed_at);assert.equal(gone.text,'Willow words kept during held autosave 37');await expect(page.locator('.draft-detail [role=alert]')).toHaveCount(0);
 }finally{await poll.release();await autosave.release();}
 record('6f C timing: Queue poll and held autosave preserve words, pointer target and Discard');
 await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});await control('add-pause',{enabled:false});const seed=await control('retention',{});
 await go('explorer/?month=2026-11&entry='+seed.plain);const inspector=page.locator('#transaction-inspector');await expect(inspector.getByRole('button',{name:'Delete',exact:true})).toBeVisible();
 const receiptPoll=await hold(page,base+'api/drafts?include_added=true','GET',rows=>rows.map(d=>d.id!==seed.added?d:{...d,receipt_reading:{state:'question',answer:{date:'2026-11-02',amount:'31.00',currency:seed.currency},entry:{date:'2026-11-01'},entry_totals:{[seed.currency]:'30.00'}}}));
 try{
  await refresh();await receiptPoll.ready;await inspector.getByRole('button',{name:'Delete',exact:true}).click();const dialog=page.getByRole('dialog',{name:'Move this entry to Trash?',exact:true}),move=dialog.getByRole('button',{name:'Move to Trash',exact:true});
  await move.hover();const box=await move.boundingBox();await receiptPoll.release();await expect(inspector.getByRole('region',{name:'Receipt reading'})).toContainText('The receipt says');await unchanged(move,box,expect);
  const save=await hold(page,base+'api/retention/operations/*','POST');try{await move.click();await save.ready;await refresh();await expect(dialog).toBeVisible();await expect(dialog).not.toContainText('Could not');await save.release();await expect(page.locator('.retention-notice')).toContainText('Willow lunch moved to Trash',{timeout:180000});}finally{await save.release();}
 }finally{await receiptPoll.release();}
 record('6f C timing: receipt dates arriving during Delete keep the confirmation target and committed action');
 await go('trash/');const row=page.locator('.trash-row'),restore=row.getByRole('button',{name:'Restore',exact:true});await expect(row).toHaveCount(1);
 const trashPoll=await hold(page,base+'api/trash','GET',null,2),restoreSave=await hold(page,base+'api/retention/operations/*','POST',value=>({...value,data_stale:true}));
 try{
  await refresh();await trashPoll.ready;await restore.hover();const box=await restore.boundingBox();await unchanged(restore,box,expect);await restore.click();await restoreSave.ready;
  await trashPoll.release();await expect(row).toHaveCount(1);await expect(page.locator('.trash-page [role=alert]')).toHaveCount(0);await restoreSave.release();await expect(page.locator('.trash-page [role=alert]')).toContainText('The change was saved, but dashboard data could not refresh.');await expect(page.locator('.trash-page [role=alert] strong')).toHaveText('Saved. The views didn’t refresh.');await expect(restore).toBeEnabled();await snap('retention-restore-refresh-error');
  const replay=page.waitForResponse(r=>r.url().startsWith(base+'api/retention/operations/')&&r.request().method()==='POST');await restore.click();assert.equal((await (await replay).json()).replayed,true,'Restore retries must replay the same committed operation');await expect(row).toHaveCount(0,{timeout:180000});
  assert.ok((await get('drafts/'+seed.added)).result.entry_id===seed.plain);await expect(page.locator('.trash-page [role=alert]')).toHaveCount(0);
 }finally{await trashPoll.release();await restoreSave.release();}
 record('6f C timing: Restore during held Trash polls and save rejects stale rows and errors');
 await go('explorer/');await page.locator('.identity:visible').click();const settings=page.getByRole('dialog',{name:'Settings',exact:true});await expect(settings.getByLabel('Delete an entry',{exact:true})).toBeEnabled();
 const preview=await hold(page,base+'api/settings/retention-preview','POST');
 try{
  await settings.getByLabel('Delete an entry',{exact:true}).selectOption('14');await preview.ready;const trash=settings.getByLabel('Keep in Trash',{exact:true});await trash.scrollIntoViewIfNeeded();await trash.hover();const box=await trash.boundingBox();
  await settings.getByLabel('Name',{exact:true}).fill('Willow timing ledger');await trash.scrollIntoViewIfNeeded();await trash.hover();const next=await trash.boundingBox();await preview.release();await expect(settings).toContainText(/older (entry becomes|entries become) deletable again/);await unchanged(trash,next,expect);assert.ok(box);await expect(settings.getByLabel('Name',{exact:true})).toHaveValue('Willow timing ledger');
  await settings.getByRole('button',{name:'Cancel',exact:true}).click();await expect(settings).toHaveCount(0);
 }finally{await preview.release();}
 record('6f C timing: time-limit preview cannot shift or disable controls or overwrite typed settings');
 await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});
}

export {hold as holdResponse};
