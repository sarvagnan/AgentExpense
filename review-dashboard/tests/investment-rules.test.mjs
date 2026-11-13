import './test-isolation.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createTestServer} from './test-isolation.mjs';
import {loadPublishedData} from './load-data.mjs';
import {rule,posting} from './store-fixtures.mjs';
import {mainPostings,ruleAmounts,complexRule,ruleDestinations,matchRules,varyingDetails} from '../scripts/recurring-ui.mjs';
import {sortRules} from '../scripts/rule-sort.mjs';
const destination='Assets:Retirement',accounts={
  'Assets:Bank':{kind:'bank',currency:'EUR',label:'Current',aliases:['bank']},
  [destination]:{kind:'investment',currency:'EUR',label:'Retirement'},'Expenses:Food':{}};
const contribution=()=>rule({description:'SIP contribution',date:'2026-11-15',postings:[posting(destination,'5000.00','EUR'),posting('Assets:Bank','-5000.00','EUR')]});
test('destination choice and helpers use investment metadata, never own bank transfers',()=>{
  assert.deepEqual(ruleDestinations(accounts),[destination,'Expenses:Food']);
  const r=contribution();assert.deepEqual(mainPostings(r,accounts).map(p=>p.account),[destination]);
  assert.deepEqual(ruleAmounts(r,accounts),{EUR:5000});assert.equal(complexRule(r,accounts),false);
  const transfer={...r,postings:r.postings.map((p,i)=>i?p:{...p,account:'Assets:Bank'})};assert.deepEqual(mainPostings(transfer,accounts),[]);
  const named={...r,description:'Regular contribution'};assert.equal(matchRules([named],'Retirement 5000 EUR',accounts,'2026-11-01','EUR').rule_id,r.id);
  const history=['5000.00','4900.00','5100.00'].map((amount,i)=>({...r,id:String(i).padStart(16,'0'),date:`2026-0${8+i}-15`,status:'confirmed',rule:null,postings:[posting(destination,amount,'EUR'),posting('Assets:Bank','-'+amount,'EUR')]}));
  assert.equal(varyingDetails(r,history,accounts).history.length,3);
  const expense=rule({id:'f000000000000002',postings:[posting('Expenses:Food','100.00','EUR'),posting('Assets:Bank','-100.00','EUR')]});
  assert.deepEqual(sortRules([r,expense],'amount','asc',accounts).map(r=>r.id),[expense.id,r.id]);
});
test('Rules show investment amounts as money set aside',async()=>{
 const server=await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
 try{
  const data=await loadPublishedData(server);data.dashboard.meta.accounts=accounts;(await server.ssrLoadModule('/app/data.ts')).setData(data);
  const {RuleSummary}=await server.ssrLoadModule('/app/rules.tsx');
  assert.match(renderToStaticMarkup(createElement(RuleSummary,{rule:contribution()})),/Money set aside/);
 }finally{await server.close();}
});
test('unit contributions show and sort by money cost while keeping their balanced editor read-only',async()=>{
 const r=contribution();r.postings[0]={...r.postings[0],commodity:'UNITS',amount:'5.00',cost:{amount:'1000.00',commodity:'EUR',per:'unit'}};
 assert.deepEqual(ruleAmounts(r,accounts),{EUR:5000});assert.equal(complexRule(r,accounts),true);
 const expense=rule({id:'f000000000000002',postings:[posting('Expenses:Food','100.00','EUR'),posting('Assets:Bank','-100.00','EUR')]});
 assert.deepEqual(sortRules([r,expense],'amount','asc',accounts).map(r=>r.id),[expense.id,r.id]);
 const server=await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
 try {
  const data=await loadPublishedData(server);data.dashboard.meta.accounts=accounts;(await server.ssrLoadModule('/app/data.ts')).setData(data);
  const {RuleSummary}=await server.ssrLoadModule('/app/rules.tsx');const html=renderToStaticMarkup(createElement(RuleSummary,{rule:r}));
  assert.match(html,/5,000\.00/);assert.doesNotMatch(html,/units/);
 } finally {await server.close();}
});
test('forecast keeps contributions outside spending while projecting cash needed',async()=>{
 const server=await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
 try{
  const {forecastProjection}=await server.ssrLoadModule('/app/forecast-projection.ts');
  const base={currency:'EUR',currentMonth:'2026-11',variableAfterDay:1,variable:{portions:[{from:1,to:31,average:31}]},items:[{kind:'Expense',month:'2026-11',date:'2026-11-20',signedExpense:40,amount:40,setAside:0},{kind:'Investment',month:'2026-11',date:'2026-11-20',signedExpense:0,amount:5000,setAside:5000}]};
  const result=forecastProjection(base,[],'2026-11-02','2026-11',false);
  assert.equal(result.total,40);assert.equal(result.committed,40);assert.equal(result.setAside,5000);assert.equal(result.cashRequired,5040);assert.equal(result.items.length,2);
  assert.equal(result.expected.at(-1).value,40);
  base.items.push({kind:'Expense',month:'2026-11',date:'2026-11-25',signedExpense:10,amount:10,setAside:1000});
  const mixed=forecastProjection(base,[],'2026-11-02','2026-11',false);assert.equal(mixed.total,50);assert.equal(mixed.setAside,6000);assert.equal(mixed.cashRequired,6050);
 }finally{await server.close();}
});
