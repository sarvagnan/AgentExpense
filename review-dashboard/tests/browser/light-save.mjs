import assert from 'node:assert/strict';

export async function runLightSave({page,base,persona,expect,control,go,refresh,record,quiesce,snap=async()=>{}}) {
  await quiesce();await control('reset-workflows',{});await control('drafts',{persona,empty:true});await control('gate',{state:'ready'});
  await control('scenario',{mode:'ready'});await go('add/');
  const words=page.locator('#expense-words'),heading=page.locator('.content h1').first(),proceed=page.getByRole('button',{name:'Continue',exact:true});
  const boxes=async()=>Promise.all([words,heading,proceed,page.locator('.sidebar nav:visible, .phone-live-nav:visible')].map(locator=>locator.boundingBox()));
  const still=(a,b)=>a.forEach((box,i)=>{assert.ok(box&&b[i]);for(const key of ['x','y'])assert.ok(Math.abs(box[key]-b[i][key])<=2,`Control ${i} moved at ${key}`);});
  const status=page.getByRole('status',{name:'Dashboard data status'});
  const clearActions=async()=>{
    const notice=await status.boundingBox();assert.ok(notice);
    for(const target of [words,proceed,page.locator('.sidebar nav:visible, .phone-live-nav:visible')]){
      const box=await target.boundingBox();assert.ok(box);
      assert.ok(box.x+box.width<=notice.x+1||notice.x+notice.width<=box.x+1||box.y+box.height<=notice.y+1||notice.y+notice.height<=box.y+1,'The data notice must not cover the input, primary action or navigation');
    }
  };
  await control('data-refresh',{mode:'hold'});
  try {
    await words.fill('Copper Finch lunch');await proceed.click();
    await expect(page.locator('.just-added')).toContainText('Added',{timeout:60000});
    const held=await control('data-refresh',{mode:'status'});
    assert.equal(held.held,true);assert.equal(held.data_stale,true,'The save answered while the rebuild was held');
    await expect(status).toContainText('Totals show the previous saved version.');
    await clearActions();
    await words.fill('Next payment, still typing');await words.focus();
    const before=await boxes(),caret=await words.evaluate(node=>node.selectionStart);
    await expect(proceed).toBeEnabled();
    const open=page.locator('.just-added').getByRole('link',{name:'Open entry',exact:true});
    await expect(open).toHaveAttribute('aria-disabled','true');
    await snap('light-save-stale');
    await control('data-refresh',{mode:'release'});
    await expect(status).toHaveCount(0,{timeout:30000});
    await expect(words).toHaveValue('Next payment, still typing');
    await expect(words).toBeFocused();
    assert.equal(await words.evaluate(node=>node.selectionStart),caret);
    still(before,await boxes());await expect(proceed).toBeEnabled();
    await snap('light-save-fresh');
    await expect(open).not.toHaveAttribute('aria-disabled','true');await open.click();
    await expect(page.getByRole('dialog')).toBeVisible();await page.keyboard.press('Escape');
    record('lighter save: committed result before held data rebuild; old totals labelled stale; next words/caret/actions preserved; fresh data and selected entry arrive without reload');
    await control('data-refresh',{mode:'hold'});
    await page.locator('.identity:visible').click();
    const settings=page.getByRole('dialog',{name:'Settings',exact:true});
    const originalName=await settings.getByLabel('Name',{exact:true}).inputValue();
    const longName='A ledger name long enough to wrap over several lines while the saved view refreshes';
    await settings.getByLabel('Name',{exact:true}).fill(longName);
    await settings.getByRole('button',{name:'Save changes',exact:true}).click();
    await expect(settings).toHaveCount(0,{timeout:60000});
    assert.equal((await control('data-refresh',{mode:'status'})).held,true);
    await clearActions();
    await words.focus();const settingBoxes=await boxes(),settingCaret=await words.evaluate(node=>node.selectionStart);
    await control('data-refresh',{mode:'release'});await expect(status).toHaveCount(0,{timeout:30000});
    await expect(words).toHaveValue('Next payment, still typing');await expect(words).toBeFocused();
    assert.equal(await words.evaluate(node=>node.selectionStart),settingCaret);still(settingBoxes,await boxes());
    await expect(page.locator('.identity-name:visible')).toHaveAttribute('title',longName);await snap('light-save-settings');
    await page.locator('.identity:visible').click();await settings.getByLabel('Name',{exact:true}).fill(originalName);
    await settings.getByRole('button',{name:'Save changes',exact:true}).click();await expect(settings).toHaveCount(0,{timeout:60000});
    await expect(status).toHaveCount(0,{timeout:30000});
    record('lighter save: Settings closes at commit during held data rebuild; next words/caret/actions survive publication');
    if(process.env.CAPTURE==='1') {
      await control('data-refresh',{mode:'fail'});await refresh();
      await expect(status).toContainText('Views could not refresh.');
      await clearActions();
      await expect(words).toHaveValue('Next payment, still typing');
      await snap('light-save-failed');
      await control('data-refresh',{mode:'release'});
      await status.getByRole('button',{name:'Retry refresh',exact:true}).click();
      await expect(status).toHaveCount(0,{timeout:30000});
      await expect(words).toHaveValue('Next payment, still typing');
      record('lighter save: visible build failure retains next words; Retry refresh repairs the generation');
    }
  } finally {await control('data-refresh',{mode:'release'});await control('data-refresh',{mode:'wait'});}
  await quiesce();await control('reset-workflows',{});await control('drafts',{persona,empty:true});
}
