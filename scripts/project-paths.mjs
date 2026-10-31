import path from 'node:path';
import { fileURLToPath } from 'node:url';
import os from 'node:os';

export const applicationRoot = fileURLToPath(new URL('../', import.meta.url));

// Relative overrides are relative to the caller's working directory, as in Python.
export function ledgerRoot() {
  const configured = process.env.LEDGER_DIR;
  const expanded = configured === '~' ? os.homedir() : configured?.startsWith('~/') ? path.join(os.homedir(), configured.slice(2)) : configured;
  return configured ? path.resolve(expanded) : path.join(applicationRoot, 'ledger');
}
