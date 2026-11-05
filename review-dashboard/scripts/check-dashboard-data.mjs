import {dashboardDataPath, guardDashboardData} from '../../scripts/dashboard-data.mjs';
guardDashboardData();
import {auditRecords} from './audit-provenance.mjs';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { loadExpenseStore } from './expense-store.mjs';
import { fundedTransactions } from './currency.mjs';

const dashboardPath = dashboardDataPath('app/data/dashboard.json');
const dashboard = JSON.parse(await readFile(dashboardPath, 'utf8'));

assert.equal(new Set(dashboard.transactions.map(entry => entry.id)).size, dashboard.transactions.length, 'Dashboard entry IDs are unique');
const { transactions: storeEntries, accounts, manifest, rules, rawRules, confirmedBalances, cardStatements } = await loadExpenseStore();
assert.deepEqual(dashboard.transactions.map(entry => entry.id), storeEntries.flatMap(entry => fundedTransactions(entry, accounts)).map(entry => entry.id), 'Dashboard IDs equal the currency-scoped non-assertion store IDs in order');
const review = JSON.parse(await readFile(dashboardDataPath('app/data/pending-transactions.json'), 'utf8'));
const dashboardIds = new Set(dashboard.transactions.map(entry => entry.id));
assert.equal(new Set(review.transactions.map(entry => entry.id)).size, review.transactions.length, 'Review IDs are unique');
assert.ok(review.transactions.every(entry => dashboardIds.has(entry.id)), 'Review IDs are a subset of dashboard IDs');

console.log('Dashboard data checks passed.');

const { buildPatterns } = await import('./add-patterns.mjs');
const patterns = JSON.parse(await readFile(dashboardDataPath('app/data/add-patterns.json'), 'utf8'));
assert.deepEqual(patterns, buildPatterns(storeEntries, accounts, manifest, rules, rawRules, await auditRecords()), 'Add patterns match the selected expense store');

assert.deepEqual(dashboard.meta.ledger, manifest, 'Dashboard includes the selected manifest');
assert.deepEqual(dashboard.meta.accounts, accounts, 'Dashboard preserves account metadata and declaration order');
assert.deepEqual(dashboard.confirmedBalances, confirmedBalances, 'Dashboard carries every statement-confirmed balance assertion');
assert.deepEqual(dashboard.cardStatements, cardStatements, 'Dashboard carries every filed card statement summary');
const storeIds = new Set(storeEntries.map(entry => entry.id));
for (const fact of [...dashboard.confirmedBalances, ...dashboard.cardStatements]) {
  assert.equal(accounts[fact.account]?.currency, fact.currency, `${fact.account}: statement fact in the account's currency`);
  assert.ok(('totalDue' in fact ? ['card'] : ['bank', 'card']).includes(accounts[fact.account]?.kind), `${fact.account}: statement fact on a statement account`);
}
for (const balance of dashboard.confirmedBalances)
  assert.ok(!storeIds.has(balance.entryId), 'A confirmed balance is an assertion-only entry; strict hledger checks verify its amount');
