// Production intake, readers, checklist and verified Apply; only fictional files.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';

export async function runReaderWorkflows({page,base,persona,engine,theme,width,stage,expect,control,go,overflow,snap,record,quiesce}) {
 const get=async path=>{const response=await fetch(base+'api/'+path);assert.ok(response.ok,await response.clone().text());return response.json();};
 for(const extension of ['csv','xlsx','xls']) {
  await quiesce();await control('reset-workflows',{});
  const config=await control('reader-scenario',{extension,tag:`${engine}-${theme}-${width}`});
  const name=`Fictional-${persona}-${engine}-${theme}-${width}.${extension}`;
  const response=await fetch(base+'__test/reader-file?extension='+extension,{method:'POST'});assert.ok(response.ok);
  const buffer=Buffer.from(await response.arrayBuffer());
  await go('add/');
  await expect(page.getByLabel('Upload files',{exact:true})).toHaveAttribute('accept',new RegExp('\\.'+extension));
  await page.getByLabel('Upload files',{exact:true}).setInputFiles({name,mimeType:extension==='csv'?'text/csv':'application/octet-stream',buffer});
  await expect(page.locator('.pending-upload')).toHaveCount(0);await expect(page.locator('.file-interpretation')).toContainText('Statement');await page.getByRole('button',{name:'Continue',exact:true}).click();
  await expect.poll(async()=>(await get('statements')).sessions.find(s=>s.original_name===name)?.state,{timeout:30000}).toBe('found');
  let session=(await get('statements')).sessions.find(s=>s.original_name===name);
  assert.equal(session.statement.account,config.account);assert.equal(session.statement.readerName,config.reader);assert.ok(session.checks.every(c=>c.ok));
  await go('queue/?session='+session.id);
  await expect(page.getByText('Closing balance',{exact:true})).toBeVisible();
  await expect(page.locator('.statement-total')).toBeVisible();
  await page.getByRole('button',{name:'Review checklist',exact:true}).click();
  await expect(page.locator('.statement-reader')).toContainText(config.reader);
  for(const {row} of session.plan.items.filter(i=>i.needs_user))await page.getByRole('combobox',{name:'Decision for '+row.description,exact:true}).selectOption('pending');
  await expect(page.getByRole('button',{name:'Review apply',exact:true})).toBeEnabled();
  await page.locator('.statement-automatic > summary').click();
  await expect(page.locator('.statement-automatic')).toContainText('Covered by opening balances');
  await page.locator('.statement-row-details').first().locator('summary').click();
  await expect(page.locator('.statement-row-details').first()).toContainText('balance');
  await overflow('7A '+extension+' bank statement');await snap('draft-statement-'+extension);
  await page.getByRole('button',{name:'Review apply',exact:true}).click();
  await expect(page.getByRole('dialog')).toContainText('Closing balance');
  await page.getByRole('button',{name:'Apply statement',exact:true}).click();
  await expect.poll(async()=>(await get('statements/'+session.id)).state,{timeout:180000}).toBe('applied');
  session=await get('statements/'+session.id);assert.equal(session.result.added,3);assert.ok(session.result.commit_id);
  const summary=JSON.parse(await readFile(join(stage.ledger,'data/card-statements.json'))).find(s=>s.account===config.account);
  assert.equal(summary.kind,'bank');assert.equal(summary['closing-balance'],'965.00');assert.equal(summary.members.length,4);assert.equal(session.result.skipped,1);assert.equal(summary.members[0].disposition,'pre-start');
  const entries=JSON.parse(await readFile(join(stage.ledger,'data/expenses/2026.json')));
  assert.ok(entries.find(e=>e.id===summary['assertion-id']).postings[0].assert);
  assert.ok((await readFile(join(stage.ledger,summary.source))).equals(buffer));
  await go('queue/?filed=2026-11-28&account='+encodeURIComponent(config.account));
  await expect(page.getByRole('link',{name:'Open filed statement',exact:true})).toBeVisible();
  await expect(page.locator('.stored-statement')).toContainText('965');
  await snap('filed-statement-'+extension);
  record('7A '+extension+' upload, detected reader/account, running balance checklist, Apply, membership, assertion, audit commit and filed view');
 }
 await quiesce();await control('reset-workflows',{});await control('gate',{state:'ready'});
 const config=await control('agent-statement-scenario',{tag:`${engine}-${theme}-${width}`});
 const response=await fetch(base+'__test/agent-statement-file',{method:'POST'});assert.ok(response.ok);
 const buffer=Buffer.from(await response.arrayBuffer()),name=`Unmapped-${persona}-${engine}-${theme}-${width}.csv`;
 await go('add/');
 await page.getByLabel('Upload files',{exact:true}).setInputFiles({name,mimeType:'text/csv',buffer});
 await expect(page.locator('.pending-upload')).toHaveCount(0);
 await expect(page.locator('.file-interpretation')).toContainText('Will be read by the agent');
 await page.getByLabel('Statement account',{exact:true}).selectOption(config.account);
 await expect(page.locator('.file-interpretation')).toContainText('Source values · Printed totals will be checked.');
 await page.getByRole('button',{name:'Continue',exact:true}).click();
 await expect.poll(async()=>(await get('statements')).sessions.find(s=>s.original_name===name)?.state,{timeout:30000}).toBe('found');
 let session=(await get('statements')).sessions.find(s=>s.original_name===name);
 assert.equal(session.extraction,'agent');assert.equal(session.agent_verified,true);assert.ok(session.learning);
 await go('queue/?session='+session.id);
 await expect(page.getByText('Read by the agent · source values and totals checked',{exact:true})).toBeVisible();
 await page.getByLabel('Save this column mapping for future statements',{exact:true}).check();
 await expect.poll(async()=>(await get('statements/'+session.id)).learn_mapping).toBe(true);
 await page.getByRole('button',{name:'Review checklist',exact:true}).click();
 for(const {row} of session.plan.items.filter(i=>i.needs_user))await page.getByRole('combobox',{name:'Decision for '+row.description,exact:true}).selectOption('pending');
 await expect(page.getByRole('button',{name:'Review apply',exact:true})).toBeEnabled();
 // A statement's Queue row carries a real, styled state tag.
 const queueRow=page.locator('.draft-queue .draft-row').filter({hasText:name});
 await expect(queueRow.locator('.draft-status')).toHaveText('Decisions');await expect(queueRow.locator('.draft-status')).toHaveClass(/state-question/);
 await overflow('7B cited agent statement');await snap('draft-agent-statement');
 await page.getByRole('button',{name:'Review apply',exact:true}).click();
 await page.getByRole('button',{name:'Apply statement',exact:true}).click();
 await expect.poll(async()=>(await get('statements/'+session.id)).state,{timeout:180000}).toBe('applied');
 await expect(page.getByRole('heading',{name:'Statement applied',exact:true})).toBeVisible();
 session=await get('statements/'+session.id);assert.ok(session.result.commit_id);
 const accounts=JSON.parse(await readFile(join(stage.ledger,'data/accounts.json'))).accounts;
 assert.equal(accounts[config.account].statement.reader,'generic-csv');
 const summary=JSON.parse(await readFile(join(stage.ledger,'data/card-statements.json'))).find(s=>s.account===config.account);
 assert.equal(summary['closing-balance'],'92.00');assert.equal(summary.members.length,2);
 assert.ok((await readFile(join(stage.ledger,summary.source))).equals(buffer));
 // Applied, the statement leaves Needs you even while focus stays in its detail.
 await expect(queueRow).toHaveCount(0);
 await snap('applied-agent-statement');
 await quiesce();await go('add/');
 await page.getByLabel('Upload files',{exact:true}).setInputFiles({name:'Next-unmapped.csv',mimeType:'text/csv',buffer});
 await expect(page.locator('.file-interpretation')).toContainText('Read by the built-in reader');
 record('7B unknown statement, cited fake reading, source and totals gate, checked Apply, optional learned mapping and built-in recognition');
 for(const useAgent of [false,true]) {
  await quiesce();await control('reset-workflows',{});await control('gate',{state:'ready'});
  const early=await control('early-card-scenario',{tag:`${engine}-${theme}-${width}`,agent:useAgent});
  const extension=useAgent?'csv':'pdf',name=`Early-${useAgent?'agent':'reader'}-${engine}-${theme}-${width}.${extension}`;
  await go('add/');
  await page.getByLabel('Upload files',{exact:true}).setInputFiles({name,mimeType:useAgent?'text/csv':'application/pdf',buffer:Buffer.from(early.content,'base64')});
  await expect(page.locator('.pending-upload')).toHaveCount(0);
  if(useAgent) {
   await expect(page.locator('.file-interpretation')).toContainText('Will be read by the agent');
   await page.getByLabel('Statement account',{exact:true}).selectOption(early.account);
  }
  await page.getByRole('button',{name:'Continue',exact:true}).click();
  await expect.poll(async()=>(await get('statements')).sessions.find(s=>s.original_name===name)?.state,{timeout:30000}).toBe('found');
  let session=(await get('statements')).sessions.find(s=>s.original_name===name);
  assert.equal(session.statement.account,early.account);assert.deepEqual(session.plan.items.map(i=>i.row.date),['2026-10-31','2026-10-29']);
  assert.ok(session.checks.every(c=>c.ok));assert.equal(session.extraction,useAgent?'agent':'parser');
  await go('queue/?session='+session.id);await page.getByRole('button',{name:'Review checklist',exact:true}).click();
  for(const {row} of session.plan.items.filter(i=>i.needs_user))await page.getByRole('combobox',{name:'Decision for '+row.description,exact:true}).selectOption('pending');
  for(const day of ['2026-10-31','2026-10-29']) {
   const item=session.plan.items.find(i=>i.row.date===day),row=page.locator(`[data-serial="${item.row.serial}"]`);
   await row.locator('.statement-row-details > summary').click();
   await expect(row.locator('.statement-row-details')).toContainText(new Intl.DateTimeFormat({asha:'en-IN',empty:'en-US',jordan:'en-US',sam:'en-GB'}[persona],{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}).format(new Date(day+'T00:00:00Z')));
  }
  await expect(page.getByRole('button',{name:'Review apply',exact:true})).toBeEnabled();
  await overflow('7A2 early card dates');await snap(`early-card-${useAgent?'agent':'reader'}-checklist`);
  await page.getByRole('button',{name:'Review apply',exact:true}).click();await page.getByRole('button',{name:'Apply statement',exact:true}).click();
  await expect.poll(async()=>(await get('statements/'+session.id)).state,{timeout:180000}).toBe('applied');
  session=await get('statements/'+session.id);assert.ok(session.result.commit_id);
  await go('queue/?filed=2026-11-28&account='+encodeURIComponent(early.account));
  await expect(page.locator('.stored-statement .statement-row')).toHaveCount(2);
  await overflow('7A2 filed early card');await snap(`early-card-${useAgent?'agent':'reader'}-filed`);
  record(`7A2 ${useAgent?'cited fake agent':'built-in reader'} accepts early card dates, shows purchase dates, and applies filed membership`);
 }
 await quiesce();await control('reset-workflows',{});await go('add/');
}
