// Data-specific regression suites explicitly choose their fictional persona.
import { disposableDashboard, sourceApplication } from './test-isolation.mjs';
import { execFileSync } from 'node:child_process';
import { readFile, rm, cp } from 'node:fs/promises';
import { after } from 'node:test';
import { join } from 'node:path';

export async function personaData(name) {
  const copy = await disposableDashboard();
  const root = join(copy.application, 'ledger');
  await cp(join(sourceApplication, 'fixtures/ledgers', name), root, { recursive: true });
  after(() => rm(copy.application, { recursive: true, force: true }));
  {
    const env = { ...copy.env, LEDGER_DIR: root };
    execFileSync(join(sourceApplication, 'review-dashboard/server/.venv/bin/python'), [join(copy.application, 'scripts/ledger/generate_ledger.py')], { env, stdio: 'pipe' });
    for (const script of ['copy-fuel-log', 'build-dashboard-data', 'build-review-data']) {
      execFileSync(process.execPath, [join(copy.dash, 'scripts', script + '.mjs')], { env, stdio: 'pipe' });
    }
    const read = async file => JSON.parse(await readFile(join(copy.dash, 'app/data', file + '.json'), 'utf8'));
    return { dashboard: await read('dashboard'), review: await read('pending-transactions'), fuel: await read('fuel-log'), root };
  }
}
