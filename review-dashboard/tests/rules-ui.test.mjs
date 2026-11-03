import './test-isolation.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createTestServer} from './test-isolation.mjs';
import {loadPublishedData} from './load-data.mjs';
import {buildPatterns} from '../scripts/add-patterns.mjs';
import {complexRule,matchRules,paymentInPeriod,periodFor,ruleAmounts,ruleStatus,statusLabel,textAmounts} from '../scripts/recurring-ui.mjs';
const accounts={'Assets:Purse':{kind:'cash',currency:'EUR',label:'Purse',aliases:['purse']},'Expenses:Club':{}};
const rule=(changes={})=>({id:'f000000000000001',description:'Willow club',date:'2026-11-15',status:'confirmed',schedule:{every:'month',from:'2026-11-15'},postings:[{account:'Expenses:Club',amount:'20.00',commodity:'EUR'},{account:'Assets:Purse',amount:'-20.00',commodity:'EUR'}],...changes});
test('instant matching agrees with the authoritative matcher across names, dates, ranking, currencies and lifecycles',()=>{
  const rules=[rule(),rule({id:'f000000000000002',description:'Willow lessons',postings:[{account:'Expenses:Lessons',amount:'21.00',commodity:'EUR'},{account:'Assets:Purse',amount:'-21.00',commodity:'EUR'}]}),rule({id:'f000000000000003',description:'Paused thing',lifecycle:'paused'})];
  const examples=['Willow 20 EUR','20 EUR','purse 20','Willow purse 22 EUR','Willow 01/11/2026','Willow 1 nov','Willow 2026-11-01','Willow 20 USD','Paused thing 20 EUR','club 1 November 2026','Willow 1. November 2026 20,50 EUR'];
  for(const locale of ['en-IN','en-US','en-GB','de-DE'])for(const text of examples){
    const expected=JSON.parse(execFileSync(process.env.VIRTUAL_ENV?process.env.VIRTUAL_ENV+'/bin/python':'python3',['-c',"import json,sys;sys.path.insert(0,'scripts/ledger');from recurring import match_text; print(json.dumps(match_text(**json.loads(sys.stdin.read()))))"],{cwd:new URL('../../',import.meta.url),input:JSON.stringify({rules,text,accounts,today:'2026-11-02',currency:'EUR',locale}),encoding:'utf8'}));
    const actual=matchRules(rules,text,accounts,'2026-11-02','EUR',locale);
    const normalize=x=>({...x,matches:x.matches.map(m=>({...m,matched_amounts:m.matched_amounts.map(a=>({...a,amount:Number(a.amount)}))}))});
    assert.deepEqual(normalize(actual),normalize(expected),locale+': '+text);
  }
});
test('calendar quantities, anchored periods, paid choices and statuses stay per rule',()=>{
  assert.deepEqual(textAmounts('1 nov 2026 2026-11-01 01/11/2026 20 EUR'),[20]);
  assert.deepEqual(textAmounts('1. November 2026 1.200,50 EUR','de-DE'),[1200.5]);
  const r=rule({schedule:{every:'month',interval:3,from:'2026-02-28','anchor-day':31}});
  assert.equal(periodFor(r,'2026-05-01'),'2026-05-31');
  assert.equal(periodFor(r,'2025-12-01'),'2025-11-30');
  assert.equal(paymentInPeriod({...r,payments:[{id:'1',date:'2026-05-02',rule:{id:r.id,occurrence:'2026-05-31'}}]},'2026-05-20').id,'1');
  assert.equal(paymentInPeriod({...r,payments:[{id:'1',date:'2026-05-02'}]},'2026-08-20'),undefined);
  assert.equal(ruleStatus(rule({date:'2026-11-15',payments:[{id:'1',date:'2026-11-02',rule:{id:r.id,occurrence:'2026-11-15'}}]}),'2026-11-02'),'stale');
  for(const [override,label] of [[{date:'2026-11-03'},'On track'],[{date:'2026-11-02'},'Due'],[{date:'2026-11-01'},'Overdue'],[{last_payment:{date:'2026-11-15'}},'Stale'],[{lifecycle:'paused'},'Paused'],[{lifecycle:'ended'},'Ended'],[{lifecycle:'removed'},'Removed']])assert.equal(statusLabel(ruleStatus(rule(override),'2026-11-02')),label);
  assert.deepEqual(ruleAmounts(rule()),{EUR:20});assert.equal(complexRule(rule()),false);assert.equal(complexRule(rule({postings:[...rule().postings,{account:'Expenses:Fees',amount:'1',commodity:'EUR'}]})),true);
});
test('host Add snapshot retains inactive rules, native full costs, aliases and paid history without changing merchant defaults',()=>{
  const raw=[rule(),rule({id:'f000000000000002',lifecycle:'paused'})];
  const normalized=raw.map(r=>({...r,postings:r.postings.map(p=>({account:p.account,amount:Number(p.amount),currency:p.commodity}))}));
  const entry={...normalized[0],id:'0000000000000001',date:'2026-10-15'};
  const prior=buildPatterns([entry],accounts,{},normalized),built=buildPatterns([entry],accounts,{},normalized,raw);
  assert.deepEqual(built.merchants,prior.merchants);assert.deepEqual(built.payments['Assets:Purse'].aliases,['purse']);assert.equal(built.rules.length,2);assert.equal(built.rules[0].last_payment.id,entry.id);assert.equal(built.rules[0].next_due,raw[0].date);assert.deepEqual(built.rules[0].postings,raw[0].postings);
  const linked={...entry,rule:{id:raw[0].id,occurrence:'2026-10-15'}};
  const audit=[{id:'a',kind:'draft',entry_id:linked.id},{kind:'rule',rule_action:'undone',undoes:'a',reversal_id:'0000000000000002',rule_changes:[]},{kind:'rule-review',rule_id:raw[0].id,rule_verdict:'propose-removal',reason:'Check it'},{kind:'rule',rule_action:'kept',rule_id:raw[0].id,rule_changes:[]}];
  const undone=buildPatterns([linked],accounts,{},normalized,raw,audit);
  assert.equal(undone.rules[0].last_payment,null);assert.deepEqual(undone.rules[0].payments,[]);assert.equal(undone.rules[0].review,null);assert.equal(undone.rules[0].review_state,'owner-kept');
});
test('guess card renders payment choices, duplicate entry link, missing payer and read-only planned conversions',async()=>{
  const server=await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
  try{
    const data=await loadPublishedData(server);data.dashboard.meta.ledger.locale='en-GB';data.dashboard.meta.ledger.defaultCurrency='EUR';data.dashboard.meta.accounts=accounts;
    (await server.ssrLoadModule('/app/data.ts')).setData(data);
    const {RuleGuessChoice}=await server.ssrLoadModule('/app/live/rule-guess.tsx');
    const match=matchRules([rule()],'Willow 21 EUR',accounts,'2026-11-02','EUR').matches[0];
    const render=r=>renderToStaticMarkup(createElement(RuleGuessChoice,{rule:r,match,text:'Willow 21 EUR',initialDate:'2026-11-02',disabled:false,onAdd:()=>{},onDecline:()=>{}}));
    const html=render(rule());assert.match(html,/Matched ‘willow’ and €21.00/);assert.match(html,/This payment only/);assert.match(html,/Update the rule too/);assert.match(html,/Not this/);
    const paid=render(rule({payments:[{id:'0000000000000001',date:'2026-11-01'}]}));assert.match(paid,/Already added for November 2026/);assert.match(paid,/View entry/);assert.match(paid,/Add another anyway/);
    const missing=render(rule({postings:[rule().postings[0],{account:'Equity:Unassigned',amount:'-20.00',commodity:'EUR'}]}));assert.match(missing,/Use this account from now on/);assert.match(missing,/Choose an account/);
    const foreign=render(rule({postings:[{...rule().postings[0],cost:{amount:'18.00',commodity:'GBP',per:'total'}},{account:'Assets:Purse',amount:'-18',commodity:'EUR'}]}));assert.match(foreign,/Planned conversion/);assert.match(foreign,/Statement reconciliation will correct it/);
    const {RuleSaved}=await server.ssrLoadModule('/app/live/rule-saved.tsx');
    const completed={id:'0'.repeat(32),created_at:'2026-11-02T12:00:00Z',result:{audit_id:'1'.repeat(32),description:'Willow club',next_due:'2026-12-15',can_undo:false}};
    const saved=draft=>renderToStaticMarkup(createElement(RuleSaved,{draft,online:true}));
    assert.doesNotMatch(saved(completed),/>Undo</);assert.match(saved({...completed,result:{...completed.result,can_undo:true,undo_until:'2999-01-01T00:00:00Z'}}),/>Undo</);assert.equal(saved({...completed,result:{...completed.result,undone:true}}),'');


  }finally{await server.close();}
});
test('Rules rows render lifecycle controls, native facts, review proposals and offline disabled controls',async()=>{
  const server=await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
  try{
    const data=await loadPublishedData(server);data.dashboard.meta.ledger.locale='en-GB';data.dashboard.meta.accounts=accounts;(await server.ssrLoadModule('/app/data.ts')).setData(data);
    const {RuleRow}=await server.ssrLoadModule('/app/rules.tsx');
    const render=(r,readonly=false)=>renderToStaticMarkup(createElement(RuleRow,{rule:r,readonly,busy:false,onEdit:()=>{},operate:async()=>true}));
    const active=render(rule({rule_status:'on-track',last_payment:{id:'0000000000000001',date:'2026-10-15'}}));assert.match(active,/€20.00/);assert.match(active,/Purse/);assert.match(active,/Monthly/);assert.match(active,/On track/);assert.match(active,/>Pause</);assert.match(active,/>End</);
    assert.match(render(rule({lifecycle:'paused'})),/>Resume</);assert.match(render(rule({lifecycle:'ended'})),/>Restore</);assert.match(render(rule({rule_status:'stale'})),/Move past last payment/);assert.match(render(rule({date:'2999-01-01',rule_status:'overdue'})),/On track/);
    const proposal=render(rule({origin:{kind:'monthly-history'},review:{rule_verdict:'propose-removal',reason:'Already covered'}}));assert.match(proposal,/Made automatically/);assert.match(proposal,/Already covered/);assert.match(proposal,/>Keep</);
    const offline=render(rule(),true);const buttons=[...offline.matchAll(/<button([^>]*)>/g)];assert.ok(buttons.length>0);assert.ok(buttons.every(b=>b[1].includes('disabled=""')));
  }finally{await server.close();}
});
