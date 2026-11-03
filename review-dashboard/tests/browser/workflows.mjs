import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';

export async function runWorkflows({page,base,persona,engine,theme,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}){
 const tag=`${engine}-${theme}-${width}`,november=`Fictional-November-${tag}.pdf`,foreignFile=`Fictional-Foreign-${tag}.pdf`,newAccountFile=`Fictional-New-Account-${tag}.pdf`;
 const get=async path=>{const r=await fetch(base+'api/'+path);assert.ok(r.ok,await r.clone().text());return r.json();};
 const wait=async(id,state)=>{await expect.poll(async()=>(await get('drafts/'+id)).state,{timeout:180000}).toBe(state);return get('drafts/'+id);};
 const receipt=Buffer.from(await (await fetch(base+'__test/receipt',{method:'POST'})).arrayBuffer());
 const submit=async(text,attachment)=>{
  await go('add/');await page.locator('#expense-words').fill(text);
  await expect(page.locator('#expense-words')).toHaveValue(text);
  await expect.poll(()=>page.evaluate(()=>JSON.parse(localStorage.getItem('ledger-add-v1')||'{}').text)).toBe(text);
  if(attachment){await page.getByLabel('Upload files',{exact:true}).setInputFiles({name:attachment+'.png',mimeType:'image/png',buffer:receipt});await expect(page.locator('.attachments')).toContainText(attachment+'.png');await expect(page.locator('.pending-upload')).toHaveCount(0);}
  await page.getByRole('button',{name:'Continue',exact:true}).click();
  await expect.poll(async()=>(await get('drafts?include_added=true')).find(d=>d.text===text&&d.state!=='editing')?.id,{timeout:20000}).toBeTruthy();
  return (await get('drafts?include_added=true')).find(d=>d.text===text&&d.state!=='editing').id;
 };
 const quiet=async()=>expect.poll(async()=>(await get('drafts?include_added=true')).some(d=>d.audit_pending?.length),{timeout:180000}).toBe(false);
 await control('gate',{state:'ready'});await resetDrafts({persona,empty:true});
 let config=await control('scenario',{mode:'ready',attachment:'Copper-Finch-Receipt'});
 await go('add/');
 await expect(page.locator('.composer-panel h2')).toHaveText('What happened?');await expect(page.locator('.understood-panel h2')).toHaveText('Understood as');
 const boxes=await page.locator('.add-layout .panel').filter({has:page.locator('h2')}).evaluateAll(nodes=>nodes.map(n=>{const b=n.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,bottom:b.bottom};}));
 if(width>760)assert.ok(boxes[1].x>=boxes[0].x+boxes[0].width,'Add panels sit side by side');
 await expect(page.locator('.statement-upload-panel')).toHaveCount(0);
 if(persona!=='empty')await snap('add-'+persona);
 const addedId=await submit('Copper Finch Café, 18.60, paid with my card','Copper-Finch-Receipt');
 const added=await wait(addedId,'added');assert.equal(added.result.outcome,'ready');assert.ok(added.result.commit_id);assert.equal(added.result.sources.length,1);assert.ok((await readFile(join(stage.ledger,added.result.sources[0]))).length>0);
 const rows=JSON.parse(await readFile(join(stage.ledger,'data/expenses/2026.json')));assert.ok(rows.some(r=>r.id===added.result.entry_id));
 await go('drafts/?draft='+addedId);await refresh();await expect(page.getByRole('heading',{name:'Added to your ledger',exact:true})).toBeVisible();await expect(page.locator('.draft-queue .draft-row')).toHaveCount(0);
 record('4B Add panels and expense submission through fake agent to verified ledger commit');
 // Conversion question comes from the real worker and fake agent proposal.
 config=await control('scenario',{mode:'conversion'});
 const conversion=await submit('Dinner at Juniper Table, 46.80 '+config.foreign+', on my card');
 const draft=await wait(conversion,'question');assert.equal(draft.currency_request.kind,'expense');
 await go('drafts/?draft='+conversion);await refresh();
 const form=page.locator('.currency-form'),charged=form.getByLabel('Amount in '+config.currency,{exact:true}),rate=form.getByLabel(config.currency+' for 1 '+config.foreign,{exact:true}),save=form.getByRole('button',{name:'Save expense',exact:true});
 await expect(save).toBeDisabled();await overflow('conversion blank');await snap('draft-conversion');
 for(const invalid of ['0','-1','abc','Infinity','1e3','']){await charged.fill(invalid);await expect(save).toBeDisabled();await rate.fill(invalid);await expect(save).toBeDisabled();}
 await charged.fill('51.48');await expect(rate).toHaveValue('1.1000');await expect(save).toBeEnabled();
 await rate.fill('1.1001');await expect(charged).toHaveValue('51.48');await rate.blur();await expect(rate).toHaveValue('1.1001');
 await rate.fill('1.1');await rate.blur();await expect(rate).toHaveValue('1.1000');
 await expect(form.getByLabel('This is an estimate')).toBeChecked();await expect(form).toContainText('replace an estimate with the amount actually charged');await snap('draft-conversion-filled');
 await page.setViewportSize({width:320,height:915});await overflow('conversion at 320');await page.setViewportSize({width,height:width>760?1000:915});
 await save.click();const converted=await wait(conversion,'added');assert.equal(converted.result.outcome,'ready');
 const expense=JSON.parse(await readFile(join(stage.ledger,'data/expenses/2026.json'))).find(r=>r.id===converted.result.entry_id);
 assert.equal(expense.postings[0].cost.amount,'51.48');assert.equal(expense.postings[1].amount,'-51.48');assert.match(expense.notes.join(' '),/estimate/);
 record('4B conversion blank/invalid gating, linked math in both directions, four decimals, estimate and committed total cost');
 config=await control('scenario',{mode:'transfer'});
 const transfer=await submit('Sent 1000 from '+config.source+' to '+config.destination);await wait(transfer,'question');await go('drafts/?draft='+transfer);await refresh();
 const received=page.getByLabel('Received in '+config.destinationCurrency,{exact:true}),transferRate=page.getByLabel(config.destinationCurrency+' for 1 '+config.sourceCurrency,{exact:true}),saveTransfer=page.getByRole('button',{name:'Save transfer',exact:true});
 await expect(saveTransfer).toBeDisabled();await snap('draft-transfer');
 for(const value of ['','0','-1','oops']){await received.fill(value);await expect(saveTransfer).toBeDisabled();}
 await transferRate.fill('1.16');await expect(received).toHaveValue('1160.00');await received.fill('1160');await expect(transferRate).toHaveValue('1.1600');await snap('draft-transfer-filled');
 await saveTransfer.click();const transferred=await wait(transfer,'added');
 const savedTransfer=JSON.parse(await readFile(join(stage.ledger,'data/expenses/2026.json'))).find(r=>r.id===transferred.result.entry_id);
 assert.deepEqual(savedTransfer.postings.map(p=>p.commodity),[config.sourceCurrency,config.destinationCurrency]);assert.equal(Number(savedTransfer.postings[0].amount),-1000);assert.equal(Number(savedTransfer.postings[1].amount),1160);
 record('4B transfer links received amount/rate and saves both native amounts');
 await control('scenario',{mode:'question'});const question=await submit('Paper Kite Market, groceries yesterday');await wait(question,'question');await go('drafts/?draft='+question);await refresh();
 await expect(page.getByRole('button',{name:'Send answer',exact:true})).toBeDisabled();
 await page.locator('.draft-question select').selectOption({index:1});await expect(page.getByRole('button',{name:'Send answer',exact:true})).toBeEnabled();await snap('draft-question');
 await control('scenario',{mode:'ready'});await page.getByRole('button',{name:'Send answer',exact:true}).click();await wait(question,'added');
 await control('scenario',{mode:'failure'});const failed=await submit('Cedar Taxi, paid with my card','Cedar-Taxi-Receipt');await wait(failed,'failed');await go('drafts/?draft='+failed);await refresh();
 await expect(page.locator('.draft-words')).toContainText('Cedar Taxi');await expect(page.locator('.attachments')).toContainText('Cedar-Taxi-Receipt.png');await snap('draft-failed');await quiet();
 await control('scenario',{mode:'ready',attachment:'Cedar-Taxi-Receipt'});await page.getByRole('button',{name:'Retry',exact:true}).click();await wait(failed,'added');
 record('4B account selection required before answering, failed input preserved and real Retry succeeds');
 // Real statement upload reads printed totals, replaces the conversion estimate,
 // and forces every unsafe row choice before the checked apply.
 config=await control('scenario',{mode:'ready'});
 const metadata=JSON.parse(await readFile(join(stage.ledger,'data/accounts.json'))).accounts;
 const identifier=metadata[config.account].statement?.identifier || '5678';
 const pdf=Buffer.from(await (await fetch(base+`__test/statement?currency=${config.currency}&foreign=${config.foreign}&identifier=${identifier}`,{method:'POST'})).arrayBuffer());
 await go('add/');await page.getByLabel('Upload files',{exact:true}).setInputFiles({name:november,mimeType:'application/pdf',buffer:pdf});
 await expect(page.locator('.pending-upload')).toHaveCount(0);await expect(page.locator('.file-interpretation')).toContainText('Statement');await snap('add-r3-statement-detected');await page.getByRole('button',{name:'Continue',exact:true}).click();
 await expect.poll(async()=>(await get('statements')).sessions.find(s=>s.original_name===november&&['found','currency_mismatch'].includes(s.state))?.id,{timeout:30000}).toBeTruthy();
 let session=(await get('statements')).sessions.find(s=>s.original_name===november);
 if(session.state==='currency_mismatch'){await page.getByLabel('Account in '+config.currency).selectOption(config.account);await page.getByRole('button',{name:'Use this account',exact:true}).click();await expect.poll(async()=>(await get('statements/'+session.id)).state,{timeout:30000}).toBe('found');}
 await go('drafts/?session='+session.id);await page.getByRole('button',{name:'Review checklist',exact:true}).click();await expect(page.getByRole('button',{name:'Review apply',exact:true})).toBeDisabled();
await expect(page.locator('.statement-conversion')).toContainText('51.48');await expect(page.locator('.statement-conversion')).toContainText('51.62');await snap('draft-statement');
 await page.getByRole('combobox',{name:'Decision for Juniper Table',exact:true}).selectOption('conversion');
 await expect(page.locator('.statement-decision').filter({hasText:'Replace estimate with posted amount'})).toBeVisible();
 for(const serial of ['WF-002','WF-003']){
  const row=page.locator(`[data-serial="${serial}"]`);await row.getByRole('combobox').first().selectOption('pending');await expect(row.locator('.statement-decision')).toContainText('Pending for Review');
 }
 await expect(page.getByRole('button',{name:'Review apply',exact:true})).toBeEnabled();await page.getByRole('button',{name:'Review apply',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Apply statement',exact:true}).click();
 await expect.poll(async()=>(await get('statements/'+session.id)).state,{timeout:180000}).toBe('applied');
 // The page must observe completion before navigation; its poll also refreshes
 // dashboard data. Server-only completion can race that refresh during unload.
 await expect(page.getByRole('heading',{name:'Statement applied',exact:true})).toBeVisible({timeout:180000});
 const posted=JSON.parse(await readFile(join(stage.ledger,'data/expenses/2026.json'))).find(r=>r.id===converted.result.entry_id);
 assert.equal(posted.postings[0].cost.amount,'51.62');assert.equal(posted.postings[1].amount,'-51.62');assert.match(posted.notes.join(' '),/replaces 51.48/);
 record('4B matching-currency statement upload, checklist gating and checked apply replaces the currency estimate');
 // An explicit mismatch never starts an agent conversion or permits Apply.
 const mismatch=Buffer.from(await (await fetch(base+`__test/statement?currency=${config.foreign}&foreign=${config.currency}&identifier=${identifier}`,{method:'POST'})).arrayBuffer());
 await go('add/');await page.getByLabel('Upload files',{exact:true}).setInputFiles({name:foreignFile,mimeType:'application/pdf',buffer:mismatch});
 await expect(page.locator('.statement-currency')).toBeVisible({timeout:30000});await expect(page.locator('.statement-currency')).toContainText('currencies must match');
 await expect(page.getByRole('button',{name:'Continue',exact:true})).toBeDisabled();
 assert.equal((await get('statements')).sessions.some(s=>s.original_name===foreignFile),false,'A mismatch stays staged before Continue');
 await snap('statement-mismatch');
 const compatible=Object.entries(metadata).find(([,m])=>['bank','card'].includes(m.kind)&&m.currency===config.foreign)?.[0];assert.ok(compatible);
 await page.getByLabel('Account in '+config.foreign).selectOption(compatible);await expect(page.getByRole('button',{name:'Continue',exact:true})).toBeEnabled();await snap('add-r3-currency-resolved');await page.getByRole('button',{name:'Continue',exact:true}).click();
 await expect.poll(async()=>(await get('statements')).sessions.find(s=>s.original_name===foreignFile)?.state,{timeout:30000}).toBe('found');
 await go('add/');await page.getByLabel('Upload files',{exact:true}).setInputFiles({name:newAccountFile,mimeType:'application/pdf',buffer:mismatch});
 await page.getByRole('button',{name:'Add an account in '+config.foreign,exact:true}).click();
 const inline=page.locator('.inline-account');await inline.getByLabel('Kind',{exact:true}).selectOption('card');await inline.getByLabel('Name',{exact:true}).fill('Willow Statement '+theme+' '+width);await expect(inline.getByLabel('Currency',{exact:true})).toHaveValue(config.foreign);await snap('statement-add-account');
 assert.equal(Object.values(JSON.parse(await readFile(join(stage.ledger,'data/accounts.json'))).accounts).some(m=>m.label==='Willow Statement '+theme+' '+width),false);
 await page.getByRole('button',{name:'Continue',exact:true}).click();
 await expect.poll(async()=>(await get('statements')).sessions.find(s=>s.original_name===newAccountFile)?.state,{timeout:180000}).toBe('found');
 record('4B statement mismatch blocks Continue, supports reassignment and stages account creation until a verified Continue');
 await quiet();await quiesce();await control('reset-workflows',{});
 // Stable queue/working/empty capture states are ignored fixture records.
 const queue=await resetDrafts({persona:'jordan'});await go('drafts/');await refresh();await snap('draft-queue');
 if(width>760){const links=page.locator('.draft-queue a.draft-row');await links.first().focus();await page.keyboard.press('End');await expect(links.last()).toBeFocused();await page.keyboard.press('j');await expect(links.filter({hasText:'Juniper Table'})).toBeFocused();}
 if(width<760){await expect(page.locator('.draft-detail-wrap')).toBeHidden();await page.locator('.draft-row').first().click();await expect(page.getByRole('link',{name:'All drafts',exact:true})).toBeVisible();await page.getByRole('link',{name:'All drafts',exact:true}).click();await expect(page.locator('.draft-queue')).toBeVisible();}
 await go('drafts/?draft='+queue.ids.at(-1));await refresh();await expect(page.locator('.agent-next')).toContainText('You can leave this page');await snap('draft-working');
 await resetDrafts({persona,empty:true});await go('drafts/');await refresh();await expect(page.getByText('No drafts waiting.',{exact:true})).toBeVisible();await snap('draft-empty');
 record('4B phone list/detail All drafts navigation, safe working state and empty actionable badge');
}
