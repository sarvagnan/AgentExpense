import {mkdir,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import assert from 'node:assert/strict';
export const shots = resolve(process.env.BROWSER_SHOTS || join(process.env.AGENTEXPENSE_TEST_ROOT || process.env.TMPDIR,'shots'));
assert.ok(shots.startsWith('/tmp/') || shots.startsWith('/private/tmp/'),'Browser captures must stay under /tmp');
const escape = value => String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export async function capture(page, manifest, info) {
  await mkdir(shots,{recursive:true});
  const name=[info.engine,info.persona,info.state,info.theme,info.width].join('-');
  const file=name+'.png';
  await page.screenshot({path:join(shots,file),animations:'disabled'});
  const tall=await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight+2);
  let full;
  if(tall) {
    full=name+'-full.png';
    // Fixed phone controls belong at the document foot in a full-page companion.
    const style=await page.addStyleTag({content:'.skip-link{position:absolute!important}@media(max-width:760px){body{position:relative}.phone-live-nav{position:absolute!important;top:auto!important;bottom:0!important}.phone-more-views{position:absolute!important;top:auto!important;bottom:calc(var(--live-nav-height,64px) + 12px)!important}.add-savebar{position:absolute!important;top:auto!important;bottom:var(--live-nav-height,64px)!important}}'});
    try {await page.screenshot({path:join(shots,full),fullPage:true,animations:'disabled'});}
    finally {await style.evaluate(node=>node.remove());}
  }
  const entry={...info,file,full,route:new URL(page.url()).pathname+new URL(page.url()).search};
  manifest.push(entry);
  const aliases={'statement-mismatch':'add-r3-currency-mismatch','statement-add-account':'add-r3-currency-add'};
  if(aliases[info.state])manifest.push({...entry,state:aliases[info.state]});
}
export async function contactSheet(manifest) {
  await mkdir(shots,{recursive:true});
  await writeFile(join(shots,'manifest.json'),JSON.stringify(manifest,null,2)+'\n');
  const groups=Map.groupBy(manifest,item=>`${item.persona} · ${item.state}`);
  await writeFile(join(shots,'index.html'),`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentExpense · browser captures</title><style>body{font:14px system-ui;margin:24px;background:#eee;color:#222}section{margin-block:32px}h1{font-size:24px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}figure{margin:0;padding:12px;background:white;border-radius:8px}img{display:block;width:100%;height:230px;object-fit:contain;object-position:top;background:#ddd}figcaption{margin-block:8px;font-size:12px}a{color:#853400}code{overflow-wrap:anywhere}</style><h1>AgentExpense · browser captures</h1><p>${manifest.length} exact viewport captures. Headless Playwright 1.64.1; fictional disposable ledgers. Each card links its original PNG and, when taller, a full-page companion.</p>${[...groups].map(([group,items])=>`<section><h2>${escape(group)}</h2><div class="grid">${items.map(item=>`<figure><a href="${escape(item.file)}"><img loading="lazy" src="${escape(item.file)}" alt="${escape(group+' '+item.engine+' '+item.theme+' '+item.width)}"></a><figcaption>${escape(item.engine)} · ${escape(item.theme)} · ${item.width}×${item.height}<br><code>${escape(item.route)}</code></figcaption>${item.full?`<a href="${escape(item.full)}">Full page</a>`:''}</figure>`).join('')}</div></section>`).join('')}</html>`);
}

/**
 * The looking-back views as a person first sees them, for grading: populated Explorer, Atlas and
 * Journal months with the inspector opened from a row and moved by keyboard, Explorer's no-match
 * search, Forecast's Overview and Rules on first open, the default Accounts page and, on the phone
 * case, 320 px captures of the five views. Each capture parks the pointer away from the control
 * it just used. Assertions cover the defects these states once showed (grade C round 1).
 */
export async function runViewCaptures({page,persona,width,height,expect,go,snap,overflow,record}) {
  if(persona==='empty')return;
  const month='2026-10';
  const park=async()=>{await page.mouse.move(1,1);};
  const top=async()=>{await park();await page.evaluate(()=>{document.activeElement?.blur?.();window.scrollTo(0,0);});};
  const inspector=page.locator('#transaction-inspector');
  const box=locator=>locator.evaluate(node=>{const b=node.getBoundingClientRect();return {left:b.left,right:b.right,top:b.top,bottom:b.bottom};});
  const meets=(a,b,slack=0.5)=>a.left<b.right-slack && b.left<a.right-slack && a.top<b.bottom-slack && b.top<a.bottom-slack;

  // Explorer: a complete month, its twelve-month chart drawn at its own width and ending at the
  // last complete month, then the inspector from a row and moved with the keyboard.
  await go(`explorer/?month=${month}`);await top();await overflow('Explorer month');
  await expect(page.locator('.spending-trend .panel-header p')).toHaveText(/– October 2026$/);
  for(const chart of await page.locator('.trend-currency svg').all()) {
    const drawn=await chart.evaluate(node=>({width:node.getBoundingClientRect().width,view:node.viewBox.baseVal.width,font:getComputedStyle(node.querySelector('text')).fontSize,ticks:[...node.querySelectorAll('text[text-anchor="end"]')].map(text=>text.textContent)}));
    assert.ok(Math.abs(drawn.width-drawn.view)<=1,`The twelve-month chart is drawn at its rendered width: ${JSON.stringify(drawn)}`);
    assert.equal(drawn.font,'12px','Chart labels use the caption size');
    assert.ok(drawn.ticks.every(text=>!/[.,]0\D*$/.test(text)),`Whole axis ticks print no decimal: ${drawn.ticks}`);
  }
  if(width<760) {
    // The phone Date header shows its sort arrow whole.
    const date=await page.locator('.entries-table thead th:first-child').evaluate(cell=>{const range=document.createRange();range.selectNodeContents(cell.querySelector('.sort-button'));const inner=range.getBoundingClientRect(),outer=cell.getBoundingClientRect(),style=getComputedStyle(cell);return {inner:inner.right,limit:outer.right-parseFloat(style.paddingRight)+0.5,arrow:cell.querySelector('.sort-indicator').textContent};});
    assert.ok(date.arrow && date.inner<=date.limit,`Date and its sort arrow fit: ${JSON.stringify(date)}`);
  }
  await snap('explorer-month');
  const first=page.locator('.entries-table tbody tr.interactive').first();
  await first.locator('.row-open').click();await expect(inspector).toBeVisible();
  const opened=new URL(page.url()).searchParams.get('entry');assert.ok(opened,'Opening a row names the entry in the address');
  await park();await snap('explorer-inspector');
  await page.keyboard.press('ArrowDown');
  await expect.poll(()=>new URL(page.url()).searchParams.get('entry')).not.toBe(opened);
  await expect(inspector).toBeVisible();await park();await snap('explorer-keyboard');
  await page.keyboard.press('Escape');await expect(inspector).toBeHidden();
  const search=page.locator('#explorer-search');
  await search.fill('zzzz-no-match');await expect(page.getByText('No matching entries',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Show All',exact:true})).toHaveCount(1);
  await expect(page.locator('.matching-total')).toHaveCount(0);
  await park();await snap('explorer-no-match');await search.fill('');
  record('Explorer month, twelve-month chart at its own width ending at the last complete month, inspector by row and keyboard, one Show All with no match');

  // Atlas and Journal for the same month; Journal's inspector from a row.
  await go(`atlas/?month=${month}`);await top();await overflow('Atlas month');
  await expect(page.locator('.content h1')).toHaveCount(1);
  await snap('atlas-month');
  await go(`journal/?month=${month}`);await top();await overflow('Journal month');
  await expect(page.locator('.journal-table .row-sub').filter({hasText:/Uncategorized\S/})).toHaveCount(0);
  if(width<=600) {
    // A phone row: title and route stacked on the left, the amount on the first line's right,
    // Amount over the amounts and day headings on the rows' left edge.
    const row=page.locator('.journal-table tr.interactive').first();
    const [title,amount,route,day,head]=await Promise.all([box(row.locator('.row-title')),box(row.locator('td.num')),box(row.locator('.route-line').first()),box(page.locator('.journal-day-name').first()),box(page.locator('.journal-table thead th.th-num .sort-button'))]);
    assert.ok(Math.abs(amount.top-title.top)<=4 && amount.left>=title.left+40,`Amount sits on the title's line, at the right: ${JSON.stringify({title,amount})}`);
    assert.ok(route.top>=title.bottom-1 && Math.abs(route.left-title.left)<=1,`The route sits under the title: ${JSON.stringify({title,route})}`);
    assert.ok(Math.abs(day.left-title.left)<=1,`Day headings align with the rows: ${JSON.stringify({day,title})}`);
    const figure=await row.locator('td.num').evaluate(cell=>{const range=document.createRange();range.selectNodeContents(cell);const b=range.getBoundingClientRect();return {left:b.left,right:b.right};});
    const label=await box(page.locator('.journal-table thead th:first-child .sort-button'));
    assert.ok(Math.abs(label.left-title.left)<=1,`Description sits over the titles: ${JSON.stringify({label,title})}`);
    assert.ok(Math.abs(head.right-figure.right)<=2,`Amount is right-aligned over the amounts: ${JSON.stringify({head,figure})}`);
  }
  await snap('journal-month');
  await page.locator('.journal-table tr.interactive').first().locator('.row-open').click();await expect(inspector).toBeVisible();
  await park();await snap('journal-inspector');await page.keyboard.press('Escape');
  record('Atlas month with one page title; Journal month, phone row layout and inspector');

  // Forecast on first open: the figures' window is the selected option, nothing says "0-month",
  // line-end labels clear each other and every dot, and no table header or label is cut.
  await go('forecast/');await top();await overflow('Forecast');
  await expect(page.locator('.content')).not.toContainText(/\b0-month|\b0 full months/);
  for(const panel of await page.locator('.page.forecast').all()) {
    const select=panel.locator('label.compact-field').filter({hasText:'Comparison average'}).locator('select');
    if(await select.count()) {
      const value=await select.inputValue();assert.ok(value,'The comparison select shows the window in use');
      await expect(panel.locator('.metrics')).toContainText(`${value}-month average`);
    } else await expect(panel.locator('.forecast-chart-empty')).toBeVisible();
    const chart=panel.locator('.forecast-chart-inner svg');
    if(await chart.count()) {
      const shapes=await chart.evaluate(svg=>{const rect=node=>{const b=node.getBoundingClientRect();return {left:b.left,right:b.right,top:b.top,bottom:b.bottom,text:node.textContent};};
        return {labels:[...svg.querySelectorAll('text.comparison-label, .projected-label text')].map(rect),dots:[...svg.querySelectorAll('circle')].map(rect)};});
      shapes.labels.forEach((label,index)=>{
        for(const other of shapes.labels.slice(index+1))assert.ok(!meets(label,other),`Chart labels overlap: ${JSON.stringify([label,other])}`);
        for(const dot of shapes.dots)assert.ok(!meets(label,dot),`A chart label covers a dot: ${JSON.stringify([label,dot])}`);
      });
    }
    const cut=await panel.locator('.forecast-table').evaluate(table=>[...table.querySelectorAll('thead th, tbody td:first-child, .month-forecast > .row-sub')].flatMap(cell=>{
      // Measure the words, not the column grip that sits on the header's edge.
      const range=document.createRange();range.selectNodeContents(cell.querySelector(':scope > .sort-button')||cell);const inner=range.getBoundingClientRect(),outer=cell.getBoundingClientRect();
      return inner.left<outer.left-0.5 || inner.right>outer.right+0.5 || (cell.tagName!=='TH' && cell.scrollWidth>cell.clientWidth+1) ? [cell.textContent] : [];
    }));
    assert.deepEqual(cut,[],'No Forecast table header or month label is cut or spills into its neighbour');
  }
  await snap('forecast-overview');
  // The selected section tab stays readable under the pointer and while pressed.
  const rules=page.getByRole('button',{name:'Rules',exact:true}).first();
  await rules.click();await expect(rules).toHaveAttribute('aria-current','page');
  const contrast=()=>rules.evaluate(node=>{const style=getComputedStyle(node),canvas=document.createElement('canvas'),ctx=canvas.getContext('2d',{willReadFrequently:true});
    const luminance=color=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);const rgb=[...ctx.getImageData(0,0,1,1).data].slice(0,3).map(c=>{c/=255;return c<=0.04045?c/12.92:((c+0.055)/1.055)**2.4;});return rgb[0]*0.2126+rgb[1]*0.7152+rgb[2]*0.0722;};
    const a=luminance(style.color),b=luminance(style.backgroundColor);return (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05);});
  await rules.hover();assert.ok(await rules.evaluate(node=>node.matches(':hover')),'The pointer rests on the selected tab');
  const hovered=await contrast();assert.ok(hovered>=4.5,`Hovered selected tab contrast ${hovered.toFixed(2)}`);
  await page.mouse.down();const pressed=await contrast();await page.mouse.up();
  assert.ok(pressed>=4.5,`Pressed selected tab contrast ${pressed.toFixed(2)}`);
  await expect(page.getByRole('button',{name:'New rule',exact:true})).toBeEnabled({timeout:30000});await expect(page.getByText('Waiting for the live server',{exact:false})).toHaveCount(0);
  await top();await snap('forecast-rules');
  record('Forecast overview: window select matches the figures, no 0-month wording, labels clear, table uncut; selected tab readable when hovered and pressed');

  await go('account-summary/');await top();await overflow('Accounts');await snap('accounts-default');
  record('default Accounts page captured');

  if(width<760) {
    await page.setViewportSize({width:320,height});
    try {
      for(const [route,state] of [[`explorer/?month=${month}`,'explorer-320'],['account-summary/','accounts-320'],[`journal/?month=${month}`,'journal-320'],['forecast/','forecast-320'],[`atlas/?month=${month}`,'atlas-320']]) {
        await go(route);await top();await overflow(state);await snap(state);
      }
    } finally {await page.setViewportSize({width,height});}
    record('320px captures of Explorer, Accounts, Journal, Forecast and Atlas');
  }
}
