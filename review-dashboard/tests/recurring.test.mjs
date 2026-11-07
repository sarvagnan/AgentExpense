import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { temporaryDirectory } from './test-isolation.mjs';
import { expandForecast, loadExpenseStore, occurrenceDate } from '../scripts/expense-store.mjs';
import {accounts, entry, manifest, posting, rule as storeRule} from './store-fixtures.mjs';
const r = (overrides = {}) => storeRule({date:'2026-02-28',description:'Willow club',schedule:{every:'month',from:'2026-02-28',interval:1,'anchor-day':31},
  postings:[posting('Expenses:Club','10.00','EUR'),posting('Assets:Purse','-10.00','EUR')],...overrides});

test('recurring forecasts keep advanced anchors and exclude every inactive lifecycle', () => {
  assert.deepEqual(expandForecast([r()], '2026-02-01','2026-06-01').map(x=>x.date),['2026-02-28','2026-03-31','2026-04-30','2026-05-31']);
  for (const lifecycle of ['paused','ended','removed']) assert.deepEqual(expandForecast([r({lifecycle})],'2026-02-01','2026-06-01'),[]);
  assert.equal(occurrenceDate('2027-02-28',12,29),'2028-02-29');
  assert.equal(occurrenceDate('2099-02-28',12,29),'2100-02-28');
  assert.deepEqual(expandForecast([r({schedule:{every:'month',interval:3,from:'2026-02-28','anchor-day':31}})],'2026-02-01','2027-01-01').map(x=>x.date),['2026-02-28','2026-05-31','2026-08-31','2026-11-30']);
});

test('Node reader validates recurring store metadata, links, preferences and anchors',async t=>{
  const root=await temporaryDirectory('recurring-store-');t.after(()=>rm(root,{recursive:true,force:true}));
  await mkdir(path.join(root,'data/expenses'),{recursive:true});
  const put=(name,value)=>writeFile(path.join(root,name),JSON.stringify(value));
  await put('ledger.json',manifest({name:'Offline recurring',defaultCurrency:'EUR',modules:{investments:false}}));
  await put('data/accounts.json',{commodities:['EUR'],accounts:accounts({'Assets:Purse':{kind:'cash',currency:'EUR'},'Expenses:Club':{}})});
  const actual=entry({date:'2026-01-31',description:'Willow club',postings:r().postings,rule:{id:'f000000000000001',occurrence:'2026-01-31'}});
  await put('data/expenses/2026.json',[actual]);
  const rule=r({lifecycle:'paused',origin:{kind:'monthly-history',words:null,'entry-ids':[actual.id],'came-back':true},created:'2026-02-01'});
  await put('data/forecast.json',[rule]);await put('data/recurring-settings.json',{'never-automatic':['Expenses:Food']});
  const data=await loadExpenseStore(root);assert.equal(data.rules[0].lifecycle,'paused');assert.deepEqual(data.transactions[0].rule,actual.rule);assert.deepEqual(data.recurringSettings,{'never-automatic':['Expenses:Food']});
  for (const invalid of [{...rule,lifecycle:'unknown'},{...rule,schedule:{...rule.schedule,'anchor-day':32}},{...rule,lifecycle:'removed'},{...rule,origin:{...rule.origin,'entry-ids':['0'.repeat(16)]}},{...rule,date:'2026-02-27'}]) {
    await put('data/forecast.json',[invalid]);await assert.rejects(loadExpenseStore(root));
  }
  await put('data/forecast.json',[rule]);await put('data/recurring-settings.json',{'never-automatic':['Expenses:Food'],machine:true});await assert.rejects(loadExpenseStore(root));
  await put('data/recurring-settings.json',{'never-automatic':[]});await put('data/expenses/2026.json',[{...actual,rule:{...actual.rule,id:'a'.repeat(16)}}]);await assert.rejects(loadExpenseStore(root),/rule link/);
});
