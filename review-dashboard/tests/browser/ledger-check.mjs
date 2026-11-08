// hledger's background second check, fed by serve.py's test control (never a broken fixture):
// held open while saves go on, failing (banner, Queue item, Apply waiting), passing on Check
// again, and the fake agent's fix approved in Edit entry.
import assert from 'node:assert/strict';

const KINDS=[['unbalanced','An entry doesn’t balance'],['conversion','An entry mixes currencies without a conversion'],['assertion','A confirmed balance doesn’t hold'],
  ['account','An account isn’t declared'],['commodity','A currency isn’t declared'],['dates','Entries are out of date order'],['parse','hledger couldn’t read the ledger']];

export async function runLedgerCheck({page,base,persona,engine,theme,width,expect,control,go,refresh,overflow,snap,record,quiesce}) {
  const get=async path=>{const response=await fetch(base+'api/'+path);assert.ok(response.ok,await response.clone().text());return response.json();};
  const banner=page.getByRole('alert',{name:'Ledger check problem'});
  // FLOW-01: a late result never moves the heading, the main input or the main action.
  const boxes=async locators=>Promise.all(locators.map(async l=>{const b=await l.boundingBox();assert.ok(b,'Box measured');return b;}));
  // WCAG 2.4.11 and the save bar: the fixed banner never covers what the person is using.
  const clear=async(locator,label)=>{const [a,b]=await Promise.all([locator.boundingBox(),banner.boundingBox()]);assert.ok(a&&b,label+': boxes measured');
    assert.ok(a.y+a.height<=b.y+1||b.y+b.height<=a.y+1||a.x+a.width<=b.x+1||b.x+b.width<=a.x+1,`${label} is under the banner: ${JSON.stringify(a)} vs ${JSON.stringify(b)}`);};
  const still=(before,after,label)=>before.forEach((b,i)=>{assert.ok(Math.abs(b.y-after[i].y)<=2&&Math.abs(b.x-after[i].x)<=2,`${label}: element ${i} moved from ${b.x},${b.y} to ${after[i].x},${after[i].y}`);});
  await quiesce();await control('ledger-check',{mode:'real'});await control('reset-workflows',{});await control('drafts',{persona,empty:true});await control('gate',{state:'ready'});
  try {
    // A balance-style statement's account (Apply also confirms its closing balance).
    const config=await control('reader-scenario',{extension:'csv',tag:`check-${engine}-${theme}-${width}`});
    await go('add/');
    const heading=page.locator('.content h1').first(),words=page.locator('#expense-words'),proceed=page.getByRole('button',{name:'Continue',exact:true});
    const core=()=>boxes([heading,words,proceed,page.locator(width<700?'.phone-live-nav':'.sidebar nav')]);

    // 1. Hold the background check open: no banner yet, and saves keep working.
    const held=await control('ledger-check',{mode:'fail',kind:'assertion',account:config.account,data:true,hold:true});
    assert.equal(held.checking,true);assert.equal(held.item,null);
    await refresh();await expect(banner).toHaveCount(0);
    await page.locator('.identity:visible').click();const settings=page.getByRole('dialog',{name:'Settings',exact:true});
    await settings.getByLabel('Name',{exact:true}).fill('Ledger check fixture');await settings.getByRole('button',{name:'Save changes',exact:true}).click();
    await expect(settings).toHaveCount(0,{timeout:120000});
    assert.equal((await control('ledger-check',{mode:'status'})).checking,true,'The save committed while the check was still held');
    await words.fill('Paper Kite notebook');await page.waitForLoadState('networkidle');await refresh();await expect(banner).toHaveCount(0);
    record('ledger check held open: no banner, and a save commits meanwhile');

    // 2. Make it fail: the banner arrives in its own space; nothing the person is using moves.
    const before=await core();
    await control('ledger-check',{mode:'release'});
    await expect(banner).toBeVisible({timeout:30000});await expect(banner).toContainText('The second ledger check found a problem.');
    still(before,await core(),'Banner arrival on Add');await expect(words).toHaveValue('Paper Kite notebook');await expect(words).toBeFocused();
    await clear(words,'The focused words');await clear(page.locator('.add-savebar'),'The Add save bar');
    await overflow('Ledger check banner');await page.mouse.move(1,1);await snap('ledger-check-banner');
    await words.fill('');

    // Apply (and so the confirmed balance) waits for the account involved, with the reason beside it.
    const name=`Fictional-check-${persona}-${engine}-${theme}-${width}.csv`;
    const file=Buffer.from(await(await fetch(base+'__test/reader-file?extension=csv',{method:'POST'})).arrayBuffer());
    await page.getByLabel('Upload files',{exact:true}).setInputFiles({name,mimeType:'text/csv',buffer:file});
    await expect(page.locator('.pending-upload')).toHaveCount(0);await proceed.click();
    await expect.poll(async()=>(await get('statements')).sessions.find(s=>s.original_name===name)?.state,{timeout:60000}).toBe('found');
    const session=(await get('statements')).sessions.find(s=>s.original_name===name);
    await go('queue/?session='+session.id);await page.getByRole('button',{name:'Review checklist',exact:true}).click();
    for(const {row} of session.plan.items.filter(i=>i.needs_user))await page.getByRole('combobox',{name:'Decision for '+row.description,exact:true}).selectOption('pending');
    const review=page.getByRole('button',{name:'Review apply',exact:true});
    await expect(review).toBeDisabled();await expect(page.locator('.statement-review-action > span')).toHaveText('Waits for the ledger check');
    await expect(page.locator('#statement-wait-reason')).toContainText('This account waits until the ledger check passes.');
    await expect(review).toHaveAttribute('aria-describedby','statement-wait-reason');
    const refused=await fetch(base+`api/statements/${session.id}/apply`,{method:'POST'});assert.equal(refused.status,409,'The server refuses Apply too');
    await overflow('Apply waiting for the ledger check');await page.mouse.move(1,1);await snap('ledger-check-apply-waiting');
    record('ledger check failing: banner without moving the page, Apply and confirm disabled with the reason');

    // The Queue item, for each kind of finding, and the app-problem case.
    await go('queue/');const item=page.getByRole('article',{name:'Ledger check',exact:true});
    const row=page.getByRole('region',{name:'Needs you'}).getByRole('link',{name:/Ledger check/});
    await expect(row).toBeVisible();await expect(page.getByRole('heading',{name:'Queue',exact:true})).toBeVisible();
    await row.click();await expect(item).toBeVisible();
    await expect(item).toContainText('A confirmed balance doesn’t hold');await expect(item).toContainText(config.account.split(':').at(-1));
    await expect(item.getByRole('button',{name:'Check again',exact:true})).toBeVisible();
    // A ledger with no entries yet (the empty persona) has none to edit: the item says so and offers Check again only.
    const named=(await control('ledger-check',{mode:'status'})).item.entry;
    await expect(item.getByRole('link',{name:'Edit the entry'})).toHaveCount(named?1:0);await expect(item.getByRole('button',{name:'Ask the agent to fix it',exact:true})).toHaveCount(named?1:0);
    if(!named)await expect(item).toContainText('No entries changed since the last good check.');
    await item.locator('.ledger-check-details summary').click();await expect(item.locator('.ledger-check-details pre')).toContainText('Balance assertion failed in');
    for(const [kind,title] of KINDS) {
      await control('ledger-check',{mode:'fail',kind,data:true});await go('queue/?check=ledger');
      await expect(item).toHaveAttribute('data-kind',kind);await expect(item).toContainText(title);await overflow('Ledger check '+kind);await page.mouse.move(1,1);await snap('ledger-check-'+kind);
    }
    await control('ledger-check',{mode:'fail',kind:'unbalanced',data:false});await go('queue/?check=ledger');
    await expect(item).toContainText('This is a problem in AgentExpense, not in your entries.');await expect(item.getByRole('button',{name:'Copy details'})).toBeVisible();
    await expect(item.getByRole('button',{name:'Ask the agent to fix it',exact:true})).toHaveCount(0);await page.mouse.move(1,1);await snap('ledger-check-app-problem');
    record('ledger check Queue item: plain words for every kind, Details, and the app-problem case');

    // 3. Check again with a pass: the banner goes and the item closes; Apply works again.
    await control('ledger-check',{mode:'pass',check:false});
    await item.getByRole('button',{name:'Check again',exact:true}).click();
    await expect(item).toContainText('The second check passed.',{timeout:120000});await expect(banner).toHaveCount(0);
    await expect(page.getByRole('region',{name:'Needs you'}).getByRole('link',{name:/Ledger check/})).toHaveCount(0);
    // The decisions already moved the statement to its checklist.
    await go('queue/?session='+session.id);
    await expect(review).toBeEnabled();await expect(page.locator('#statement-wait-reason')).toHaveCount(0);await review.click();await page.getByRole('dialog').getByRole('button',{name:'Apply statement',exact:true}).click();
    await expect.poll(async()=>(await get('statements/'+session.id)).state,{timeout:180000}).toBe('applied');
    record('ledger check Check again passes: banner gone, item closed, Apply allowed again');

    // 4. Ask the agent to fix it: the fake agent proposes, the person approves, the check passes.
    const failing=await control('ledger-check',{mode:'fail',kind:'account',data:true,once:true,fix:true});
    assert.equal(failing.item.entry.id,failing.target);
    await go('queue/?check=ledger');await expect(item).toContainText('An account isn’t declared');
    await item.getByRole('button',{name:'Ask the agent to fix it',exact:true}).click();
    await expect(item.getByRole('link',{name:'Review the fix',exact:true})).toBeVisible({timeout:60000});
    await item.getByRole('link',{name:'Review the fix',exact:true}).click();
    const form=page.getByRole('article',{name:'Edit entry',exact:true});await expect(form).toBeVisible();
    await expect(form.locator('.ledger-fix-notice')).toContainText('The agent’s proposed fix for the ledger check');
    await expect(form.getByLabel('Notes',{exact:true})).toHaveValue(/Checked for the ledger check/);
    await page.mouse.move(1,1);await snap('ledger-check-fix-proposal');
    // Keyboard focus through the form's end stays clear of the banner (reveal and scroll padding).
    for(const control of [form.getByLabel('Notes',{exact:true}),form.getByRole('button',{name:'Check entry',exact:true})]){await control.focus();await page.waitForTimeout(50);await clear(control,'Focused '+(await control.getAttribute('aria-label')||'Check entry'));}
    await form.getByRole('button',{name:'Check entry',exact:true}).click();await expect(form).toContainText('Checks passed');
    // The fix draft completes on the server before the save answers (the data rebuild runs in
    // between). Hold that answer, let a Queue poll see the completed draft, then release it:
    // Edit entry stays open and shows the saved correction.
    const fixId=new URL(page.url()).searchParams.get('draft');let releaseSave;const gate=new Promise(resolve=>{releaseSave=resolve;});
    const holdSave=async route=>{const response=await route.fetch({timeout:180000});await gate;await route.fulfill({response});};
    await page.route('**/api/entries/*/corrections/*',holdSave);
    try {
      await form.getByRole('button',{name:'Save correction'}).click();
      await expect.poll(async()=>(await get('drafts/'+fixId)).state,{timeout:180000}).toBe('added');
      // Pointer and focus leave Queue, so its interaction snapshot lets the poll through.
      // (A button disabled while saving loses focus without a blur event, so focus passes through the panel's heading and out.)
      await page.mouse.move(1,1);await form.locator('h2').first().evaluate(h=>{h.tabIndex=-1;h.focus();});await page.locator('.content h1').first().focus();await refresh();
      await expect(page.locator(`a.draft-row[href$="?draft=${fixId}"]`)).toHaveCount(0,{timeout:30000});
      await expect(form).toBeVisible();
    } finally {releaseSave();}
    await page.unroute('**/api/entries/*/corrections/*',holdSave);
    await expect(page.getByText('Correction saved',{exact:true})).toBeVisible({timeout:180000});
    await expect(banner).toHaveCount(0,{timeout:30000});
    const done=await control('ledger-check',{mode:'status'});assert.equal(done.outcome,'pass');assert.equal(done.item,null);
    record('ledger check fix: fake agent proposal approved in Edit entry, check passes, item closes');
  } finally {
    await control('ledger-check',{mode:'release'});await control('ledger-check',{mode:'real',check:false});
  }
}
