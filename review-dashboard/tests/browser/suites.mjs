import {assertActionCoverage} from './action-coverage.mjs';
import {runDataDeparture} from './data-departure.mjs';
import {consumeInjected422} from './expected-errors.mjs';
import {runDirectStates,runAccountCreate,runReviewCoverage} from './direct-states.mjs';
import {runImmediateLatency} from './immediate-latency.mjs';
import {observeDirectWrites} from './direct-write-observer.mjs';
import {runImmediateActions,runAccountCurrencyAction} from './immediate-actions.mjs';
import {benignConsole} from './console-filter.mjs';
import {runRetention} from './retention.mjs';
import {runViewCaptures} from './capture.mjs';
import {runAccountGroups} from './account-groups.mjs';
import {runFirstRun} from './first-run.mjs';
import {runRetentionTiming} from './retention-timing.mjs';
import {runViewTiming} from './view-timing.mjs';
import {runVarying} from './varying.mjs';
import {runUpdateNotice} from './update-notice.mjs';
import {runConfirmedBalances} from './confirmed-balances.mjs';
import {runLedgerCheck} from './ledger-check.mjs';
import {runLightSave} from './light-save.mjs';
import {runAdd} from './add.mjs';
import {runAttachments} from './attachments.mjs';
import {runAddPolish} from './add-polish.mjs';
import {runCorrections} from './corrections.mjs';
import {runRules} from './rules.mjs';
import {runFinish} from './finish.mjs';
import {runReaderWorkflows} from './readers.mjs';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {runReview} from './review.mjs';
import {runWorkflows} from './workflows.mjs';
import {execFileSync} from 'node:child_process';
import {gzipSync} from 'node:zlib';

const routes=['add','queue','review','explorer','atlas','journal','forecast','account-summary','statements'];
const labels=['Add','Queue','Review','Explorer','Atlas','Journal','Forecast','Accounts'];
const sizes=[{width:1440,height:1000},{width:412,height:915}];
const locale={asha:'en-IN',jordan:'en-US',sam:'en-GB',empty:'en-US'};
const zone={asha:'Asia/Kolkata',jordan:'America/Chicago',sam:'Europe/Berlin',empty:'America/Chicago'};

export async function runSuite({browser,engine,stage,expect,results,capture,checkpoint=async()=>{},themes=['light','dark']}) {
  const {base,persona,ledger}=stage;
  // macOS WebKit follows the system keyboard-access preference. Option-Tab
  // includes links and buttons even when full keyboard access is off.
  const shellTab=engine==='webkit' && process.platform==='darwin'?'Alt+Tab':'Tab';
  const initialManifest=JSON.parse(await readFile(join(ledger,'ledger.json'),'utf8'));
  const initialAccounts=await readFile(join(ledger,'data/accounts.json'),'utf8');
  const originalExpenses=await readFile(join(ledger,'data/expenses/2026.json'),'utf8');
  const money=(value,currency)=>new Intl.NumberFormat(locale[persona],{style:'currency',currency,minimumFractionDigits:2,maximumFractionDigits:2}).format(value);
  const control=async(path,body)=>{
    const response=await fetch(base+'__test/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    assert.ok(response.ok,`${path}: ${await response.clone().text()}`);return response.json();
  };
  for(const theme of themes)for(const size of sizes) {
    const {width,height}=size;
    if(process.env.BROWSER_CASE && process.env.BROWSER_CASE!==`${theme}-${width}`)continue;
    const context=await browser.newContext({viewport:size,colorScheme:theme,locale:locale[persona],timezoneId:zone[persona],reducedMotion:'reduce',acceptDownloads:true});
    await context.addInitScript(value=>{if(location.protocol==='http:'||location.protocol==='https:')localStorage.setItem('ledger-appearance',value);},theme);
    // WebKit trace screenshots capture through macOS WindowServer, which holds the surfaces while
    // the display sleeps until it aborts and ends the login session (microsoft/playwright#43066).
    await context.tracing.start({screenshots:engine!=='webkit'&&process.env.TRACE_SCREENSHOTS!=='0',snapshots:true});
    stage.activeContext=context;stage.activeCase=`${engine}-${theme}-${width}`;
    const page=await context.newPage(),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('console',message=>{if(message.type()==='error'&&!consumeInjected422(page,message)&&!benignConsole(message.text()))errors.push(message.text());});
    await page.route('**/*',route=>{const url=route.request().url();return url.startsWith(base) || url.startsWith('blob:') || url.startsWith('data:')?route.continue():route.abort('blockedbyclient');});
    const record=(name)=>{results.push({persona,engine,theme,width,test:name,pass:true});console.log(`  PASS ${persona} ${engine} ${theme} ${width}: ${name}`);};
    const commits=()=>execFileSync('jj',['-R',ledger,'log','-r','ancestors(@)','--no-graph','-T','commit_id'],{env:stage.env,encoding:'utf8'}).trim().length/40;
    const timed=async(name,run)=>{
      if(process.env.BROWSER_PROFILE!=='1')return run();
      const before=commits(),started=performance.now();
      try{return await run();}finally{console.log('SUITE_TIMING '+JSON.stringify({persona,engine,theme,width,name,seconds:(performance.now()-started)/1000,commits:commits()-before}));}
    };
    const snap=(state)=>capture(page,{state,theme,width,height});
    const directObserver=await observeDirectWrites({page,base,snap,record});
    const go=async(route)=>{await page.waitForLoadState('networkidle');await page.goto(base+route);await page.locator('.content h1').first().waitFor();await expect(page.locator('.loading-state')).toHaveCount(0);await page.waitForLoadState('networkidle');};
    const overflow=async(label)=>{
      // On overflow, name the outermost elements reaching past the viewport, so a failure says where.
      const value=await page.evaluate(()=>{const width=innerWidth,wide=document.documentElement.scrollWidth>width+1||document.body.scrollWidth>width+1;
        const past=wide?[...document.body.querySelectorAll('*')].filter(node=>node.getBoundingClientRect().right>width+1&&!(node.parentElement&&node.parentElement.getBoundingClientRect().right>width+1)).slice(0,5).map(node=>`${node.tagName.toLowerCase()}.${[...node.classList].join('.')} ${Math.round(node.getBoundingClientRect().width)}px`):[];
        return {width,html:document.documentElement.scrollWidth,body:document.body.scrollWidth,...(past.length?{past}:{})};});
      assert.ok(value.html<=value.width+1 && value.body<=value.width+1,`${label}: horizontal overflow ${JSON.stringify(value)}`);
    };
    // Firefox's protocol converts rectangle endpoints separately and can report
    // 43.999969px for a rendered 44px control at a fractional screen position.
    const emptyTargets=async()=>{
      if(page.viewportSize().width>=760)return;
      const heights=await page.locator('.empty .button.primary').evaluateAll(nodes=>nodes.map(node=>node.getBoundingClientRect().height));
      for(const height of heights)assert.ok(height>=44,`Empty-view primary actions have a 44px phone touch target: ${height}px at ${page.url()}`);
    };
    const refresh=async()=>{await page.evaluate(()=>window.dispatchEvent(new Event('drafts-changed')));};
    const quiesce=async({writesSettled=false}={})=>{
      if(page.url().startsWith(base)){
        // Fixture deletion must not manufacture a missing composer recovery ID.
        // Await real network completion, clear only the test composer's recovery
        // copy, then unload the app before deleting ignored server records.
        if(!writesSettled)await page.waitForLoadState('networkidle');
        await page.evaluate(()=>{localStorage.removeItem('ledger-add-v1');for(const key of Object.keys(sessionStorage))if(key.startsWith('ledger-add-visit-')||key.startsWith('ledger-form-')||key.startsWith('ledger-direct-writes-'))sessionStorage.removeItem(key);});
        await page.goto('about:blank');
      }
    };
    const resetDrafts=async(body)=>{await quiesce();return control('drafts',body);};
    const identity=()=>page.locator('.identity:visible');
    const openSettings=async()=>{await identity().click();await expect(page.getByRole('dialog',{name:'Settings',exact:true})).toBeVisible();await expect(page.getByLabel('Name',{exact:true})).toBeFocused();};
    const closeDialog=async()=>{await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0);};
    try {
      if(process.env.BROWSER_FOCUS==='route-targets') {
        for(const route of routes){await go(route+'/');await overflow(route);await emptyTargets();await page.setViewportSize({width:320,height});await overflow(route+' at 320');await emptyTargets();await page.setViewportSize(size);await emptyTargets();await snap('route-targets-'+route);record('route targets: '+route+' fits target/320px and its rendered phone primary targets are at least 44px');}
        await directObserver.close();assert.deepEqual(errors,[]);await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(['post-direct','post-add'].includes(process.env.BROWSER_FOCUS)) {
        // Resume only suites not reached when an earlier primary case failed.
        if(process.env.BROWSER_FOCUS==='post-add'){
          await runCorrections({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts});await runConfirmedBalances({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts});await runLedgerCheck({page,base,persona,engine,theme,width,expect,control,go,refresh,overflow,snap,record,quiesce});await runAccountCreate({page,base,persona,expect,control,go,quiesce,snap,record});await runDirectStates({page,base,persona,stage,expect,control,go,quiesce,snap,record,errors});
        }
        await runDataDeparture({page,base,expect,control,go,record,snap});await runImmediateActions({page,base,persona,expect,control,go,quiesce,snap,record,errors});await runLightSave({page,base,persona,expect,control,go,refresh,record,quiesce,snap});await runRetention({page,base,persona,width,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts});await runRetentionTiming({page,base,persona,expect,control,go,refresh,snap,record,quiesce,resetDrafts});await runViewTiming({page,base,persona,expect,control,go,refresh,record,quiesce,resetDrafts});await runAccountGroups({page,base,persona,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,money});await runVarying({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts});await runFirstRun({page,base,persona,width,expect,control,overflow,snap,record,quiesce,consoleErrors:errors});if(process.env.BROWSER_FOCUS==='post-add'){console.log('ACTION_COVERAGE_COMPLETE '+JSON.stringify(assertActionCoverage(page)));record('resumed post-Add suites have complete action-table evidence');}await directObserver.close();assert.deepEqual(errors,[]);await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='review-round') {
        await runAccountCreate({page,base,persona,expect,control,go,quiesce,snap,record});await runDirectStates({page,base,persona,stage,expect,control,go,quiesce,snap,record,errors});await runDataDeparture({page,base,expect,control,go,record,snap});await runImmediateActions({page,base,persona,expect,control,go,quiesce,snap,record,errors});await runFirstRun({page,base,persona,width,expect,control,overflow,snap,record,quiesce,consoleErrors:errors});console.log('ACTION_COVERAGE_COMPLETE '+JSON.stringify(assertActionCoverage(page)));await directObserver.close();assert.deepEqual(errors,[]);await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='recovery-settings') {await runDataDeparture({page,base,expect,control,go,record,snap});await runImmediateActions({page,base,persona,expect,control,go,quiesce,snap,record,errors});await directObserver.close();assert.deepEqual(errors,[]);await context.tracing.stop();await context.close();continue;}
      if(process.env.BROWSER_FOCUS==='data-departure') {await runDataDeparture({page,base,expect,control,go,record,snap});await directObserver.close();assert.deepEqual(errors,[]);await context.tracing.stop();await context.close();continue;}
      if(process.env.BROWSER_FOCUS==='account-create') {await runAccountCreate({page,base,persona,expect,control,go,quiesce,snap,record});await directObserver.close();assert.deepEqual(errors,[]);await context.tracing.stop();await context.close();continue;}
      if(process.env.BROWSER_FOCUS==='review-writes') {await runReviewCoverage({page,base,persona,stage,expect,control,go,quiesce,snap,record});await directObserver.close();assert.deepEqual(errors,[]);await context.tracing.stop();await context.close();continue;}
      if(process.env.BROWSER_FOCUS==='direct-states') {
        await runDirectStates({page,base,persona,stage,expect,control,go,quiesce,snap,record,errors});await directObserver.close();assert.deepEqual(errors,[]);await context.tracing.stop();await context.close();continue;
      }
      if(process.env.BROWSER_FOCUS==='latency') {
        await runImmediateLatency({page,base,expect,go,record,control,quiesce,stage});await directObserver.close();assert.deepEqual(errors,[]);await context.tracing.stop();await context.close();continue;
      }
      if(process.env.BROWSER_FOCUS==='immediate') {
        await runImmediateActions({page,base,persona,expect,control,go,quiesce,snap,record,errors});
        assert.deepEqual(errors,[],'No browser errors');await directObserver.close();await context.tracing.stop();await context.close();continue;
      }
      if(process.env.BROWSER_FOCUS==='account-currency') {
        await runAccountCurrencyAction({page,base,expect,control,go,quiesce,snap,record,errors});
        assert.deepEqual(errors,[],'No browser errors');await directObserver.close();await context.tracing.stop();await context.close();continue;
      }
      if(process.env.BROWSER_FOCUS==='light-save') {
        await runLightSave({page,base,persona,expect,control,go,refresh,record,quiesce,snap});
        await directObserver.close();await context.tracing.stop();await context.close();continue;
      }
      if(process.env.BROWSER_FOCUS==='cleanup') {
        await go('add/');await expect(page.getByRole('heading',{name:'Add',exact:true})).toBeVisible();
        console.log('CLEANUP_BROWSER_READY');
        if(process.env.BROWSER_CLEANUP_PROBE==='term')await new Promise(()=>{});
        assert.notEqual(process.env.BROWSER_CLEANUP_PROBE,'fail','Deliberate test-root cleanup failure');
        await directObserver.close();await context.tracing.stop();await context.close();continue;
      }
      const table = JSON.parse(await readFile(join(stage.env.DASHBOARD_DATA_DIR,'app/data/dashboard.json'),'utf8')).meta.currencyMinorUnits;
      assert.deepEqual(table,JSON.parse(await readFile(join(stage.app,'schema/currency-minor-units.json'),'utf8')),'The page data ships the versioned minor-unit table');
      await page.goto(base+'add/');
      const formatterChecks = await page.evaluate(async ({base,table})=>{
        const source=await (await fetch(base+'__test/formatting',{method:'POST'})).text();
        const {currencyNumberFormat,money,minorUnits,setMoneySettings}=await import('data:text/javascript,'+encodeURIComponent(source));
        setMoneySettings({locale:'en'},table);
        return Object.entries(table).every(([currency,digits])=>{
          const formatter=currencyNumberFormat(currency), options=formatter.resolvedOptions();
          return minorUnits(currency)===digits && options.minimumFractionDigits===digits && options.maximumFractionDigits===digits && money(1.23456,currency)===formatter.format(1.23456);
        });
      },{base,table});
      assert.equal(formatterChecks,true,'All currency codes use the app minor-unit table in this engine');
      record('all currency minor units are explicit in the page formatter');
      if(process.env.BROWSER_FOCUS==='part-c-details') {
        const args={browser,context,page,base,persona,engine,theme,width,height,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts};
        for(const run of [runReview,runRules,runAdd,runCorrections,runConfirmedBalances])await timed(run.name,()=>run(args));
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='review') {
        await timed('runReview',()=>runReview({browser,context,page,base,persona,engine,theme,width,height,stage,expect,control,go,overflow,snap,record,quiesce}));
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='workflows') {
        await timed('runWorkflows',()=>runWorkflows({page,base,persona,engine,theme,width,height,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='readers') {
        await timed('runReaderWorkflows',()=>runReaderWorkflows({page,base,persona,engine,theme,width,stage,expect,control,go,overflow,snap,record,quiesce}));
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='add-polish') {
        await runAddPolish({page,persona,expect,control,go,snap,record,quiesce,resetDrafts});
        assert.deepEqual(errors,[]);await context.tracing.stop();await context.close();await checkpoint();continue;
      }
      if(['attachments','attachments-queue','attachments-deadlines'].includes(process.env.BROWSER_FOCUS)) {
        await runAttachments({page,base,persona,expect,control,go,record,quiesce,resetDrafts,snap,queueOnly:process.env.BROWSER_FOCUS==='attachments-queue',timeoutOnly:process.env.BROWSER_FOCUS==='attachments-deadlines'});
        await directObserver.close();assert.deepEqual(errors,[]);
        await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='add') {
        await runAttachments({page,base,persona,expect,control,go,record,quiesce,resetDrafts,snap});
        await timed('runAdd',()=>runAdd({page,base,persona,engine,theme,width,height,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='retention-timing') {
        await runRetentionTiming({page,base,persona,expect,control,go,refresh,snap,record,quiesce,resetDrafts});
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        await context.tracing.stop();await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='account-groups') {
        await timed('runAccountGroups',()=>runAccountGroups({page,base,persona,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,money}));
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        record('console and page errors');await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='view-timing') {
        await runViewTiming({page,base,persona,expect,control,go,refresh,record,quiesce,resetDrafts});
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='retention') {
        await runRetention({page,base,persona,width,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts});
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='first-run') {
        await timed('runFirstRun',()=>runFirstRun({page,base,persona,width,expect,control,overflow,snap,record,quiesce,consoleErrors:errors}));
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        record('console and page errors');await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='varying') {
        await runVarying({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts});
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        record('console and page errors');await context.tracing.stop();await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='confirmed-balances') {
        await runConfirmedBalances({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts});
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        record('console and page errors');await context.tracing.stop();await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='ledger-check') {
        await timed('runLedgerCheck',()=>runLedgerCheck({page,base,persona,engine,theme,width,expect,control,go,refresh,overflow,snap,record,quiesce}));
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        record('console and page errors');await context.tracing.stop();await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='corrections') {
        await timed('runCorrections',()=>runCorrections({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='rules') {
        await timed('runRules',()=>runRules({page,base,persona,engine,theme,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='update-notice') {
        await runUpdateNotice({page,base,expect,go,snap,overflow,record});
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        record('console and page errors');await context.tracing.stop();await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='views') {
        await timed('runViewCaptures',()=>runViewCaptures({page,persona,width,height,expect,go,snap,overflow,record}));
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        record('console and page errors');await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='finish') {
        await timed('runFinish',()=>runFinish({page,base,persona,engine,theme,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
        await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      await control('gate',{state:'ready'});
      const seeded=await resetDrafts({persona,empty:persona==='empty'});
      await go('add/');await expect(page.locator('#expense-words')).toBeEnabled();
      await expect(page.locator('.agent-notice')).toHaveCount(0);
      // A single ordered source list, with one divider separating work and views.
      assert.deepEqual(await page.locator('.sidebar nav a').evaluateAll(nodes=>nodes.map(node=>node.title)),labels);
      assert.equal(await page.locator('.sidebar nav hr').count(),1);
      assert.equal(await page.locator('.sidebar nav a[href*="statements"]').count(),0);
      await expect(identity()).toContainText(initialManifest.name);
      const nav=page.locator(width>760?'.sidebar nav':'.phone-live-nav');
      const draftsLink=nav.locator('a[href$="queue/"]');
      if(seeded.attention)await expect(draftsLink.locator('.nav-count')).toHaveText(new RegExp(`^(?:, )?${seeded.attention}(?: needs? attention)?$`));
      else await expect(draftsLink.locator('.nav-count')).toHaveCount(0);
      const reviewData=await (await fetch(base+'api/review')).json();
      const reviewLink=nav.locator('a[href$="review/"]');
      if(reviewData.count)await expect(reviewLink.locator('.nav-count')).toContainText(String(reviewData.count));
      else await expect(reviewLink.locator('.nav-count')).toHaveCount(0);
      await expect(reviewLink.locator('.important')).toHaveCount(reviewData.important?1:0);
      if(width>760) {
        await page.locator('.skip-link').focus();await page.keyboard.press(shellTab);await expect(page.locator('.sidebar .wordmark')).toBeFocused();
        await page.keyboard.press(shellTab);await expect(identity()).toBeFocused();
        for(const name of labels){await page.keyboard.press(shellTab);await expect(page.locator('.sidebar nav a').filter({has:page.locator('.sidebar-label',{hasText:new RegExp('^'+name+'$')})})).toBeFocused();}
        await page.locator(':focus').evaluate(element=>element.blur());
      }
      if(width<760) {
        assert.deepEqual(await page.locator('.phone-live-nav > a,.phone-live-nav > button').evaluateAll(nodes=>nodes.map(node=>node.textContent.replace(/\d+/g,'').trim())),['Add','Queue','Review','Views']);
        await page.getByRole('button',{name:'Views',exact:true}).click();
        assert.deepEqual(await page.locator('#phone-more-views nav a').allTextContents(),labels.slice(3));
        await expect(page.locator('#phone-more-views nav a').first()).toBeFocused();
        for(let i=1;i<5;i++){await page.keyboard.press(shellTab);await expect(page.locator('#phone-more-views nav a').nth(i)).toBeFocused();}
        const appearanceRow=page.locator('#phone-more-views .appearance');
        await page.keyboard.press(shellTab);await expect(appearanceRow).toBeFocused();
        await expect(appearanceRow).toHaveText((theme==='light'?'Dark':'Light')+' appearance');
        await expect(appearanceRow.locator('.icon')).toHaveCount(1);
        assert.equal(await appearanceRow.evaluate(node=>getComputedStyle(node).borderTopWidth),'0px');
        await page.keyboard.press('Enter');await expect(page.locator('html')).toHaveAttribute('data-theme',theme==='light'?'dark':'light');
        await page.keyboard.press('Space');await expect(page.locator('html')).toHaveAttribute('data-theme',theme);
        await page.keyboard.press('Escape');await expect(page.getByRole('button',{name:'Views',exact:true})).toBeFocused();
        await page.getByRole('button',{name:'Views',exact:true}).click();
      }
      if(width<760) {
        const contrasts=await page.locator('.phone-live-nav .nav-count').evaluateAll(nodes=>nodes.map(node=>{
          const style=getComputedStyle(node),canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');
          const luminance=color=>{ctx.fillStyle=color;ctx.fillRect(0,0,1,1);const rgb=[...ctx.getImageData(0,0,1,1).data].slice(0,3).map(c=>{c/=255;return c<=0.04045?c/12.92:((c+0.055)/1.055)**2.4;});return rgb[0]*0.2126+rgb[1]*0.7152+rgb[2]*0.0722;};
          const a=luminance(style.color),b=luminance(style.backgroundColor);
          return {position:style.position,contrast:(Math.max(a,b)+0.05)/(Math.min(a,b)+0.05)};
        }));
        for(const badge of contrasts){assert.equal(badge.position,'static');assert.ok(badge.contrast>=4.5,JSON.stringify(badge));}
        for(const link of await page.locator('#phone-more-views nav a').all()) {
          assert.equal(await link.evaluate(node=>getComputedStyle(node).color),await page.locator('body').evaluate(node=>getComputedStyle(node).color));
        }
      }
      if(width>760) {
        // Footer: Trash, appearance and Help as one column of ordinary rows, one Settings entry
        // (the identity), no visible status line, and a build time that never breaks inside.
        const footer=await page.locator('.sidebar-footer').evaluate(node=>{
          const rows=[...node.children].filter(child=>getComputedStyle(child).display!=='none');
          const box=element=>element.getBoundingClientRect();
          const time=node.querySelector('.data-built-time');
          return {classes:rows.map(row=>row.className),icons:[...node.querySelectorAll('.sidebar-button > .icon')].map(icon=>Math.round(box(icon).left)),labels:[...node.querySelectorAll('.sidebar-button .sidebar-label')].map(label=>Math.round(box(label).left)),
            gaps:rows.slice(1).map((row,i)=>Math.round(box(row).top-box(rows[i]).bottom)),colors:[...node.querySelectorAll('.sidebar-button')].map(button=>getComputedStyle(button).color),
            settings:node.querySelectorAll('[title="Settings"]').length,status:document.querySelectorAll('.sidebar-status').length,timeWrap:getComputedStyle(time).whiteSpace,timeLines:time.getClientRects().length};
        });
        assert.deepEqual(footer.classes,['sidebar-button','sidebar-button','sidebar-button','data-built'],'Trash is an ordinary footer row and nothing else sits in the footer');
        assert.equal(new Set(footer.icons).size,1,`Footer icons share one column: ${footer.icons}`);assert.equal(new Set(footer.labels).size,1,`Footer labels share one column: ${footer.labels}`);
        assert.equal(new Set(footer.gaps).size,1,`Footer rows are evenly spaced: ${footer.gaps}`);assert.equal(new Set(footer.colors).size,1,'Trash is not accent-coloured');
        const navIcon=await page.locator('.sidebar nav a .icon').first().evaluate(icon=>Math.round(icon.getBoundingClientRect().left));assert.ok(Math.abs(navIcon-footer.icons[0])<=1,`Footer icons align with the view icons (${navIcon} vs ${footer.icons[0]})`);
        assert.equal(footer.settings,0,'Settings has one entry: the identity');assert.equal(footer.status,0,'No visible status line in the sidebar');
        assert.equal(footer.timeWrap,'nowrap');assert.equal(footer.timeLines,1,'The build time stays on one line');
      } else {
        // Phone Views sheet (still open): view links, then quiet rows (appearance, Trash, Help), then Settings and Close views.
        await expect(page.locator('#phone-more-views')).toBeVisible();
        assert.deepEqual(await page.locator('#phone-more-views .phone-more-rows > *').evaluateAll(nodes=>nodes.map(node=>node.textContent.replace(/[,\d]|items?$/g,'').trim())),[(theme==='light'?'Dark':'Light')+' appearance','Trash','Help & Shortcuts']);
        assert.deepEqual(await page.locator('#phone-more-views > .button').allTextContents(),['Settings','Close views']);
        for(const row of await page.locator('#phone-more-views .phone-more-rows > *').all())assert.equal(await row.evaluate(node=>getComputedStyle(node).borderTopWidth),'0px','Sheet rows are text rows, not outlined buttons');
      }
      await overflow('navigation');await snap('nav-'+persona);record('navigation order, actionable badges and plain Review count');
      if(width<760)await page.keyboard.press('Escape');
      await page.keyboard.press('Tab');
      await openSettings();
      await expect(page.getByLabel('Main currency',{exact:true})).toBeEnabled();
      await expect(page.locator('.agent-ready')).toBeVisible();
      // As it opens: the agent status sits after name and currency, fully above the footer, and the
      // close button's tooltip stays hidden (a tap can leave the pointer over it on phones).
      const opened=await page.getByRole('dialog').evaluate(dialog=>{const box=selector=>dialog.querySelector(selector).getBoundingClientRect();const close=dialog.querySelector('.dialog-close');return {currency:box('[aria-labelledby="settings-currency-label"]').bottom,slot:box('.agent-status-slot').top,slotBottom:box('.agent-status-slot').bottom,footer:box('.dialog-footer').top,limits:box('.retention-settings').top,tip:getComputedStyle(close,'::after').opacity,scroll:dialog.querySelector('.settings-body').scrollTop};});
      assert.ok(opened.currency<opened.slot&&opened.slot<opened.limits,'Agent status follows name and currency, before Time limits');
      assert.ok(opened.slotBottom<=opened.footer,`Agent status is visible without scrolling (${opened.slotBottom} > ${opened.footer})`);assert.equal(opened.scroll,0);
      assert.equal(opened.tip,'0','The Close (Esc) tooltip is hidden when Settings opens');
      // A-2 N1: Ready is a line at the top of its reserved slot, not an empty-looking box.
      const ready=await page.locator('.agent-status-slot').evaluate(slot=>{const style=getComputedStyle(slot),line=slot.querySelector('.agent-ready').getBoundingClientRect(),box=slot.getBoundingClientRect();return {fill:style.backgroundColor,border:style.borderTopColor,offset:line.top-box.top};});
      assert.match(ready.fill,/rgba\(0, 0, 0, 0\)|transparent/,'Ready has no panel fill');assert.match(ready.border,/rgba\(0, 0, 0, 0\)|transparent/,'Ready has no panel border');assert.ok(ready.offset<=14,`Ready sits at the slot's top (${ready.offset})`);
      const opposite=theme==='light'?'dark':'light';
      await page.getByRole('dialog').getByRole('button',{name:(opposite==='light'?'Light':'Dark')+' appearance',exact:true}).click();await expect(page.locator('html')).toHaveAttribute('data-theme',opposite);
      await page.getByRole('dialog').getByRole('button',{name:(theme==='light'?'Light':'Dark')+' appearance',exact:true}).click();await expect(page.locator('html')).toHaveAttribute('data-theme',theme);
      await expect(page.getByRole('dialog')).toContainText('New accounts start in this currency. Changing it doesn’t change existing accounts.');
      if(persona==='sam')await expect(page.locator('[data-existing-currencies]')).toHaveText('Bramble Current and Bramble Card kept GBP.');
      await overflow('Settings');await snap('settings-'+persona);
      // Tab order and focus containment use the real dialog, including reverse wrap.
      await page.getByLabel('Name',{exact:true}).focus();
      await page.keyboard.press('Tab');await expect(page.getByLabel('Main currency',{exact:true})).toBeFocused();
      await page.keyboard.press('Shift+Tab');await expect(page.getByLabel('Name',{exact:true})).toBeFocused();
      for(let i=0;i<12;i++){await page.keyboard.press('Tab');assert.ok(await page.getByRole('dialog').evaluate(dialog=>dialog.contains(document.activeElement)));}
      await closeDialog();await expect(identity()).toBeFocused();
      for(const narrow of [320,width]){
        await page.setViewportSize({width:narrow,height});await openSettings();await overflow('Settings at '+narrow);if(narrow===320)await snap('settings-320');await closeDialog();
      }
      record('Settings controls, keyboard order, focus return and 320px fit');
      await timed('runUpdateNotice',()=>runUpdateNotice({page,base,expect,go,snap,overflow,record}));
      if(theme==='light' && width===1440) {
        const settingsStarted=performance.now(), settingsBefore=commits();
        const changedName=initialManifest.name+' Test';
        const nextCurrency=initialManifest.defaultCurrency==='GBP'?'USD':'GBP';
        await openSettings();await page.getByLabel('Name',{exact:true}).fill(changedName);await page.getByLabel('Main currency',{exact:true}).selectOption(nextCurrency);
        await page.getByRole('button',{name:'Save changes',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0,{timeout:120000});
        const saved=JSON.parse(await readFile(join(ledger,'ledger.json'),'utf8'));
        assert.equal(saved.name,changedName);assert.equal(saved.defaultCurrency,nextCurrency);
        assert.equal(await readFile(join(ledger,'data/accounts.json'),'utf8'),initialAccounts);
        assert.equal(await readFile(join(ledger,'data/expenses/2026.json'),'utf8'),originalExpenses);
        assert.match(execFileSync('jj',['-R',ledger,'log','-r','@-','--no-graph','-T','description'],{env:stage.env,encoding:'utf8'}),/Change default currency/);
        assert.equal(execFileSync('jj',['-R',ledger,'diff','--summary'],{env:stage.env,encoding:'utf8'}).trim(),'');
        await openSettings();await page.getByLabel('Name',{exact:true}).fill(initialManifest.name);await page.getByLabel('Main currency',{exact:true}).selectOption(initialManifest.defaultCurrency);await page.getByRole('button',{name:'Save changes',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0,{timeout:120000});
        console.log('SUITE_TIMING '+JSON.stringify({persona,engine,theme,width,name:'settings',seconds:(performance.now()-settingsStarted)/1000,commits:commits()-settingsBefore}));
        record('Settings name and main currency commit, preserve existing accounts and entries, and restore');
      }
      // All routes render with no console/page errors, no document overflow, both themes and widths.
      for(const route of routes) {
        await go(route+'/');await overflow(route);
        await page.setViewportSize({width:320,height});await overflow(route+' at 320');await page.setViewportSize(size);
        if(persona==='empty' && ['explorer','atlas','journal','forecast'].includes(route)) {
          await expect(page.locator('.viewing-empty')).toBeVisible();
          await expect(page.locator('.content svg[role="img"]')).toHaveCount(0);
          await snap(route+'-empty');
        }
        if(persona==='empty' && ['review','account-summary'].includes(route))await snap(route==='review'?'review-empty':'accounts-no-balances');
        if(persona==='empty' && route==='atlas'){const icons=await page.evaluate(()=>[document.querySelector('.viewing-empty > .icon')?.innerHTML,document.querySelector('a[href$="atlas/"] .icon')?.innerHTML,document.querySelector('a[href$="explorer/"] .icon')?.innerHTML]);assert.ok(icons[1],'The Atlas navigation icon is present');assert.equal(icons[0],icons[1],'Atlas’s empty state uses the Atlas icon');assert.notEqual(icons[0],icons[2]);}
        if(persona==='empty' && route==='account-summary') {
          // Grade A-2: an empty ledger's Accounts offers opening balances, opening the first run's accounts step.
          const setup=page.locator('.opening-invitation').getByRole('link',{name:'Set up opening balances',exact:true});
          await expect(setup).toBeVisible();await expect(page.locator('.opening-invitation').getByRole('link',{name:'Add your first expense',exact:true})).toBeVisible();
          await setup.click();await expect(page.getByRole('heading',{name:'Your accounts, as of today',exact:true})).toBeVisible();
          assert.ok(page.url().includes('setup=today'),'The setup step is in the address');
          const declared=Object.values(JSON.parse(initialAccounts).accounts).filter(meta=>meta.kind&&meta.currency).length;
          await expect(page.locator('.setup-account')).toHaveCount(declared);await expect(page.locator('.setup-account .account-name').first()).toHaveAttribute('readonly','');
          await page.locator('.setup-account .account-balance').first().fill('75');await page.locator('.setup-account .account-balance').first().blur();
          await expect(page.locator('.setup-account .account-balance').first()).toHaveValue('75.00');
          await overflow('opening balances from Accounts');await snap('accounts-opening-setup');
          await page.goBack();await expect(page.getByRole('heading',{name:'Accounts',exact:true})).toBeVisible();await expect(setup).toBeVisible();
          record('empty Accounts routes to opening balances and back without a commit');
        }
        await emptyTargets();
      }
      record('all nine routes render at 320px and target viewport without overflow or console errors');
      // November is empty in every source persona, even though data ends in October.
      const stale=JSON.parse(await readFile(join(stage.dash,'app/data/dashboard.json'),'utf8'));
      const currentMonth=stage.env.DASHBOARD_TODAY.slice(0,7);
      stale.meta.defaultMonth='2026-10';stale.meta.today='2026-10-31';
      stale.months=stale.months.filter(row=>row.month!==currentMonth);
      const oldData=route=>route.fulfill({status:200,contentType:'application/gzip',body:gzipSync(JSON.stringify(stale))});
      await page.route('**/dashboard.json.gz',oldData);
      await go('explorer/');
      await expect(page.getByText('Nothing recorded this month yet',{exact:true})).toBeVisible();
      await expect(page.locator('.metrics,.calendar,.currency-series')).toHaveCount(0);
      await snap('explorer-current-empty');
      await page.unroute('**/dashboard.json.gz',oldData);
      await page.getByRole('button',{name:'Previous month',exact:true}).click();
      await expect(page).toHaveURL(/month=2026-10/);
      if(persona!=='empty')await expect(page.locator('.entries-table tbody tr').first()).toBeVisible();
      await page.getByRole('button',{name:'Next month',exact:true}).click();
      await expect(page.getByText('Nothing recorded this month yet',{exact:true})).toBeVisible();
      await go('forecast/');
      if(persona!=='empty') {
        for(const list of await page.locator('.forecast-items').all()) {
          await expect(list.locator('.estimate')).toHaveCount(1);
          await expect(list.locator('li').last()).toHaveClass('estimate');
          await expect(list.locator('.estimate .row-sub')).toContainText('The whole month');
          await expect(list.locator('.estimate .row-sub')).toContainText('a month');
          await expect(list.locator('.estimate .row-sub')).toHaveCSS('white-space','normal');
        }
        await snap('forecast-variable-whole-month');
      }
      record('empty current month opens by default, previous month is one step back, Forecast has one final variable row per currency');
      // Real gate checks run against the fake CLI; GET status never triggers them.
      for(const state of ['signed out','paused','unavailable']) {
        const status=await control('gate',{state});assert.equal(status.state,state);
        await go('add/');await refresh();if(state==='unavailable')await expect(page.locator('.agent-notice')).toHaveCount(0);else {await expect(page.locator('.agent-notice')).toHaveAttribute('data-agent-state',state);await expect(page.locator('.agent-notice')).toHaveCount(1);}
        await page.locator('#expense-words').fill('Paper Kite Market, 12, paid with cash');
        const save=page.getByRole('button',{name:'Continue',exact:true});
        if(state==='unavailable')await expect(save).toBeEnabled();else await expect(save).toBeDisabled();
        await expect(page.locator('#expense-words')).toBeEnabled();await expect(page.getByRole('button',{name:'Upload',exact:true})).toBeEnabled();
        if(state==='signed out'){await expect(page.locator('.agent-notice code')).toHaveText('make agent-login');await expect(page.locator('.agent-notice strong')).toHaveText('The agent isn’t signed in.');}
        if(state==='paused'){await expect(page.locator('.agent-notice')).toContainText('Statements read by the built-in reader still work');await expect(page.locator('.agent-notice strong')).toHaveText('Add is paused.');
          // The gate's diagnostic stays behind Check details.
          await expect(page.locator('.agent-notice details summary')).toHaveText('Check details');await expect(page.locator('.agent-notice details')).not.toHaveAttribute('open','');await expect(page.locator('.agent-notice details > p')).toBeHidden();}
        // The save bar never says it is ready while the agent keeps Continue disabled.
        if(state!=='unavailable'){await expect(page.locator('.add-savebar strong')).not.toHaveText('Ready to continue');await expect(page.locator('.add-savebar strong')).toHaveText(state==='paused'?'Add is paused':'Sign in to continue');}
        const addTitle=state==='unavailable'?null:await page.locator('.agent-notice strong').textContent();
        if(state==='unavailable')await expect(page.locator('.agent-notice')).toHaveCount(0);
        await overflow('notice '+state);if(persona!=='empty')await snap('notice-add-'+persona+'-'+state.replace(' ','-'));
        await openSettings();await expect(page.locator('.settings-body .agent-notice')).toHaveAttribute('data-agent-state',state);await overflow('Settings '+state);
        if(addTitle)await expect(page.locator('.settings-body .agent-notice strong')).toHaveText(addTitle);
        // The status slot keeps its reserved height: this state fits inside it, so its arrival moved nothing.
        const slot=await page.locator('.agent-status-slot').evaluate(node=>({height:node.getBoundingClientRect().height,min:parseFloat(getComputedStyle(node).minHeight),scroll:node.scrollHeight,client:node.clientHeight}));
        assert.ok(Math.abs(slot.height-slot.min)<1&&slot.scroll<=slot.client+1,`Agent status ${state} fits its reserved slot ${JSON.stringify(slot)}`);
        if(persona==='jordan')await snap('settings-'+state.replace(' ','-'));
        await closeDialog();
        await go('queue/'+(seeded.ids.length?'?draft='+seeded.ids[0]:''));await refresh();
        await expect(page.locator('.agent-notice')).toHaveCount(1);
        if(addTitle)await expect(page.locator('.agent-notice strong')).toHaveText(addTitle);
        if(seeded.ids.length){
          const answer=page.locator('.draft-question select');
          await answer.selectOption({index:1});await expect(answer).toBeEnabled();
          const send=page.getByRole('button',{name:'Send answer',exact:true});
          if(state==='unavailable')await expect(send).toBeEnabled();else await expect(send).toBeDisabled();
        }
        if(persona==='jordan')await snap('notice-drafts-'+state.replace(' ','-'));
        if(persona==='asha' || persona==='jordan') {
          const failed=seeded.ids[persona==='asha'?1:2];
          await go('queue/?draft='+failed);await refresh();
          const retry=page.getByRole('button',{name:'Retry',exact:true});
          if(state==='unavailable')await expect(retry).toBeEnabled();else await expect(retry).toBeDisabled();
        }
        await go('statements/');await expect(page).toHaveURL(base+'add/');await expect(page.getByLabel('Upload files',{exact:true})).toBeEnabled();
      }
      record('fake-driven signed out, paused and unavailable notices and submission/retry/answer gating; statement selection remains usable');
      if(theme==='light' && width===1440) {
        await control('gate',{state:'ready',check:false});
        await go('add/');await refresh();
        await expect(page.locator('.agent-notice')).toHaveCount(0);
        await page.locator('#expense-words').fill('Paper Kite Market, 12, paid with cash');
        await page.getByRole('button',{name:'Continue',exact:true}).click();
        await expect.poll(async()=>{const rows=await (await fetch(base+'api/drafts')).json();return rows.some(row=>row.text==='Paper Kite Market, 12, paid with cash' && row.state==='question');},{timeout:20000}).toBe(true);
        assert.equal((await (await fetch(base+'api/agent-status')).json()).state,'ready');
        assert.ok((await readFile(join(stage.root,'fake.calls'),'utf8')).includes('stdin_closed'));
        record('an unavailable check retries on the next real draft through the offline fake agent');
      }
      await control('gate',{state:'ready'});
      await resetDrafts({persona,empty:true});
      // One shared word only suggests a persona's usual payment, with no primary button; choosing it opens the
      // card, whose Add as usual is then the page's one primary action (grade B-2 N1, N2).
      const weak={asha:['Paper Kite Market, 12, paid with cash','Paper Lantern subscription'],jordan:['Birch rent 100 USD','October rent']}[persona];
      if(weak) {
        await go('add/');await refresh();
        const words=page.locator('#expense-words'),guess=page.getByRole('complementary',{name:'Usual payment'}),save=page.locator('.add-savebar .button'),barTitle=page.locator('.add-savebar strong');
        const idle=(await words.boundingBox()).height;
        await words.fill(weak[0]);await expect(guess).toContainText('Is this your '+weak[1]+'?');await expect(save).toBeEnabled();
        await expect(guess.locator('.primary')).toHaveCount(0);await expect(page.getByRole('button',{name:'Add as usual',exact:true})).toHaveCount(0);
        await expect(save).toHaveText('Continue');await expect(save).toHaveClass(/\bprimary\b/);await expect(barTitle).toHaveText('Ready to continue');
        await page.mouse.move(1,1);await snap('usual-suggestion');
        await guess.getByRole('button',{name:'Show usual payment',exact:true}).click();await expect(guess.getByRole('heading',{name:weak[1],exact:true})).toBeVisible();
        await expect(guess.getByRole('button',{name:'Add as usual',exact:true})).toBeEnabled();await expect(save).toBeEnabled();
        await expect(save).toHaveText('Send to the agent instead');await expect(save).not.toHaveClass(/\bprimary\b/);await expect(barTitle).toHaveText('Add as usual above, or send it to the agent');
        assert.deepEqual(await page.locator('.add-page .button.primary').filter({visible:true}).allInnerTexts(),['Add as usual'],'Add as usual is the only primary button beside the card');
        await expect(page.locator('.expense-interpretation')).toHaveCount(0);
        if(width>1000)assert.ok((await words.boundingBox()).height<=idle+1,'The card does not stretch the words box');
        const sizes=await guess.evaluate(node=>[...node.querySelectorAll('.rule-match-reason,.live-hint')].map(n=>getComputedStyle(n).fontSize));
        assert.equal(new Set(sizes).size,1,'Card hints share one type size: '+sizes.join(', '));
        await page.mouse.move(1,1);await snap('usual-chosen');
        await words.fill('');await expect(guess).toHaveCount(0);await expect(save).toHaveText('Continue');
        record('one shared word suggests a usual payment quietly; the chosen card keeps Add as usual as the one primary action');
        await resetDrafts({persona,empty:true}); // Typing autosaved an editing draft; the empty Queue checks follow.
      }
      await go('queue/');await refresh();await expect(page.locator('.agent-notice')).toHaveCount(0);
      await expect(page.getByText('No items waiting.',{exact:true})).toBeVisible();
      await expect(page.locator('.drafts-page .viewing-empty > .icon')).toHaveCount(1);await expect(page.locator('.drafts-page .viewing-empty').getByRole('link',{name:'Add an expense',exact:true})).toBeVisible();
      await expect(page.locator('.drafts-page .page-heading p')).not.toContainText('0 need');
      await expect(page.locator('.sidebar a[href$="queue/"] .nav-count,.phone-live-nav a[href$="queue/"] .nav-count')).toHaveCount(0);
      if(persona==='empty')await snap('drafts-empty');
      record('ready has no banner, and no pending work has no Drafts badge');
      // Existing and opening-only account currency dialogs use the real endpoints.
      const metadata=JSON.parse(initialAccounts).accounts;
      const fixed=persona==='asha'?'Assets:Bank:Saffron Reserve':persona==='sam'?'Assets:Bank:Bramble Current':persona==='jordan'?'Assets:Bank:Alder Checking':null;
      if(fixed) {
        await go('account-summary/?account='+encodeURIComponent(fixed));
        await page.getByRole('button',{name:'Account details',exact:true}).click();
        await expect(page.locator('[data-fixed-currency]')).toHaveText(metadata[fixed].currency);
        await expect(page.getByRole('dialog').getByRole('combobox')).toHaveCount(0);
        await expect(page.getByRole('dialog')).toContainText('so its currency is fixed.');
        // C20: Name, Kind and Currency are spaced evenly.
        const gaps=await page.getByRole('dialog').evaluate(dialog=>{const terms=[...dialog.querySelectorAll('.account-details-facts dt')],values=[...dialog.querySelectorAll('.account-details-facts dd')];return terms.slice(1).map((term,i)=>term.getBoundingClientRect().top-values[i].getBoundingClientRect().bottom);});
        assert.equal(gaps.length,2);assert.ok(Math.abs(gaps[0]-gaps[1])<=2,`Account facts share one rhythm (${gaps})`);
        assert.doesNotMatch(await page.getByRole('dialog').innerText(),/transaction [0-9a-f]{16}|Assets:|Liabilities:/);
        assert.equal(await page.locator('.dialog-close').evaluate(node=>getComputedStyle(node,'::after').opacity),'0');
        await overflow('fixed currency dialog');if(persona==='sam')await snap('account-fixed');
        await page.setViewportSize({width:320,height});await overflow('fixed currency at 320');await page.setViewportSize(size);await closeDialog();
      }
      if(persona==='jordan') {
        const accountStarted=performance.now(),accountBefore=commits();
        await go('account-summary/?account='+encodeURIComponent('Assets:Bank:Meadow Savings')+'|USD');
        await page.getByRole('button',{name:'Account details',exact:true}).click();
        await expect(page.getByLabel('Currency',{exact:true})).toHaveValue('USD');
        await page.getByLabel('Currency',{exact:true}).selectOption('EUR');
        const opening=JSON.parse(originalExpenses).flatMap(row=>row.postings).filter(post=>post.account==='Assets:Bank:Meadow Savings').reduce((sum,post)=>sum+Number(post.amount),0);
        await expect(page.getByRole('dialog')).toContainText('Opening balance: '+money(opening,'EUR'));
        await overflow('changeable account dialog');await snap('account-changeable');
        await page.setViewportSize({width:320,height});await overflow('changeable currency at 320');await page.setViewportSize(size);
        if(theme==='light' && width===1440) {
          await page.getByRole('button',{name:'Save account',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0,{timeout:120000});
          const saved=JSON.parse(await readFile(join(ledger,'data/accounts.json'),'utf8'));
          assert.equal(saved.accounts['Assets:Bank:Meadow Savings'].currency,'EUR');
          const body={account:'Assets:Bank:Meadow Savings',currency:'USD'};
          const response=await fetch(base+'api/accounts/currency',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});assert.ok(response.ok,await response.text());
          await page.reload();
        } else await closeDialog();
        console.log('SUITE_TIMING '+JSON.stringify({persona,engine,theme,width,name:'account-currency',seconds:(performance.now()-accountStarted)/1000,commits:commits()-accountBefore}));
      }
      record('fixed currency is plain text with cause; opening-only currency relabels and commits without conversion');
      if(persona==='sam') {
        for(const [month,values] of [['06',{GBP:1846.2}],['07',{GBP:892.4,EUR:1268.6}],['08',{EUR:2034.8}]]) {
          await go('explorer/?month=2026-'+month);
          for(const [currency,value] of Object.entries(values))await expect(page.locator('.metrics .metric').first()).toContainText(money(value,currency));
          assert.equal(await page.locator('.trend-currency[data-currency="GBP"] svg').count(),1);
          assert.equal(await page.locator('.trend-currency[data-currency="EUR"] svg').count(),1);
          for(const chart of await page.locator('.trend-currency').all())assert.equal(await chart.locator('svg').count(),1);
          assert.equal(await page.locator('.explorer-aside .calendar').count(),Object.keys(values).length);
          await expect(page.locator('.entries-table')).not.toContainText('Opening balance');
          if(month==='07') {
            await expect(page.locator('.mixed-category').filter({hasText:'Food'})).toContainText('GBP');
            await expect(page.locator('.mixed-category').filter({hasText:'Food'})).toContainText('EUR');
            const built=JSON.parse(await readFile(join(stage.dash,'app/data/dashboard.json'),'utf8'));
            for(const currency of ['GBP','EUR']) {
              const row=built.months.find(row=>row.month==='2026-'+month&&row.currency===currency);
              const bars=page.locator(`.category-currency[data-currency="${currency}"] .bar-previous`);
              await expect(bars).toHaveCount(row.comparison?row.categories.length:0);
              if(!row.comparison)await expect(page.locator(`.category-currency[data-currency="${currency}"] .delta`)).toHaveCount(0);
            }
            await expect(page.locator('.aside-section-head').filter({hasText:'Categories'})).toContainText('whole month');
            const colours=await page.locator('.trend-currency .trend-line').evaluateAll(nodes=>nodes.map(node=>getComputedStyle(node).stroke));
            assert.equal(new Set(colours).size,2);
            for(const currency of ['GBP','EUR'])assert.equal(await page.locator(`.category-currency[data-currency="${currency}"] i`).first().evaluate(node=>getComputedStyle(node).backgroundColor),await page.locator(`.trend-currency[data-currency="${currency}"] .trend-line`).first().evaluate(node=>getComputedStyle(node).stroke));
          } else await expect(page.locator('.mixed-categories')).toHaveCount(0);
          if(width<760) {
            assert.ok((await page.locator('.entries-panel').boundingBox()).width>width*0.85,'Explorer entries use the full phone content width');
            const checkRows=async()=>{
              const rows=await page.locator('.entries-table tr.interactive').evaluateAll(nodes=>nodes.map(node=>{
                const date=node.querySelector('td.date'),description=node.querySelector('.col-description'),amount=node.querySelector('td.num'),payment=node.querySelector('td.col-payment');
                const boxes=[date,description,amount].map(cell=>{const b=cell.getBoundingClientRect();return {left:b.left,right:b.right,top:b.top,bottom:b.bottom};});
                return {boxes,paymentVisible:payment.getClientRects().length>0,metadata:node.querySelectorAll('.phone-entry-meta').length};
              }));
              assert.ok(rows.length>0);
              for(const row of rows) {
                assert.equal(row.paymentVisible,false,'Payment account appears only in merchant metadata on phones');
                assert.equal(row.metadata,1);
                const [date,description,amount]=row.boxes;
                assert.ok(date.right<=description.left+1 && description.right<=amount.left+1,'Date, description and amount sit beside each other');
                assert.ok(date.top<description.bottom && amount.top<description.bottom,'Date and amount share the description row');
              }
            };
            await checkRows();await page.setViewportSize({width:320,height});await checkRows();await overflow('Sam phone rows at 320');await page.setViewportSize(size);
          }
          await overflow('Sam '+month);await snap('explorer-'+({ '06':'june','07':'july','08':'august'}[month]));
        }
        // C11: the two-currency page is the one Accounts page: per-currency summaries by group, each account listed once, the day stepper.
        await go('account-summary/');const summary=page.locator('.currency-summary');
        await expect(summary).toContainText('GBP accounts');await expect(summary).toContainText('EUR accounts');
        await expect(summary).toContainText('£3,284.65');await expect(summary).toContainText('€6,558.80');await expect(summary).toContainText('€286.40 owed');
        await expect(page.locator('.currency-account-row')).toHaveCount(0);await expect(summary.locator('.account-row')).toHaveCount(0);
        for(const name of ['Birken Current','Bramble Current','Birken Card','Bramble Card'])await expect(page.locator('.account-list .account-row-name',{hasText:new RegExp('^'+name+'$')})).toHaveCount(1);
        await expect(page.locator('.month-stepper[aria-labelledby="as-of-label"]')).toBeVisible();await expect(page.locator('.page-header input[type="date"]:not(.date-input)')).toHaveCount(0);
        for(const row of await summary.locator('.currency-group-row').all())assert.ok(Math.abs((await row.boundingBox()).height-(width<760?44:36))<=1,'Currency summary rows are one line');
        for(const narrow of [320,width]){await page.setViewportSize({width:narrow,height});await overflow('two-currency Accounts at '+narrow);}
        await page.setViewportSize(size);await page.mouse.move(1,1);await snap('accounts-populated');
        await summary.getByRole('button',{name:'Credit cards'}).first().click();await expect(page.locator('.group-view')).toBeVisible();assert.ok(page.url().includes('account=Credit+cards')||page.url().includes('account=Credit%20cards'),'A currency summary row opens its group');
        await page.goBack();await expect(summary).toBeVisible();record('Sam June/July/August native amounts, separate chart axes and native net worth minus debt');
      }
      if(persona==='empty') {
        const openingStarted=performance.now(),openingBefore=commits();
        await control('openings',{enabled:true});await go('account-summary/');
        await expect(page.getByText('Only opening balances so far.',{exact:true})).toBeVisible();await expect(page.locator('.single-currency')).toContainText('$6,152.75');
        await expect(page.locator('.single-currency > .panel')).toHaveCount(1);await expect(page.locator('.net-worth')).toHaveCount(0);
        await expect(page.locator('.single-currency h2')).toHaveText('Net balance');
        await expect(page.locator('.single-currency')).not.toContainText('USD accounts');
        await expect(page.locator('.single-currency small').last()).toHaveText('Card · owed, including unbilled charges');
        await expect(page.locator('.currency-groups')).toContainText('$337.50 owed');await expect(page.locator('.content svg[role="img"]')).toHaveCount(0);
        if(width<760)assert.ok((await page.locator('.opening-invitation .button.primary').boundingBox()).height>=44,'Opening-account invitation has a 44px phone touch target');
        await snap('accounts-empty');
        await page.getByRole('button',{name:'Meadow Savings',exact:true}).click();await expect(page.getByLabel('Currency',{exact:true})).toHaveValue('USD');await page.getByLabel('Currency',{exact:true}).selectOption('EUR');await expect(page.getByRole('dialog')).toContainText('€4,650.00');await snap('account-opening-changeable');await closeDialog();
        await go('explorer/');await expect(page.getByText('Your spending starts here.',{exact:false})).toBeVisible();await expect(page.locator('.content svg[role="img"]')).toHaveCount(0);
        for(const view of ['atlas','forecast']){await go(view+'/');await expect(page.getByText(view==='atlas'?'Nothing recorded this month yet':'No expenses yet.',{exact:true})).toBeVisible();await expect(page.locator('.content svg[role="img"]')).toHaveCount(0);}
        // Empty Forecast uses the standard header: the title first, with Overview/Rules as its tools.
        assert.equal(await page.locator('.content .page > :first-child').evaluate(node=>node.className),'page-header');
        await expect(page.locator('.page-header .page-tools .forecast-sections')).toHaveCount(1);await expect(page.locator('.viewing-empty > .icon')).toHaveCount(1);
        await control('openings',{enabled:false});console.log('SUITE_TIMING '+JSON.stringify({persona,engine,theme,width,name:'opening-balances',seconds:(performance.now()-openingStarted)/1000,commits:commits()-openingBefore}));record('opening-only Accounts subtracts debt with no invented history; Explorer invites the first expense');
      }
      if(persona==='asha') {
        await go('account-summary/?account=Investments');
        await expect(page.locator('.holdings-table')).toBeVisible();await expect(page.locator('.fund-stats').first()).toContainText('Unrealised gain');
        await expect(page.getByRole('region',{name:'Investment holdings'})).toBeVisible();
        await expect(page.locator('.holdings-table tbody')).toContainText('5.000');
        await snap('investments-portfolio');
        await expect(page.locator('.value-chart').first()).toBeVisible();
        // Accessible chart keyboard inspection and real holdings drill-down.
        const plot=page.getByRole('slider',{name:'Mutual fund values by NAV date'}).first();
        await plot.focus();await page.keyboard.press('End');await expect(plot).toHaveAttribute('aria-valuenow',await plot.getAttribute('aria-valuemax'));await expect(page.locator('.chart-tooltip').first()).toBeVisible();await page.keyboard.press('Home');await expect(plot).toHaveAttribute('aria-valuenow','1');await page.keyboard.press('ArrowRight');await page.keyboard.press('Escape');await expect(plot).toBeFocused();await expect(page.locator('.chart-tooltip')).toHaveCount(0);
        await page.locator('.holdings-table a.row-open').filter({hasText:'Demo Index'}).click();
        await expect(page.locator('.accounts')).toHaveAttribute('data-portfolio','false');
        await expect(page.locator('.fund-stats')).toContainText('Units');
        await snap('investment-activity');

        await page.goBack();await expect(page.locator('.holdings-table')).toBeVisible();
        await overflow('Asha investments');record('Asha holdings, cost/value/gain, chart keyboard inspection, account drill-down and history return');
      }
      // BROWSER_FOCUS=shell stops here: navigation, Settings, notices, empty views and account dialogs.
      // BROWSER_FOCUS=area-c adds the looking-back views and the suites that open them.
      if(process.env.BROWSER_FOCUS==='area-c') {
      await timed('runViewCaptures',()=>runViewCaptures({page,persona,width,height,expect,go,snap,overflow,record}));
      await timed('runFinish',()=>runFinish({page,base,persona,engine,theme,width,height,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      await timed('runRules',()=>runRules({page,base,persona,engine,theme,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      await timed('runCorrections',()=>runCorrections({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      await timed('runConfirmedBalances',()=>runConfirmedBalances({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      await timed('runRetention',()=>runRetention({page,base,persona,width,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      await timed('runAccountGroups',()=>runAccountGroups({page,base,persona,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,money}));
      await timed('runVarying',()=>runVarying({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      } else if(process.env.BROWSER_FOCUS!=='shell') {
      await timed('runViewCaptures',()=>runViewCaptures({page,persona,width,height,expect,go,snap,overflow,record}));
      await timed('runReview',()=>runReview({browser,context,page,base,persona,engine,theme,width,height,stage,expect,control,go,overflow,snap,record,quiesce}));
      await timed('runWorkflows',()=>runWorkflows({page,base,persona,engine,theme,width,height,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      await timed('runFinish',()=>runFinish({page,base,persona,engine,theme,width,height,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      await timed('runReaderWorkflows',()=>runReaderWorkflows({page,base,persona,engine,theme,width,stage,expect,control,go,overflow,snap,record,quiesce}));
      await timed('runRules',()=>runRules({page,base,persona,engine,theme,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      await runAttachments({page,base,persona,expect,control,go,record,quiesce,resetDrafts,snap});
        await timed('runAdd',()=>runAdd({page,base,persona,engine,theme,width,height,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      await timed('runCorrections',()=>runCorrections({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      await timed('runConfirmedBalances',()=>runConfirmedBalances({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      await timed('runLedgerCheck',()=>runLedgerCheck({page,base,persona,engine,theme,width,expect,control,go,refresh,overflow,snap,record,quiesce}));
      await timed('runAccountCreate',()=>runAccountCreate({page,base,persona,expect,control,go,quiesce,snap,record}));
      await timed('runDirectStates',()=>runDirectStates({page,base,persona,stage,expect,control,go,quiesce,snap,record,errors}));
      await timed('runDataDeparture',()=>runDataDeparture({page,base,expect,control,go,record,snap}));
      await timed('runImmediateActions',()=>runImmediateActions({page,base,persona,expect,control,go,quiesce,snap,record,errors}));
      await timed('runLightSave',()=>runLightSave({page,base,persona,expect,control,go,refresh,record,quiesce,snap}));
      await timed('runRetention',()=>runRetention({page,base,persona,width,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      await timed('runRetentionTiming',()=>runRetentionTiming({page,base,persona,expect,control,go,refresh,snap,record,quiesce,resetDrafts}));
      await timed('runViewTiming',()=>runViewTiming({page,base,persona,expect,control,go,refresh,record,quiesce,resetDrafts}));
      await timed('runAccountGroups',()=>runAccountGroups({page,base,persona,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,money}));
      await timed('runVarying',()=>runVarying({page,base,persona,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts}));
      await timed('runFirstRun',()=>runFirstRun({page,base,persona,width,expect,control,overflow,snap,record,quiesce,consoleErrors:errors}));
      console.log('ACTION_COVERAGE_COMPLETE '+JSON.stringify(assertActionCoverage(page)));record('every declared action has held failure, exact committed Retry, named usable control, geometry and focus evidence');
      }
      await directObserver.close();
      assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
      record('console and page errors');
      await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});
      await context.close();
      await checkpoint();
    } catch(error) {
      await page.screenshot({path:join(stage.root,`failure-${engine}-${theme}-${width}.png`),fullPage:true}).catch(()=>{});
      await context.tracing.stop({path:join(stage.root,`failure-${engine}-${theme}-${width}.zip`)}).catch(()=>{});
      await context.close();throw new Error(`${persona} ${engine} ${theme} ${width}: ${error.stack}`,{cause:error});
    }
  }
}
