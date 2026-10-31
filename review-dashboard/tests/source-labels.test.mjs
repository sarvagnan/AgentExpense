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

test('review source type follows metadata for unconventional card and paying bank paths', () => {
 const accounts = {'Assets:Checking:Day':{kind:'bank',label:'Day balance'},'Assets:Checking:Night':{kind:'bank',label:'Night balance'},'Liabilities:Plastic:Main':{kind:'card',label:'Travel'}};
 const row = (...postings) => ({sources:['Bills/invoice.pdf'],postings});
 const bank = (name,amount=-100) => ({account:`Assets:Checking:${name}`,amount});
 assert.equal(sourceType(row(bank('Day')),accounts),'Day balance');
 assert.equal(sourceType(row(bank('Day',100),bank('Night')),accounts),'Night balance');
 assert.equal(sourceType(row(bank('Day',100)),accounts),'Day balance');
 assert.equal(sourceType(row({account:'Liabilities:Plastic:Main',amount:100}),accounts),'Travel');
 assert.equal(sourceType(row({account:'Other',amount:100}),accounts),'Derived');
 assert.equal(sourceType(row({account:'Card',amount:-100}),{Card:{kind:'card'}}),'Card');
});
