import {disposableDashboard,temporaryDirectory,createTestServer} from './test-isolation.mjs';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {forecastProjection} from '../app/forecast-projection.ts';
import * as store from './store-fixtures.mjs';

const bank='Assets:Current',reserve='Assets:Reserve',units='Assets:Units';
const p=store.posting;
const rule=(id,description,postings,date='2026-11-20')=>store.rule({id:id.toString(16).padStart(16,'0'),date,description,postings});
async function build({rules=[],actual=[],holdings=false}={}) {
 const work=await temporaryDirectory('investment-forecast-'),copy=await disposableDashboard(join(work,'app')),root=join(work,'ledger');
 await mkdir(join(root,'data/expenses'),{recursive:true});
 const write=(file,value)=>writeFile(join(root,file),JSON.stringify(value));
 await write('ledger.json',store.manifest({defaultCurrency:'USD',modules:{investments:holdings},locale:'en-US'}));
 await write('data/accounts.json',{commodities:['USD','EUR','UNITS'],accounts:store.accounts({[bank]:{kind:'bank',currency:'USD',label:'Current'},[reserve]:{kind:'investment',currency:'USD',label:'Reserve'},[units]:{kind:'investment',currency:'UNITS',label:'Units'},'Expenses:Fees':{},'Expenses:Service':{},'Income:Salary':{}})});
 await write('data/expenses/2026.json',actual);await write('data/forecast.json',rules);
 execFileSync(process.execPath,[join(copy.dash,'scripts/build-dashboard-data.mjs')],{env:{...copy.env,LEDGER_DIR:root,DASHBOARD_TODAY:'2026-11-16'},stdio:'pipe'});
 return JSON.parse(await readFile(join(copy.dash,'app/data/dashboard.json'),'utf8'));
}
const contribution=(id=1,description='Reserve contribution',date)=>rule(id,description,[p(reserve,'500.00','USD'),p(bank,'-500.00','USD')],date);

test('builder publishes contribution direction, including zero for a withdrawal and only the salary contribution part',async()=>{
 const data=await build({rules:[contribution(),rule(2,'Reserve withdrawal',[p(reserve,'-200.00','USD'),p(bank,'200.00','USD')]),rule(3,'Salary split',[p('Income:Salary','-100.00','USD'),p(bank,'80.00','USD'),p(reserve,'20.00','USD')])]});
 const items=Object.fromEntries(data.forecast.items.filter(i=>i.month==='2026-11').map(i=>[i.description,i]));
 assert.equal(items['Reserve withdrawal'].setAside,0);assert.equal(items['Reserve contribution'].setAside,500);assert.equal(items['Salary split'].setAside,20);
 const server=await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
 try {
  (await server.ssrLoadModule('/app/data.ts')).setData({dashboard:data,review:{},fuel:{}});
  const {forecastProjection}=await server.ssrLoadModule('/app/forecast-projection.ts');
  const projected=forecastProjection(data.forecast,data.months,'2026-11-16','2026-11',false);
  assert.equal(projected.setAside,520);assert.equal(projected.cashRequired,520);assert.equal(projected.committed,0);
  const {default:Forecast}=await server.ssrLoadModule('/app/forecast.tsx'),html=renderToStaticMarkup(createElement(Forecast));
  const row=description=>html.split('<li').find(part=>part.includes('row-title">'+description))?.split('</li>')[0];
  assert.ok(row('Reserve withdrawal'));assert.doesNotMatch(row('Reserve withdrawal'),/set-aside|Money set aside/);
  assert.match(row('Reserve contribution'),/class="set-aside"/);
  assert.match(row('Salary split'),/Income/);assert.match(row('Salary split'),/\$100/);assert.doesNotMatch(row('Salary split'),/Money set aside/);
 } finally {await server.close();}
});

test('builder allocates a foreign fee after costed principal, so cash needed counts the bank payment once',async()=>{
 const cost=(amount,commodity)=>({amount,commodity,per:'total'});
 const postings=[p(units,'10.00','UNITS',{cost:cost('100.00','USD')}),p('Expenses:Fees','1.00','EUR',{cost:cost('2.00','USD')}),p(bank,'-102.00','USD')];
 const data=await build({rules:[rule(4,'Unit contribution with foreign fee',postings)],actual:[store.entry({id:'eeeeeeeeeeeeeee1',date:'2026-10-10',status:'confirmed',description:'Unit contribution with foreign fee',postings})]});
 assert.equal(data.transactions[0].signedExpense,2);assert.equal(data.months.find(m=>m.month==='2026-10').total,2);
 const item=data.forecast.items.find(i=>i.month==='2026-11');assert.equal(item.signedExpense,2);assert.equal(item.setAside,100);
 const result=forecastProjection(data.forecast,data.months,'2026-11-16','2026-11',false);
 assert.equal(result.committed,2);assert.equal(result.setAside,100);assert.equal(result.cashRequired,102);
});

test('every rule retains its unpaid occurrence after an unrelated later actual, with recorded spending unchanged',async()=>{
 const unpaid=rule(1,'Unpaid contribution',[p(reserve,'100.00','USD'),p(bank,'-100.00','USD')],'2026-11-10');
 const service=rule(2,'Unpaid service',[p('Expenses:Service','11.00','USD'),p(bank,'-11.00','USD')],'2026-11-08');
 const later=contribution(3,'Later contribution','2026-12-15');
 const actual=[store.entry({id:'eeeeeeeeeeeeeee1',date:'2026-11-01',status:'confirmed',postings:[p('Expenses:Service','7.00','USD'),p(bank,'-7.00','USD')]})];
 const before=await build({rules:[unpaid,service,later],actual});
 const after=await build({rules:[unpaid,service,later],actual:[...actual,store.entry({id:'eeeeeeeeeeeeeee2',date:'2026-11-15',status:'confirmed',description:'Later contribution',postings:later.postings,rule:{id:later.id,occurrence:'2026-11-15'}})]});
 assert.deepEqual(after.forecast.items.filter(i=>i.month==='2026-11').map(i=>[i.description,i.date]),[['Unpaid service','2026-11-08'],['Unpaid contribution','2026-11-10']]);
 const spending=data=>data.months.map(({month,currency,total,categories,daily})=>({month,currency,total,categories,daily}));
 assert.deepEqual(spending(after),spending(before));
 const result=forecastProjection(after.forecast,after.months,'2026-11-16','2026-11',false);
 assert.equal(result.committed,11);assert.equal(result.setAside,100);assert.equal(result.cashRequired,111);
});
