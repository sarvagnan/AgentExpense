import { copyFile, cp, mkdir, readdir, symlink } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {dashboardDataPath, guardDashboardData} from '../../scripts/dashboard-data.mjs';
import { ledgerRoot } from '../../scripts/project-paths.mjs';

guardDashboardData();
const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const ledger = ledgerRoot();
// Vite does not copy the checkout's generated public data. Publish only the selected output.
const assets = fileURLToPath(new URL('../public/', import.meta.url));
for (const name of await readdir(assets)) if (name !== 'data') await cp(path.join(assets,name),path.join(dist,name),{recursive:true});
await mkdir(path.join(dist,'data'),{recursive:true});
for (const name of await readdir(dashboardDataPath('public/data'))) {
  if (/^[a-z-]+\.json\.gz$/.test(name)) await copyFile(dashboardDataPath('public/data',name),path.join(dist,'data',name));
}
const views = ['explorer', 'atlas', 'journal', 'forecast', 'review', 'account-summary', 'add', 'queue', 'trash', 'drafts', 'statements'];

// Static file servers use directory indexes and follow the relative evidence links.
for (const view of views) {
  const directory = path.join(dist, view);
  await mkdir(directory, { recursive: true });
  await copyFile(path.join(dist, 'index.html'), path.join(directory, 'index.html'));
}
await symlink(path.relative(dist, path.join(ledger, 'Bills')), path.join(dist, 'bills'));
const statements = path.join(dist, 'statements');
await mkdir(statements, { recursive: true });
const workbooks = (await readdir(ledger)).filter((name) => /\.xlsx?$/.test(name));
for (const name of workbooks) {
  await symlink(path.relative(statements, path.join(ledger, name)), path.join(statements, name));
}
console.log(`Created static entry pages for ${views.join(', ')}; linked Bills and ${workbooks.length} statement workbooks.`);
