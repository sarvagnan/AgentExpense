import './test-isolation.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { displayAccountLabel, summarizeAccounts } from '../app/account-labels.ts';

test('personal payable and receivable labels omit the owner by position, for any name', () => {
  for (const relationship of ['Accounts Payable', 'Accounts Receivable']) {
    for (const owner of ['Morgan', 'Taylor']) {
      for (const separator of [':', ' · ']) {
        assert.equal(displayAccountLabel(['Casey', relationship, owner, 'Water Tax', 'Flat'].join(separator)), 'Casey · Water Tax · Flat');
        assert.equal(displayAccountLabel(['Casey', relationship, owner].join(separator)), 'Casey');
      }
    }
  }
});

test('names outside the owner slot remain meaningful expense and account segments', () => {
  assert.equal(displayAccountLabel('Expenses:Clothes:Willow Cloth:Asha'), 'Willow Cloth · Asha');
  assert.equal(displayAccountLabel('Assets:Bank:Salary Account'), 'Salary Account');
  assert.equal(displayAccountLabel('Casey:Accounts Payable:Morgan:Gift:Morgan'), 'Casey · Gift · Morgan');
  assert.equal(displayAccountLabel('Casey:Accounts Receivable:Morgan:Gift:Casey'), 'Casey · Gift · Casey');
});

test('structural shortening retains useful banks, cards and investment names', () => {
  assert.equal(displayAccountLabel('Assets:Bank:Savings Account'), 'Savings Account');
  assert.equal(displayAccountLabel('Liabilities:Credit Cards:Rewards Card'), 'Rewards Card');
  assert.equal(displayAccountLabel('Assets:Investments:Mutual Funds:Example Index'), 'Example Index');
});

test('side summaries aggregate accounts and preserve full paths while shortening the largest', () => {
  const account = 'Casey:Accounts Payable:Morgan:Water Tax';
  assert.deepEqual(summarizeAccounts([
    { account: 'Assets:Bank:Savings Account', amount: -100, label: 'Savings Account' },
    { account, amount: -60, label: account }, { account, amount: -60, label: account },
  ]), { short: 'Casey · Water Tax + 1 more', full: `${account} + Assets:Bank:Savings Account` });
  assert.deepEqual(summarizeAccounts([]), { short: '—', full: '—' });
});
