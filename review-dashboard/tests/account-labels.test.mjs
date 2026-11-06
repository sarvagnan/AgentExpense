import './test-isolation.mjs';
import {bank,card,person,metadata} from './metadata-fixture.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { accountFieldLabel, displayAccountLabel, summarizeAccounts } from '../app/account-labels.ts';
test('metadata supplies compact labels regardless of path or owner grammar', () => {
 for (const account of [bank,card,person,'Holdings:Index']) assert.equal(displayAccountLabel(account,metadata),metadata[account].label);
 assert.equal(displayAccountLabel('Expenses:Clothes:Willow Cloth:Coat',metadata),'Willow Cloth · Coat');
 assert.equal(displayAccountLabel('Undeclared',metadata),'Undeclared');
});
test('side summaries aggregate repeated accounts, keep metadata labels and preserve full paths', () => {
 assert.deepEqual(summarizeAccounts([{account:bank,amount:-100},{account:person,amount:-60},{account:person,amount:-60}],metadata),{short:'Casey + 1 more',full:`${person} + ${bank}`});
 assert.deepEqual(summarizeAccounts([],metadata),{short:'—',full:'—'});
});
test('form and card account names: an account’s own label, or its category path; never a raw ledger path', () => {
 for (const account of [bank,card,person]) assert.equal(accountFieldLabel(account,metadata),metadata[account].label);
 assert.equal(accountFieldLabel('Expenses:Rule Workflow:Hawthorn dues',metadata),'Rule Workflow › Hawthorn dues');
 assert.equal(accountFieldLabel('Expenses:Groceries',metadata),'Groceries');
 assert.equal(accountFieldLabel('Income:Salary',metadata),'Salary');
 assert.equal(accountFieldLabel('Equity:Opening Balances',metadata),'Equity › Opening Balances');
 for (const account of ['Expenses:Rule Workflow:Hawthorn dues','Income:Salary','Equity:Opening Balances',bank]) assert.doesNotMatch(accountFieldLabel(account,metadata),/:/);
});
