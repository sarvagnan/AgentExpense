import path from 'node:path';
import os from 'node:os';
import {existsSync, readFileSync, realpathSync, mkdirSync, writeFileSync, rmSync} from 'node:fs';
import {applicationRoot, ledgerRoot} from './project-paths.mjs';

export function dashboardDataRoot() {
  const configured = process.env.DASHBOARD_DATA_DIR;
  const expanded = configured === '~' ? os.homedir() : configured?.startsWith('~/') ? path.join(os.homedir(), configured.slice(2)) : configured;
  return configured ? path.resolve(process.env.npm_lifecycle_event ? process.env.INIT_CWD || process.cwd() : process.cwd(), expanded) : path.join(applicationRoot, 'review-dashboard');
}
export const dashboardDataPath = (...parts) => path.join(dashboardDataRoot(), ...parts);

// Call before any output mutation, including when a later builder runs alone.
export function guardDashboardData() {
  const stamp = dashboardDataPath('app/data/.source-ledger.json');
  const expected = {format: 1, ledger: realpathSync(ledgerRoot())};
  let replace = false;
  if (existsSync(stamp)) {
    let value;
    try { value = JSON.parse(readFileSync(stamp, 'utf8')); } catch (error) { if (!(error instanceof SyntaxError)) throw error; }
    replace = value?.format !== expected.format || value?.ledger !== expected.ledger;
    if (replace && process.env.DASHBOARD_DATA_REPLACE !== '1') throw new Error('DASHBOARD_DATA_DIR belongs to a different ledger; choose another output folder or set DASHBOARD_DATA_REPLACE=1 to replace it.');
    if (!replace) return;
  }
  if (replace) for (const folder of ['app/data', 'public/data']) rmSync(dashboardDataPath(folder), {recursive:true, force:true});
  mkdirSync(path.dirname(stamp), {recursive:true});
  try { writeFileSync(stamp, JSON.stringify(expected) + '\n', {flag:'wx'}); }
  catch (error) { if (error.code === 'EEXIST') return guardDashboardData(); throw error; }
}
