import assert from 'node:assert/strict';

const available={enabled:true,current:'0.1.0',latest:'0.2.0',checkedAt:'2026-11-01T06:00:00+00:00',failed:false,command:'make update'};
const current={enabled:true,current:'0.1.0',latest:null,checkedAt:'2026-11-01T06:00:00+00:00',failed:false,command:'make update'};
const failed={enabled:true,current:'0.1.0',latest:null,checkedAt:'2026-11-01T06:00:00+00:00',failed:true,command:'make update'};

// Settings shows the update status known when it opened. The server's real reply is
// "off" for every fixture, so the states it can show are fulfilled here.
export async function runUpdateNotice({page,base,expect,go,snap,overflow,record}){
  const dialog=page.getByRole('dialog',{name:'Settings',exact:true}),notice=dialog.locator('[data-update-notice]');
  const open=async()=>{await page.locator('.identity:visible').click();await expect(dialog).toBeVisible();await expect(dialog.getByLabel('Name',{exact:true})).toBeFocused();};
  const close=async()=>{await dialog.getByRole('button',{name:'Cancel',exact:true}).click();await expect(dialog).toHaveCount(0);};
  const pattern=base+'api/update';

  await go('explorer/');await open();await expect(dialog.getByRole('heading',{name:'Agent status',exact:true})).toBeVisible();
  await expect(notice).toHaveCount(0);await close();
  record('update notice: off unless local.json turns it on');

  for(const [state,body,text] of [['available',available,'AgentExpense 0.2.0 is available. You have 0.1.0.'],['current',current,'AgentExpense 0.1.0. No newer release as of'],['failed',failed,'couldn’t read the list of releases']]){
    const reply=async route=>route.fulfill({json:body});
    await page.route(pattern,reply);
    try{
      const read=page.waitForResponse(response=>response.url()===pattern);await go('explorer/');await read;await open();
      await expect(notice).toHaveAttribute('data-update-notice',state);await expect(notice).toContainText(text);
      if(state==='available')await expect(notice.locator('code')).toHaveText('make update');
      await notice.scrollIntoViewIfNeeded();await overflow('Settings update notice '+state);await snap('settings-update-'+state);await close();
    }finally{await page.unroute(pattern,reply);}
  }
  record('update notice: available, up to date and failed checks in Settings');

  // Hold the status read while the person works in Settings: the open dialog never changes.
  let seen,release;const requested=new Promise(done=>seen=done),gate=new Promise(done=>release=done);
  const held=async route=>{seen();await gate;await route.fulfill({json:available}).catch(()=>{});};
  await page.route(pattern,held);
  try{
    await page.goto(base+'explorer/');await page.locator('.content h1').first().waitFor();await requested;
    await open();const name=dialog.getByLabel('Name',{exact:true}),appearance=dialog.locator('.appearance');
    await name.fill('Typed while the update check was held');await appearance.scrollIntoViewIfNeeded();await appearance.hover();
    const box=await appearance.boundingBox();
    const answered=page.waitForResponse(response=>response.url()===pattern);release();await answered;
    await expect(name).toHaveValue('Typed while the update check was held');await expect(notice).toHaveCount(0);
    await expect(appearance).toBeEnabled();const next=await appearance.boundingBox();assert.ok(box&&next);
    for(const key of ['x','y','width','height'])assert.ok(Math.abs(next[key]-box[key])<1,`${key}: Settings moved when the update status arrived`);
    await expect(dialog.locator('[role=alert]')).toHaveCount(0);await close();
    await open();await expect(notice).toHaveAttribute('data-update-notice','available');await close();
  }finally{release();await page.unroute(pattern,held);}
  // A reply that arrives after Settings has closed leaves nothing behind.
  let seenLate,releaseLate;const lateRequested=new Promise(done=>seenLate=done),lateGate=new Promise(done=>releaseLate=done);
  const late=async route=>{seenLate();await lateGate;await route.fulfill({json:current}).catch(()=>{});};
  await page.route(pattern,late);
  try{
    await open();await lateRequested;await close();
    const answered=page.waitForResponse(response=>response.url()===pattern);releaseLate();await answered;
    await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.locator('.live-error')).toHaveCount(0);
    await open();await expect(notice).toHaveAttribute('data-update-notice','current');await close();
  }finally{releaseLate();await page.unroute(pattern,late);}
  record('update notice timing: a held status read never changes or moves open Settings, and a late one shows next time');
}
