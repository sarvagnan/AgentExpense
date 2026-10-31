// The hand-kept source belongs to the ledger; this app copy is generated.
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { ledgerRoot } from '../../scripts/project-paths.mjs';

import { readManifest } from './ledger-manifest.mjs';
const manifest = await readManifest();
const destination = new URL('../app/data/fuel-log.json', import.meta.url);
await mkdir(new URL('./', destination), { recursive: true });
try {
  const source = await readFile(path.join(ledgerRoot(), 'data/fuel-log.json'), 'utf8');
  const log = JSON.parse(source);
  const {loadExpenseStore} = await import('./expense-store.mjs');
  const {transactionCurrency} = await import('./currency.mjs');
  const {transactions, accounts} = await loadExpenseStore();
  log.entries = log.entries.map(item => ({...item, currency: item.currency || transactionCurrency(transactions.find(entry => entry.id === item.entryId) || {postings: []}, accounts, manifest.defaultCurrency)}));
  await writeFile(destination, log.entries.length ? JSON.stringify(log, null, 2) + '\n' : source);
}
catch (error) { if (error.code !== 'ENOENT') throw error; await writeFile(destination, JSON.stringify({ meta: {}, entries: [] }) + '\n'); }
console.log('Copied the ledger fuel log into generated dashboard data.');
