import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {gone} from './held.mjs';

export async function openLiveRules({page,base,expect}) {
  // Exercise Forecast navigation with the existing live connection, rather than
  // racing a fresh document's independent connection initialization.
  if(!page.url().startsWith(base)){await page.goto(base+'add/');await page.locator('.content h1').first().waitFor();}
  await page.waitForLoadState('networkidle');
  const forecast=page.getByRole('link',{name:'Forecast',exact:true});
  if(!await forecast.isVisible())await page.getByRole('button',{name:'Views',exact:true}).click();
  await forecast.click();await expect(page.getByRole('button',{name:'Overview',exact:true})).toHaveAttribute('aria-current','page');await page.waitForLoadState('networkidle');
  let release;const held=new Promise(resolve=>{release=resolve;});let requested=false,hostReads=0,offline=false;
  const pattern='**/add-patterns.json.gz';
  const deferred=async route=>{hostReads++;await held;await route.fulfill({status:200,contentType:'application/json',body:'{"rules":[]}'}).catch(error=>{if(!gone(page,error))throw error;});};
  const health='**/api/health',unavailable=route=>route.fulfill({status:200,contentType:'application/json',body:'{"ok":false}'});
  const track=request=>{if(request.url()===base+'api/rules')requested=true;};
  await page.route(pattern,deferred);page.on('request',track);
  try {
    await page.getByRole('button',{name:'Rules',exact:true}).click();
    await expect.poll(()=>requested).toBe(true);
    await expect(page.getByRole('button',{name:'New rule',exact:true})).toBeEnabled();
    await page.route(health,unavailable);offline=true;await page.evaluate(()=>window.dispatchEvent(new Event('drafts-changed')));
    await expect(page.getByText('Rules are read-only here.',{exact:false})).toContainText('Reconnect');await expect(page.getByRole('button',{name:'New rule',exact:true})).toBeDisabled();
    await page.unroute(health,unavailable);offline=false;await page.evaluate(()=>window.dispatchEvent(new Event('drafts-changed')));
    await expect(page.getByRole('button',{name:'New rule',exact:true})).toBeEnabled();
  } finally {
    if(offline)await page.unroute(health,unavailable);
    release();page.off('request',track);await page.unroute(pattern,deferred);await page.waitForLoadState('networkidle');
  }
  assert.equal(hostReads,1,'Connection changes must not restart the one-time host snapshot read');
  await expect(page.getByRole('article',{name:'Birch rent',exact:true})).toBeVisible();
}

export async function runRules({page,base,persona,engine,theme,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}) {
  await page.clock.setFixedTime(new Date('2026-11-02T12:00:00Z'));
  const get=async path=>{const response=await fetch(base+'api/'+path);assert.ok(response.ok,await response.clone().text());return response.json();};
  const clickOp=async button=>{const [result]=await Promise.all([page.waitForResponse(r=>r.url().includes('/api/rules/operations/')&&r.request().method()==='POST',{timeout:180000}),button.click()]);assert.ok(result.ok(),await result.text());};
  const wait=async(id,state='added')=>{await expect.poll(async()=>(await get('drafts/'+id)).state,{timeout:180000}).toBe(state);return get('drafts/'+id);};
  const reload=async()=>{await page.waitForLoadState('networkidle');await page.reload();await page.waitForLoadState('networkidle');};
  await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});await control('gate',{state:'ready'});
  const seed=await control('rules',{});
  const byName=async name=>(await get('rules')).rules.find(r=>r.description===name);
  const words=()=>page.locator('#expense-words'),guess=()=>page.getByRole('complementary',{name:'Usual payment'}),saved=()=>page.getByRole('article',{name:'Added payment'}).first();
  const type=async text=>{await words().fill(text);await expect(words()).toHaveValue(text);await expect(guess()).toBeVisible();};
  // Captures wait for the words check and the live match to settle: the bar's button is enabled then.
  const settled=async()=>{await expect(page.locator('.add-savebar .button')).toBeEnabled();await page.mouse.move(1,1);};
  const add=async(text,configure=async()=>{})=>{
    await type(text);await configure();await expect(guess().getByRole('button',{name:'Add as usual',exact:true})).toBeEnabled();
    await guess().getByRole('button',{name:'Add as usual',exact:true}).click();
    await expect(words()).toHaveValue('');
    const draft=await expect.poll(async()=>(await get('drafts?include_added=true')).find(d=>d.text===text)?.id).toBeTruthy().then(async()=>(await get('drafts?include_added=true')).find(d=>d.text===text));
    const result=await wait(draft.id);await refresh();await expect(page.locator('.just-added-row').first()).toContainText(result.result.description);return result;
  };
  await go('add/');await expect(words()).toBeEnabled();
  const countPath=join(stage.root,'fake.count');
  const fakeCount=async()=>readFile(countPath,'utf8').catch(()=> '0');
  const beforeFake=await fakeCount();
  await type('Birch rent 100 '+seed.currency);await expect(guess()).toContainText('Matched ‘birch’, ‘rent’');await expect(guess()).toContainText(new RegExp('100'));
  // A fixed card: Add as usual is the only primary action, and the bar says what to do (grade B-2 N1).
  await expect(guess().getByRole('button',{name:'Add as usual',exact:true})).toBeEnabled();await settled();
  await expect(page.locator('.add-savebar .button')).toHaveText('Send to the agent instead');await expect(page.locator('.add-savebar .button')).not.toHaveClass(/\bprimary\b/);
  await expect(page.locator('.add-savebar strong')).toHaveText('Add as usual above, or send it to the agent');
  assert.deepEqual(await page.locator('.add-page .button.primary').filter({visible:true}).allInnerTexts(),['Add as usual']);
  await overflow('rule guess');await snap('rule-guess');
  await guess().getByRole('button',{name:'Not this',exact:true}).click();await expect(guess()).toHaveCount(0);
  await expect.poll(async()=>(await get('drafts')).find(d=>d.text.startsWith('Birch rent'))?.declined_rules).toEqual([seed.rules[0].id]);
  await reload();await expect(words()).toHaveValue('Birch rent 100 '+seed.currency);await expect(guess()).toHaveCount(0);
  assert.equal(await fakeCount(),beforeFake,'Not this never invokes the fake agent');
  assert.equal((await get('drafts')).filter(d=>d.state!=='editing').length,0);
  // One shared word is two quiet suggestions, not a pick list; choosing one opens its card (grade B-2 N2).
  await type('Clover');await expect(guess()).toContainText('Is this one of your usual payments?');await expect(guess().locator('.primary')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Continue',exact:true})).toHaveClass(/\bprimary\b/);await settled();await snap('rule-suggestions');
  await guess().getByRole('button',{name:'Clover lessons',exact:true}).click();await expect(guess().getByRole('heading',{name:'Clover lessons',exact:true})).toBeVisible();await expect(guess().getByRole('button',{name:'Add as usual',exact:true})).toBeEnabled();
  await type('Clover 40 '+seed.currency);await guess().getByLabel('Which rule did you mean?').selectOption(seed.rules[1].id);await expect(guess()).toContainText('Clover music');await expect(guess().getByRole('button',{name:'Add as usual',exact:true})).toBeEnabled();await settled();await snap('rule-pick-list');
  record('6c2 live typed guess, ranked pick list and durable Not this without submission');
  await resetDrafts({persona,empty:true});await go('add/');
  const first=await add('Birch rent 105 '+seed.currency,async()=>{await expect(guess().getByLabel('This payment only',{exact:true})).toBeChecked();await settled();await snap('rule-amount-this-payment');});
  assert.equal((await byName('Birch rent')).postings[0].amount,'100.00');
  await expect(saved().getByRole('button',{name:'Undo',exact:true})).toHaveCount(0);await expect(saved()).toContainText('Next due');await snap('rule-saved');
  const eligibility=await get('entries/'+first.result.entry_id+'/retention');
  const removed=await fetch(base+'api/retention/operations/'+crypto.randomUUID().replaceAll('-',''),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'delete',entry_id:first.result.entry_id,version:eligibility.version})});assert.ok(removed.ok,await removed.clone().text());await refresh();
  await expect.poll(async()=>(await byName('Birch rent')).date).toBe('2026-11-02');
  await page.waitForLoadState('networkidle');
  record('6c2 usual payment commits entry and advance together, this-payment amount, checked Delete and Trash');
  const second=await add('Birch rent 108 '+seed.currency,async()=>{await guess().getByLabel('Update the rule too',{exact:true}).check();await settled();await snap('rule-amount-update-rule');});
  assert.equal((await byName('Birch rent')).postings[0].amount,'108.00');
  await type('Birch rent 108 '+seed.currency);await expect(guess()).toContainText('Already added for');await expect(guess().getByRole('link',{name:'View entry'})).toHaveAttribute('href',new RegExp(second.result.entry_id));
  await expect(guess().getByRole('button',{name:'Add as usual',exact:true})).toBeDisabled();await settled();await snap('rule-already-added');
  await words().fill('Birch rent 109 '+seed.currency);await guess().getByLabel('Add another anyway',{exact:true}).check();
  const [duplicateResponse]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/usual')&&r.request().method()==='POST'),guess().getByRole('button',{name:'Add as usual',exact:true}).click()]);
  assert.ok(duplicateResponse.ok(),await duplicateResponse.text());const duplicate=await duplicateResponse.json();await expect(words()).toHaveValue('');
  await wait(duplicate.id);
  await refresh();await page.waitForLoadState('networkidle');await expect(page.locator('.just-added-row').first()).toContainText('Birch rent');await expect(saved().getByRole('button',{name:'Undo',exact:true})).toHaveCount(0);
  record('6c2 updating rule amount, duplicate-period disclosure, View entry and explicit override');
  await add('Fable support 15 '+seed.currency,async()=>{await expect(guess().getByRole('button',{name:'Add as usual',exact:true})).toBeDisabled();await guess().getByLabel('Paying account',{exact:true}).selectOption(seed.pay);await expect(guess().getByRole('button',{name:'Add as usual',exact:true})).toBeEnabled();await guess().getByLabel('Use this account from now on',{exact:true}).check();await settled();await snap('rule-missing-payer');});
  assert.equal((await byName('Fable support')).postings.at(-1).account,seed.pay);
  const converted=await add('Lantern journal 10 '+seed.foreign,async()=>{await expect(guess()).toContainText('Planned conversion');await expect(guess()).toContainText('Statement reconciliation will correct it');await settled();await snap('rule-other-currency');});
  assert.equal(converted.result.usual_payment.estimated,true);await expect(saved()).toContainText('Added at the planned conversion');
  assert.equal(await fakeCount(),beforeFake,'Every usual addition bypasses the agent');
  record('6c2 missing payer with remember choice and other-currency planned conversion');
  // Normal fake-agent additions expose automatically made and paused follow-ups.
  await words().fill('Pine studio subscription 22 '+seed.currency);await page.getByRole('button',{name:'Continue',exact:true}).click();await expect(words()).toHaveValue('');
  await expect.poll(async()=>(await get('drafts?include_added=true')).find(d=>d.text.startsWith('Pine studio'))?.state,{timeout:180000}).toBe('added');await refresh();
  await expect(saved()).toContainText('Made a rule for Pine studio');await expect(saved().getByRole('button',{name:'Edit rule'})).toBeVisible();await snap('rule-automatic-made');
  await clickOp(saved().getByRole('button',{name:'Don’t make a rule',exact:true}));await expect(saved().getByRole('button',{name:'Don’t make a rule',exact:true})).toHaveCount(0);
  assert.equal((await byName('Pine studio')).lifecycle,'removed');
  const pine=(await get('drafts?include_added=true')).find(d=>d.text.startsWith('Pine studio'));assert.ok(pine.result.entry_id);
  await control('rule-agent',{});
  await words().fill('Cedar archive payment 20 '+seed.currency);await page.getByRole('button',{name:'Continue',exact:true}).click();await expect(words()).toHaveValue('');
  await expect.poll(async()=>(await get('drafts?include_added=true')).find(d=>d.text.startsWith('Cedar archive'))?.state,{timeout:180000}).toBe('added');await refresh();
  await expect(saved()).toContainText('Resume Cedar archive?');await snap('rule-resume-match');await clickOp(saved().getByRole('button',{name:'Resume',exact:true}));
  await expect.poll(async()=>(await byName('Cedar archive')).lifecycle).toBe('active');
  record('6c2 normal additions offer Edit rule, Don’t make a rule and Resume paused match');
  await openLiveRules({page,base,expect});
  record('6c2 live Rules load independently, retain live data and read the host snapshot once across connection changes');
  const row=name=>page.getByRole('article',{name,exact:true});
  await expect(row('Birch rent').locator('.rule-status')).toHaveText('On track');await expect(row('Fern rent').locator('.rule-status')).toHaveText('Stale');await expect(row('Hawthorn dues').locator('.rule-status')).toHaveText('Due');await expect(row('Iris cover').locator('.rule-status')).toHaveText('Overdue');await expect(row('Juniper ended').locator('.rule-status')).toHaveText('Ended');
  await expect(row('Iris cover')).toContainText('Made automatically');await expect(row('Iris cover')).toContainText('Weekly review proposes removal');await snap('rules-statuses');
  await clickOp(row('Iris cover').getByRole('button',{name:'Keep',exact:true}));await expect(row('Iris cover')).not.toContainText('Weekly review proposes removal');
  await clickOp(row('Hawthorn dues').getByRole('button',{name:'Pause',exact:true}));await expect(row('Hawthorn dues').locator('.rule-status')).toHaveText('Paused');await snap('rules-paused');
  await clickOp(row('Hawthorn dues').getByRole('button',{name:'Resume',exact:true}));await expect(row('Hawthorn dues').locator('.rule-status')).toHaveText('Due');
  await clickOp(row('Hawthorn dues').getByRole('button',{name:'End',exact:true}));await expect(row('Hawthorn dues').locator('.rule-status')).toHaveText('Ended');
  await clickOp(row('Hawthorn dues').getByRole('button',{name:'Restore',exact:true}));await expect(row('Hawthorn dues').locator('.rule-status')).toHaveText('Due');
  await clickOp(row('Fern rent').getByRole('button',{name:'Move past last payment',exact:true}));await expect(row('Fern rent').locator('.rule-status')).toHaveText('On track');
  await clickOp(row('Juniper ended').getByRole('button',{name:'Remove',exact:true}));await expect(row('Juniper ended').locator('.rule-status')).toHaveText('Removed');
  record('6c2 Rules lifecycle statuses, Keep proposal, pause/resume, end/restore, stale catch-up and remove');
  await row('Hawthorn dues').getByRole('button',{name:'Edit rule',exact:true}).click();
  const editor=()=>page.getByRole('dialog',{name:'Edit rule',exact:true});
  await editor().getByLabel('Description',{exact:true}).fill('Hawthorn annual dues');await editor().getByLabel('Amount ('+seed.currency+')',{exact:true}).fill('32.50');await editor().getByLabel('Schedule',{exact:true}).selectOption('year');await editor().getByLabel('Next date',{exact:true}).fill('2026-12-02');await snap('rules-edit');
  await clickOp(editor().getByRole('button',{name:'Save rule',exact:true}));await expect(editor()).toHaveCount(0);await expect(row('Hawthorn annual dues')).toContainText('Yearly');
  await row('Lantern journal').getByRole('button',{name:'Edit rule',exact:true}).click();await expect(editor().getByRole('region',{name:'Read-only postings'})).toBeVisible();await expect(editor().locator('.rule-readonly')).toContainText('total');await expect(editor().getByLabel('Amount',{exact:true})).toHaveCount(0);
  // C10: read-only postings name accounts as the rest of the app does; the path stays in the tooltip.
  for(const term of await editor().locator('.rule-readonly dt').all()){assert.doesNotMatch(await term.textContent(),/^(Expenses|Assets|Liabilities|Income|Equity):/);assert.match(await term.getAttribute('title'),/:/);}
  await snap('rules-complex-readonly');await editor().getByRole('button',{name:'Cancel',exact:true}).click();
  await page.getByRole('button',{name:'New rule',exact:true}).click();const newRule=page.getByRole('dialog',{name:'New rule',exact:true});
  await newRule.getByLabel('Description',{exact:true}).fill('Kestrel club');await newRule.getByLabel('Amount',{exact:true}).fill('16.25');await newRule.getByLabel('Paying account',{exact:true}).selectOption(seed.pay);await newRule.getByLabel('Category',{exact:true}).selectOption('Expenses:Rule Workflow:Juniper ended');await newRule.getByLabel('Next date',{exact:true}).fill('2026-12-03');
  // C10: New rule has a plus icon, the Category shows labels while its value stays the path, and Amount is a plain field group.
  assert.equal(await page.locator('.rules-toolbar .button.primary .icon').innerHTML(),await page.evaluate(()=>'<path d="M12 5.5v13M5.5 12h13"></path>'),'New rule uses the plus icon');
  const category=newRule.getByLabel('Category',{exact:true});await expect(category).toHaveValue('Expenses:Rule Workflow:Juniper ended');
  assert.equal(await category.evaluate(select=>select.selectedOptions[0].textContent),'Rule Workflow › Juniper ended');
  assert.deepEqual(await category.evaluate(select=>[...select.options].filter(option=>option.value&&/^Expenses:/.test(option.textContent)).length),0,'No category option shows a raw path');
  assert.equal(await newRule.locator('.rule-amount-choice').evaluate(node=>getComputedStyle(node).borderTopStyle),'none','The Amount group has no fieldset box');
  await page.mouse.move(1,1);await snap('rules-new');await clickOp(newRule.getByRole('button',{name:'Save rule',exact:true}));await expect(newRule).toHaveCount(0);await expect(row('Kestrel club')).toBeVisible();
  const suggestions=page.getByRole('region',{name:'Suggested rules'});
  await expect(suggestions).toContainText('Mallow workshop');await snap('rules-suggested');await clickOp(suggestions.locator('article').filter({has:page.getByRole('heading',{name:'Mallow workshop',exact:true})}).getByRole('button',{name:'Confirm rule',exact:true}));await expect(row('Mallow workshop')).toBeVisible();
  await clickOp(suggestions.locator('article').filter({has:page.getByRole('heading',{name:'Olive workshop',exact:true})}).getByRole('button',{name:'Dismiss',exact:true}));await expect(suggestions).not.toContainText('Olive workshop');await reload();await expect(suggestions).not.toContainText('Olive workshop');
  await overflow('Rules');await page.setViewportSize({width:320,height:915});await overflow('Rules at 320px');await page.setViewportSize({width,height:width>760?1000:915});
  record('6c2 edit simple and complex rules, New rule, confirm/durably dismiss suggestions, responsive Rules');
  // Health returns an offline state without inventing network errors.
  await page.route('**/api/health',route=>route.fulfill({status:200,contentType:'application/json',body:'{"ok":false}'}));await reload();await expect(page.getByText('Rules are read-only here.',{exact:false})).toContainText('Reconnect');await expect(page.getByRole('button',{name:'New rule',exact:true})).toBeDisabled();await expect(row('Kestrel club').getByRole('button',{name:'Edit rule',exact:true})).toBeDisabled();await snap('rules-offline');
  record('6c2 built host Rules remain readable with changes disabled without server');
  const logs=(await import('node:fs/promises')).readdir;
  const audit=(await Promise.all((await logs(join(stage.ledger,'data/additions'))).map(y=>readFile(join(stage.ledger,'data/additions',y),'utf8')))).join('\n').split('\n').filter(Boolean).map(JSON.parse);
  assert.ok(audit.some(r=>r.draft_id===first.id&&r.usual_payment));assert.ok(audit.some(r=>r.retention_action==='deleted'&&r.selected.includes(first.result.entry_id)));assert.ok(audit.some(r=>r.usual_payment?.another_anyway));assert.ok(audit.some(r=>r.rule_action==='kept'));assert.ok(audit.some(r=>r.rule_action==='removed'&&r.rule_changes.some(c=>c.before===null)));
  const completed=(await get('drafts?include_added=true')).filter(d=>!d.trashed_at);const unsettled=completed.filter(d=>d.state!=='added'||d.audit_pending);assert.deepEqual(unsettled.map(d=>[d.state,d.text,!!d.audit_pending]),[],'Every submitted write settled before fixture reset');
  await quiesce({writesSettled:true});await page.unroute('**/api/health');await control('reset-review',{});await resetDrafts({persona,empty:true});
}
