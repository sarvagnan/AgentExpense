import {dashboardDataPath, guardDashboardData} from '../../scripts/dashboard-data.mjs';
guardDashboardData();
// The hand-kept source belongs to the ledger; this app copy is generated.
import { readFile, mkdir, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { ledgerRoot } from '../../scripts/project-paths.mjs';

import { hasExtension, readManifest } from './ledger-manifest.mjs';
const manifest = await readManifest();
const destination = dashboardDataPath('app/data/fuel-log.json');
await mkdir(path.dirname(destination), { recursive: true });
if (!hasExtension(manifest, 'fuel')) {
  await rm(destination, {force: true});
} else {
// Store errors name their own cause; only problems with the fuel log itself are
// reported as fuel-log problems.
const {loadExpenseStore} = await import('./expense-store.mjs');
const {transactionCurrency} = await import('./currency.mjs');
const {transactions, accounts} = await loadExpenseStore();
const invalid = (reason, cause) => new Error(`The fuel extension is listed; supply a valid data/fuel-log.json: ${reason}`, {cause});
let source, log;
try { source = await readFile(path.join(ledgerRoot(), 'data/fuel-log.json'), 'utf8'); }
catch (error) { throw invalid(error.code === 'ENOENT' ? 'the file is missing.' : error.message, error); }
// V8's parse messages quote file contents; name only the problem.
try { log = JSON.parse(source); } catch (error) { throw invalid('it is not valid JSON.', error); }
if (!Array.isArray(log?.entries) || log.entries.some(item => item === null || typeof item !== 'object' || Array.isArray(item))) throw invalid('entries must be a list of objects.');
log.entries = log.entries.map(item => ({...item, currency: item.currency || transactionCurrency(transactions.find(entry => entry.id === item.entryId) || {postings: []}, accounts, manifest.defaultCurrency)}));
await writeFile(destination, log.entries.length ? JSON.stringify(log, null, 2) + '\n' : source);
}
console.log('Copied the ledger fuel log into generated dashboard data.');
