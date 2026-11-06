import assert from 'node:assert/strict';

export async function runCorrections({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}) {
  await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});await control('gate',{state:'ready'});
  const seed=await control('rules',{}),get=async path=>{const r=await fetch(base+'api/'+path);assert.ok(r.ok,await r.clone().text());return r.json();};
  // The same UTC instant falls on different local calendar dates in the matrix.
  await page.clock.setFixedTime(new Date('2026-11-02T00:30:00Z'));
  await go('add/');const words=page.locator('#expense-words'),guess=page.getByRole('complementary',{name:'Usual payment'});
  await words.fill('Birch rent today');await expect(guess).toBeVisible();
  const today=await page.evaluate(()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;});
  await expect(guess.getByLabel('Payment date')).toHaveValue(today);
  await words.fill('Birch rent yesterday');
  const yesterday=await page.evaluate(()=>{const now=new Date();now.setDate(now.getDate()-1);return `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;});
  await expect(guess.getByLabel('Payment date')).toHaveValue(yesterday);await expect(guess).toContainText('(yesterday)');await expect(guess.getByRole('button',{name:'Add as usual'})).toBeEnabled();await page.mouse.move(1,1);await snap('usual-typed-date');
  await guess.getByLabel('Payment date').fill('');await expect(guess.getByRole('button',{name:'Add as usual'})).toBeDisabled();await guess.getByLabel('Payment date').fill(yesterday);
  await guess.getByRole('button',{name:'Not this',exact:true}).click();await expect(guess).toHaveCount(0);
  await words.fill('Birch rent yesterday please');await expect.poll(async()=>(await get('drafts')).find(d=>d.text==='Birch rent yesterday please')?.declined_rules).toEqual([seed.rules[0].id]);await page.waitForLoadState('networkidle');await page.reload();await expect(words).toHaveValue('Birch rent yesterday please');await expect(guess).toHaveCount(0);
  await page.evaluate(()=>localStorage.removeItem('ledger-add-v1'));await page.reload();await expect(words).toHaveValue('Birch rent yesterday please');await expect(guess).toHaveCount(0);
  await words.fill('Clover 40 '+seed.currency);await expect(guess.getByLabel('Which rule did you mean?')).toBeVisible();await guess.getByRole('button',{name:'Not this',exact:true}).click();await expect(guess).toHaveCount(0);
  await expect.poll(async()=>(await get('drafts')).find(d=>d.text==='Clover 40 '+seed.currency)?.declined_rules?.length).toBe(3);
  record('6d typed local date and monotonic Not this across changed words, reload, recovery and pick list');
  await resetDrafts({persona,empty:true});await go('add/');await words.fill('Birch rent yesterday');await expect(guess.getByRole('button',{name:'Add as usual'})).toBeEnabled();await guess.getByRole('button',{name:'Add as usual'}).click();
  let added;await expect.poll(async()=>{added=(await get('drafts?include_added=true')).find(d=>d.text==='Birch rent yesterday');return added?.state;},{timeout:180000}).toBe('added');
  await go('queue/?correct='+added.result.entry_id);await expect(page.getByRole('article',{name:'Edit entry',exact:true})).toBeVisible();
  const form=page.getByRole('article',{name:'Edit entry',exact:true});await expect(form.getByLabel('Date',{exact:true})).toHaveValue(yesterday);
  // One "Edit entry" heading (the page's); the panel names the entry. Controls use the system's buttons.
  await expect(page.getByRole('heading',{name:'Edit entry',exact:true})).toHaveCount(1);await expect(form.getByRole('button',{name:'Attach files',exact:true})).toBeVisible();await expect(form.locator('section[aria-label="Saved sources"]:not(:has(input))')).toHaveCount(0);await expect(form).toContainText('No changes yet');
  await expect(form.getByText('Agent doubts',{exact:true})).toHaveCount(0);await form.getByLabel('Description',{exact:true}).fill('Birch rent corrected');await form.getByLabel('Reason (optional)').fill('Receipt confirmed the description');
  await snap('correction-from-drafts');await form.getByRole('button',{name:'Check entry',exact:true}).click();await expect(form).toContainText('Checks passed');
  await form.getByRole('button',{name:'Save correction'}).click();await expect(page.getByText('Correction saved',{exact:true})).toBeVisible({timeout:180000});await snap('correction-saved');await page.getByRole('button',{name:'Back to Queue',exact:true}).click();
  const entryId=added.result.entry_id,month=yesterday.slice(0,7);
  let savedName='Birch rent corrected';
  for(const view of ['explorer','journal']) {
    await go(`${view}/?month=${month}&entry=${entryId}`);const details=page.getByRole('dialog');
    await expect(details).toBeVisible();await details.getByRole('button',{name:'Edit'}).click();
    await expect(form.getByLabel('Description',{exact:true})).toHaveValue(savedName);
    // Edit entry keeps the view it came from selected, never Queue.
    const label=view==='explorer'?'Explorer':'Journal',wide=(page.viewportSize()?.width||0)>760;
    if(wide){await expect(page.locator('.sidebar nav a[aria-current="page"]')).toHaveAttribute('title',label);await expect(page.locator('.sidebar nav a[href$="queue/"]')).not.toHaveAttribute('aria-current','page');}
    else{await expect(page.locator('.phone-live-nav > button[aria-controls="phone-more-views"]')).toHaveAttribute('aria-current','page');await expect(page.locator('.phone-live-nav a[href$="queue/"]')).not.toHaveAttribute('aria-current','page');}
    await snap('correction-from-'+view);
    savedName+=' · '+view;await form.getByLabel('Description',{exact:true}).fill(savedName);
    await form.getByRole('button',{name:'Check entry',exact:true}).click();await expect(form).toContainText('Checks passed');
    await form.getByRole('button',{name:'Save correction'}).click();await expect(page.getByText('Correction saved',{exact:true})).toBeVisible({timeout:180000});
    await refresh();await page.waitForLoadState('networkidle');await expect(page.getByText('Correction saved',{exact:true})).toBeVisible();
    await overflow('correction '+view);await snap('correction-'+view+'-saved');
    await page.getByRole('button',{name:'Back',exact:true}).click();await expect(details.getByRole('button',{name:'Edit'})).toBeVisible();assert.ok(page.url().includes(view+'/'));await expect(details).toContainText(savedName);
    await details.getByRole('button',{name:'Close details'}).click();
  }
  record('6d correction from Drafts, Explorer and Journal with saved identity and Back targets');
  await quiesce();const locked=await control('correction-statement',{});await go('explorer/?month=2026-11&entry='+locked.entry_id);await page.getByRole('dialog').getByRole('button',{name:'Edit'}).click();
  await expect(form).toContainText('Amounts, currencies, costs, posting count and paying accounts are locked');
  for(const input of await form.getByLabel('Amount',{exact:true}).all())await expect(input).toHaveAttribute('readonly','');
  for(const input of await form.getByLabel('Currency',{exact:true}).all())await expect(input).toHaveAttribute('readonly','');
  await expect(form.getByRole('group',{name:'Posting 2',exact:true}).getByLabel('Account',{exact:true})).toHaveAttribute('readonly','');
  await expect(form.getByLabel('Description',{exact:true})).toBeEditable();await snap('correction-statement-locked');await form.getByRole('button',{name:'Back',exact:true}).click();await page.getByRole('button',{name:'Close details'}).click();
  record('6d statement correction fields look and behave read-only');
  await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});await control('gate',{state:'ready'});await control('rules',{});await control('receipt-answer',{date:'2026-11-01',amount:'120.00'});
  await page.clock.setFixedTime(new Date('2026-11-02T12:00:00Z'));await go('add/');await words.fill('Birch rent today');
  const receipt=Buffer.from(await(await fetch(base+'__test/receipt',{method:'POST'})).arrayBuffer());await page.getByLabel('Upload files',{exact:true}).setInputFiles({name:'Willow-receipt.png',mimeType:'image/png',buffer:receipt});await expect(page.locator('.pending-upload')).toHaveCount(0);
  await expect(guess.getByRole('button',{name:'Add as usual'})).toBeEnabled();await guess.getByRole('button',{name:'Add as usual'}).click();
  await expect.poll(async()=>{added=(await get('drafts?include_added=true')).find(d=>d.text==='Birch rent today');return added?.receipt_reading?.state;},{timeout:180000}).toBe('question');
  await page.evaluate(()=>{for(const key of Object.keys(sessionStorage))if(key.startsWith('ledger-add-visit-'))sessionStorage.removeItem(key);});
  await go('explorer/?month=2026-11&entry='+added.result.entry_id);const receiptInspector=page.locator('#transaction-inspector');await expect(receiptInspector.getByRole('region',{name:'Receipt reading'})).toContainText('The receipt says');await snap('receipt-question-inspector');await receiptInspector.getByRole('button',{name:'Close details'}).click();record('6f C pending receipt questions remain reachable from Explorer without the Add visit');
  await go('add/?draft='+added.id);await page.locator('.receipt-inline').getByRole('button',{name:'More',exact:true}).click();const question=page.getByRole('region',{name:'Receipt reading'});await expect(question).toContainText('The receipt says');await overflow('receipt question');await snap('receipt-question');
  await question.getByRole('button',{name:'Keep the entry'}).click();await expect(question).toContainText('Entry kept. Receipt notes saved.',{timeout:180000});
  record('6d usual saves first, reads filed receipt afterwards and records the owner choice');
  await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});
}
