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

const routes=['add','drafts','review','explorer','atlas','journal','forecast','account-summary','statements'];
const labels=['Add','Drafts','Review','Explorer','Atlas','Journal','Forecast','Accounts'];
const sizes=[{width:1440,height:900},{width:412,height:915}];
const locale={asha:'en-IN',jordan:'en-US',sam:'en-GB',empty:'en-US'};
const zone={asha:'Asia/Kolkata',jordan:'America/Chicago',sam:'Europe/Berlin',empty:'America/Chicago'};

export async function runSuite({browser,engine,stage,expect,results,capture,checkpoint=async()=>{}}) {
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
  for(const theme of ['light','dark'])for(const size of sizes) {
    const {width,height}=size;
    const context=await browser.newContext({viewport:size,colorScheme:theme,locale:locale[persona],timezoneId:zone[persona],reducedMotion:'reduce',acceptDownloads:true});
    await context.addInitScript(value=>{if(location.protocol==='http:'||location.protocol==='https:')localStorage.setItem('ledger-appearance',value);},theme);
    await context.tracing.start({screenshots:true,snapshots:true});
    const page=await context.newPage(),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
    await page.route('**/*',route=>{const url=route.request().url();return url.startsWith(base) || url.startsWith('blob:') || url.startsWith('data:')?route.continue():route.abort('blockedbyclient');});
    const record=(name)=>{results.push({persona,engine,theme,width,test:name,pass:true});console.log(`  PASS ${persona} ${engine} ${theme} ${width}: ${name}`);};
    const snap=(state)=>capture(page,{state,theme,width,height});
    const go=async(route)=>{await page.waitForLoadState('networkidle');await page.goto(base+route);await page.locator('.content h1').first().waitFor();await expect(page.locator('.loading-state')).toHaveCount(0);await page.waitForLoadState('networkidle');};
    const overflow=async(label)=>{
      const value=await page.evaluate(()=>({width:innerWidth,html:document.documentElement.scrollWidth,body:document.body.scrollWidth}));
      assert.ok(value.html<=value.width+1 && value.body<=value.width+1,`${label}: horizontal overflow ${JSON.stringify(value)}`);
    };
    const refresh=async()=>{await page.evaluate(()=>window.dispatchEvent(new Event('drafts-changed')));};
    const quiesce=async({writesSettled=false}={})=>{
      if(page.url().startsWith(base)){
        // Fixture deletion must not manufacture a missing composer recovery ID.
        // Await real network completion, clear only the test composer's recovery
        // copy, then unload the app before deleting ignored server records.
        if(!writesSettled)await page.waitForLoadState('networkidle');
        await page.evaluate(()=>localStorage.removeItem('ledger-add-v1'));
        await page.goto('about:blank');
      }
    };
    const resetDrafts=async(body)=>{await quiesce();return control('drafts',body);};
    const identity=()=>page.locator('.identity:visible');
    const openSettings=async()=>{await identity().click();await expect(page.getByRole('dialog',{name:'Settings',exact:true})).toBeVisible();await expect(page.getByLabel('Name',{exact:true})).toBeFocused();};
    const closeDialog=async()=>{await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0);};
    try {
      if(process.env.BROWSER_FOCUS==='rules') {
        await runRules({page,base,persona,engine,theme,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts});
        assert.deepEqual(errors,[],'No browser console errors or uncaught page errors');
        await context.tracing.stop({path:join(stage.root,`${engine}-${theme}-${width}.zip`)});await context.close();await checkpoint();continue;
      }
      if(process.env.BROWSER_FOCUS==='finish') {
        await runFinish({page,base,persona,engine,theme,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts});
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
      const draftsLink=nav.locator('a[href$="drafts/"]');
      if(seeded.attention)await expect(draftsLink.locator('.nav-count')).toHaveText(new RegExp(`^(?:, )?${seeded.attention}(?: need attention)?$`));
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
        assert.deepEqual(await page.locator('.phone-live-nav > a,.phone-live-nav > button').evaluateAll(nodes=>nodes.map(node=>node.textContent.replace(/\d+/g,'').trim())),['Add','Drafts','Review','Views']);
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
      await overflow('navigation');await snap('nav-'+persona);record('navigation order, actionable badges and plain Review count');
      if(width<760)await page.keyboard.press('Escape');
      await page.keyboard.press('Tab');
      await openSettings();
      await expect(page.getByLabel('Main currency',{exact:true})).toBeEnabled();
      await expect(page.locator('.agent-ready')).toBeVisible();
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
        await page.setViewportSize({width:narrow,height});await openSettings();await overflow('Settings at '+narrow);await closeDialog();
      }
      record('Settings controls, keyboard order, focus return and 320px fit');
      if(theme==='light' && width===1440) {
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
        if(width<760)for(const action of await page.locator('.empty .button.primary').all())assert.ok((await action.boundingBox()).height>=44,'Empty-view primary actions have a 44px phone touch target');
      }
      record('all nine routes render at 320px and target viewport without overflow or console errors');
      // November is empty in every source persona, even though data ends in October.
      const stale=JSON.parse(await readFile(join(stage.dash,'app/data/dashboard.json'),'utf8'));
      const currentMonth=stage.env.DASHBOARD_TODAY.slice(0,7);
      stale.meta.defaultMonth='2026-10';stale.meta.today='2026-10-31';
      stale.months=stale.months.filter(row=>row.month!==currentMonth);
      const oldData=route=>route.fulfill({status:200,contentType:'application/gzip',body:gzipSync(JSON.stringify(stale))});
      await page.route('**/data/dashboard.json.gz',oldData);
      await go('explorer/');
      await expect(page.getByText('Nothing recorded this month yet',{exact:true})).toBeVisible();
      await expect(page.locator('.metrics,.calendar,.currency-series')).toHaveCount(0);
      await snap('explorer-current-empty');
      await page.unroute('**/data/dashboard.json.gz',oldData);
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
        await go('add/');await refresh();await expect(page.locator('.agent-notice')).toHaveAttribute('data-agent-state',state);
        await expect(page.locator('.agent-notice')).toHaveCount(1);
        await page.locator('#expense-words').fill('Paper Kite Market, 12, paid with cash');
        const save=page.getByRole('button',{name:'Send to agent',exact:true});
        if(state==='unavailable')await expect(save).toBeEnabled();else await expect(save).toBeDisabled();
        await expect(page.locator('#expense-words')).toBeEnabled();await expect(page.getByRole('button',{name:'Photos or files',exact:true})).toBeEnabled();
        if(state==='signed out')await expect(page.locator('.agent-notice code')).toHaveText('make agent-login');
        if(state==='paused'){await page.locator('.agent-reason summary').click();await expect(page.locator('.agent-reason p')).toBeVisible();await expect(page.locator('.agent-reason')).toContainText('exec_command');await page.locator('.agent-reason summary').click();}
        if(state==='unavailable')await expect(page.locator('.agent-notice')).toContainText('It will retry on the next draft.');
        await overflow('notice '+state);if(persona!=='empty')await snap('notice-add-'+persona+'-'+state.replace(' ','-'));
        await openSettings();await expect(page.locator('.settings-body .agent-notice')).toHaveAttribute('data-agent-state',state);await overflow('Settings '+state);
        if(persona==='jordan')await snap('settings-'+state.replace(' ','-'));
        await closeDialog();
        await go('drafts/'+(seeded.ids.length?'?draft='+seeded.ids[0]:''));await refresh();
        await expect(page.locator('.agent-notice')).toHaveCount(1);
        if(seeded.ids.length){
          const answer=page.locator('.draft-question select');
          await answer.selectOption({index:1});await expect(answer).toBeEnabled();
          const send=page.getByRole('button',{name:'Send answer',exact:true});
          if(state==='unavailable')await expect(send).toBeEnabled();else await expect(send).toBeDisabled();
        }
        if(persona==='jordan')await snap('notice-drafts-'+state.replace(' ','-'));
        if(persona==='asha' || persona==='jordan') {
          const failed=seeded.ids[persona==='asha'?1:2];
          await go('drafts/?draft='+failed);await refresh();
          const retry=page.getByRole('button',{name:'Retry',exact:true});
          if(state==='unavailable')await expect(retry).toBeEnabled();else await expect(retry).toBeDisabled();
        }
        await go('statements/');await expect(page).toHaveURL(base+'add/');await expect(page.getByLabel('Upload statement PDF')).toBeEnabled();
      }
      record('fake-driven signed out, paused and unavailable notices and submission/retry/answer gating; statement selection remains usable');
      if(theme==='light' && width===1440) {
        await control('gate',{state:'ready',check:false});
        await go('add/');await refresh();
        await expect(page.locator('.agent-notice')).toHaveAttribute('data-agent-state','unavailable');
        await page.locator('#expense-words').fill('Paper Kite Market, 12, paid with cash');
        await page.getByRole('button',{name:'Send to agent',exact:true}).click();
        await expect.poll(async()=>{const rows=await (await fetch(base+'api/drafts')).json();return rows.some(row=>row.text==='Paper Kite Market, 12, paid with cash' && row.state==='question');},{timeout:20000}).toBe(true);
        assert.equal((await (await fetch(base+'api/agent-status')).json()).state,'ready');
        assert.ok((await readFile(join(stage.root,'fake.calls'),'utf8')).includes('stdin_closed'));
        record('an unavailable check retries on the next real draft through the offline fake agent');
      }
      await control('gate',{state:'ready'});
      await resetDrafts({persona,empty:true});
      await go('drafts/');await refresh();await expect(page.locator('.agent-notice')).toHaveCount(0);
      await expect(page.getByText('No drafts waiting.',{exact:true})).toBeVisible();
      await expect(page.locator('.sidebar a[href$="drafts/"] .nav-count,.phone-live-nav a[href$="drafts/"] .nav-count')).toHaveCount(0);
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
        assert.doesNotMatch(await page.getByRole('dialog').innerText(),/transaction [0-9a-f]{16}|Assets:|Liabilities:/);
        assert.equal(await page.locator('.dialog-close').evaluate(node=>getComputedStyle(node,'::after').opacity),'0');
        await overflow('fixed currency dialog');if(persona==='sam')await snap('account-fixed');
        await page.setViewportSize({width:320,height});await overflow('fixed currency at 320');await page.setViewportSize(size);await closeDialog();
      }
      if(persona==='jordan') {
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
        await go('account-summary/');await expect(page.locator('.currency-groups')).toContainText('GBP accounts');await expect(page.locator('.currency-groups')).toContainText('EUR accounts');
        await expect(page.locator('.currency-groups')).toContainText('£3,284.65');await expect(page.locator('.currency-groups')).toContainText('€6,558.80');await expect(page.locator('.currency-groups')).toContainText('€286.40 owed');
        for(const narrow of [320,width]) {
          await page.setViewportSize({width:narrow,height});
          const headline=await page.locator('.net-worth').evaluate(node=>{
            const label=node.querySelector('h2'),total=node.querySelector('strong'),a=label.getBoundingClientRect(),b=total.getBoundingClientRect();
            return {label:label.textContent,labelBottom:a.bottom,totalTop:b.top,fontSize:parseFloat(getComputedStyle(total).fontSize)};
          });
          assert.equal(headline.label,'Net worth');assert.ok(headline.totalTop>=headline.labelBottom,'Net worth has a dedicated total below its label');assert.ok(headline.fontSize>=22,'Net worth retains headline emphasis');
          await overflow('Net worth at '+narrow);
        }
        await snap('accounts-populated');record('Sam June/July/August native amounts, separate chart axes and native net worth minus debt');
      }
      if(persona==='empty') {
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
        await control('openings',{enabled:false});record('opening-only Accounts subtracts debt with no invented history; Explorer invites the first expense');
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
      await runReview({browser,context,page,base,persona,engine,theme,width,height,stage,expect,control,go,overflow,snap,record,quiesce});
      await runWorkflows({page,base,persona,engine,theme,width,height,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts});
      await runFinish({page,base,persona,engine,theme,width,height,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts});
      await runReaderWorkflows({page,base,persona,engine,theme,width,stage,expect,control,go,overflow,snap,record,quiesce});
      await runRules({page,base,persona,engine,theme,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,resetDrafts});
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
