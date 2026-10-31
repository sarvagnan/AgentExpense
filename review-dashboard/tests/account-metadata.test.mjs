import './test-isolation.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import {defaultAccount, expenseCategory, reviewKeys} from '../scripts/account-metadata.mjs';
import {buildPatterns, patternOf} from '../scripts/add-patterns.mjs';
import {bank, card, person, metadata} from './metadata-fixture.mjs';
test('display categories inherit from the nearest Expenses ancestor and retain the existing fallback', () => {
 const accounts = {
  Expenses: {category:'Everyday'},
  'Expenses:Market': {category:'Groceries'},
  'Expenses:Market:Dining': {category:'Meals'},
  'Expenses:Market:Produce': {},
 };
 assert.equal(expenseCategory('Expenses:Market:Produce', accounts), 'Groceries');
 assert.equal(expenseCategory('Expenses:Market:Produce:Fruit', accounts), 'Groceries');
 assert.equal(expenseCategory('Expenses:Market:Dining', accounts), 'Meals');
 assert.equal(expenseCategory('Expenses:Market:Dining:Corner Store', accounts), 'Meals');
 assert.equal(expenseCategory('Expenses:Marketplace', accounts), 'Everyday');
 assert.equal(expenseCategory('Expenses:Food:Dining'), 'Food & dining');
 assert.equal(expenseCategory('Expenses:Transport:Fuel'), 'Transport');
 assert.equal(expenseCategory('Expenses:Corner Store'), 'Corner Store');
 assert.equal(expenseCategory('Expenses'), 'Other');
});
test('two ledgers share a browser without sharing decisions, draft edits or export reminders; overrides retain legacy keys', () => {
 const one = reviewKeys({id:'one',review:{storageKey:null}}), two = reviewKeys({id:'two'});
 const browser = new Map(Object.values(one).map(key => [key,'saved work']));
 assert.equal(one.decisions,'agentexpense-review-v2:one');
 for (const key of Object.values(two)) assert.equal(browser.has(key),false);
 assert.deepEqual(reviewKeys({id:'two',review:{storageKey:'agentexpense-review-v2'}}),{decisions:'agentexpense-review-v2',drafts:'agentexpense-review-drafts-v1',exported:'agentexpense-review-exported-at'});
 const override = reviewKeys({id:'one',review:{storageKey:'custom'}});
 assert.deepEqual(override,{decisions:'custom',drafts:'custom-drafts-v1',exported:'custom-exported-at'});
});
test('default account follows declaration order and falls back to the first metadata account or empty', () => {
 assert.equal(defaultAccount({[card]:metadata[card],[bank]:metadata[bank], Other:{kind:'bank'}}),bank);
 assert.equal(defaultAccount({Expense:{},[card]:metadata[card]}),card);
 assert.equal(defaultAccount({Expense:{}}),'');
});
test('history classification uses case-insensitive category segments and metadata settlement directions', () => {
 const entry = postings => ({description:'An unfamiliar outlet',date:'2026-01-01',postings});
 const p = (account,amount) => ({account,amount});
 for (const [account,id] of [['Expenses:Motoring:fUeL','fuel'],['Expenses:Travel:Taxi','ride'],['Expenses:Travel:Cab','ride'],['Expenses:Travel:Auto','ride'],['Expenses:Travel:Ride','ride'],['Expenses:Monthly:subscriptions','sub'],['Expenses:Food:Dining','dining'],['Expenses:Food:Restaurants','dining'],['Expenses:Food:Eating Out','dining'],['Income:Rewards','rewards'],['Income:Reward','rewards'],['Income:Cashback','rewards']]) assert.equal(patternOf(entry([p(account,10),p(card,-10)]),metadata),id);
 assert.equal(patternOf(entry([p(bank,-10),p(card,10)]),metadata),'cardbill');
 const repayment = entry([p(person,-10),p(bank,10)]);
 assert.equal(patternOf(repayment,metadata),'payback');
 assert.equal(buildPatterns([repayment],metadata).patterns.payback.usualPay,bank);
 assert.equal(patternOf(entry([p(person,10),p(bank,-10)]),metadata),null);
 const noHistory = buildPatterns([],metadata);
 assert.deepEqual(noHistory.payments[bank],metadata[bank]);
 assert.deepEqual(noHistory.people,['Casey']);
});
