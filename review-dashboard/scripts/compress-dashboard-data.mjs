import {dashboardDataPath, guardDashboardData} from '../../scripts/dashboard-data.mjs';
guardDashboardData();
import { mkdir, readFile, writeFile, rename, rm } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';

import { hasExtension, readManifest } from './ledger-manifest.mjs';
const fuel = hasExtension(await readManifest(), 'fuel');
if (!fuel) await rm(dashboardDataPath('public/data/fuel-log.json.gz'), {force: true});

// Tailscale Serve sends static files uncompressed, so publish gzip copies for the app to fetch and decompress.
const target = dashboardDataPath('public/data/');
await mkdir(target, { recursive: true });

let total = 0;
for (const name of ['dashboard', 'pending-transactions', ...(fuel ? ['fuel-log'] : []), 'add-patterns']) {
  const json = JSON.stringify(JSON.parse(await readFile(dashboardDataPath('app/data', `${name}.json`), 'utf8')));
  const gzipped = gzipSync(json, { level: 9 });
  // The live server streams these files while a save rebuilds them: replace each one whole.
  const destination = dashboardDataPath('public/data', `${name}.json.gz`), temporary = `${destination}.${process.pid}.tmp`;
  try { await writeFile(temporary, gzipped); await rename(temporary, destination); }
  finally { await rm(temporary, { force: true }); }
  total += gzipped.length;
  console.log(`Wrote public/data/${name}.json.gz (${(gzipped.length / 1024).toFixed(1)} KB)`);
}
console.log(`Compressed dashboard data: ${(total / 1024).toFixed(1)} KB total`);
