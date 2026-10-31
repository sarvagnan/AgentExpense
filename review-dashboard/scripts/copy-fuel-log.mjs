// The hand-kept source belongs to the ledger; this app copy is generated.
import { copyFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { ledgerRoot } from '../../scripts/project-paths.mjs';

import { readManifest } from './ledger-manifest.mjs';
await readManifest();
const destination = new URL('../app/data/fuel-log.json', import.meta.url);
await mkdir(new URL('./', destination), { recursive: true });
try { await copyFile(path.join(ledgerRoot(), 'data/fuel-log.json'), destination); }
catch (error) { if (error.code !== 'ENOENT') throw error; await writeFile(destination, JSON.stringify({ meta: {}, entries: [] }) + '\n'); }
console.log('Copied the ledger fuel log into generated dashboard data.');
