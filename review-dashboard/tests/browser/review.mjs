import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';

export async function runReview({browser,context,page,base,persona,engine,theme,width,height,stage,expect,control,go,overflow,snap,record,quiesce}){
 const get=async()=>{const r=await fetch(base+'api/review');assert.ok(r.ok,await r.clone().text());return r.json();};
 const reset=async()=>{await quiesce();await control('reset-review',{});await go('review/');await expect(page.locator('.server-review-content')).toBeVisible();};
 const waitCount=async count=>{await expect.poll(async()=>(await get()).count,{timeout:180000}).toBe(count);await expect(page.locator('.server-review .page-header')).toContainText(`${count} waiting`);};
 await reset();let state=await get();
 assert.equal(state.count,persona==='asha'?24:persona==='jordan'?2:0);
 assert.equal(state.important,persona==='asha'?3:0);
 const nav=page.locator(width>760?'.sidebar nav':'.phone-live-nav').locator('a[href$="review/"]');
 if(state.count){await expect(nav.locator('.nav-count')).toContainText(String(state.count));await expect(nav.locator('.important')).toHaveCount(state.important?1:0);}else await expect(nav.locator('.nav-count')).toHaveCount(0);
 await overflow('Review initial');await snap('review-'+(persona==='asha'?'asha':persona==='jordan'?'jordan':'empty'));
 await page.setViewportSize({width:320,height:915});await overflow('Review at 320');await page.setViewportSize({width,height});
 if(!state.count){await expect(page.getByText('Nothing to review.',{exact:true})).toBeVisible();record('5 empty Review uses server count and has no badge');return;}
 if(persona==='asha'){
  const cards=page.locator('.review-card[data-kind="duplicate"]');await expect(cards).toHaveCount(2);
  await expect(cards.first()).toContainText('more than three days');
  const boxes=await cards.first().locator('.review-comparison>div').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,bottom:r.bottom};}));
  if(width>760)assert.ok(boxes[1].x>boxes[0].x);else assert.ok(boxes[1].y>=boxes[0].bottom);
  await expect(page.locator('.review-doubt')).toContainText('workshop');
  const device=await browser.newContext({viewport:{width,height},colorScheme:theme});const second=await device.newPage();
  const secondErrors=[];second.on('pageerror',e=>secondErrors.push(e.message));second.on('console',m=>{if(m.type()==='error')secondErrors.push(m.text());});
  try{
   await second.goto(base+'review/');await expect(second.locator('.server-review-content')).toBeVisible();
   await cards.first().getByRole('button',{name:'These are separate',exact:true}).click();await waitCount(23);
   await expect(page.getByText('Marked as separate.',{exact:true})).toBeVisible();await snap('review-separate');
   await expect(second.locator('.server-review .page-header')).toContainText('23 waiting');
   await page.getByRole('button',{name:'Undo',exact:true}).click();await waitCount(24);await expect(cards).toHaveCount(2);
   await page.locator('.review-history summary').click();
   await expect(page.locator('.review-history')).toContainText('Separation undone');
   await cards.first().getByRole('button',{name:'These are separate',exact:true}).click();await waitCount(23);
   await page.locator('.review-history').getByRole('button',{name:'Undo separation',exact:true}).click();await waitCount(24);
   record('5 one-click separation, immediate/history undo, and count synchronized across two devices');
   await cards.first().getByRole('button',{name:'Same expense',exact:true}).click();
   let dialog=page.getByRole('dialog',{name:'Same expense?',exact:true});await expect(dialog).toBeVisible();
   await expect(dialog).toContainText('Tamarind Pantry');await expect(dialog).toContainText('Pantry restock');
   assert.doesNotMatch(await dialog.textContent(),/\b[0-9a-f]{16}\b|ST-072|RC-019/);
   await snap('review-confirm');await page.setViewportSize({width:320,height:915});await overflow('Merge dialog at 320');await page.setViewportSize({width,height});
   await dialog.getByRole('button',{name:'Cancel',exact:true}).click();await expect(dialog).toHaveCount(0,{timeout:180000});assert.equal((await get()).count,24);
   const before=(await get()).items.find(i=>i.kind==='duplicate');const kept=before.entries.find(e=>e.id===before.kept_id),removed=before.entries.find(e=>e.id!==before.kept_id);
   await cards.first().getByRole('button',{name:'Same expense',exact:true}).click();dialog=page.getByRole('dialog',{name:'Same expense?',exact:true});
   await dialog.getByRole('button',{name:'Keep statement entry',exact:true}).click();await waitCount(23);
   await expect(page.locator('.review-settled')).toContainText('Pantry restock');await snap('review-merged');
   state=await get();assert.equal(state.entries[removed.id],undefined);assert.deepEqual(state.entries[kept.id].postings,kept.postings);assert.ok(removed.sources.every(s=>state.entries[kept.id].sources.includes(s)));
   await expect(second.locator('.server-review .page-header')).toContainText('23 waiting');
   assert.deepEqual(secondErrors,[]);
   record('5 merge confirmation/cancel names human entries, preserves statement postings and moves receipts/notes');
  }finally{await device.close();}
 }
 // Every populated persona exercises Follow-up and Resolve, using a genuinely
 // pending entry rather than a local-only decision.
 state=await get();const item=state.items.find(i=>i.kind==='pending'),entry=item.entries[0],initialCount=state.count;
 const open=async()=>{await page.locator('.review-rest-row').filter({hasText:entry.description}).getByRole('button',{name:'Review',exact:true}).click();await expect(page.getByRole('dialog')).toBeVisible();};
 if(!await page.locator('.review-rest-row').filter({hasText:entry.description}).count())await page.getByRole('button',{name:/Show .* more/}).click();
 await open();let dialog=page.getByRole('dialog');
 await dialog.getByLabel('Ledger account',{exact:true}).fill('Expenses:Education');await dialog.getByLabel('Label (optional)',{exact:true}).fill('Willow learning');await dialog.getByLabel('Notes (optional)',{exact:true}).fill('Checked invented receipt');
 await dialog.getByRole('button',{name:'Mark for Follow-up',exact:true}).click();await expect(dialog).toHaveCount(0,{timeout:180000});await waitCount(initialCount);
 state=await get();assert.equal(state.decisions[entry.id].status,'followup');assert.equal(state.entries[entry.id].description,entry.description);
 await open();dialog=page.getByRole('dialog');await expect(dialog.getByLabel('Notes (optional)',{exact:true})).toHaveValue('Checked invented receipt');
 // Deliver the successful commit with an unreadable response: the app must keep
 // its recovery request, then safely retry the same ID after document reload.
 await page.route('**/api/review/actions',async route=>{const response=await route.fetch({timeout:180000});assert.ok(response.ok());await route.fulfill({status:200,contentType:'application/json',body:'{'});},{times:1});
 await dialog.getByRole('button',{name:'Resolve',exact:true}).click();
 await expect(page.locator('[aria-label="Unsent review"]')).toBeVisible();
 await expect(dialog.getByRole('button',{name:'Resolve',exact:true})).toBeEnabled({timeout:180000});
 await go('review/');await expect(page.locator('[aria-label="Unsent review"]')).toBeVisible();
 await page.getByRole('button',{name:'Retry saved request',exact:true}).click();await expect(page.locator('[aria-label="Unsent review"]')).toHaveCount(0,{timeout:180000});await waitCount(initialCount-1);
 state=await get();assert.equal(state.entries[entry.id].status,'confirmed');assert.equal(state.entries[entry.id].description,'Willow learning');
 assert.equal(state.history.filter(h=>h.action==='resolve'&&h.changes.some(c=>c.entry_id===entry.id)).length,1);
 record('5 Follow-up retains entry; Resolve saves account/label/notes; reload retries a lost response exactly once');
 // Export an actual file, restore the disposable baseline, import and preview
 // it through the server, then compare the committed decision across contexts.
 const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'Export decisions',exact:true}).click();const download=await downloaded;
 const file=join(stage.root,`review-${engine}-${theme}-${width}.json`);await download.saveAs(file);
 const payload=JSON.parse(await readFile(file,'utf8'));assert.equal(payload.reviewVersion,2);
 await reset();await page.getByLabel('Import review decisions',{exact:true}).setInputFiles(file);dialog=page.getByRole('dialog',{name:'Move saved decisions',exact:true});await expect(dialog).toBeVisible();
 await expect(dialog).toContainText('decisions apply');await dialog.getByRole('button',{name:'Apply valid decisions',exact:true}).click();await expect(dialog).toHaveCount(0,{timeout:180000});
 state=await get();assert.ok(state.history.some(h=>h.action==='import'));assert.equal(state.decisions[entry.id].status,'resolved');
 record('5 version 2 Export/Import previews and applies valid decisions in a server commit');
 await reset();state=await get();const ordinary=state.items.find(i=>i.kind==='pending').entries[0];
 const key=JSON.parse(await readFile(join(stage.ledger,'ledger.json'))).review?.storageKey || `agentexpense-review-v2:${JSON.parse(await readFile(join(stage.ledger,'ledger.json'))).id}`;
 await page.evaluate(({key,id})=>localStorage.setItem(key,JSON.stringify({[id]:{status:'followup',account:'',label:'',notes:'Move my saved work',updatedAt:'2026-11-01T12:00:00Z'}})),{key,id:ordinary.id});
 await go('review/');await expect(page.getByText('Move 1 saved decisions into the ledger?',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Preview saved decisions',exact:true}).click();dialog=page.getByRole('dialog',{name:'Move saved decisions',exact:true});await expect(dialog).toContainText('1 decisions apply');
 await dialog.getByRole('button',{name:'Apply valid decisions',exact:true}).click();await expect(dialog).toHaveCount(0,{timeout:180000});
 await expect(page.getByText('Move 1 saved decisions into the ledger?',{exact:true})).toHaveCount(0);
 assert.equal((await get()).decisions[ordinary.id].notes,'Move my saved work');
 await page.evaluate(key=>{localStorage.removeItem(key);localStorage.removeItem(key+':later');},key);
 record('5 legacy per-ledger browser decisions prompt, preview and audited migration');
 await reset();await overflow('Review after restoration');
}
