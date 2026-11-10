import assert from 'node:assert/strict';
import {hold,guard} from './held.mjs';
import {expectInjectedHttp} from './expected-errors.mjs';

export async function runAttachments({page,base,persona,expect,control,go,record,quiesce,resetDrafts,snap,queueOnly=false,timeoutOnly=false}){
 if(!queueOnly){
 await quiesce();await control('reset-workflows',{});await resetDrafts({persona,empty:true});await control('gate',{state:'ready'});await go('add/');
 if(!timeoutOnly){
 const receipt=Buffer.from(await(await fetch(base+'__test/receipt',{method:'POST'})).arrayBuffer());
 const picker=page.getByLabel('Upload files',{exact:true}),words=page.locator('#expense-words');
 let sent=0;const count=request=>{if(request.method()==='POST'&&/\/api\/drafts\/[^/]+\/files$/.test(request.url()))sent++;};page.on('request',count);
 await control('hold-uploads',{action:'hold',count:3});
 const pending={ready:expect.poll(async()=>(await control('hold-uploads',{action:'status'})).held,{timeout:20000}).toBe(3),release:()=>control('hold-uploads',{action:'release'})};
 try{
  await picker.setInputFiles(Array.from({length:13},(_,i)=>({name:`Fictional-page-${i+1}.png`,mimeType:'image/png',buffer:receipt})));
  await pending.ready;assert.equal(sent,3,'Only three requests may start while the first three remain held');
  await expect(page.locator('.pending-upload')).toHaveCount(13);await expect(page.getByRole('progressbar')).toHaveCount(13);
  const waitingRow=page.locator('.pending-upload').filter({hasText:'Fictional-page-12.png'});
  const rowTop=await waitingRow.evaluate(n=>n.getBoundingClientRect().top+scrollY);
  await page.getByRole('button',{name:'Cancel Fictional-page-13.png',exact:true}).click();
  await expect(page.locator('.pending-upload')).toHaveCount(12);
  await expect(page.getByRole('button',{name:'Cancel Fictional-page-13.png',exact:true})).toHaveCount(0);
  assert.ok(Math.abs((await waitingRow.evaluate(n=>n.getBoundingClientRect().top+scrollY))-rowTop)<2,'A late file list must not duplicate held uploads and move waiting rows');
  await words.fill('Words typed while twelve files wait');await words.focus();
  await expect(words).toHaveValue('Words typed while twelve files wait');assert.equal(sent,3);
  await snap('attachments-held');
  await pending.release();await expect(page.locator('.pending-upload')).toHaveCount(0,{timeout:30000});
  assert.equal(sent,12);await expect(page.locator('.attachments > li')).toHaveCount(12);await expect(words).toHaveValue('Words typed while twelve files wait');await expect(words).toBeFocused();
 }finally{await pending.release();page.off('request',count);}
 // Fill the same item to its limit through the real endpoint, then check the next
 // chosen file's own row carries the server's plain refusal.
 let draft;await expect.poll(async()=>{const drafts=await(await fetch(base+'api/drafts')).json();draft=drafts.find(d=>d.text==='Words typed while twelve files wait');return !!draft;}).toBe(true);
 for(const n of [10,10,10,8]){const body=new FormData();for(let i=0;i<n;i++)body.append('files',new Blob([receipt],{type:'image/png'}),'Fictional-extra.png');const response=await fetch(base+`api/drafts/${draft.id}/files`,{method:'POST',body});assert.equal(response.status,201);}
 await page.reload();await expect(page.locator('.attachments > li')).toHaveCount(50);await expect(picker).toBeEnabled();
 expectInjectedHttp(page,base+`api/drafts/${draft.id}/files`,413);
 const knownError=page.waitForResponse(r=>r.url()===base+`api/drafts/${draft.id}/files`&&r.status()===413);
 await picker.setInputFiles({name:'Fictional-file-51.png',mimeType:'image/png',buffer:receipt});await knownError;
 const refused=page.locator('.pending-upload').filter({hasText:'Fictional-file-51.png'});await expect(refused).toContainText('At most 50 files per item');await expect(refused.getByRole('button',{name:'Retry upload'})).toBeVisible();await refused.scrollIntoViewIfNeeded();await snap('attachments-limit');
 // This expected 413 is accounted for by this exact request and assertion.
 record('attachments: twelve files, three held concurrent requests, row progress, newer words/focus, and file 51 refusal');
 }
 await quiesce();await control('reset-workflows',{});await resetDrafts({persona,empty:true});
 const failed=await control('attachment-timeouts',{});await go(`queue/?draft=${failed.id}`);
 const detail=page.getByRole('article',{name:'Item details'});
 await expect(detail).toContainText('three tries of 1 second',{timeout:30000});await expect(detail).toContainText('Nothing was saved. Retry or finish the entry yourself.');
 await expect(page.locator('section[aria-label="Needs you"]')).toContainText('Failed');
 await expect(detail.getByRole('button',{name:'Retry',exact:true})).toBeVisible();await snap('attachments-timeout-failed');
 await control('attachment-timeouts',{action:'arm'});
 const retryResponse=page.waitForResponse(r=>r.url()===base+`api/drafts/${failed.id}/retry`&&r.request().method()==='POST');
 const retry=await hold(page,base+`api/drafts/${failed.id}/retry`,{method:'POST'});
 try{await detail.getByRole('button',{name:'Retry',exact:true}).click();await retry.ready;await retry.release();assert.equal((await(await retryResponse).json()).consecutive_timeouts,0);}finally{await retry.release();}
 try{
  await expect.poll(async()=>(await control('attachment-timeouts',{action:'status'})).held,{timeout:20000}).toBe(true);
  const d=await(await fetch(base+`api/drafts/${failed.id}`)).json();assert.equal(d.state,'queued');assert.equal(d.consecutive_timeouts,1);assert.equal(d.runs.length,4);assert.ok(d.runs.every(r=>r.duration>=1));
 }finally{await control('attachment-timeouts',{action:'release'});}
 await expect.poll(async()=>{const d=await(await fetch(base+`api/drafts/${failed.id}`)).json();return d.state==='added'&&d.result?.outcome==='not_expense'&&!d.audit_pending?.length;},{timeout:30000}).toBe(true);
 record('attachments: three real local deadlines show Failed reason and Retry in Needs you; Retry resets the count');
 await quiesce();await control('reset-workflows',{});await resetDrafts({persona,empty:true});
 }
 if(timeoutOnly)return;
 await quiesce();await control('reset-workflows',{});await resetDrafts({persona,empty:true});
 const receiptFailure=await control('attachment-receipt-failed',{});
 // Keep any eager correction claim open across dismissal/navigation. A resolved
 // notice must be discardable without starting that claim at all.
 let claimAttempts=0,releaseClaims;const claimGate=new Promise(resolve=>releaseClaims=resolve),claimWork=[];
 const claimUrl=base+`api/drafts/${receiptFailure.fix}/claim`;
 const claimHandler=route=>{claimAttempts++;const work=guard(route,async()=>{const response=await route.fetch();await claimGate;await route.fulfill({response});});claimWork.push(work);return work;};
 await page.route(claimUrl,claimHandler);
 try{
 await go(`queue/?draft=${receiptFailure.id}`);
 const section=page.locator('section[aria-label="Needs you"]');await expect(section).toContainText('Fictional saved receipt payment');
 // A separate resolved proposal has no typed amount; the only payment here is saved.
 await expect(section.locator('.queue-heading')).not.toContainText('100');
 const receiptPanel=page.getByRole('region',{name:'Receipt reading'});await expect(receiptPanel.getByRole('button',{name:'Retry',exact:true})).toBeVisible();
 await receiptPanel.scrollIntoViewIfNeeded();await snap('attachments-receipt-dismiss');
 const dismissed=page.waitForResponse(r=>r.url().endsWith(`/drafts/${receiptFailure.id}/receipt-dismiss`)&&r.request().method()==='POST');
 await receiptPanel.getByRole('button',{name:'Dismiss',exact:true}).click();assert.equal((await dismissed).status(),200);
 await expect(section).not.toContainText('Fictional saved receipt payment');
 const saved=await(await fetch(base+`api/drafts/${receiptFailure.id}`)).json();assert.equal(saved.state,'added');assert.equal(saved.receipt_reading.state,'dismissed');assert.deepEqual(saved.result.sources,[receiptFailure.evidence]);
 await page.goto(base+`queue/?draft=${receiptFailure.fix}`);await expect(page.locator(`a.draft-row[href*="${receiptFailure.fix}"] .draft-status`)).toHaveText('Resolved');await expect(page.getByText('The ledger check now passes',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'Retry',exact:true})).toHaveCount(0);
 await page.getByText('The ledger check now passes',{exact:true}).scrollIntoViewIfNeeded();await snap('attachments-resolved-fix');
 const discarded=page.waitForResponse(r=>r.url()===base+`api/drafts/${receiptFailure.fix}`&&r.request().method()==='DELETE');
 await page.getByRole('button',{name:'Discard proposal',exact:true}).click();assert.equal((await discarded).status(),204);
 await expect(page.getByText('The ledger check now passes',{exact:true})).toHaveCount(0);assert.equal(claimAttempts,0,'A resolved notice must not acquire the correction form');
 }finally{releaseClaims();await Promise.all(claimWork);await page.unroute(claimUrl,claimHandler);}
 record('Queue: failed receipt Dismiss keeps payment/evidence and excludes its amount; resolved fix shows notice without Retry and can be discarded');
 await quiesce();await control('reset-workflows',{});await resetDrafts({persona,empty:true});
}
