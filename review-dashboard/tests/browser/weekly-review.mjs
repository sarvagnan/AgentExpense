import assert from 'node:assert/strict';
import {hold,settleTasks} from './held.mjs';
import {fixtureContext} from './suites.mjs';

const runId = 'a'.repeat(32);
const profile = {adapter:{id:'fixture-review',display_name:'Fixture reviewer'},model:'fixture-review-model',effort:'high'};
const gate = {state:'ready',reason:null};
const clock = new Date('2026-11-22T10:00:00Z');

export async function runWeeklyReview({page:shared,base,persona,theme,expect,record,snap}) {
  // page.clock belongs to the whole browser context, so the controlled clock gets its own context.
  const context = await fixtureContext(shared.context().browser(),persona,theme,shared.viewportSize());
  const page = await context.newPage(), errors = [];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.request().url().startsWith(base)?route.continue():route.abort('blockedbyclient'));
  const go = async path=>{
    await page.goto(base+path);
    await expect(page.locator('.content h1').first()).toBeVisible();
    await page.waitForLoadState('networkidle');
  };
  const status = {enabled:false,day:'Monday',active:null,history:[],next_offset:null,gate,agent_pause:null};
  const weekly = '**/api/review/weekly*', schedule = '**/api/settings/weekly-review', agent = '**/api/agent-status';
  const saves = [], actions = [];
  let inherited = false;
  await page.route(agent, async route => {
    const response = await route.fetch();
    await route.fulfill({response,json:{...await response.json(),reviewer:{...gate,...profile,inherited}}});
  });
  await page.route(weekly, route => {
    if (route.request().method() === 'POST') {
      actions.push(route.request().postDataJSON());
      status.active = {run_id:runId,state:'waiting',reason:'Waiting for foreground work',checked:3,issues:0,
        batch_count:4,committed_batches:1,adapter:'fixture-review',model:profile.model,effort:profile.effort};
      return route.fulfill({json:{run_id:runId}});
    }
    return route.fulfill({json:status});
  });
  await page.route(schedule, route => {
    const body = route.request().postDataJSON();
    saves.push(body);
    Object.assign(status,{enabled:body.enabled,day:body.day});
    return route.fulfill({json:{enabled:body.enabled,day:body.day}});
  });
  try {
    await page.clock.install({time:clock});
    await go('review');
    await page.locator('.identity:visible').click();
    const settings = page.getByRole('dialog',{name:'Settings'});
    await expect(settings.getByText('Fixture reviewer · fixture-review-model · high',{exact:true})).toBeVisible();
    await settings.getByLabel('Run a review every week').check();
    await settings.getByLabel('Day',{exact:true}).selectOption('Thursday');
    await settings.getByRole('button',{name:'Save changes'}).click();
    await expect(settings).toHaveCount(0);
    assert.deepEqual(saves.map(({enabled,day})=>({enabled,day})),[{enabled:true,day:'Thursday'}]);
    assert.match(saves[0].request_id,/^[a-f0-9]{32}$/);
    inherited = true;
    await go('review');
    await page.locator('.identity:visible').click();
    await expect(settings.getByText('Same as adding')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(settings).toHaveCount(0);
    await page.getByRole('button',{name:'Weekly review',exact:true}).click();
    await page.clock.pauseAt(new Date(await page.evaluate(()=>Date.now())+1000));
    await page.getByRole('button',{name:'Review now'}).click();
    await expect(page.getByText('Waiting for foreground work',{exact:true})).toBeVisible();
    assert.equal(actions[0].action,'start');
    await snap('weekly-review-waiting',{target:page});
    status.agent_pause = {kind:'usage_limit',reason:'Fixture limit',resumes_at:'2026-11-22T10:30:00Z'};
    await settleTasks(page);
    await page.clock.runFor(2000);
    await expect(page.getByRole('heading',{name:'Paused for usage limit'})).toBeVisible();
    await snap('weekly-review-usage',{target:page});
    status.agent_pause = {kind:'auth',reason:'Fixture sign-in',resumes_at:'2026-11-22T10:30:00Z'};
    await settleTasks(page);
    await page.clock.runFor(2000);
    await expect(page.getByText('make agent-login ROLE=review')).toBeVisible();
    await expect(page.getByText(/Sign in to Fixture reviewer/)).toBeVisible();
    status.agent_pause = null;
    Object.assign(status.active,{state:'failed',stage:'summary',retry_action:'retry-summary',reason:'Fictional report retry'});
    await settleTasks(page);
    await page.clock.runFor(2000);
    await page.getByRole('button',{name:'Retry summary'}).click();
    assert.equal(actions.at(-1).action,'retry-summary');
    assert.equal(actions.at(-1).run_id,runId);
    record('weekly-review-settings-and-wait-states');
    status.active = null;
    status.agent_pause = null;
    const issue = 'b'.repeat(32), source = 'c'.repeat(32);
    const issueHref = base+'queue/?review_issue_id='+issue;
    status.history = [{
      run_id:runId, status:'completed', ended:clock.toISOString(), period:['2026-11-09','2026-11-15'],
      adapter:'fixture-review', model:profile.model, effort:profile.effort, checked:36, issues:1, carried:1, rule_proposals:0,
      report:{fine:[{number:1,reason:'The balances agree.',lines:[source]}],
        decisions:[{number:2,id:'d'.repeat(32),issue_id:issue,href:new URL(issueHref).pathname+new URL(issueHref).search,
          source_href:base+'api/review/sources/'+source,status:'open',title:'Blue Kite Books',
          first_raised:'2026-11-09T10:00:00Z',decide:'Was the invoice in EUR or USD?',evidence:[]}],
        patterns:[{number:3,note:'<strong>Two receipt dates moved.</strong>',suggested_change:'Show the receipt time zone.'}]}
    }];
    await settleTasks(page);
    await page.clock.runFor(2000);
    await page.getByRole('button',{name:'View report',exact:true}).first().click();
    const decisions = page.getByRole('region',{name:'Decisions',exact:true});
    await expect(decisions.getByRole('link',{name:'Blue Kite Books',exact:true})).toHaveAttribute('href',new URL(issueHref).pathname+new URL(issueHref).search);
    await expect(decisions.locator('li')).toHaveAttribute('value','2');
    await expect(decisions.getByText(/Raised before/)).toBeVisible();
    await expect(page.getByText('<strong>Two receipt dates moved.</strong>')).toBeVisible();
    const bounds = await decisions.boundingBox(), fine = await page.getByRole('region',{name:'What was fine'}).boundingBox();
    if (page.viewportSize().width > 760) assert.ok(fine.x >= bounds.x+bounds.width);
    else assert.ok(fine.y >= bounds.y+bounds.height);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Report fits the viewport');
    assert.deepEqual(errors,[]);
    await snap('weekly-review-report',{target:page});
    record('weekly-review-report-links-carry-over');
  } finally {
    await context.close();
  }
}

export async function runWeeklyReviewIssue({page,base,expect,go,control,refresh,record,snap,quiesce}) {
  await quiesce();
  await control('reset-review',{});
  const seed = await control('weekly-review-issue',{});
  const url = 'queue/?review_issue_id='+seed.id, queue = '**/api/drafts?include_added=true&include_review=true';
  const dialog = page.getByRole('dialog',{name:'Keep as is'});
  const issue = page.getByRole('article',{name:'Review item'});
  try {
    await go(url);
    await expect(issue.getByText('Which shop was this?')).toBeVisible();
    await issue.getByRole('button',{name:'Apply suggestion'}).click();
    const editor = page.getByRole('article',{name:'Edit entry'});
    await expect(editor.getByLabel('Description',{exact:true})).toHaveValue('Paper Kite Workshop');
    await expect(editor.getByText(/Saved.*Meadow receipt/)).toBeVisible();
    await editor.getByRole('button',{name:'Back to review item'}).click();
    await expect(issue.getByRole('button',{name:'Apply suggestion'})).toBeVisible();

    await issue.getByRole('button',{name:'Keep as is'}).click();
    await expect(dialog.getByLabel('Resolution note (optional)')).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(issue.getByRole('button',{name:'Keep as is'})).toBeFocused();
    await issue.getByRole('button',{name:'Answer',exact:true}).click();
    const answer = issue.getByLabel('Your answer'), send = issue.getByRole('button',{name:'Send answer'});
    const held = await hold(page,queue,{transform:body => {
      const next=JSON.parse(body);next.review.items=[];next.review.needs_you=0;return JSON.stringify(next);
    }});
    try {
      await refresh();await held.ready;
      await answer.fill('A newer answer');await send.hover();const before=await send.boundingBox();
      await refresh();await held.release();
      await expect(answer).toHaveValue('A newer answer');
      await expect(send).toBeEnabled();
      const after=await send.boundingBox();for(const key of ['x','y','width','height'])assert.ok(Math.abs(after[key]-before[key])<1,key);
    } finally {await held.release();}
    record('weekly-review-poll-preserves-open-form');

    await answer.fill('');
    await issue.getByLabel('Choose a supporting file').setInputFiles({name:'Paper Kite invoice.pdf',mimeType:'application/pdf',buffer:Buffer.from(await (await fetch(base+'__test/blank-pdf',{method:'POST'})).arrayBuffer())});
    await expect(send).toBeEnabled();
    const submitted = page.waitForRequest(request=>request.url().includes('/answer/')&&request.method()==='POST');
    await send.click();
    const body = (await submitted).postDataJSON();
    assert.equal(body.text,'');assert.match(body.draft_id,/^[a-f0-9]{32}$/);
    await expect(issue.getByText('Conversation',{exact:true})).toBeVisible();
    await snap('weekly-review-attachment');
    await go('explorer/?month=2026-10&entry='+seed.entry_id);
    await expect(page.getByRole('heading',{name:'Entry history'})).toBeVisible();
    await expect(page.getByText('Owner answer',{exact:true})).toBeVisible();

    await go(url);
    await issue.getByRole('button',{name:'Apply suggestion'}).click();
    await expect(editor.getByLabel('Description',{exact:true})).toHaveValue('Paper Kite Workshop');
    await editor.getByRole('button',{name:'Check entry'}).click();
    const saved = page.waitForRequest(request=>request.url().includes('/corrections/')&&request.method()==='POST');
    await editor.getByRole('button',{name:'Save',exact:true}).click();
    assert.equal((await saved).postDataJSON().review_suggestion_id,seed.suggestion_id);
    await expect(page.getByRole('heading',{name:'Correction saved',exact:true})).toBeVisible({timeout:120000});
    await go('review');
    await page.getByText('Change history',{exact:true}).click();
    await expect(page.getByText('Correction saved',{exact:true})).toBeVisible();
    record('weekly-review-issue-editor-and-attachment');
  } finally {
    await quiesce();await control('reset-review',{});
  }
}
