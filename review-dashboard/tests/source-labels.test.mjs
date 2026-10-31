import './test-isolation.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { sourceKind, sourceType } from '../scripts/source-labels.mjs';

test('source kind prioritizes explicit statement membership and bank rows over file names', () => {
  assert.equal(sourceKind({ 'statement-end': '2026-09-28', sources: ['Bills/receipt.pdf'] }), 'Statement');
  assert.equal(sourceKind({ 'source-row': 1, sources: ['Bills/receipt.pdf'] }), 'Bank import');
  for (const source of ['Bills/confirmation.md', 'user-provided fuel log', 'User-confirmed payment']) {
    assert.equal(sourceKind({ sources: [source] }), 'User confirmed');
  }
  assert.equal(sourceKind({ sources: ['Bills/photo.jpg'] }), 'Receipt');
  assert.equal(sourceKind({ sources: ['bank.xlsx'] }), 'Derived');
  assert.equal(sourceKind({}), 'Derived');
});

test('review source type follows card or paying bank, regardless of PDF evidence', () => {
  const row = (...postings) => ({ sources: ['Bills/invoice.pdf'], postings });
  const bank = (name, amount = -100) => ({ account: `Assets:Bank:${name}`, amount });
  assert.equal(sourceType(row(bank('Savings Account'))), 'Savings account');
  assert.equal(sourceType(row(bank('Salary Account'))), 'Salary account');
  assert.equal(sourceType(row(bank('Salary Account', 100), bank('Savings Account'))), 'Savings account');
  assert.equal(sourceType(row(bank('Savings Account', 100))), 'Savings account');
  assert.equal(sourceType(row(bank('Other'))), 'Derived');
  assert.equal(sourceType(row(bank('Savings Account'), { account: 'Liabilities:Credit Cards:Rewards Card', amount: 100 })), 'Credit card');
  assert.equal(sourceType(row({ account: 'Assets:Wallets:App Store Balance', amount: -100 })), 'Derived');
});
