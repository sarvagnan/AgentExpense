import assert from 'node:assert/strict';

export async function runAddPolish({page,persona,expect,control,go,snap,record,quiesce,resetDrafts}){
 await quiesce();await control('reset-workflows',{});await resetDrafts({persona,empty:true});await control('gate',{state:'ready'});await control('confirmed-balances',{});await go('add/');
 const words=page.locator('#expense-words'),guess=page.getByRole('complementary',{name:'Usual payment'}),notice=page.getByRole('alert',{name:'Confirmed balance refusal'});
 await words.fill('Cedar learning on 30 October');await guess.getByLabel('Payment date').fill('2026-10-30');await guess.getByRole('checkbox',{name:/Add another anyway/}).check();await guess.getByRole('button',{name:'Add as usual'}).click();await expect(notice).toBeVisible();
 const spacing=await notice.evaluate(node=>{const facts=node.querySelector('.facts').getBoundingClientRect(),heading=node.querySelector('section h3').getBoundingClientRect();return {gap:heading.top-facts.bottom,expected:parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--space-4'))};});
 assert.ok(spacing.gap>=spacing.expected,`Possible duplicates needs the section spacing token: ${JSON.stringify(spacing)}`);
 await expect(notice.locator(':scope > div > p').first()).toHaveText(/^The /);await expect(page.locator('.add-savebar .live-error')).toHaveCount(0);await snap('add-polish-refusal');
 await notice.getByRole('button',{name:'Discard',exact:true}).click();await expect(words).toHaveValue('');
 await words.fill('Lunch on 2026-11-02');const date=page.getByTitle('Date · You typed it',{exact:true});
 const expected=await page.evaluate(locale=>new Intl.DateTimeFormat(locale,{day:'numeric',month:'short',year:'numeric'}).format(new Date(2026,10,2,12)),{asha:'en-IN',empty:'en-US',jordan:'en-US',sam:'en-GB'}[persona]);
 await expect(date).toHaveText(expected);await date.click();await page.getByRole('dialog').getByLabel('Date',{exact:true}).fill('2026-11-03');await page.getByRole('button',{name:'Use this',exact:true}).click();await expect(date).not.toContainText('2026-11-03');await snap('add-polish-date');
 record('Add polish: duplicate section spacing, capital source, notice without duplicate save error, localized typed and edited date chips');
 await quiesce();await control('reset-workflows',{});await resetDrafts({persona,empty:true});
}
