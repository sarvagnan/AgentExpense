import {dashboardDataPath, guardDashboardData} from '../../scripts/dashboard-data.mjs';
guardDashboardData();
// The hand-kept source belongs to the ledger; this app copy is generated.
import { readFile, mkdir, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { ledgerRoot } from '../../scripts/project-paths.mjs';

import { moduleSettings, readManifest } from './ledger-manifest.mjs';
const manifest = await readManifest();
const destination = dashboardDataPath('app/data/fuel-log.json');
await mkdir(path.dirname(destination), { recursive: true });
if (!moduleSettings(manifest).fuel) {
  await rm(destination, {force: true});
} else {
try {
  const source = await readFile(path.join(ledgerRoot(), 'data/fuel-log.json'), 'utf8');
  const log = JSON.parse(source);
  const {loadExpenseStore} = await import('./expense-store.mjs');
  const {transactionCurrency} = await import('./currency.mjs');
  const {transactions, accounts} = await loadExpenseStore();
  log.entries = log.entries.map(item => ({...item, currency: item.currency || transactionCurrency(transactions.find(entry => entry.id === item.entryId) || {postings: []}, accounts, manifest.defaultCurrency)}));
  await writeFile(destination, log.entries.length ? JSON.stringify(log, null, 2) + '\n' : source);
}
catch (error) { throw new Error('Fuel is enabled; supply a valid data/fuel-log.json.', {cause: error}); }
}
console.log('Copied the ledger fuel log into generated dashboard data.');
