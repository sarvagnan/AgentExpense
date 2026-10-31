import { copyFile, mkdir, readdir, symlink } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ledgerRoot } from '../../scripts/project-paths.mjs';

const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const ledger = ledgerRoot();
const views = ['explorer', 'atlas', 'journal', 'forecast', 'review', 'account-summary', 'add', 'drafts', 'statements'];

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
