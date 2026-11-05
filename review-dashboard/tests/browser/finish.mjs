import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {runMonthCutoff} from './month-cutoff.mjs';

export async function runFinish({page,base,persona,engine,theme,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}) {
 const json=async(path,body)=>{const response=await fetch(base+'api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});assert.ok(response.ok,await response.clone().text());return response.json();};
 const get=async path=>(await fetch(base+'api/'+path)).json();
 const wait=async(id,state)=>{await expect.poll(async()=>(await get('drafts/'+id)).state,{timeout:180000}).toBe(state);return get('drafts/'+id);};
 await control('gate',{state:'ready'});await resetDrafts({persona,empty:true});
 await control('scenario',{mode:'ready'});
 const seeded=await control('finish',{});
 await go('add/');await refresh();
 await expect(page.locator('.agent-notice')).toHaveCount(1);await expect(page.locator('.agent-notice')).toHaveAttribute('data-agent-state','outage');await expect(page.locator('.agent-notice')).toContainText('Resumes at');await expect(page.locator('.agent-notice')).toContainText('Statements read by the built-in reader still work');
 await page.locator('#expense-words').fill('Fictional words kept during pause');
 await expect(page.getByRole('button',{name:'Continue',exact:true})).toBeDisabled();await expect(page.locator('#expense-words')).toHaveValue('Fictional words kept during pause');
 await snap('pause-add');
 // Add's approved pause notice blocks Continue. The backend queue and human
 // transaction contracts remain available independently of agent readiness.
 const queued=await json('drafts',{text:'Fictional queued through the API during pause'});await json('drafts/'+queued.id+'/save',{});await wait(queued.id,'queued');
 await go('queue/?draft='+seeded.id);await refresh();
 await expect(page.locator('.agent-notice')).toHaveCount(1);await expect(page.locator('.agent-notice')).toContainText('The agent is paused (usage limit).');await snap('pause-usage-limit');
 await page.getByRole('button',{name:'Finish it yourself',exact:true}).click();
 const form=page.getByRole('article',{name:'Finish it yourself',exact:true});
 await expect(form.getByLabel('Description',{exact:true})).toHaveValue('Copper Finch Café');
 await form.getByLabel('Date',{exact:true}).fill(stage.env.DASHBOARD_TODAY);
 await form.getByLabel('Description',{exact:true}).fill(`Copper Finch hand entry ${engine} ${theme} ${width}`);
 await form.getByRole('button',{name:'Add posting',exact:true}).click();await form.getByRole('button',{name:'Remove posting 3',exact:true}).click();
 await form.getByLabel('Notes',{exact:true}).fill('Finished from a fictional receipt');
 const receipt=Buffer.from(await (await fetch(base+'__test/receipt',{method:'POST'})).arrayBuffer());
 await form.getByLabel('Attachments',{exact:true}).setInputFiles({name:'Fictional-Hand-Receipt.png',mimeType:'image/png',buffer:receipt});
 await expect(form.getByText('1 attachment',{exact:true})).toBeVisible();
 await form.getByRole('button',{name:'Check entry',exact:true}).click();await expect(form.getByRole('heading',{name:'Checks passed',exact:true})).toBeVisible();
 await overflow('finish form');await snap('finish-by-hand');await page.setViewportSize({width:320,height:915});await overflow('finish form 320');await page.setViewportSize({width,height:width>760?1000:915});
 await form.getByRole('button',{name:'Back to Queue',exact:true}).click();
 await page.getByRole('button',{name:'Finish it yourself',exact:true}).click();
 await expect(form.getByLabel('Notes',{exact:true})).toHaveValue('Finished from a fictional receipt');
 await form.getByRole('button',{name:'Check entry',exact:true}).click();await expect(form.getByRole('heading',{name:'Checks passed',exact:true})).toBeVisible();
 await form.getByRole('button',{name:'Save and add',exact:true}).click();
 const added=await wait(seeded.id,'added');assert.equal(added.finished_by.actor,'user');assert.equal(added.result.sources.length,1);
 const auditFiles=(await import('node:fs/promises')).readdir;
 const years=await auditFiles(join(stage.ledger,'data/additions'));
 const audit=(await Promise.all(years.map(y=>readFile(join(stage.ledger,'data/additions',y),'utf8')))).join('\n').split('\n').filter(Boolean).map(JSON.parse);
 assert.ok(audit.some(r=>r.draft_id===seeded.id&&r.finished_by?.actor==='user'&&r.account_consent?.method==='hand-ticks'));
 await go('forecast/');
 const forecasts=page.locator('.forecast');
 const remaining=new Date(Number(stage.env.DASHBOARD_TODAY.slice(0,4)),Number(stage.env.DASHBOARD_TODAY.slice(5,7)),0).getDate()-Number(stage.env.DASHBOARD_TODAY.slice(8,10));
 await expect(forecasts.locator('.estimate .row-sub').filter({hasText:`The ${remaining} days left`})).toHaveCount(1);
 for(const list of await page.locator('.forecast-items').all()) {await expect(list.locator('.estimate')).toHaveCount(1);await expect(list.locator('li').last()).toHaveClass('estimate');await expect(list.locator('.estimate .row-sub')).toHaveCSS('white-space','normal');}
 await overflow('single variable row after current-month entry');await snap('forecast-variable-days-left');
 record('6b Forecast shows one final variable row with remaining days after a checked current-month addition');
 await runMonthCutoff({page,stage,expect,go,overflow,snap,record});
 await quiesce();await control('reset-workflows',{});await control('finish',{clear:true});
 record('6a outage notice and hand form preserve claim work, upload evidence and commit without agent');
 await control('scenario',{mode:'new-root'});
 const draft=await json('drafts',{text:'Fictional shared purchase'});await json('drafts/'+draft.id+'/save',{});await wait(draft.id,'question');
 await go('queue/?draft='+draft.id);await refresh();
 await expect(page.getByRole('heading',{name:'Create these accounts?',exact:true})).toBeVisible();await expect(page.locator('.draft-detail')).toContainText('Shared:Accounts Receivable:Finch');
 await expect(page.locator('.draft-detail .queue-pieces:empty')).toHaveCount(0);await overflow('new account consent');await snap('new-account-question');await page.getByRole('button',{name:'Create and add',exact:true}).click();
 await wait(draft.id,'added');record('6a new top-level account question approves exact proposal without a second agent call');
 await quiesce();await control('reset-workflows',{});
}
