import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {gunzipSync,gzipSync} from 'node:zlib';
import {forward,hold} from './held.mjs';

// Step 6g: one-line account rows, group headings that open a group view, the transfers
// switch, mixed currencies, the phone picker, history and a data reload held open.
// `/__test/account-groups` installs fictional Rowan accounts in the persona's own currency
// (a confirmed bank balance, a filed card statement, a wallet, a person and a transfer
// between two banks); `{restore:true}` puts the baseline back.
const GROUPS={'Banks':'banks','Credit cards':'cards','Prepaid':'prepaid','People':'people','Cash':'cash','Loans':'loans','Investments':'investments'};

export async function runAccountGroups({page,base,persona,width,stage,expect,control,go,refresh,overflow,snap,record,quiesce,money}){
 await quiesce();await control('reset-review',{});
 const {currency}=await control('account-groups',{});
 try{
  const data=JSON.parse(await readFile(join(stage.dash,'app/data/dashboard.json'),'utf8'));
  const main=data.meta.ledger.defaultCurrency,accounts=data.meta.accounts;
  const phone=width<760,narrow=width<=900;
  const m=value=>money(value,currency);
  const list=page.locator('.account-list');
  const openList=async()=>{if(narrow&&!(await list.isVisible()))await page.getByRole('button',{name:'More account context',exact:true}).click();await expect(list).toBeVisible();};
  const groupHeading=name=>list.locator('.account-group-open').filter({has:page.locator('strong',{hasText:new RegExp(`^${name}$`)})});
  const view=page.locator('.group-view');

  // 1. The list: one line per row at one fixed height, truncated names with their full text.
  await go('account-summary/?account='+encodeURIComponent('Assets:Bank:Rowan Everyday'));await openList();
  const rows=await list.locator('.account-row,.account-group-open').evaluateAll(nodes=>nodes.map(node=>{const name=node.querySelector('.account-row-name');return {height:node.getBoundingClientRect().height,lines:name?Math.round(name.getBoundingClientRect().height/parseFloat(getComputedStyle(name).lineHeight||'20')):1,subs:node.querySelectorAll('.row-sub,small').length,text:node.textContent,title:name?.getAttribute('title')};}));
  const expected=phone?44:36;
  for(const row of rows){assert.ok(Math.abs(row.height-expected)<0.6,`Every list row is ${expected}px high: ${row.text} is ${row.height}`);assert.equal(row.subs,0,`No second line under ${row.text}`);}
  const long=list.locator('.account-row').filter({hasText:'Rowan Reserve'});
  const truncated=await long.locator('.account-row-name').evaluate(node=>({overflow:node.scrollWidth>node.clientWidth,ellipsis:getComputedStyle(node).textOverflow,wrap:getComputedStyle(node).whiteSpace,title:node.title}));
  assert.equal(truncated.ellipsis,'ellipsis');assert.equal(truncated.wrap,'nowrap');assert.equal(truncated.title,'Rowan Reserve for the long weekend away fund');
  assert.ok(truncated.overflow||width>=1440,'The long name truncates in the narrow list');
  await expect(long).toHaveAccessibleName(/Rowan Reserve for the long weekend away fund/);
  // Card amounts read as owed (positive) with no Owed/In credit line; other currencies carry their code.
  await expect(list.locator('.account-row').filter({hasText:'Rowan Card'}).locator('.num')).toHaveText(m(25.5));
  for(const [name,meta] of Object.entries(accounts))if(meta.kind&&meta.kind!=='investment'&&meta.currency!==main&&data.transactions.some(t=>t.postings.some(p=>p.account===name))) {
   const amount=await list.locator('.account-row').filter({hasText:meta.label||name.split(':').at(-1)}).first().locator('.num').textContent();
   assert.ok(amount.endsWith(' '+meta.currency),`${meta.label}: ${amount} shows its currency code after the amount`);assert.ok(!amount.includes(meta.currency+' ')&&!/[£€$₹]/.test(amount),'Code, not symbol, after the amount');
  }
  for(const row of rows.filter(row=>row.title===undefined||row.title===null))assert.ok(!/ owed$/.test(row.text),'Headings show no account count and no owed suffix');
  // Focus stays inside the list's scroll box: an inset outline.
  await long.focus();await page.keyboard.press('ArrowUp');await page.keyboard.press('ArrowDown');
  const outline=await long.evaluate(node=>({offset:parseFloat(getComputedStyle(node).outlineOffset),width:parseFloat(getComputedStyle(node).outlineWidth)}));
  assert.ok(outline.offset<=-outline.width,`The focus outline is inset (${JSON.stringify(outline)})`);
  // C19: a heading's total is as large as the amounts under it and bolder, so the hierarchy reads top-down.
  const weights=await list.evaluate(node=>{const total=node.querySelector('.account-group-total'),amount=node.querySelector('.account-row .num');const a=getComputedStyle(total),b=getComputedStyle(amount);return {total:parseFloat(a.fontSize),amount:parseFloat(b.fontSize),weight:Number(a.fontWeight),rowWeight:Number(b.fontWeight)};});
  assert.ok(weights.total>=weights.amount&&weights.weight>weights.rowWeight,`Group totals outrank account amounts (${JSON.stringify(weights)})`);
  await overflow('Accounts list rows');await snap('accounts-list-rows');
  record('6g account rows: one line, fixed height, truncation with full name, codes for other currencies, inset focus');

  // 2. Every heading opens its group; arrows move through headings and rows alike.
  const present=await list.locator('.account-group-open strong').allTextContents();
  assert.ok(['Banks','Credit cards','Prepaid','People'].every(group=>present.includes(group)),'The fixture fills four groups');
  for(const group of present) {
   await openList();await groupHeading(group).click();
   await expect(page).toHaveURL(new RegExp(`account=${encodeURIComponent(group).replace(/%20/g,'(%20|\\+)')}`));
   await expect(page).toHaveTitle(`${group} — Accounts — Ledger`);
   if(group==='Investments'){await expect(page.locator('.accounts')).toHaveAttribute('data-portfolio','true');await expect(page.locator('.holdings-table')).toBeVisible();continue;}
   await expect(view).toHaveAttribute('data-group',group);
   await expect(view.getByRole('heading',{name:/^Total over time/}).first()).toBeVisible();
   await expect(view.getByRole('heading',{name:'Accounts',exact:true})).toBeVisible();await expect(view.getByRole('heading',{name:'Activity',exact:true})).toBeVisible();
   await expect(view.getByLabel('Period')).toHaveValue('30');
   if(narrow)await page.getByRole('button',{name:'Less account context',exact:true}).click();
   // Phone captures start at the group view itself; the full-page companion keeps the page above it.
   if(narrow)await view.evaluate(node=>node.scrollIntoView({block:'start',behavior:'instant'}));
   // C12: the table's last row has no partial bottom border under its first cell.
   const borders=await view.locator('.group-accounts-table tbody tr').last().evaluate(row=>[...row.children].map(cell=>parseFloat(getComputedStyle(cell).borderBottomWidth)));
   assert.ok(borders.every(width=>width===borders[0]),`The last row's cells share one bottom border (${borders})`);
   await overflow(group+' group view');await snap(`accounts-group-${GROUPS[group]}`);
  }
  const cell=(account,label)=>view.locator('.group-accounts-table tr').filter({hasText:account}).locator(`[data-label="${label}"]`);
  await go('account-summary/?account=Banks');
  await expect(cell('Rowan Everyday','Balance')).toHaveText(m(830));await expect(cell('Rowan Everyday','Money in')).toHaveText(m(30));await expect(cell('Rowan Everyday','Money out')).toHaveText(m(200));
  await expect(cell('Rowan Everyday','Last confirmed balance')).toContainText(m(840));await expect(cell('Rowan Reserve','Last confirmed balance')).toHaveText('None');
  await go('account-summary/?account='+encodeURIComponent('Credit cards'));
  await expect(cell('Rowan Card','Owed')).toHaveText(m(25.5));await expect(cell('Rowan Card','Charged')).toHaveText(m(65.5));await expect(cell('Rowan Card','Paid')).toHaveText(m(40));await expect(cell('Rowan Card','Last statement')).toContainText(m(40)+' due');
  await expect(view.locator('.group-accounts-table th[scope="col"]')).toHaveText(['Account','Owed','Charged','Paid','Last statement']);
  // The card's own detail still names what its amount is.
  await go('account-summary/?account='+encodeURIComponent('Liabilities:Credit Cards:Rowan Card'));
  await expect(page.locator('.stack .panel-header .summary-total-label').first()).toHaveText('Owed');await expect(page.locator('.stack .panel-header .summary-total').first()).toHaveText(m(25.5));
  await expect(page.getByRole('heading',{name:'Owed over time',exact:true})).toBeVisible();
  await go('account-summary/?account=Prepaid');
  await expect(cell('Rowan Wallet','Balance')).toHaveText(m(55));await expect(cell('Rowan Wallet','Added')).toHaveText(m(20));await expect(cell('Rowan Wallet','Spent')).toHaveText(m(15));await expect(cell('Rowan Wallet','Last used')).toContainText('16');
  await go('account-summary/?account=People');
  await expect(cell('Robin Vale','Balance')).toHaveText('Settled');await expect(cell('Robin Vale','Lent or paid for them')).toHaveText(m(30));await expect(cell('Robin Vale','Paid back')).toHaveText(m(30));await expect(cell('Robin Vale','Last settled')).toContainText('20');
  // An account name opens that account.
  await view.locator('.group-accounts-table').getByRole('link',{name:'Robin Vale',exact:true}).click();await expect(page).toHaveURL(/Reimbursements/);await expect(page.locator('.group-view')).toHaveCount(0);
  await expect(page.getByRole('heading',{name:'Robin Vale',level:2})).toBeFocused();
  record('6g group views: every heading opens its kind with the right columns and figures');

  await go('account-summary/?account=Banks');await openList();
  const banks=groupHeading('Banks');await banks.focus();await expect(banks).toBeFocused();
  const order=await list.locator('.account-row,.account-group-open').evaluateAll(nodes=>nodes.map(node=>node.classList.contains('account-group-open')?'group:'+node.querySelector('strong').textContent:'row'));
  const cards=order.indexOf('group:Credit cards');assert.ok(cards>0);
  for(let i=order.indexOf('group:Banks');i<cards;i++)await page.keyboard.press('ArrowDown');
  await expect(groupHeading('Credit cards')).toBeFocused();await expect(page).toHaveURL(/account=Credit(%20|\+)cards/);await expect(view).toHaveAttribute('data-group','Credit cards');
  await page.keyboard.press('ArrowDown');await expect(list.locator('.account-row').nth(order.slice(0,cards+1).filter(x=>x==='row').length)).toBeFocused();
  await expect(page.locator('.group-view')).toHaveCount(0);
  await page.keyboard.press('ArrowUp');await expect(groupHeading('Credit cards')).toBeFocused();await expect(view).toHaveAttribute('data-group','Credit cards');
  await page.keyboard.press('Home');await expect(groupHeading(present[0])).toBeFocused();
  record('6g keyboard: the arrow keys move through headings and rows, each heading opening its group');

  // 3. Transfers between two banks of the group are hidden behind a switch.
  await go('account-summary/?account=Banks');
  const activity=view.getByRole('region',{name:'Banks activity'}),toggle=activity.getByRole('switch',{name:'Show transfers between Banks'});
  await expect(activity).not.toContainText('Move to Rowan Reserve');await expect(activity).toContainText('1 transfer between Banks hidden');await expect(toggle).not.toBeChecked();
  await expect(activity).toContainText('Rowan Card payment');
  if(width<=600)assert.equal(await activity.locator('td.group-change').first().evaluate(node=>getComputedStyle(node,'::before').content),'none','A phone row shows the signed change without a label');
  await toggle.check();await expect(activity.locator('tr[data-internal="true"]')).toContainText('Move to Rowan Reserve');await expect(page).toHaveURL(/transfers=shown/);
  await expect(activity.locator('tr[data-internal="true"] .group-change')).toHaveText(money(0,currency));
  if(narrow)await activity.evaluate(node=>node.scrollIntoView({block:'start',behavior:'instant'}));
  await snap('accounts-group-transfers');
  await toggle.uncheck();await expect(activity).not.toContainText('Move to Rowan Reserve');
  record('6g transfers between accounts of a group are hidden behind the switch');

  // 4. Currencies stay apart: one chart and one total per currency, never added.
  const bankCurrencies=[...new Set(Object.values(accounts).filter(a=>a.kind==='bank').filter(a=>data.transactions.some(t=>t.postings.some(p=>accounts[p.account]===a))).map(a=>a.currency))];
  await expect(view.locator('.group-chart')).toHaveCount(bankCurrencies.length);
  for(const code of bankCurrencies){await expect(view.locator(`.group-chart[data-currency="${code}"] svg[role="img"]`)).toHaveCount(1);}
  if(bankCurrencies.length>1){
   await expect(view.locator('.group-chart h3').first()).toContainText('Total over time · ');
   if(narrow)await view.evaluate(node=>node.scrollIntoView({block:'start',behavior:'instant'}));
   const total=await view.locator('.summary-total').first().textContent();assert.equal(total.split(' · ').length,bankCurrencies.length,'Each currency has its own total');
   if(persona==='sam')await snap('accounts-group-mixed');
  }
  record(`6g ${bankCurrencies.length>1?'mixed-currency':'single-currency'} Banks: one chart and one total per currency`);

  // 5. The phone picker lists each group's view before the accounts.
  if(narrow) {
   const picker=page.getByLabel('Choose account');
   const options=await picker.locator('option').allTextContents();
   const groupsListed=options.filter(text=>text.endsWith(' · all accounts'));
   assert.deepEqual(groupsListed.filter(text=>text!=='Investments · all accounts'||present.includes('Investments')),present.map(group=>`${group} · all accounts`));
   assert.ok(options.indexOf(groupsListed.at(-1))<options.findIndex(text=>!text.endsWith(' · all accounts')),'Group views come before the individual accounts');
   await picker.selectOption({label:'Prepaid · all accounts'});await expect(view).toHaveAttribute('data-group','Prepaid');await expect(page).toHaveTitle('Prepaid — Accounts — Ledger');
   await snap('accounts-group-picker');record('6g phone picker lists every group before its accounts');
  }

  // 6. Back and forward move between group views.
  await go('account-summary/?account=Banks');await openList();await groupHeading('Credit cards').click();await expect(view).toHaveAttribute('data-group','Credit cards');
  await page.goBack();await expect(view).toHaveAttribute('data-group','Banks');await expect(page).toHaveTitle('Banks — Accounts — Ledger');
  await page.goForward();await expect(view).toHaveAttribute('data-group','Credit cards');await expect(page).toHaveTitle('Credit cards — Accounts — Ledger');
  record('6g back and forward return to each group view');

  // 7. A data reload landing while a group view is open keeps the view, its period and the switch.
  await go('account-summary/?account=Banks');
  const drafts=base+'api/drafts?include_added=true',failures=[];
  const reload=await hold(page,'**/dashboard.json.gz',{transform:body=>{const next=JSON.parse(gunzipSync(body));next.transactions.find(t=>t.description==='Rowan Card payment').description='Rowan Card payment, reloaded';return gzipSync(JSON.stringify(next));}});
  let added=false;
  const forwarded=forward(page,async(response,route)=>route.fulfill({response,json:[{id:'6'.repeat(32),state:'added',text:'Rowan timing',pieces:null,created_at:new Date().toISOString(),saved_at:null,queued_at:null,files:[],runs:[],questions:[],error:null,waiting:null,result:{outcome:'added',entry_id:'6'.repeat(16)}},...await response.json()]}),{failures});
  const addition=route=>{if(added)return route.fallback();added=true;return forwarded(route);};
  await page.route(drafts,addition);
  try{
   await refresh();await reload.ready;
   const switchControl=view.getByRole('switch',{name:'Show transfers between Banks'});
   await view.getByLabel('Period').selectOption('all');await switchControl.check();await switchControl.hover();const before=await switchControl.boundingBox();
   await reload.release();await expect(view).toContainText('Rowan Card payment, reloaded');
   await expect(view).toHaveAttribute('data-group','Banks');await expect(view.getByLabel('Period')).toHaveValue('all');await expect(switchControl).toBeChecked();await expect(switchControl).toBeEnabled();
   await expect(view.locator('tr[data-internal="true"]')).toContainText('Move to Rowan Reserve');
   const after=await switchControl.boundingBox();for(const key of ['x','y','width','height'])assert.ok(Math.abs(after[key]-before[key])<1,`The switch stayed under the pointer (${key})`);
   assert.match(page.url(),/period=all/);assert.match(page.url(),/transfers=shown/);
   assert.deepEqual(failures,[],'The forwarded Queue read reached the server');
  }finally{await reload.release();await page.unroute(drafts,addition);}
  record('6g timing: a held data reload keeps the group view, its period and the transfers switch');
 }finally{await quiesce();await control('account-groups',{restore:true});}
}
