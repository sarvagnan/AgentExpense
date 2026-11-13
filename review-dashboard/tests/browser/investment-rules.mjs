import assert from 'node:assert/strict';

/** The investment destination, saved rule and cash budget in every persona/engine/size. */
export async function runInvestmentRules({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}) {
 const published=async()=>{await expect.poll(async()=>{const response=await fetch(base+'api/data-status');assert.ok(response.ok);const status=await response.json();assert.ok(!status.refresh_error,status.refresh_error||'Published views have no refresh error');return !status.data_stale&&!status.updating;},{timeout:120000}).toBe(true);};
 const numeric=text=>Number(text.replace(/[^0-9.-]/g,''));
 const fact=async(facts,label)=>{const term=facts.getByText(label,{exact:true});return await term.count()?numeric(await term.locator('xpath=following-sibling::dd[1]').innerText()):0;};
 const spending=async facts=>Object.fromEntries(await Promise.all(['Recorded so far','Spending commitments','Estimated variable'].map(async label=>[label,await fact(facts,label)])));
 await quiesce();await control('reset-review',{});await resetDrafts({persona,empty:true});
 const seed=await control('rules',{investment:true});await published();
 await go('forecast/?section=rules');await expect(page.getByRole('button',{name:'New rule',exact:true})).toBeEnabled();
 const row=name=>page.getByRole('article',{name,exact:true});
 await expect(row('SIP contribution')).toContainText('Money set aside');
 await row('SIP contribution').getByRole('button',{name:'Edit rule',exact:true}).click();
 const editor=page.getByRole('dialog',{name:'Edit rule',exact:true}),destination=editor.getByRole('combobox',{name:'Invest in',exact:true});
 await expect(destination).toHaveValue('Assets:Rule Reserve');await expect(editor.getByText('Invest in',{exact:true})).toBeVisible();
 assert.match(await destination.locator('option:checked').textContent(),/Invest in · Retirement Reserve/);
 await expect(editor.getByLabel('Varies',{exact:true})).toBeEnabled();await overflow('Investment rule editor');await snap('investment-editor');
 await editor.getByLabel('Description',{exact:true}).fill('SIP reserve contribution');
 const [response]=await Promise.all([page.waitForResponse(r=>r.url().includes('/api/rules/operations/')&&r.request().method()==='POST'),editor.getByRole('button',{name:'Save rule',exact:true}).click()]);assert.ok(response.ok(),await response.text());await expect(editor).toHaveCount(0);
 await expect(row('SIP reserve contribution')).toContainText('Money set aside');
 await page.getByRole('combobox',{name:'Sort rules',exact:true}).selectOption('amount');await page.getByRole('combobox',{name:'Order',exact:true}).selectOption('desc');
 const names=await page.locator('.rules-list .rule-row h2').allTextContents();assert.ok(names.indexOf('SIP reserve contribution')<names.indexOf('Birch rent'));
 await published();await page.reload();await expect(page.getByRole('button',{name:'New rule',exact:true})).toBeEnabled();await expect(page.locator('.data-refresh-notice')).toHaveCount(0);await expect(page.getByRole('combobox',{name:'Sort rules',exact:true})).toHaveValue('amount');await expect(row('SIP reserve contribution')).toContainText('5,000');await overflow('Investment rule list');await snap('investment-rules');
 // Compare the published spending facts before and after another contribution.
 await go('forecast/');const beforeFacts=page.locator('.page.forecast').filter({has:page.locator('.forecast-items').getByText('SIP reserve contribution',{exact:true})}).locator('.forecast-breakdown');
 await expect.poll(()=>fact(beforeFacts,'Money set aside'),{timeout:120000}).toBe(5000);
 const beforeSpending=await spending(beforeFacts);
 await page.getByRole('button',{name:'Rules',exact:true}).click();await expect(page.getByRole('button',{name:'New rule',exact:true})).toBeEnabled();
 // Create a second contribution with the same simple editor used for categories.
 await page.getByRole('button',{name:'New rule',exact:true}).click();const fresh=page.getByRole('dialog',{name:'New rule',exact:true});
 await fresh.getByLabel('Description',{exact:true}).fill('Annual reserve contribution');await fresh.locator('input[inputmode="decimal"]').fill('600');await fresh.getByRole('combobox',{name:'Paying account',exact:true}).selectOption(seed.pay);await fresh.getByRole('combobox',{name:'Category or investment',exact:true}).selectOption('Assets:Rule Reserve');await fresh.getByLabel('Schedule',{exact:true}).selectOption('year');await fresh.getByLabel('Next date',{exact:true}).fill('2026-11-20');
 const [made]=await Promise.all([page.waitForResponse(r=>r.url().includes('/api/rules/operations/')&&r.request().method()==='POST'),fresh.getByRole('button',{name:'Save rule',exact:true}).click()]);assert.ok(made.ok(),await made.text());await expect(fresh).toHaveCount(0);
 await published();await go('forecast/');
 const forecast=page.locator('.page.forecast').filter({has:page.locator('.forecast-items').getByText('SIP reserve contribution',{exact:true})});
 await expect(forecast).toHaveCount(1);const item=forecast.locator('.forecast-items li').filter({hasText:'SIP reserve contribution'});
 await expect(item).toContainText('Money set aside');await expect(item).toHaveClass(/\bset-aside\b/);
 assert.ok(!(await item.locator('.row-title').evaluate(node=>getComputedStyle(node).textDecorationLine)).includes('line-through'),'A contribution is an upcoming payment, without a struck-through title');
 const withdrawal=forecast.locator('.forecast-items li').filter({hasText:'Reserve withdrawal'});
 await expect(withdrawal).toContainText('Investments');await expect(withdrawal).not.toContainText('Money set aside');await expect(withdrawal).not.toHaveClass(/\bset-aside\b/);
 const facts=forecast.locator('.forecast-breakdown');await expect(facts.getByText('Money set aside',{exact:true})).toBeVisible();await expect(facts.getByText('Cash still needed',{exact:true})).toBeVisible();
 const summary=await facts.locator('dt').allTextContents();assert.ok(summary.includes('Spending commitments'));
 await expect.poll(()=>fact(facts,'Money set aside'),{timeout:120000}).toBe(5600);
 assert.deepEqual(await spending(facts),beforeSpending,'Another contribution changes neither recorded spending, spending commitments nor variable estimates');
 const expectedCash=beforeSpending['Spending commitments']+beforeSpending['Estimated variable']+5600;
 assert.ok(Math.abs(await fact(facts,'Cash still needed')-expectedCash)<=1,'Cash needed includes both contributions; whole-currency display rounding differs by at most one');
 await expect(forecast.locator('.forecast-items li').filter({hasText:'Annual reserve contribution'})).toContainText('Money set aside');
 await expect(forecast.locator('.forecast-variable')).not.toContainText('Annual reserve contribution');
 await expect(page.locator('.data-refresh-notice')).toHaveCount(0);
 await expect(forecast.locator('.forecast-variable')).not.toContainText('SIP reserve contribution');
 await overflow('Investment forecast');await item.scrollIntoViewIfNeeded();await snap('investment-forecast');record('Investment destination edit/create, amount sort and retained URL; published Forecast set aside 5,000 → 5,600 and cash needed with unchanged spending');
 await quiesce({writesSettled:true});await control('reset-review',{});await resetDrafts({persona,empty:true});
}
