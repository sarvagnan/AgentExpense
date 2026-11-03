import {dashboardDataPath, guardDashboardData} from '../../scripts/dashboard-data.mjs';
guardDashboardData();
import { mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';

import { moduleSettings, readManifest } from './ledger-manifest.mjs';
const modules = moduleSettings(await readManifest());
if (!modules.fuel) await rm(dashboardDataPath('public/data/fuel-log.json.gz'), {force: true});

// Tailscale Serve sends static files uncompressed, so publish gzip copies for the app to fetch and decompress.
const target = dashboardDataPath('public/data/');
await mkdir(target, { recursive: true });

let total = 0;
for (const name of ['dashboard', 'pending-transactions', ...(modules.fuel ? ['fuel-log'] : []), 'add-patterns']) {
  const json = JSON.stringify(JSON.parse(await readFile(dashboardDataPath('app/data', `${name}.json`), 'utf8')));
  const gzipped = gzipSync(json, { level: 9 });
  await writeFile(dashboardDataPath('public/data', `${name}.json.gz`), gzipped);
  total += gzipped.length;
  console.log(`Wrote public/data/${name}.json.gz (${(gzipped.length / 1024).toFixed(1)} KB)`);
}
console.log(`Compressed dashboard data: ${(total / 1024).toFixed(1)} KB total`);
