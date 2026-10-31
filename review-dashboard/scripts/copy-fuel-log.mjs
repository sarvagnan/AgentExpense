// The hand-kept source belongs to the ledger; this app copy is generated.
import { copyFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { ledgerRoot } from '../../scripts/project-paths.mjs';

const destination = new URL('../app/data/fuel-log.json', import.meta.url);
await mkdir(new URL('./', destination), { recursive: true });
await copyFile(path.join(ledgerRoot(), 'data/fuel-log.json'), destination);
console.log('Copied the ledger fuel log into generated dashboard data.');
