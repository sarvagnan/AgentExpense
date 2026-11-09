import {assertOwnAlertsClear} from './action-alerts.mjs';
import {coveredAction} from './action-coverage.mjs';
import {directAction} from './direct-states.mjs';
import assert from 'node:assert/strict';
import {hold as holdResponse, settleTasks} from './held.mjs';
import {holdWrite} from './immediate-actions.mjs';

// The first-run screens (step 9-2) on a fresh template copy of each persona's ledger,
// in its own currency and locale. Both paths save through real locked, verified
// commits; the persona's files are restored afterwards.
const POLL_MS = 1500;
// Hold real responses, rather than depending on one engine being slower.
const hold = (page, pattern, method) => holdResponse(page, pattern, {method, limit:60000});

async function unchanged(locator, box) {
  const next = await locator.boundingBox(); assert.ok(next && box);
  for (const key of ['x', 'y', 'width', 'height']) assert.ok(Math.abs(next[key] - box[key]) < 1, `${key}: control moved during a background response (${box[key]} → ${next[key]}; ${JSON.stringify(await locator.page().evaluate(() => ({scrollY, innerHeight, height:document.documentElement.scrollHeight})))})`);
}

export async function runFirstRun({page, base, persona, width, expect, control, overflow, snap: capture = async () => {}, record, quiesce, consoleErrors=[]}) {
  await page.addInitScript(()=>{window.__setupInputEvents=[];document.addEventListener('input',event=>{if(event.target.matches('input,textarea'))window.__setupInputEvents.push({id:event.target.id,label:event.target.closest('label')?.textContent?.trim(),value:event.target.value,trusted:event.isTrusted});},true);});
  const fillChecked=async(field,value)=>{const before=await field.evaluate(node=>node.dataset.inputEvents=String(Number(node.dataset.inputEvents||0)));await field.evaluate(node=>{if(!node.dataset.counting){node.dataset.counting='yes';node.addEventListener('input',()=>node.dataset.inputEvents=String(Number(node.dataset.inputEvents||0)+1));}});await field.fill(value);const count=await field.evaluate(node=>Number(node.dataset.inputEvents));console.log('FILL_CHECK '+JSON.stringify({persona,engine:page.context().browser().browserType().name(),width,value,inputEventsBefore:Number(before),inputEventsAfter:count}));assert.ok(count>Number(before),'Each fill dispatches an input event before the value assertion');await expect(field).toHaveValue(value);};
  // Each capture starts at the top of the page; tall screens also get a full-page companion.
  const snap = async state => { await page.evaluate(() => { document.activeElement?.blur?.(); window.scrollTo(0, 0); }); await capture(state); };
  const get = async path => { const r = await fetch(base + 'api/' + path); assert.ok(r.ok, await r.clone().text()); return r.json(); };
  const file = async (kind, currency, name) => {
    const r = await fetch(`${base}__test/first-run-file?kind=${kind}&currency=${currency}`, {method:'POST'}); assert.ok(r.ok, await r.clone().text());
    return {name, mimeType:kind === 'agent' ? 'text/csv' : 'application/pdf', buffer:Buffer.from(await r.arrayBuffer())};
  };
  const heading = name => page.locator('.setup-content h1').filter({hasText:name});
  const open = async (path = 'add/') => { await page.goto(base + path); await expect(page.locator('.setup-content h1')).toBeVisible({timeout:30000}); };
  const names = {asha:'Asha Rao', jordan:'Jordan Lee', sam:'Sam Okafor', empty:'Jordan Lee'};
  const errors = page.locator('.setup-content [role=alert]');
  await quiesce();
  try {
    // ---- Start from today ----
    const fresh = await control('first-run', {});
    assert.equal(fresh.data_stale, false, fresh.refresh_error);
    const manifest = (await get('setup'));
    assert.deepEqual(manifest, {fresh:true, accounts:[]});
    await open();
    const currency = await page.locator('#setup-currency').inputValue(), foreign = currency === 'EUR' ? 'GBP' : 'EUR';
    await expect(heading('A little about you')).toBeVisible();
    const name = page.locator('#setup-name'), next = page.getByRole('button', {name:'Continue', exact:true});
    await expect(name).toHaveValue('New ledger');
    await fillChecked(name, '');await expect(next).toBeDisabled();
    await page.evaluate(() => { document.activeElement?.blur?.(); window.scrollTo(0, 0); });
    await overflow('first-about');await snap('first-about');
    await fillChecked(name, '   ');await expect(next).toBeDisabled();
    await fillChecked(name, names[persona]);await expect(next).toBeEnabled();
    await control('data-refresh',{mode:'hold'});
    const about = page.waitForResponse(r => r.url().endsWith('/api/setup/about') && r.request().method() === 'POST');
    await name.press('Enter');
    assert.equal((await about).status(), 200);
    await expect(heading('How would you like to start?')).toBeFocused();
    assert.equal((await get('setup')).fresh, true);
    await page.evaluate(() => window.scrollTo(0, 0));await overflow('first-start');await snap('first-start');
    record('first run: About you needs a name, saves name and currency in one verified commit');
    // Back returns to About with the saved name; Continue without changes saves nothing.
    await page.getByRole('button', {name:'Back', exact:true}).click();
    await expect(name).toHaveValue(names[persona]);
    let aboutWrites = 0; const countAbout = request => { if (request.url().endsWith('/api/setup/about')) aboutWrites++; };
    page.on('request', countAbout);
    await next.click();await expect(heading('How would you like to start?')).toBeVisible();
    page.off('request', countAbout);assert.equal(aboutWrites, 0);
    assert.equal((await control('data-refresh',{mode:'status'})).held,true);
    await control('data-refresh',{mode:'release'});await control('data-refresh',{mode:'wait'});
    await page.getByRole('button', {name:/^Start from today/}).click();
    await expect(heading('Your accounts, as of today')).toBeFocused();
    const rows = page.locator('.setup-account');
    const fill = async (index, kind, label, balance) => {
      const row = rows.nth(index);
      await row.getByLabel('Kind').selectOption(kind);await fillChecked(row.getByLabel('Account name'), label);
      await fillChecked(row.locator('.account-balance'), balance);
    };
    const finishSetup = page.getByRole('button', {name:'Finish setup', exact:true});
    await expect(rows).toHaveCount(1);await expect(finishSetup).toBeDisabled();
    await expect(page.locator('#setup-finish-hint')).toHaveText('Enter a name and balance for every account.');
    await fill(0, 'bank', 'Alder Checking', '1840.25');
    await page.getByRole('button', {name:'Add account', exact:true}).click();
    await expect(rows.nth(1).getByLabel('Account name')).toBeFocused();
    await fill(1, 'bank', 'Meadow Savings', '4650.00');
    // Force publication and a row insertion/removal between input and blur.
    await control('data-refresh',{mode:'hold'});await control('data-refresh',{mode:'retry'});
    const meadow=rows.nth(1).getByLabel('Account name');await fillChecked(meadow,'Meadow Savings under publication');
    await page.getByRole('button',{name:'Add account',exact:true}).click();await rows.last().locator('.account-remove').click();
    await control('data-refresh',{mode:'release'});await control('data-refresh',{mode:'wait'});await meadow.blur();await expect(meadow).toHaveValue('Meadow Savings under publication');await fillChecked(meadow,'Meadow Savings');
    record('opening input: every fill has an immediate value assertion and input event; held publication plus row insertion/removal preserves the name through blur');
    await page.getByRole('button', {name:'Add account', exact:true}).click();
    await fill(2, 'card', 'Lumen Card', '337.50');
    await expect(rows.nth(2).locator('.field-label').nth(2)).toHaveText('Amount owed');
    await expect(rows.nth(2).locator('.kind-hint')).toHaveText('Include charges not yet billed, not just your last statement balance.');
    await expect(rows.nth(0).locator('.kind-hint')).toHaveCount(0);
    const locale = {asha:'en-IN', jordan:'en-US', sam:'en-GB', empty:'en-US'}[persona];
    const money = (value, code) => new Intl.NumberFormat(locale, {style:'currency', currency:code, minimumFractionDigits:2, maximumFractionDigits:2}).format(value);
    await expect(rows.nth(0).locator('.opening-unit span')).toContainText('1,840.25');
    // Grade A-2: Remove sits in the fields row (no line of its own), and a whole balance gains its decimals on leaving the field.
    const remove=await rows.nth(1).evaluate(row=>{const button=row.querySelector('.account-remove').getBoundingClientRect(),fields=row.querySelector('.account-fields').getBoundingClientRect(),line=row.querySelector('.opening-line').getBoundingClientRect();return {inside:button.top>=fields.top-1&&button.bottom<=fields.bottom+1,lineHeight:line.height};});
    assert.ok(remove.inside,'Remove sits within the account fields');assert.ok(remove.lineHeight<=24,`The opening line holds only its text (${remove.lineHeight})`);
    await fillChecked(rows.nth(1).locator('.account-balance'), '4650');await rows.nth(1).locator('.account-balance').blur();
    await expect(rows.nth(1).locator('.account-balance')).toHaveValue(currency==='JPY'?'4650':'4650.00');
    for (let i = 0; i < 3; i++) await expect(rows.nth(i).locator('.account-currency')).toHaveValue(currency);
    await expect(finishSetup).toBeEnabled();
    await page.evaluate(() => { document.activeElement?.blur?.(); window.scrollTo(0, 0); });
    await overflow('first-accounts');await snap('first-accounts');
    // Invalid amounts, duplicate names and an unnamed row block Finish with a reason.
    await fillChecked(rows.nth(0).locator('.account-balance'), '12.345');
    await expect(rows.nth(0).locator('.setup-error')).toContainText('decimal places');await expect(finishSetup).toBeDisabled();
    await fillChecked(rows.nth(0).locator('.account-balance'), '1840.25');
    await fillChecked(rows.nth(1).getByLabel('Account name'), 'alder checking');
    await expect(rows.nth(1).locator('.setup-error')).toContainText('Another account has this name');await expect(finishSetup).toBeDisabled();
    await fillChecked(rows.nth(1).getByLabel('Account name'), 'Meadow Savings');await expect(finishSetup).toBeEnabled();
    // More kinds: a Person row explains what its balance means, then is removed.
    await page.getByRole('button', {name:'Add account', exact:true}).click();
    await fill(3, 'person', 'Casey Morgan', '75');
    await expect(rows.nth(3).locator('.kind-hint')).toContainText('Money owed between you and someone.');
    await expect(rows.nth(3).getByLabel('Kind').locator('optgroup[label="More"] option')).toHaveText(['Investment', 'Person']);
    await expect(page.locator('.setup-more-kinds')).toHaveText('More kinds: Investment · Person — money owed between you and someone.');
    await page.evaluate(() => { document.activeElement?.blur?.(); window.scrollTo(0, 0); });
    await overflow('first-accounts-more');await snap('first-accounts-more');
    await rows.nth(3).getByRole('button', {name:'Remove Casey Morgan'}).click();await expect(rows).toHaveCount(3);
    // A second currency on one account; the others keep the main currency.
    await rows.nth(1).locator('.account-currency').selectOption(foreign);
    await expect(rows.nth(1).locator('.opening-unit span')).toHaveText(await money(4650, foreign));
    await expect(rows.nth(0).locator('.account-currency')).toHaveValue(currency);
    await overflow('first-accounts-mixed');await snap('first-accounts-mixed');
    record('first run: opening-balance rows, card amount owed, More kinds, second currency, validation');
    // Timing: Finish held in flight. A disabled second click sends nothing; Back
    // and fields remain available. An unchanged completion lands on Add.
    let openings = 0; const countOpenings = request => { if (request.url().endsWith('/api/setup/opening')) openings++; };
    page.on('request', countOpenings);
    await control('data-refresh',{mode:'hold'});
    const held = await hold(page, base + 'api/setup/opening', 'POST');
    try {
      await finishSetup.click();await held.ready;
      const saving = page.getByRole('button', {name:'Saving…', exact:true});
      await expect(saving).toBeDisabled();
      await saving.click({force:true});
      await expect(page.getByRole('button', {name:'Back', exact:true})).toBeEnabled();
      await expect(rows.nth(0).locator('.account-balance')).toBeEnabled();
      await expect(errors).toHaveCount(0);
    } finally { await held.release(); }
    await expect(page.locator('.content h1')).toHaveText('Add', {timeout:60000});
    page.off('request', countOpenings);assert.equal(openings, 1, 'Finish sends one request');
    await expect(page).toHaveURL(/\/add\/$/);
    const notice = page.locator('.first-run-done');
    await expect(notice).toContainText('You’re ready to start.');await expect(notice).toContainText('Your opening balances are in Accounts.');
    await expect(page.locator('.page-heading p')).toHaveText('Your ledger is ready. Add your first expense.');
    await expect(page.locator('.content h1')).toBeFocused();
    await page.evaluate(() => window.scrollTo(0, 0));await overflow('first-done-today');await snap('first-done-today');
    const setup = await get('setup');
    assert.equal(setup.fresh, false);
    assert.deepEqual(setup.accounts.sort(), ['Assets:Bank:Alder Checking', 'Assets:Bank:Meadow Savings', 'Liabilities:Credit Cards:Lumen Card']);
    // The confirmation belongs to this first visit; Accounts holds the balances.
    await notice.getByRole('link', {name:'Accounts'}).click();
    await expect(page.locator('.content h1').first()).toBeVisible();
    await expect(page.locator('.setup-content')).toHaveCount(0);
    assert.equal((await control('data-refresh',{mode:'status'})).held,true);
    await control('data-refresh',{mode:'release'});await control('data-refresh',{mode:'wait'});
    await page.goBack();await expect(page.locator('.content h1')).toHaveText('Add');await expect(page.locator('.first-run-done')).toHaveCount(0);
    record('first run: Start from today saves accounts and opening balances once, even with Finish held in flight');

    console.log('FIRST_RUN_INPUT_EVENTS '+JSON.stringify({persona,width,events:await page.evaluate(()=>window.__setupInputEvents)}));
    // ---- Bring in past statements ----
    await quiesce();
    await control('first-run', {agent_currency:foreign});
    // Timer: a reading file is polled every POLL_MS, checked with a controlled clock.
    {
      // page.clock belongs to the whole browser context, so the clock gets its own context.
      const clockContext = await page.context().browser().newContext({viewport:page.viewportSize(), locale:{asha:'en-IN', jordan:'en-US', sam:'en-GB', empty:'en-US'}[persona]});
      const clockPage = await clockContext.newPage();
      await clockPage.route('**/*', route => { const url = route.request().url(); return url.startsWith(base) || url.startsWith('blob:') || url.startsWith('data:') ? route.continue() : route.abort('blockedbyclient'); });
      await clockPage.clock.install();
      await clockPage.goto(base + 'add/?setup=import');
      await expect(clockPage.locator('.setup-content h1')).toHaveText('Let’s read your statements', {timeout:30000});
      // Pause the controlled clock: timers now run only when the test advances it.
      // A few seconds ahead: under load the running clock must not pass the pause point first.
      await clockPage.clock.pauseAt(await clockPage.evaluate(() => Date.now() + 5000));
      const uploaded = clockPage.waitForResponse(r => r.url().endsWith('/api/statements') && r.request().method() === 'POST');
      await clockPage.locator('input[type=file]').setInputFiles([await file('card', currency, 'clock-check.pdf')]);
      const session = await (await uploaded).json();
      await expect(clockPage.locator('.setup-file[data-file="clock-check.pdf"]')).toHaveAttribute('data-state', 'reading');
      // The poll timer starts in the effect after this render; let it start before advancing.
      await settleTasks(clockPage);
      let polls = 0; clockPage.on('request', request => { if (request.method() === 'GET' && request.url().endsWith('/api/statements/' + session.id)) polls++; });
      await clockPage.clock.runFor(POLL_MS - 1);await clockPage.waitForTimeout(50);
      assert.equal(polls, 0, 'No poll before the interval');
      const polled = clockPage.waitForRequest(request => request.url().endsWith('/api/statements/' + session.id));
      await clockPage.clock.runFor(1);await polled;
      assert.equal(polls, 1, 'One poll at the interval');
      await clockContext.close();
    }
    record('first run timing: reading files are polled once per interval (controlled clock)');
    await quiesce();
    await control('first-run', {agent_currency:foreign});
    await open();
    await expect(page.locator('#setup-name')).toHaveValue('New ledger');
    await page.getByRole('button', {name:'Continue', exact:true}).click();
    await page.getByRole('button', {name:/^Bring in past statements/}).click();
    await expect(heading('Let’s read your statements')).toBeFocused();
    const finishImport = page.getByRole('button', {name:/^Finish with/});
    await expect(finishImport).toBeDisabled();
    const row = name => page.locator(`.setup-file[data-file="${name}"]`);
    const cardName = 'lumen-october.pdf', agentName = 'willow-export.csv', badName = 'meadow-october.pdf';
    // Timing: the first poll of three reading files is held while one file is removed;
    // the late response for it must not bring it back or show an error.
    let badId = null;
    const learn = async response => { if (response.request().method() === 'POST' && response.url().endsWith('/api/statements') && response.ok()) { const value = await response.json().catch(() => ({})); if (value.original_name === badName) badId = value.id; } };
    page.on('response', learn);
    const poll = await hold(page, url => !!badId && url.pathname.endsWith('/api/statements/' + badId), 'GET');
    try {
      await page.locator('input[type=file]').setInputFiles([await file('card', currency, cardName), await file('agent', foreign, agentName), await file('bad', currency, badName)]);
      await poll.ready;page.off('response', learn);
      await row(badName).getByRole('button', {name:'Remove ' + badName}).click();
      await expect(row(badName)).toHaveCount(0);
    } finally { await poll.release(); }
    await expect(row(cardName)).toHaveAttribute('data-state', 'currency_mismatch', {timeout:60000});
    await expect(row(agentName)).toHaveAttribute('data-state', 'account_needed', {timeout:60000});
    await page.waitForTimeout(POLL_MS + 200);
    await expect(row(badName)).toHaveCount(0);await expect(errors).toHaveCount(0);
    record('first run timing: a held poll answer never brings back a removed file');
    await page.locator('input[type=file]').setInputFiles([await file('bad', currency, badName)]);
    await expect(row(badName)).toHaveAttribute('data-state', 'currency_mismatch', {timeout:60000});
    await expect(row(cardName).locator('.file-detail')).toHaveText(`Printed in ${currency}.`);
    await expect(page.locator('#import-status')).toHaveText('Choose an account for every file, or remove it. Nothing has been saved.');
    // Timing: an account created for the last file is held while the first file's form
    // is filled; the typed name is kept, nothing above moves, and the new account is used.
    await row(cardName).getByRole('button', {name:`Add an account in ${currency}`}).click();
    const cardForm = row(cardName).getByRole('group', {name:'New account'});
    await row(badName).getByRole('button', {name:`Add an account in ${currency}`}).click();
    const badForm = row(badName).getByRole('group', {name:'New account'});
    await fillChecked(badForm.getByLabel('Account name'), 'Meadow Savings');
    await expect(badForm.getByLabel('Currency')).toBeDisabled();await expect(badForm.getByLabel('Currency')).toHaveValue(currency);
    const creating = await hold(page, base + 'api/setup/accounts', 'POST');
    let box;
    try {
      await badForm.getByRole('button', {name:'Add account', exact:true}).click();await creating.ready;
      await expect(badForm.getByRole('button', {name:'Saving…', exact:true})).toBeDisabled();
      await cardForm.getByLabel('Kind').selectOption('card');await fillChecked(cardForm.getByLabel('Account name'), 'Lumen Card');
      // Measure where a person pointing at the field sees it: fully in view, whole-pixel scroll.
      await cardForm.getByLabel('Account name').evaluate(element => { element.scrollIntoView({block:'center'}); window.scrollTo(0, Math.round(window.scrollY)); });
      await cardForm.getByLabel('Account name').hover();
      box = await cardForm.getByLabel('Account name').boundingBox();
    } finally { await creating.release(); }
    await expect(row(badName)).toHaveAttribute('data-state', /reading|couldnt_read/, {timeout:60000});
    await expect(cardForm.getByLabel('Account name')).toHaveValue('Lumen Card');
    await expect(cardForm.getByLabel('Account name')).toBeEnabled();await unchanged(cardForm.getByLabel('Account name'), box);
    await expect(row(badName)).toHaveAttribute('data-state', 'couldnt_read', {timeout:60000});
    await page.getByRole('button',{name:'Choose files',exact:true}).hover();await page.getByRole('button',{name:'Choose files',exact:true}).focus();
    await expect(row(badName).locator('.setup-tag')).toHaveText('Couldn’t be read');
    await expect(row(badName).locator('.file-detail')).toContainText("printed totals don't add up");
    // One plain sentence; the checker's figures are behind Details.
    await expect(row(badName).locator('.file-detail')).toHaveText("The printed totals don't add up, so nothing was saved.");
    await expect(row(badName).locator('.file-more summary')).toHaveText('Details');
    record('first run timing: a held account creation keeps another file’s typed account and position');
    // Give the file another valid card account. Reload also checks that its
    // kept account form survives before the following held creation begins.
    const alternateResponse=await page.request.post(base+'api/setup/accounts',{data:{kind:'card',label:'Cedar alternate',currency}});assert.ok(alternateResponse.ok());const alternate=await alternateResponse.json();
    await page.reload({waitUntil:'networkidle'});await expect(cardForm.getByLabel('Account name')).toHaveValue('Lumen Card');
    // Account creation uses the same held refusal/Retry pattern. Its original
    // request stays frozen while the person types the next account's fields.
    const cardNameField=cardForm.getByLabel('Account name'),cardSave=cardForm.locator('.setup-new-account-actions .primary');
    const paints=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const accountFailure=await holdWrite(page,base+'api/setup/accounts',{fail:true});
    try{
      await cardSave.click();await accountFailure.ready;await expect(cardForm.locator('.action-feedback [data-action-state="saving"]')).toBeVisible();await expect(cardForm.locator('.action-feedback')).toContainText('Saving…');
      await fillChecked(cardNameField, 'Willow next account');await cardNameField.focus();
      await paints();const controls=[cardNameField,cardSave,cardForm.locator('h3')],before=await Promise.all(controls.map(node=>node.boundingBox()));
      await capture('immediate-setup-account-pending');await accountFailure.release();
      await expect(cardForm.locator('.action-feedback')).toContainText('Your words are kept');await paints();await expect(cardNameField).toHaveValue('Willow next account');await expect(cardNameField).toBeFocused();
      const after=await Promise.all(controls.map(node=>node.boundingBox()));before.forEach((box,index)=>{for(const key of ['x','y'])assert.ok(Math.abs(box[key]-after[index][key])<=2,'Account creation feedback moved '+key);});
      await capture('immediate-setup-account-failed');
    }finally{await accountFailure.release();}
    await fillChecked(cardNameField, 'Lumen Card');
    const accountSuccess=await holdWrite(page,base+'api/setup/accounts');
    try{
      await cardForm.locator('.action-feedback').getByRole('button',{name:'Retry',exact:true}).click();const accepted=await accountSuccess.ready;await expect(cardForm.locator('.action-feedback [data-action-state="saving"]')).toBeVisible();
      await row(cardName).locator('select.file-account').selectOption(alternate.account);
      await fillChecked(cardNameField, 'Willow account typed during Retry');await cardNameField.focus();const controls=[cardNameField,cardSave,cardForm.locator('h3')],before=await Promise.all(controls.map(node=>node.boundingBox()));await paints();
      const exact=page.waitForResponse(response=>response.request()===accepted);exact.catch(()=>{});await accountSuccess.release();const committed=await exact;assert.ok(committed.ok());const outcome=await committed.json();await expect(cardForm.locator('.action-feedback')).toContainText('Saved.',{timeout:60000});await expect(cardForm.locator('.action-feedback [data-action-state="saved"]')).toBeVisible();
      await expect(row(cardName)).toHaveAttribute('data-state', 'found', {timeout:60000});await expect(cardSave).toHaveText('Add account');
      await expect(cardNameField).toHaveValue('Willow account typed during Retry');await expect(cardNameField).toBeFocused();await paints();const after=await Promise.all(controls.map(node=>node.boundingBox()));before.forEach((box,index)=>{for(const key of ['x','y'])assert.ok(Math.abs(box[key]-after[index][key])<=2,'Account success moved control '+index+' at '+key);});assert.match(outcome.commit_id,/^[a-f0-9]{40}$/);assert.equal(outcome.label,accepted.postDataJSON().label);assert.equal(outcome.currency,accepted.postDataJSON().currency);await assertOwnAlertsClear(page,expect,await cardNameField.evaluateHandle(node=>node.closest('.setup-file')));coveredAction(page,'setup-account',{failed:true,saved:true,visibleSaved:'Owned account Saved.',outcomeChecked:true,ownAlertsCleared:true,usable:'Account name',before,after,outcome});console.log('ACTION_COVERAGE '+JSON.stringify({action:'setup-account',failed:true,saved:true,visibleSaved:'Owned account Saved.',outcomeChecked:true,ownAlertsCleared:true,usable:'Account name',before,after,outcome}));
      const selected=await (await page.request.get(base+'api/statements/'+await row(cardName).getAttribute('data-session'))).json();assert.equal(selected.statement.account,alternate.account,'The late declaration must keep the newer file-account choice');
      await capture('immediate-setup-account-saved-newer-fields');
    }finally{await accountSuccess.release();}
    record('first run account: held failure and Retry keep newer fields/file-account choice/focus and ≤2px controls');
    await page.getByRole('button',{name:'Choose files',exact:true}).hover();await page.getByRole('button',{name:'Choose files',exact:true}).focus();
    await expect(row(cardName).getByRole('option',{name:`Lumen Card (${currency})`,exact:true})).toHaveCount(1);
    const cardSession=await row(cardName).getAttribute('data-session');
    const reread=page.waitForResponse(async response=>response.request().method()==='GET'&&response.url()===base+'api/statements/'+cardSession&&await response.json().then(value=>value.state==='found'&&value.statement?.account==='Liabilities:Credit Cards:Lumen Card'),{timeout:60000});reread.catch(()=>{});
    await row(cardName).locator('select.file-account').selectOption('Liabilities:Credit Cards:Lumen Card');await page.getByRole('button',{name:'Choose files',exact:true}).hover();await page.getByRole('button',{name:'Choose files',exact:true}).focus();await reread;await expect(row(cardName)).toHaveAttribute('data-state','found',{timeout:60000});
    // The agent file's currency is unknown until it is read: add an account and choose its currency.
    await expect(row(agentName).locator('.field-label').first()).toHaveText('Account');
    await row(agentName).getByRole('button', {name:'Add an account', exact:true}).click();
    const agentForm = row(agentName).getByRole('group', {name:'New account'});
    await expect(row(cardName).locator('.setup-tag')).toHaveText('Read by a built-in reader');await expect(row(cardName).locator('.file-detail')).toHaveText('3 entries found · totals checked');await expect(row(cardName).locator('select.file-account')).toHaveValue('Liabilities:Credit Cards:Lumen Card');
    await fillChecked(agentForm.getByLabel('Account name'), 'Willow Travel');await agentForm.getByLabel('Currency').selectOption(foreign);
    await agentForm.getByRole('button', {name:'Add account', exact:true}).click();
    await expect(row(agentName)).toHaveAttribute('data-state', 'found', {timeout:90000});
    // File headers and account options wait while the pointer/focus uses their
    // controls. Leave them explicitly before checking the published reading.
    await expect(cardNameField).toHaveValue('Willow account typed during Retry');
    await page.getByRole('button',{name:'Choose files',exact:true}).hover();await page.getByRole('button',{name:'Choose files',exact:true}).focus();
    await expect(row(agentName).locator('.setup-tag')).toHaveText('Read by the agent');
    await expect(row(agentName).locator('.file-detail')).toHaveText('2 entries found · source values and totals checked');
    await expect(row(agentName).locator('.field-label').first()).toHaveText(`Account · file in ${foreign}`);
    const summary = page.locator('.import-summary');
    await expect(summary.locator('strong')).toHaveText('5 entries found, 5 need your review.');
    await expect(finishImport).toBeDisabled();await expect(page.locator('#import-status')).toHaveText('Save or cancel the open account form before finishing the statements.');await cardForm.getByRole('button',{name:'Cancel',exact:true}).click();
    await expect(page.locator('#import-status')).toHaveText('Currencies match. The unread file won’t be imported; you can try it again later.');
    await expect(finishImport).toHaveText('Finish with 2 read files');await expect(finishImport).toBeEnabled();
    await page.evaluate(() => { document.activeElement?.blur?.(); window.scrollTo(0, 0); });
    await overflow('first-import');await snap('first-import');
    // A file moved to an account in another currency blocks Finish, offers an account in
    // the file's currency, and is resolved by choosing a matching account again.
    await row(agentName).locator('select.file-account').selectOption('Liabilities:Credit Cards:Lumen Card');
    await expect(row(agentName).locator('.currency-warning')).toHaveText(`This file is in ${foreign}, but Lumen Card is in ${currency}. Choose ${/^[AEIOU]/.test(foreign) ? 'an' : 'a'} ${foreign} account or remove the file. Nothing has been saved.`);
    await expect(finishImport).toBeDisabled();await expect(finishImport).toHaveText('Finish with 2 read files');
    await expect(row(agentName).locator('.setup-tag')).toHaveText('Read by the agent');
    await expect(page.locator('#import-status')).toHaveText('Resolve the currency mismatch before finishing. Nothing has been saved.');
    await expect(row(agentName).getByRole('button', {name:`Add an account in ${foreign}`})).toBeVisible();
    await page.evaluate(() => { document.activeElement?.blur?.(); window.scrollTo(0, 0); });
    await overflow('first-import-mismatch');await snap('first-import-mismatch');
    await row(agentName).locator('select.file-account').selectOption('Assets:Bank:Willow Travel');
    await expect(row(agentName).locator('.currency-warning')).toHaveCount(0);
    await expect(row(agentName)).toHaveAttribute('data-state', 'found', {timeout:60000});
    await expect(finishImport).toBeEnabled();
    await page.evaluate(() => { document.activeElement?.blur?.(); window.scrollTo(0, 0); });
    await overflow('first-import-resolved');await snap('first-import-resolved');
    record('first run: built-in and agent readings, unread file, currency mismatch blocks Finish and resolves');

    // A check finishing after Import mounted must update its reason without a
    // failed Finish request. A later pass clears the refusal in the same screen.
    const checkedAccount=(await get('statements')).sessions.find(s=>s.statement&&['found','review'].includes(s.state)).statement.account;
    try {
      await control('ledger-check',{mode:'fail',kind:'assertion',account:checkedAccount});
      await expect(page.locator('#import-status')).toContainText('The second ledger check (hledger) found a problem',{timeout:15000});
      await expect(finishImport).toBeDisabled();
      await control('ledger-check',{mode:'pass'});
      await expect(finishImport).toBeEnabled({timeout:15000});
      await expect(page.locator('#import-status')).not.toContainText('The second ledger check');
      record('first run: late ledger check failure and pass update Finish and its reason without clicking it');
    } finally { await control('ledger-check',{mode:'real',check:false}); }
    // Timing: Finish held in flight; the late answer still saves both files once.
    let imports = 0; const countImports = request => { if (request.url().endsWith('/api/setup/import')) imports++; };
    page.on('request', countImports);
    await directAction({page,base,expect,snap:capture,record})('setup-import',finishImport,'**/api/setup/import',{container:page.locator('.setup-content'),other:page.getByRole('button',{name:'Back',exact:true})});
    await expect(page.locator('.content h1')).toHaveText('Add', {timeout:120000});
    page.off('request', countImports);assert.equal(imports, 2,'One held failure and one committed Retry');
    const importNotice = page.locator('.first-run-done');
    await expect(importNotice).toContainText('Statements imported.');await expect(importNotice).toContainText('5 entries found. 5 are waiting in Review.');
    // The unread file is the Queue badge's one item, and the notice says so.
    await expect(importNotice).toContainText('1 file couldn’t be read; it is in Queue to try again.');
    await expect(page.locator(width > 760 ? '.sidebar nav a[href$="queue/"] .nav-count' : '.phone-live-nav a[href$="queue/"] .nav-count')).toContainText('1');
    // A confirmation: its body is no larger than its title, inside a plain panel border.
    const scale = await importNotice.evaluate(node => ({title:parseFloat(getComputedStyle(node.querySelector('strong')).fontSize), body:parseFloat(getComputedStyle(node.querySelector('p')).fontSize), border:getComputedStyle(node).borderTopColor, separator:getComputedStyle(document.querySelector('.panel')||node).borderTopColor}));
    assert.ok(scale.body <= scale.title, `Notice body ${scale.body}px is larger than its title ${scale.title}px`);
    assert.equal(scale.border, scale.separator, 'The confirmation uses the plain panel border, not the warning outline');
    const review = await get('review');
    assert.equal(review.count, 5);
    const nav = page.locator(width > 760 ? '.sidebar nav' : '.phone-live-nav');
    await expect(nav.locator('a[href$="review/"] .nav-count')).toContainText('5');
    await page.evaluate(() => window.scrollTo(0, 0));await overflow('first-done-import');await snap('first-done-import');
    assert.equal((await get('setup')).fresh, false);
    record('first run: Bring in past statements imports read files in order, with pending rows in Review');
  } finally {
    await control('data-refresh',{mode:'release'});await control('data-refresh',{mode:'wait'});
    await quiesce();
    const restored = await control('first-run', {restore:true});
    assert.equal(restored.data_stale, false, restored.refresh_error);
  }
}
