import assert from 'node:assert/strict';
import {holdWrite} from './immediate-actions.mjs';
async function sample(page,button,held,selector){
 await page.evaluate(selector=>{
  window.__immediate={};
  document.addEventListener('pointerdown',event=>{window.__immediate.down=performance.now();requestAnimationFrame(()=>requestAnimationFrame(()=>{window.__immediate.pressed=performance.now();window.__immediate.pressedStyle=getComputedStyle(event.target.closest('button')).filter;}));},{once:true,capture:true});
  document.addEventListener('click',()=>{window.__immediate.click=performance.now();const observer=new MutationObserver(()=>{if(!document.querySelector(selector))return;observer.disconnect();requestAnimationFrame(()=>requestAnimationFrame(()=>window.__immediate.pending=performance.now()));});observer.observe(document.body,{subtree:true,attributes:true,childList:true,characterData:true});},{once:true,capture:true});
 },selector);
 await button.hover();await page.mouse.down();await page.waitForFunction(()=>window.__immediate.pressed!==undefined);await page.mouse.up();await held.ready;await page.waitForFunction(()=>window.__immediate.pending!==undefined);
 const timing=await page.evaluate(()=>window.__immediate);assert.notEqual(timing.pressedStyle,'none','Pressed feedback is visibly styled');const result={pressed:timing.pressed-timing.down,pending:timing.pending-timing.click};assert.ok(result.pressed<=100&&result.pending<=100,JSON.stringify(result));return result;
}
export async function runImmediateLatency({page,base,expect,go,record,control,quiesce,stage}){
 await go('add/');await page.locator('.identity:visible').click();const dialog=page.getByRole('dialog',{name:'Settings',exact:true}),name=dialog.getByLabel('Name',{exact:true}),original=await name.inputValue(),settings=[];
 for(let index=0;index<5;index++){
  await name.fill('Fictional latency sample '+index);const button=dialog.getByRole('button',{name:'Save changes',exact:true}),held=await holdWrite(page,'**/api/settings/name');
  try{await expect(button).toBeEnabled();settings.push(await sample(page,button,held,'.action-feedback [data-action-state="saving"]'));await name.fill('Next words during latency sample '+index);await name.focus();await held.release();await expect(dialog.locator('.action-feedback')).toContainText('Saved.',{timeout:60000});await expect(name).toBeFocused();}finally{await held.release();}
 }
 await name.fill(original);await dialog.getByRole('button',{name:'Save changes',exact:true}).click();await expect(dialog).toHaveCount(0,{timeout:60000});
 const hand=[];
 for(let index=0;index<5;index++){
  await quiesce();await control('gate',{state:'ready'});await control('scenario',{mode:'ready'});const seeded=await control('finish',{});await go('queue/?draft='+seeded.id);await page.getByRole('button',{name:'Finish it yourself',exact:true}).click();const form=page.getByRole('article',{name:'Finish it yourself',exact:true});await form.getByLabel('Date',{exact:true}).fill(stage.env.DASHBOARD_TODAY);await form.getByLabel('Description',{exact:true}).fill('Fictional heavy latency sample '+index);await form.getByRole('button',{name:'Check entry',exact:true}).click();await expect(form).toContainText('Checks passed');await page.waitForLoadState('networkidle');
  const button=form.locator('.detail-footer .primary'),held=await holdWrite(page,'**/api/drafts/'+seeded.id+'/hand-add');
  try{await expect(button).toBeEnabled();hand.push(await sample(page,button,held,`[data-action-path="drafts/${seeded.id}/hand-add"][data-action-state="saving"]`));await held.release();await expect(form.locator('.action-feedback')).toContainText('Saved.',{timeout:120000});await form.getByRole('button',{name:'Back to Queue',exact:true}).click();}finally{await held.release();await control('finish',{clear:true});}
 }
 console.log('IMMEDIATE_LATENCY '+JSON.stringify({viewport:page.viewportSize(),settings,hand,method:'pointer/click to second animation frame; computed pressed style and pending DOM, held before POST'}));record('large-ledger Settings and Hand add pressed/pending within 100ms in five held-response samples per form');
}
