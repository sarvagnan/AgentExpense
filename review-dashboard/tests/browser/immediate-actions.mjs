import {expectInjected422} from './expected-errors.mjs';
import assert from 'node:assert/strict';
import {guard,gone} from './held.mjs';

// The request has not reached the server while this gate is closed. This proves
// pending is visible before a commit exists, rather than exploiting a slow engine.
export async function holdWrite(page,pattern,{fail=false,method='POST'}={}){
 let open,finished,resolveReady,arrived=false;const gate=new Promise(resolve=>open=resolve),ready=new Promise(resolve=>resolveReady=resolve),done=new Promise(resolve=>finished=resolve);let error;
 const handler=async route=>{if(route.request().method()!==method)return route.fallback();arrived=true;resolveReady(route.request());await page.unroute(pattern,handler);error=await guard(route,async()=>{await gate;if(fail){expectInjected422(page,route.request().url());await route.fulfill({status:422,contentType:'application/json',body:JSON.stringify({detail:'This change could not be saved. Your words are kept.'})});}else await route.continue();});finished();};
 await page.route(pattern,handler);
 return {ready,async release(){open();if(arrived)await done;else await page.unroute(pattern,handler);if(error&&!gone(page,error))throw error;}};
}

const coordinates=async locators=>Promise.all(locators.map(node=>node.boundingBox()));
function still(before,after){before.forEach((box,index)=>{assert.ok(box&&after[index]);for(const key of ['x','y'])assert.ok(Math.abs(box[key]-after[index][key])<=2,`Control ${index} ${key}: ${box[key]} → ${after[index][key]}`);});}
export async function runImmediateActions({page,base,persona,expect,control,go,quiesce,snap=async()=>{},record,errors=[]}){
 await quiesce();await control('gate',{state:'ready'});await control('drafts',{persona,empty:true});await go('add/');
 const words=page.locator('#expense-words');await words.fill('Words kept while another form saves');
 await page.locator('.identity:visible').click();let dialog=page.getByRole('dialog',{name:'Settings',exact:true}),name=dialog.getByLabel('Name',{exact:true});
 const original=await name.inputValue();await name.fill('Fictional immediate save');
 const {directAction}=await import('./direct-states.mjs');const action=directAction({page,base,expect,snap,record});
 await action('settings-name',dialog.locator('.dialog-footer .primary'),'**/api/settings/name',{input:name,container:dialog,closeAfter:false,onPending:()=>name.fill('Newer name typed before the refusal'),onRetryPending:()=>name.fill('Next edit while retry is pending')});
 const currency=dialog.getByLabel('Main currency',{exact:true}),oldCurrency=await currency.inputValue();await currency.selectOption(oldCurrency==='EUR'?'USD':'EUR');await action('settings-currency',dialog.locator('.dialog-footer .primary'),'**/api/settings/default-currency',{input:currency,container:dialog,closeAfter:false});
 const limit=dialog.getByLabel('Delete an entry',{exact:true}),oldLimit=await limit.inputValue();await limit.selectOption(oldLimit==='7'?'14':'7');await expect(dialog.locator('.dialog-footer .primary')).toBeEnabled();await action('time-limits',dialog.locator('.dialog-footer .primary'),'**/api/retention/operations/*',{input:limit,container:dialog,closeAfter:false});
 await currency.selectOption(oldCurrency);await limit.selectOption(oldLimit);await name.fill(original);await expect(dialog.locator('.dialog-footer .primary')).toBeEnabled();await dialog.locator('.dialog-footer .primary').click();await expect(dialog).toHaveCount(0,{timeout:120000});await page.locator('.identity:visible').click();dialog=page.getByRole('dialog',{name:'Settings',exact:true});
 await dialog.getByRole('button',{name:'Cancel',exact:true}).click();await expect(words).toHaveValue('Words kept while another form saves');
 // A queued second action can be started after leaving the first form.
 await page.locator('.identity:visible').click();dialog=page.getByRole('dialog',{name:'Settings',exact:true});name=dialog.getByLabel('Name',{exact:true});await name.fill('First ordered save');
 const held=await holdWrite(page,'**/api/settings/name');let posted=0;const track=req=>{if(req.method()==='POST'&&req.url().endsWith('/api/settings/name'))posted++;};page.on('request',track);
 try{
  await dialog.getByRole('button',{name:'Save changes',exact:true}).click();await held.ready;await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
  await words.fill('Second expense while a direct save waits');const continueButton=page.locator('.add-savebar button');await expect(continueButton).toBeEnabled();await continueButton.click({trial:true,timeout:5000});await snap('immediate-next-action-usable');await page.locator('.identity:visible').click();dialog=page.getByRole('dialog',{name:'Settings',exact:true});name=dialog.getByLabel('Name',{exact:true});await name.fill('Second ordered save');await dialog.getByRole('button',{name:'Save changes',exact:true}).click();
  await expect(dialog.locator('.action-feedback')).toContainText('Waiting for the earlier action');assert.equal(posted,1,'Second direct write has not overtaken the held first write');
  // The older completion must not close this newly opened dialog.
  await name.fill('Words typed after both submissions');await name.focus();const box=await name.boundingBox();await held.release();await expect(dialog.locator('.action-feedback')).toContainText('Saved.');await expect(name).toHaveValue('Words typed after both submissions');await expect(name).toBeFocused();still([box],[await name.boundingBox()]);
  record('immediate FIFO: leave pending Settings, type, reopen and start another save; no overtaking, late close or input loss');
 }finally{page.off('request',track);await held.release();}
 await name.fill(original);await dialog.getByRole('button',{name:'Save changes',exact:true}).click();await expect(dialog).toHaveCount(0,{timeout:60000});
 // Reload preserves an uncertain request and never silently submits it again.
 await page.locator('.identity:visible').click();dialog=page.getByRole('dialog',{name:'Settings',exact:true});await dialog.getByLabel('Name',{exact:true}).fill('Fictional reload recovery');
 const reloadGate=await holdWrite(page,'**/api/settings/name',{fail:true});
 try{
  await dialog.getByRole('button',{name:'Save changes',exact:true}).click();await reloadGate.ready;page.once('dialog',event=>event.accept());await page.reload({waitUntil:'domcontentloaded'});await expect(words).toBeVisible();
  const shelf=page.locator('.pending-actions');await expect(shelf).toContainText('needs attention');await shelf.locator('summary').click();await expect(shelf).toContainText(/The page reloaded before the result arrived|Could not reach the live server\. Check your connection and try again\./);await expect(words).toHaveValue('Second expense while a direct save waits');await snap('immediate-reload-interrupted');
  await reloadGate.release();await shelf.getByRole('button',{name:'Retry',exact:true}).click();await expect(shelf).toHaveCount(0,{timeout:60000});await expect(words).toHaveValue('Second expense while a direct save waits');
  record('immediate reload: no automatic financial retry; original request and operation identity retained, explicit Retry and composer recovery');
 }finally{await reloadGate.release();}
 await page.locator('.identity:visible').click();dialog=page.getByRole('dialog',{name:'Settings',exact:true});await dialog.getByLabel('Name',{exact:true}).fill(original);await dialog.getByRole('button',{name:'Save changes',exact:true}).click();await expect(dialog).toHaveCount(0,{timeout:60000});
 // Force ae-yu0 through the actual Add restore path. A completed item can reply late.
 const competing=await page.request.post(base+'api/drafts',{data:{text:'Other server words must not replace newer typing',pieces:null,client_updated_at:Date.now()+1000000}});assert.ok(competing.ok());
 const created=await page.request.post(base+'api/drafts',{data:{text:'Original recovered words',pieces:null}});assert.ok(created.ok());const draft=await created.json();
 await page.evaluate(id=>localStorage.setItem('ledger-add-v1',JSON.stringify({id,text:'Original recovered words',edits:{}})),draft.id);
 let release,ready;const gate=new Promise(resolve=>release=resolve),arrived=new Promise(resolve=>ready=resolve);
 const pattern=base+'api/drafts/'+draft.id;
 const restore=async route=>{if(route.request().method()!=='GET')return route.fallback();await page.unroute(pattern,restore);await guard(route,async()=>{const response=await route.fetch();ready();await gate;const value=await response.json();await route.fulfill({response,json:{...value,state:'added',result:{outcome:'added'}}});});};
 await page.route(pattern,restore);
 try{
  await page.goto(base+'add/');await arrived;await expect(words).toBeEditable();await words.fill('New words typed during the late restore');await words.focus();const box=await words.boundingBox();release();await expect(page.getByText('Restoring your uploaded item…',{exact:true})).toHaveCount(0);await expect(words).toHaveValue('New words typed during the late restore');await expect(words).toBeFocused();still([box],[await words.boundingBox()]);
  record('ae-yu0 forced browser regression: completed restore reply cannot overwrite newer Add words or focus');
 }finally{release();await page.unroute(pattern,restore);}
 await quiesce();await control('drafts',{persona,empty:true});
 await runAccountCurrencyAction({page,base,expect,control,go,quiesce,snap,record,errors});
}

/** An opening-only fictional account makes this dialog available in every persona. */
export async function runAccountCurrencyAction({page,base,expect,control,go,quiesce,snap=async()=>{},record,errors=[]}){
 const post=async(path,body)=>{const response=await fetch(base+'api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});assert.ok(response.ok,await response.clone().text());return response.json();};
 await quiesce();await control('first-run',{});
 try{
  const settings=await (await fetch(base+'api/settings')).json(),currency=settings.currency,target=currency==='EUR'?'USD':'EUR',account='Assets:Bank:Willow Reserve';
  await post('setup/about',{name:'Fictional account actions',currency});
  await post('setup/opening',{start:'2026-11-01',accounts:[{kind:'bank',label:'Willow Reserve',currency,balance:'100.00'}]});
  await go('account-summary/');await page.getByRole('button',{name:'Willow Reserve',exact:true}).click();
  const dialog=page.getByRole('dialog'),field=dialog.getByLabel('Currency',{exact:true}),button=dialog.locator('.dialog-footer .primary'),heading=dialog.locator('h2').first();
  await expect(field).toHaveValue(currency);await field.selectOption(target);
  const {directAction}=await import('./direct-states.mjs');await directAction({page,base,expect,snap,record})('account-currency',button,'**/api/accounts/currency',{input:field,container:dialog,closeAfter:false,onPending:()=>field.selectOption(currency),beforeRetry:()=>field.selectOption(target),onRetryPending:()=>field.selectOption(currency)});
  const committed=await (await fetch(base+'api/settings?account='+encodeURIComponent(account))).json();assert.equal(committed.account_currency,target);await field.selectOption(currency);await button.click();await expect(dialog).toHaveCount(0,{timeout:120000});const restored=await (await fetch(base+'api/settings?account='+encodeURIComponent(account))).json();assert.equal(restored.account_currency,currency);
  record('account currency: held refusal/Retry retain newer selection and focus, ≤2px controls, committed result, then an explicit save back');
 }finally{await quiesce();await control('first-run',{restore:true});}
}
