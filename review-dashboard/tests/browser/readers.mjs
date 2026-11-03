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
  await go('drafts/?session='+session.id);
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
  await go('drafts/?filed=2026-11-28&account='+encodeURIComponent(config.account));
  await expect(page.getByRole('link',{name:'Open filed statement',exact:true})).toBeVisible();
  await expect(page.locator('.stored-statement')).toContainText('965');
  await snap('filed-statement-'+extension);
  record('7A '+extension+' upload, detected reader/account, running balance checklist, Apply, membership, assertion, audit commit and filed view');
 }
 await quiesce();await control('reset-workflows',{});await go('add/');
}
