import assert from 'node:assert/strict';

const draftPath = '**/api/drafts/*';
const statementPath = '**/api/statements/*';
const limitsPath = '**/api/retention/operations/*';
const rulesPath = '**/api/rules/operations/*';
const words = '#expense-words';
const addCore = {input: words, action: '.add-savebar > button'};
const setupCore = {input: '.setup-content .account-balance', action: '.setup-footer .primary'};
const settingsCore = {input: '#settings-name', limit: '.retention-settings select', action: '.settings-form .dialog-footer .primary'};
const handCore = {input: '[data-testid=entry-description]', action: '.hand-finish .detail-footer .primary'};
const result = '.just-added-row';
const records = body => {
  const data = JSON.parse(body);
  return Array.isArray(data) ? data : data.sessions ?? [data];
};
const draftPublication = predicate => ({pattern: /\/api\/drafts(?:\/(?:[a-f0-9]{32}))?(?:\?|$)/,
  method: 'GET', accept: body => records(body).some(predicate), repeat: true});
const statementPublication = state => ({pattern: /\/api\/statements(?:\/(?:[a-f0-9]{32}))?(?:\?|$)/,
  method: 'GET', accept: body => records(body).some(row => row.state === state), repeat: true});
const addPanels = [{name: 'composer and interpretation', left: '.composer-panel', right: '.understood-panel', equalHeight: true}];
const addScreen = {core: addCore, actionGroup: 'Add composer', pairs: addPanels, sections: ['.understood-panel', '.just-added'], blocks: ['.composer-tools .text-button']};
const response = (pattern, method = 'POST', accept) => ({pattern, method, accept});
const followup = (name, selector, marker) => ({name, selector, marker});

async function firstAccount(t, fromAccounts) {
  const {page, expect, step, arrival, button, type} = t;
  const row = page.locator('.setup-account').first();
  if (await row.getByLabel('Account name').isEditable()) {
    await step('Type account name', () => type(row.getByLabel('Account name'), 'Willow Current'), {typing: true, core: {...setupCore, input: '.setup-account .account-name'}});
  }
  const count = await page.locator('.setup-account').count();
  for (let i = 0; i < count; i++) {
    await step(`Type opening balance ${i + 1}`, () => type(page.locator('.account-balance').nth(i), i ? '18.00' : '84.25'), {typing: true, core: {...setupCore, input: `#setup-accounts .setup-account:nth-child(${i + 1}) .account-balance`}});
  }
  const ready = page.locator('.first-run-done');
  await arrival('Finish setup', response('**/api/setup/opening'), () => button('Finish setup').click(),
    () => expect(ready).toContainText('Your opening balances are in Accounts.'), {
      core: setupCore, actionGroup: 'Opening-balance form',
      done: {...addScreen, followups: [followup('Accounts link', '.first-run-done a[href*="account-summary"]')]},
    });
  await step('See opening balance in Accounts', async () => {
    await ready.getByRole('link', {name: 'Accounts'}).click();
    await expect(page.getByRole('heading', {name: 'Accounts', exact: true})).toBeVisible();
  }, {watch: ['.first-run-done', '.account-list', '.opening-invitation']});
  if (fromAccounts) await expect(page.locator('.accounts')).toContainText('Only opening balances so far.');
}

async function openEntryEditor(t, {edit, open}) {
  const title = edit ? 'Edit entry' : 'Finish it yourself';
  const form = t.page.getByRole('article', {name: title, exact: true});
  const claimed = response(draftPath + '/claim');
  const editor = {core: handCore, watch: ['.hand-finish', '.draft-detail']};
  const ready = () => t.expect(form.getByLabel('Description', {exact: true})).toBeEditable();
  if (!edit) {
    await t.arrival(title, claimed, open, ready, editor);
    return form;
  }
  const prepared = await t.prepare(claimed);
  try {
    await t.arrival(title, response('**/api/entries/*/correction-form'), open,
      () => t.expect(form).toBeVisible(), editor);
    await t.arrival('Entry form ready', claimed, null, ready,
      {...editor, prepared, timingFrom: title});
  } finally {
    await prepared?.release();
  }
  return form;
}

async function checkedEntry(t, {edit, open}) {
  const {page, expect, step, arrival, type} = t;
  const form = await openEntryEditor(t, {edit, open});
  const desc = form.getByLabel('Description', {exact: true});
  const formCore = handCore;
  await step('Type description', () => type(desc, 'Willow market corrected'), {core: formCore, typing: true});
  const checks = form.getByRole('heading', {name: 'Checks passed', exact: true});
  await arrival('Check entry', response(draftPath + '/hand-form'),
    () => form.getByRole('button', {name: 'Check entry', exact: true}).click(),
    () => expect(checks).toBeVisible(), {core: formCore, actionGroup: 'Entry editor', watch: ['[aria-label="Entry checks"]']});
  const endpoint = edit ? '**/api/entries/*/corrections/*' : draftPath + '/hand-add';
  const confirm = edit ? 'Save' : 'Save and add';
  const done = edit ? () => expect(page.getByText('Correction saved', {exact: true})).toBeVisible() : async () => {};
  const publication = draftPublication(row => row.state === 'added' && row.finished_by?.actor === 'user');
  const prepared = edit ? null : await t.prepare(publication);
  try {
    await arrival(confirm, response(endpoint), () => form.getByRole('button', {name: confirm, exact: true}).click(),
      done, {core: formCore, watch: ['.hand-finish', '.draft-detail', '.action-feedback']});
    if (!edit) await arrival('Hand addition saved', publication, null, async () => {
      await expect.poll(async () => (await t.get(`drafts/${t.handDraft}`)).state).toBe('added');
      await expect(form.locator('[data-action-state=saved]')).toBeVisible();
    }, {core: formCore, prepared, timingFrom: confirm, watch: ['.hand-finish', '.draft-detail']});
  } finally {
    await prepared?.release();
  }
  if (!edit) await step('Back to Queue', () => form.getByRole('button', {name: 'Back to Queue', exact: true}).click(),
    {watch: ['.draft-workspace', '.viewing-empty']});
}

async function upload(t, file) {
  const {page, expect, control, step, checkpoint, delay, timed} = t;
  if (!timed) await control('hold-uploads', {action: 'hold', count: 1});
  try {
    await step('Upload file', () => page.getByLabel('Upload files', {exact: true}).setInputFiles(file), {
      ...addScreen, watch: ['.pending-upload', '.file-interpretation'], deferTiming: true,
    });
    if (!timed) {
      await expect.poll(async () => (await control('hold-uploads', {action: 'status'})).held).toBe(1);
      await checkpoint('Upload working', addScreen);
      await delay();
    }
  } finally {
    if (!timed) await control('hold-uploads', {action: 'release'});
  }
  await expect(page.locator('.pending-upload')).toHaveCount(0);
  await expect(page.locator('.file-interpretation').filter({hasText: file.name})).toBeVisible();
  await checkpoint('File understood', {...addScreen, timing: timed});
}

export const flowCases = {
  'first-run': async t => {
    const {page, expect, control, go, step, arrival, button, type} = t;
    await control('first-run', {});
    await go('add/', 'A little about you', {core: {...setupCore, input: '#setup-name'}});
    await step('Type ledger name', () => type(page.locator('#setup-name'), 'Willow Ledger'),
      {typing: true, core: {...setupCore, input: '#setup-name'}});
    await arrival('Continue', response('**/api/setup/about'), () => button('Continue').click(),
      () => expect(page.getByRole('heading', {name: 'How would you like to start?'})).toBeVisible(),
      {core: {...setupCore, input: '#setup-name'}, done: {core: {action: '.setup-content .setup-choice:first-of-type'}}});
    await step('Start from today', async () => {
      await button(/^Start from today/).click();
      await expect(page.getByRole('heading', {name: 'Your accounts, as of today'})).toBeVisible();
    }, {core: setupCore, watch: ['.setup-content h1']});
    await firstAccount(t, false);
  },
  'time-limits': async t => {
    const {page, expect, control, go, step, arrival, button, type} = t;
    await control('retention', {});
    await go('explorer/', 'Explorer');
    const dialog = page.getByRole('dialog', {name: 'Settings', exact: true});
    const limit = dialog.getByLabel('Delete an entry', {exact: true});
    await arrival('Open Settings', response('**/api/settings/retention', 'GET'),
      () => page.locator('.identity:visible').click(), () => expect(limit).toBeEnabled(),
      {core: settingsCore, watch: ['#settings-title', '.retention-settings']});
    await arrival('Change Delete window', response('**/api/settings/retention-preview'), () => limit.selectOption('14'),
      () => expect(dialog.locator('.limit-effect')).toContainText(/older entr/), {
        core: settingsCore, probe: () => type(dialog.getByLabel('Name', {exact: true}), 'Willow Settings'),
        sections: ['.retention-settings'], watch: ['.limit-effect'], busy: '.limit-effect',
      });
    await arrival('Save limit', response(limitsPath), () => button('Save changes').click(),
      () => expect(dialog).toHaveCount(0), {core: settingsCore, done: {}});
    assert.equal((await t.get('settings/retention')).delete_days, 14);
  },
  'add-expense': async t => {
    const {page, expect, control, go, step, arrival, button, type} = t;
    const seed = await control('rules', {});
    await go('add/', 'Add', addScreen);
    const field = page.locator(words), added = page.locator(result).filter({hasText: 'Pine studio'});
    await step('Type expense', () => type(field, 'Pine studio subscription 22 ' + seed.currency), {...addScreen, typing: true});
    const publication = draftPublication(row => row.state === 'added' && row.text?.startsWith('Pine studio'));
    const rulePublication = {
      pattern: '**/api/rules', method: 'GET', repeat: true, limit: 45000,
      accept: body => JSON.parse(body).rules.some(rule => rule.description === 'Pine studio'),
    };
    const prepared = await t.prepare(publication);
    const ruleGate = await t.prepare(rulePublication);
    try {
      await arrival('Continue to agent', response(draftPath + '/save'), () => button('Continue').click(),
        () => expect(field).toHaveValue(''), {...addScreen, returnFocus: true});
      await arrival('Agent result', publication, null,
        () => expect(added.locator('.result-status')).toHaveText('Added'), {
          ...addScreen, prepared, timingFrom: 'Continue to agent', watch: [result], probe: () => type(field, 'Tea 12'),
        });
      await arrival('Automatic rule available', rulePublication, null,
        () => expect(added).toContainText('Made a rule for Pine studio', {timeout: 45000}), {
          ...addScreen, prepared: ruleGate, timingFrom: 'Continue to agent', watch: [result],
          probe: () => type(field, 'Tea 13'),
          followups: [followup('Edit rule', '.just-added .rule-actions button:first-child'),
            followup('Don’t make a rule', '.just-added .rule-actions button:nth-child(2)')],
        });
    } finally {
      await prepared?.release();
      await ruleGate?.release();
    }
    await arrival('Remove the automatic rule', response(rulesPath),
      () => added.getByRole('button', {name: 'Don’t make a rule', exact: true}).click(),
      () => expect(added.getByRole('button', {name: 'Don’t make a rule', exact: true})).toHaveCount(0),
      {...addScreen, watch: [result], probe: () => type(field, 'Tea 14')});
    await step('Continue typing next item', () => field.press('End').then(() => field.pressSequentially(' cash')),
      {...addScreen, typing: true});
  },
  'add-as-usual': async t => {
    const {page, expect, control, go, step, arrival, button, type} = t;
    const seed = await control('rules', {});
    await go('add/', 'Add', addScreen);
    const field = page.locator(words);
    const usual = page.getByRole('complementary', {name: 'Usual payment'});
    await step('Type usual payment', () => type(field, 'Birch rent 100 ' + seed.currency), {...addScreen, typing: true, watch: ['.rule-guess']});
    await expect(usual).toBeVisible();
    const publication = draftPublication(row => row.state === 'added' && row.text?.startsWith('Birch rent'));
    const prepared = await t.prepare(publication);
    try {
      await arrival('Add as usual', response(draftPath + '/usual'), () => button('Add as usual').click(),
        async () => {}, {...addScreen, actionGroup: undefined, core: {...addCore, action: '[aria-label="Usual payment"] .primary'}, done: {...addScreen, returnFocus: true}});
      await arrival('Usual payment saved', publication, null,
        () => expect(page.locator(result).filter({hasText: 'Birch rent'})).toContainText('Added'),
        {...addScreen, prepared, timingFrom: 'Add as usual', returnFocus: true, probe: () => type(field, 'Tea 12')});
    } finally {
      await prepared?.release();
    }
    await step('Type next expense', () => type(field, 'Tea 14'), {...addScreen, typing: true});
  },
  'receipt-question': async t => {
    const {page, expect, control, go, step, arrival, button, type, file} = t;
    await control('rules', {});
    await control('receipt-answer', {amount: '120.00', hold: !t.timed});
    await go('add/', 'Add', addScreen);
    const field = page.locator(words);
    await step('Type rent payment', () => type(field, 'Birch rent today'), {...addScreen, typing: true});
    await upload(t, await file('receipt', 'Fictional-receipt.png', 'image/png'));
    const publication = draftPublication(row => row.receipt_reading?.state === 'question');
    const prepared = await t.prepare(publication);
    const reading = page.locator('.receipt-inline');
    const keep = reading.getByRole('button', {name: 'Keep the entry', exact: true});
    try {
      await arrival('Add payment with receipt', response(draftPath + '/usual'), () => button('Add as usual').click(),
        () => expect(field).toHaveValue(''), {...addScreen, returnFocus: true});
      await control('receipt-answer', {release: true});
      await arrival('Receipt follow-up', publication, null, () => expect(keep).toBeVisible(), {
        ...addScreen, prepared, timingFrom: 'Add payment with receipt', watch: ['.receipt-inline'], probe: () => type(field, 'Tea 12'),
        followups: [followup('Use receipt', '.receipt-inline .primary'), followup('Keep entry', '.receipt-inline .detail-footer button:nth-child(2)')],
      });
    } finally {
      await control('receipt-answer', {release: true});
      await prepared?.release();
    }
    await arrival('Keep the entry', response(draftPath + '/receipt-choice'), () => keep.click(),
      () => expect(reading).toContainText('Entry kept'), {...addScreen, watch: ['.receipt-inline'], probe: () => type(field, 'Tea 14')});
  },
  'queue-question': async t => {
    const {page, expect, control, go, step, arrival, button, post} = t;
    await control('scenario', {mode: 'question'});
    const draft = await post('drafts', {text: 'Paper Kite Market, groceries yesterday'});
    await post(`drafts/${draft.id}/save`, {});
    await expect.poll(async () => (await t.get(`drafts/${draft.id}`)).state).toBe('question');
    await control('scenario', {mode: 'ready'});
    const question = {core: {input: '.draft-question select', action: '.draft-question .primary'}, sections: ['.draft-detail']};
    await go('queue/', 'Queue');
    await step('Open Queue question', async () => {
      await page.locator('.draft-row').filter({hasText: 'Paper Kite Market'}).click();
      await expect(button('Send answer')).toBeVisible();
    }, {...question, followups: [followup('Send answer', '.draft-question .primary')]});
    await step('Choose paying account', () => page.locator('.draft-question select').selectOption({index: 1}), question);
    const publication = {pattern: /\/api\/drafts(?:\?|$)/, method: 'GET',
      accept: body => !records(body).some(item => item.id === draft.id && item.state !== 'added'), repeat: true};
    const prepared = await t.prepare(publication);
    try {
      await arrival('Send answer', response(draftPath + '/answer'), () => button('Send answer').click(),
        async () => {}, {...question, watch: ['.draft-detail']});
      await arrival('Answered draft saved', publication, null, async () => {
        await expect.poll(async () => (await t.get(`drafts/${draft.id}`)).state).toBe('added');
        await expect(page.locator('.draft-row').filter({hasText: 'Paper Kite Market'})).toHaveCount(0);
      }, {...question, prepared, timingFrom: 'Send answer', watch: ['.draft-queue']});
    } finally {
      await prepared?.release();
    }
  },
  'finish-by-hand': async t => {
    await t.control('scenario', {mode: 'ready'});
    const draft = await t.control('finish', {});
    try {
      await t.go(`queue/?draft=${draft.id}`, 'Queue', {core: {action: '.draft-detail .detail-footer .primary'}});
      await checkedEntry({...t, handDraft: draft.id}, {edit: false, open: () => t.button('Finish it yourself').click()});
      assert.equal((await t.get(`drafts/${draft.id}`)).state, 'added');
    } finally {
      await t.control('finish', {clear: true});
    }
  },
  statement: async t => {
    const {page, expect, control, go, step, arrival, button, file, type} = t;
    await control('reader-scenario', {extension: 'csv', tag: 'flow'});
    const list = page.locator(result).filter({hasText: 'Fictional-statement.csv'});
    await go('add/', 'Add', addScreen);
    await upload(t, await file('reader-file?extension=csv', 'Fictional-statement.csv', 'text/csv'));
    const publication = statementPublication('found');
    const prepared = await t.prepare(publication);
    let session;
    try {
      await arrival('Continue to read statement', response(draftPath + '/continue'), () => button('Continue').click(),
        () => expect(button('Continue')).toBeDisabled(), {...addScreen, returnFocus: true});
      await arrival('Statement reading result', publication, null, async () => {
        await expect(list).toContainText('Needs decisions');
        session = (await t.get('statements')).sessions.find(row => row.original_name === 'Fictional-statement.csv');
      }, {...addScreen, prepared, timingFrom: 'Continue to read statement', probe: () => type(page.locator(words), 'Tea 12'), watch: [result],
        followups: [followup('Decide in Queue', '.just-added .result-link')]});
    } finally {
      await prepared?.release();
    }
    const link = list.getByRole('link').first();
    await step('Open statement in Queue', async () => {
      await link.click();
      await expect(button('Review checklist')).toBeVisible();
    }, {watch: ['.statement-current']});
    const checklist = {core: {input: '.statement-checklist select[id^=decision-]', action: '.statement-review-action .primary'},
      watch: ['.statement-current'], sections: ['.statement-checklist > section', '.statement-apply'],
      pairs: [{name: 'checklist and Apply', left: '.statement-checklist', right: '.statement-apply', equalHeight: false}]};
    await arrival('Review checklist', response(statementPath + '/review'), () => button('Review checklist').click(),
      () => expect(button('Review apply')).toBeVisible(), {
        ...checklist, viewChange: true, followups: [followup('First row to settle', '.statement-checklist select[id^=decision-]')],
      });
    for (const {row} of session.plan.items.filter(item => item.needs_user)) {
      await step(`Decide ${row.description}`, () => page.getByRole('combobox', {name: 'Decision for ' + row.description, exact: true}).selectOption('pending'), checklist);
    }
    await step('Review Apply', () => button('Review apply').click(), checklist);
    const applied = statementPublication('applied');
    const appliedGate = await t.prepare(applied);
    try {
      await arrival('Apply statement', response(statementPath + '/apply'), () => button('Apply statement').click(),
        async () => {}, {core: {action: '[role=dialog] .primary'}, watch: ['[role=dialog]', '.statement-current']});
      await arrival('Statement filed', applied, null,
        () => expect(page.getByRole('heading', {name: 'Statement applied', exact: true})).toBeVisible(),
        {...checklist, prepared: appliedGate, timingFrom: 'Apply statement'});
    } finally {
      await appliedGate?.release();
    }
  },
  'review-duplicate': async t => {
    const {page, expect, control, go, step, checkpoint, arrival, button, gap} = t;
    await control('review-action-scenario', {});
    const card = page.locator('.review-card[data-kind="duplicate"]').filter({hasText: 'Meadow receipt'});
    const screen = {
      core: {action: '.review-card[data-kind=duplicate]:has([data-flow-target]) .primary, [role=dialog] .primary'},
      watch: ['.review-notice', '.review-settled', '.review-card'],
      pairs: [{name: 'duplicate comparison', left: '.review-comparison > div:first-child', right: '.review-comparison > div:nth-child(2)', equalHeight: true}],
    };
    await go('review/', 'Review');
    await expect(card).toBeVisible();
    await t.selector(card.locator('.review-comparison'), 'flow-duplicate');
    await checkpoint('Review duplicate', screen);
    const verdict = '**/api/review/actions';
    const undo = button(/^Undo(?: separation)?$/);
    await arrival('These are separate', response(verdict), () => card.getByRole('button', {name: 'These are separate', exact: true}).click(),
      () => expect(page.getByText('Marked as separate.', {exact: true})).toBeVisible(), screen);
    if (await undo.count()) {
      await arrival('Undo separation', response(verdict), () => undo.click(), () => expect(card).toBeVisible(), screen);
    } else {
      gap('Review separation has no Undo control in this build. The merge branch uses the independent fixture snapshot.');
      await checkpoint('Undo separation unavailable', screen);
      await control('reset-review', {});
      await control('review-action-scenario', {});
      await go('review/', 'Review');
      await expect(card).toBeVisible();
      await t.selector(card.locator('.review-comparison'), 'flow-duplicate');
      await checkpoint('Review same-expense branch', screen);
    }
    await step('Same expense', () => card.getByRole('button', {name: 'Same expense', exact: true}).click(), screen);
    await arrival('Keep earlier entry', response(verdict), () => button('Keep earlier entry').click(),
      () => expect(page.getByRole('heading', {name: 'Same expense settled', exact: true})).toBeVisible(), screen);
    if (await undo.count()) {
      await arrival('Undo merge', response(verdict), () => undo.click(), () => expect(card).toBeVisible(), screen);
    } else {
      gap('Review merge has no Undo control in this build.');
      await checkpoint('Undo merge unavailable', screen);
    }
  },
  'edit-entry': async t => {
    const {page, control, go} = t;
    const seed = await control('retention', {});
    await go(`explorer/?month=2026-11&entry=${seed.plain}`, 'Explorer', {core: {action: '#transaction-inspector .entry-header-tools button:first-child'}});
    await checkedEntry(t, {edit: true, open: () => page.locator('#transaction-inspector').getByRole('button', {name: 'Edit', exact: true}).click()});
  },
  'delete-restore': async t => {
    const {page, expect, control, go, step, arrival, button, type} = t;
    const seed = await control('retention', {});
    await go('add/?draft=' + seed.added, 'Add', addScreen);
    const inspector = page.locator('#transaction-inspector'), core = {action: '#transaction-inspector .entry-header-tools button:first-child'};
    await step('Open entry', () => page.locator(result).getByRole('link', {name: 'Open entry', exact: true}).click(), {core, watch: ['#transaction-inspector']});
    await step('Delete entry', () => inspector.getByRole('button', {name: 'Delete', exact: true}).click(), {core: {action: '[role=dialog] .primary'}});
    const dialog = page.getByRole('dialog', {name: 'Move this entry to Trash?', exact: true});
    const confirm = {input: '[data-testid=delete-reason]', action: '[role=dialog] .primary'};
    await step('Type deletion reason', () => type(dialog.getByLabel('Reason (optional)'), 'Fictional duplicate'), {core: confirm, typing: true});
    await arrival('Move to Trash', response(limitsPath), () => button('Move to Trash').click(),
      () => expect(page.locator('.retention-notice')).toContainText('Willow lunch moved to Trash'),
      {core: confirm, done: addScreen, followups: [followup('Open Trash', '.retention-notice a')]});
    const trash = page.locator('.trash-row').filter({hasText: 'Willow lunch'});
    await step('Open Trash', async () => {
      await page.getByRole('link', {name: 'Open Trash', exact: true}).click();
      await expect(trash.getByRole('button', {name: 'Restore', exact: true})).toBeVisible();
    }, {core: {action: '.trash-row button:first-of-type'}, watch: ['.trash-row']});
    await arrival('Restore entry', response(limitsPath), () => trash.getByRole('button', {name: 'Restore', exact: true}).click(),
      () => expect(trash).toHaveCount(0), {watch: ['.trash-row', '.trash-result-row']});
  },
  explorer: async t => {
    const {page, expect, control, go, step, button} = t;
    await control('retention', {});
    await go('explorer/?month=2026-12', 'Explorer', {core: {input: '#explorer-search', action: '.month-stepper button:first-child'}});
    const entry = page.locator('.entries-table a.row-open').first();
    await step('Open previous month', async () => {
      await button('Previous month').click();
      await expect(entry).toBeVisible();
    }, {core: {input: '#explorer-search', action: '.month-stepper button:first-child'}, watch: ['.month-stepper', '.entries-table']});
    const inspector = page.locator('#transaction-inspector');
    const screen = {core: {heading: '#transaction-inspector h2', action: '#transaction-inspector .entry-header-tools button:first-child'}, watch: ['#transaction-inspector h2']};
    await step('Open inspector', async () => { await entry.click(); await expect(inspector).toBeVisible(); }, screen);
    const heading = await inspector.locator('h2').innerText();
    await step('Press Down to inspect next entry', async () => {
      await page.keyboard.press('ArrowDown');
      await expect(inspector.locator('h2')).not.toHaveText(heading);
    }, screen);
    await openEntryEditor(t, {edit: true, open: () => inspector.getByRole('button', {name: 'Edit', exact: true}).click()});
  },
  'new-rule': async t => {
    const {page, expect, control, go, step, arrival, button, type} = t;
    const seed = await control('rules', {});
    await go('forecast/', 'Forecast', {headingName: /^Forecast(?: · [A-Z]{3})?$/});
    await arrival('Open Rules', response('**/api/rules', 'GET'), () => button('Rules').click(),
      () => expect(button('New rule')).toBeEnabled(), {watch: ['.rules-page'], core: {action: '.rules-page .primary'}});
    await step('New rule', () => button('New rule').click(), {watch: ['[role=dialog]']});
    const dialog = page.getByRole('dialog', {name: 'New rule', exact: true});
    const description = dialog.getByLabel('Description', {exact: true});
    const core = {input: '[data-testid=rule-description]', action: '[role=dialog] .primary'};
    await step('Type rule description', () => type(description, 'Kestrel club'), {core, typing: true});
    await step('Set amount and schedule', async () => {
      await type(dialog.getByLabel('Amount', {exact: true}), '16.25');
      await dialog.getByLabel('Paying account', {exact: true}).selectOption(seed.pay);
      await dialog.getByLabel('Category or investment', {exact: true}).selectOption('Expenses:Rule Workflow:Juniper ended');
      await dialog.getByLabel('Next date', {exact: true}).fill('2026-12-03');
    }, {core});
    await arrival('Save rule', response(rulesPath), () => button('Save rule').click(),
      () => expect(page.getByRole('article', {name: 'Kestrel club', exact: true})).toBeVisible(),
      {core, watch: ['[role=dialog]', '.rules-page']});
  },
  'opening-balances': async t => {
    const {page, expect, go, step, control} = t;
    await control('first-run', {keepAccounts: true});
    await go('account-summary/', 'Accounts', {core: {action: '.opening-invitation a[href*="setup=today"]'}});
    await step('Set up opening balances', async () => {
      await page.getByRole('link', {name: 'Set up opening balances', exact: true}).click();
      await expect(page.getByRole('heading', {name: 'Your accounts, as of today'})).toBeVisible();
    }, {core: setupCore});
    await firstAccount(t, true);
  },
};
