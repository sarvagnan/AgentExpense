import assert from 'node:assert/strict';
import {hold,forward} from './held.mjs';
import {expectInjectedHttp} from './expected-errors.mjs';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {gzipSync} from 'node:zlib';

export async function runUiIntent({page,base,persona,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}) {
  if(process.env.UI_INTENT_NO_NAVIGATION_TIMING==='1')await page.addInitScript(()=>{const read=performance.getEntriesByType.bind(performance);performance.getEntriesByType=type=>type==='navigation'?[]:read(type);});
  const get=async path=>{const r=await fetch(base+'api/'+path);assert.ok(r.ok,await r.clone().text());return r.json();};
  await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});await control('gate',{state:'ready'});
  const seed=await control('retention',{}),entry=seed.plain;
  const form=page.getByRole('article',{name:'Edit entry',exact:true}),inspector=page.locator('#transaction-inspector');
  const current=()=>go('explorer/?month=2026-11&entry='+entry);
  const open=async()=>{await current();await inspector.getByRole('button',{name:'Edit',exact:true}).click();await expect(form.getByLabel('Description',{exact:true})).toHaveValue('Willow lunch');await expect(form.getByRole('button',{name:'Cancel',exact:true})).toBeEnabled();};
  await open();const unchangedId=(await get('drafts')).find(d=>d.correction).id;await form.getByRole('button',{name:'Cancel',exact:true}).click();await expect(inspector).toBeVisible();
  assert.equal((await get('drafts')).filter(d=>d.correction).length,0,'Unchanged Cancel deletes the temporary draft');
  assert.equal((await fetch(base+'api/drafts/'+unchangedId)).status,404,'Cancel removes the folder, including Trash reads');
  await open();const changedId=(await get('drafts')).find(d=>d.correction).id;await form.getByLabel('Description',{exact:true}).fill('Willow changed lunch');
  await form.getByRole('button',{name:'Cancel',exact:true}).click();let question=page.getByRole('alertdialog',{name:'Discard changes?',exact:true});
  await expect(question).toBeVisible();await overflow('Cancel question');await snap('intent-cancel-question');await question.getByRole('button',{name:'Keep editing',exact:true}).click();
  await expect(form.getByLabel('Description',{exact:true})).toHaveValue('Willow changed lunch');
  // Hold the actual autosave while Cancel is chosen; it must finish before removal.
  let release,started=false;const held=new Promise(r=>{release=r;});
  const handler=async route=>{started=true;await held;await route.continue().catch(()=>{});};
  await page.route('**/api/drafts/*/hand-form',handler);
  await form.getByLabel('Notes',{exact:true}).fill('Kept during held autosave');await expect.poll(()=>started).toBe(true);
  await form.getByRole('button',{name:'Cancel',exact:true}).click();question=page.getByRole('alertdialog',{name:'Discard changes?',exact:true});
  await question.getByRole('button',{name:'Discard changes',exact:true}).click();await expect(question).toBeVisible();
  release();await page.unroute('**/api/drafts/*/hand-form',handler);await expect(inspector).toBeVisible();
  assert.equal((await get('drafts')).filter(d=>d.correction).length,0);
  assert.equal((await fetch(base+'api/drafts/'+changedId)).status,404);
  assert.equal(await page.evaluate(()=>Object.keys(sessionStorage).some(key=>key.includes('hand-')&&key.includes('-form'))),false);
  record('intent Cancel unchanged and changed, Keep editing, held autosave cancellation, no draft or browser recovery');
  await open();const cancelReply=await hold(page,'**/api/drafts/*/cancel-edit',{method:'POST'});
  try{
    await form.getByRole('button',{name:'Cancel',exact:true}).click();await cancelReply.ready;
    await expect(form.getByLabel('Description',{exact:true})).toBeDisabled();await snap('intent-cancelling');
    await page.goBack();await expect(inspector).toBeVisible();await inspector.getByRole('button',{name:'Close details',exact:true}).click();
    await page.evaluate(()=>sessionStorage.removeItem('ledger-view:add'));
    await page.locator('.sidebar a[href$="add/"],.phone-live-nav a[href$="add/"]').filter({visible:true}).click();
    const newer=page.locator('#expense-words');
    // Replay the observed controlled-value writeback between input capture and
    // bubbling, so every engine checks the race without relying on its speed.
    await newer.evaluate(input=>input.addEventListener('input',()=>{input.value=input.defaultValue;},{capture:true,once:true}));
    await newer.fill('Willow words after leaving Cancel');
    await expect(newer).toHaveValue('Willow words after leaving Cancel');
    await expect.poll(()=>page.evaluate(()=>JSON.parse(localStorage.getItem('ledger-add-v1')||'null')?.text)).toBe('Willow words after leaving Cancel');
    await cancelReply.release();await page.waitForLoadState('networkidle');
    assert.equal(new URL(page.url()).pathname,new URL(base+'add/').pathname);await expect(newer).toHaveValue('Willow words after leaving Cancel');
    await newer.fill('');await page.waitForLoadState('networkidle');
  }finally{await cancelReply.release();}
  record('intent held Cancel acknowledgement cannot navigate over newer Add words after Back');


  await open();await form.getByLabel('Description',{exact:true}).fill('Willow recovered edits');
  await form.getByRole('button',{name:'Back',exact:true}).click();await expect(inspector).toBeVisible();
  await go('queue/');await expect(form).toHaveCount(0);
  if(width>760){await expect(page.getByRole('heading',{name:'Choose an item',exact:true})).toBeVisible();await snap('intent-queue-choose');}
  else {await expect(page.locator('.draft-detail-wrap')).toBeHidden();await snap('intent-queue-choose');}
  const edit=(await get('drafts')).find(d=>d.correction?.entry_id===entry);assert.ok(edit);
  await go('queue/?item='+edit.id);await expect(page.getByRole('heading',{name:'Unsaved entry changes',exact:true})).toBeVisible();await expect(form).toHaveCount(0);await expect(page.getByRole('article',{name:'Item details',exact:true})).not.toContainText('Amounts or accounts changed');await snap('intent-kept-edit-summary');
  await page.getByRole('button',{name:'Continue editing',exact:true}).click();await expect(form.getByLabel('Description',{exact:true})).toHaveValue('Willow recovered edits');
  await form.getByRole('button',{name:'Back to Queue',exact:true}).click();await expect(form).toHaveCount(0);assert.ok(!new URL(page.url()).searchParams.has('item'));
  await go('queue/?draft='+edit.id);await current();await inspector.getByRole('button',{name:'Close details',exact:true}).click();await page.locator('.sidebar a[href$="queue/"],.phone-live-nav a[href$="queue/"]').filter({visible:true}).click();await expect(form).toHaveCount(0);assert.equal(new URL(page.url()).search,'');
  await go('queue/?draft='+edit.id);const claim=await hold(page,base+'api/drafts/'+edit.id+'/claim',{method:'POST'});
  try{await page.getByRole('button',{name:'Continue editing',exact:true}).click();await claim.ready;await page.reload();}finally{await claim.release();}
await expect(form).toHaveCount(0);await expect(page.getByRole('button',{name:'Continue editing',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Continue editing',exact:true}).click();await form.getByRole('button',{name:'Cancel',exact:true}).click();
  await page.getByRole('alertdialog').getByRole('button',{name:'Discard changes',exact:true}).click();await expect(form).toHaveCount(0);
  assert.ok(!new URL(page.url()).searchParams.has('draft'));
  await open();await form.getByLabel('Description',{exact:true}).fill('Willow reload recovery');await page.reload();
  await expect(page.getByRole('heading',{name:'Queue',exact:true})).toBeVisible();await expect(form).toHaveCount(0);assert.equal(new URL(page.url()).search,'');
  record('intent Back and Cancel from Queue clear selection; reload retains edits without reopening the form');

  const kept=(await get('drafts')).find(d=>d.correction?.entry_id===entry);
  await go('queue/?draft='+kept.id);
  let obsoleteToken='',obsoleteReleases=0;const obsoleteFailures=[],releaseUrl=base+'api/drafts/'+kept.id+'/release';
  const firstClaim=await hold(page,base+'api/drafts/'+kept.id+'/claim',{method:'POST',transform:(body,response)=>{assert.equal(response.status(),200);obsoleteToken=JSON.parse(body).claim_token;assert.match(obsoleteToken,/^[a-f0-9]{64}$/);return body;}});
  const refusedRelease=forward(page,async(response,route)=>{
    assert.equal(response.status(),409,'The obsolete token cannot release the newer editor');
    assert.equal((await response.json()).detail,'This page no longer owns the item; reopen Finish it yourself');
    expectInjectedHttp(page,releaseUrl,409);await route.fulfill({response});obsoleteReleases++;
  },{failures:obsoleteFailures});
  const obsoleteRelease=route=>route.request().method()==='POST'&&route.request().postDataJSON()?.claim_token===obsoleteToken?refusedRelease(route):route.fallback();
  await page.route(releaseUrl,obsoleteRelease);
  try{
    await page.getByRole('button',{name:'Continue editing',exact:true}).click();await firstClaim.ready;
    // Stay in the same document: navigation unmounts the old editor, then reopens it.
    await page.locator('.sidebar a[href$="queue/"],.phone-live-nav a[href$="queue/"]').filter({visible:true}).click();
    await page.locator(`a.draft-row[href$="?draft=${kept.id}"]`).click();
    await page.getByRole('button',{name:'Continue editing',exact:true}).click();
    await expect(form.getByLabel('Description',{exact:true})).toHaveValue('Willow reload recovery');
    await form.getByLabel('Notes',{exact:true}).fill('New owner before the old claim reply');
    await expect.poll(async()=>(await get('drafts/'+kept.id)).hand_form.entry.notes).toEqual(['New owner before the old claim reply']);
    await firstClaim.release();
    await expect.poll(()=>{assert.deepEqual(obsoleteFailures,[]);return obsoleteReleases;}).toBe(1);
    await form.getByLabel('Notes',{exact:true}).fill('New owner after the old claim reply');
    await expect.poll(async()=>(await get('drafts/'+kept.id)).hand_form.entry.notes).toEqual(['New owner after the old claim reply']);
    await expect(form.locator('[role="alert"]')).toHaveCount(0);
    await form.getByRole('button',{name:'Back to Queue',exact:true}).click();
  }finally{await firstClaim.release();await page.unroute(releaseUrl,obsoleteRelease);}
  record('intent same-document reopen keeps its newer claim and form after an obsolete first response');
  await go('queue/?item='+kept.id);const [discarded]=await Promise.all([page.waitForResponse(r=>r.url()===base+'api/drafts/'+kept.id&&r.request().method()==='DELETE'),page.getByRole('button',{name:'Discard',exact:true}).click()]);assert.equal(discarded.status(),204);await expect.poll(()=>new URL(page.url()).searchParams.has('item')).toBe(false);
  await go('trash/?item='+kept.id);await page.locator('.trash-row').filter({hasText:'Willow lunch'}).getByRole('button',{name:'Restore',exact:true}).click();
  await expect.poll(async()=>(await get('drafts')).find(d=>d.id===kept.id)?.state,{timeout:180000}).toBe('question');
  await go('queue/?draft='+kept.id);await expect(page.getByRole('heading',{name:'Unsaved entry changes',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Continue editing',exact:true}).click();await expect(form.getByLabel('Description',{exact:true})).toHaveValue('Willow reload recovery');
  await form.getByRole('button',{name:'Back to Queue',exact:true}).click();
  record('intent Trash Restore retains correction edits in Needs you and opens the correction form');
  const restored=await get('drafts/'+kept.id),proposal=structuredClone(restored.correction.original_form);proposal.entry.description='Willow current saved entry';
  const corrected=await fetch(base+'api/entries/'+entry+'/corrections/'+crypto.randomUUID().replaceAll('-',''),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:restored.correction.version,proposal,sources:restored.correction.sources,approved_accounts:[],reason:'Fictional intervening correction'})});
  assert.ok(corrected.ok,await corrected.clone().text());
  await go('queue/?draft='+kept.id);await page.getByRole('button',{name:'Continue editing',exact:true}).click();
  await expect(inspector).toContainText('Willow current saved entry');await expect(page.locator('.explorer>.live-notice')).toContainText('The saved entry changed');await expect(form).toHaveCount(0);
  assert.equal((await get('drafts/'+kept.id)).hand_form.entry.description,'Willow reload recovery');
  await inspector.getByRole('button',{name:'Close details',exact:true}).click();await expect(inspector).toBeHidden();
  await page.getByRole('button',{name:'Previous month',exact:true}).click();await expect(page.locator('.explorer>.live-notice')).toHaveCount(0);
  await page.getByRole('button',{name:'Next month',exact:true}).click();await expect(page.locator('.explorer>.live-notice')).toContainText('The saved entry changed');
  record('intent a restored edit changed since discard opens the current entry, retains old edits, and keeps its notice on its own month');
  await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});await control('rules',{});
  await go('forecast/?section=rules');await expect(page.getByRole('combobox',{name:'Sort rules',exact:true})).toBeVisible();
  const sortHistory=await page.evaluate(()=>history.length);
  await page.getByRole('combobox',{name:'Sort rules',exact:true}).selectOption('name');await page.getByRole('combobox',{name:'Order',exact:true}).selectOption('desc');
  assert.equal(await page.evaluate(()=>history.length),sortHistory,'Rules selects replace history');
  let names=await page.locator('.rules-list .rule-row h2').allTextContents();assert.deepEqual(names,[...names].sort((a,b)=>b.localeCompare(a,undefined,{numeric:true,sensitivity:'base'})));
  await snap('intent-rules-sort');await overflow('Rules sort control');await page.reload();await expect(page.getByRole('combobox',{name:'Sort rules',exact:true})).toHaveValue('name');await expect(page.getByRole('combobox',{name:'Order',exact:true})).toHaveValue('desc');
  const id=await page.locator('.rules-list .rule-row').first().getAttribute('id');await go('forecast/?section=rules&sort=amount#'+id);await expect(page.locator('#'+id)).toBeFocused();
  record('intent Rules sorting is accessible, retained in URL, and hash links focus the rule');
  await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});
  await control('retention',{});await page.clock.setFixedTime(new Date('2026-11-10T12:00:00Z'));
  const data=JSON.parse(await readFile(join(stage.dash,'app/data/dashboard.json'),'utf8'));
  const currency=data.meta.ledger.defaultCurrency;
  const month=data.months.find(row=>row.month==='2026-11'&&row.currency===currency);
  assert.ok(month);month.total=630;month.daily=[{day:1,amount:30},{day:10,amount:100},{day:20,amount:500}];
  const handlerAverage=route=>route.fulfill({status:200,contentType:'application/gzip',body:gzipSync(JSON.stringify(data))});
  await page.route('**/dashboard.json.gz',handlerAverage);
  try{
    await go('explorer/?month=2026-11');
    const average=page.locator('#explorer-summary-average');
    if(width<760)await page.getByRole('button',{name:'More month figures',exact:true}).click();
    await expect(average).toContainText('Total to date ÷ 10 days');
    const amount=await page.evaluate(currency=>new Intl.NumberFormat(document.documentElement.lang||navigator.language,{style:'currency',currency,minimumFractionDigits:2,maximumFractionDigits:2}).format(13),currency);
    await expect(average.locator('.metric-value')).toContainText(amount);
    await go('atlas/?month=2026-11');
    const detail=page.locator('.atlas-detail').filter({hasText:'total to date ÷ 10'}).first();
    await expect(detail).toContainText('total to date ÷ 10');await expect(detail.locator('.day-chart-bars>i')).toHaveCount(30);
    const bottom=await detail.locator('.day-chart-average').evaluate(node=>parseFloat(node.style.bottom));
    assert.ok(Math.abs(bottom-2.6)<0.01,'Average line uses 130 to date / 10, with future spending still on the whole-month chart');
    record('intent fixed local date average agrees in Explorer, month panel and whole-month day chart, excluding future spending');
  }finally{await page.unroute('**/dashboard.json.gz',handlerAverage);}
  await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});
}

