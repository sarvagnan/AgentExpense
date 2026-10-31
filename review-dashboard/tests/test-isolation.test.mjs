import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdir, rm, symlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { assertDataUnchanged, snapshotData, sourceDashboard, temporaryDirectory, temporaryPath } from './test-isolation.mjs';

test('byte guard detects changed, added and removed data, including the exit-time failure', async t => {
  const application = await temporaryDirectory('guard-proof-');
  t.after(() => rm(application, { recursive: true, force: true }));
  const folder = join(application, 'review-dashboard/app/data');
  await mkdir(folder, { recursive: true });
  const file = join(folder, 'dashboard.json');
  await writeFile(file, 'original');
  const before = snapshotData(application);
  await writeFile(file, 'changed');
  assert.throws(() => assertDataUnchanged(before, application), /review-dashboard\/app\/data\/dashboard.json/);
  await rm(file);
  assert.throws(() => assertDataUnchanged(before, application), /dashboard.json/);
  await writeFile(file, 'original');
  await writeFile(join(folder, 'unexpected.json'), 'new');
  assert.throws(() => assertDataUnchanged(before, application), /unexpected.json/);
  await rm(join(folder, 'unexpected.json'));
  assertDataUnchanged(before, application);

  // Simulate a suite accidentally writing its protected source data. Only this
  // disposable source root changes; use the actual exit hook, not a test double.
  const script = join(application, 'bad-suite.mjs');
  await writeFile(script, `import ${JSON.stringify(new URL('./test-isolation.mjs', import.meta.url).href)};\nimport {writeFileSync} from 'node:fs';\nwriteFileSync(${JSON.stringify(file)}, 'fixture overwrite');\n`);
  const child = spawnSync(process.execPath, [script], { env: { ...process.env, EXPENSES_TEST_SOURCE: application }, encoding: 'utf8' });
  assert.equal(child.status, 1, child.stderr);
  assert.match(child.stderr, /Test changed protected application\/ledger files/);
  assert.match(child.stderr, /review-dashboard\/app\/data\/dashboard.json/);
  console.log(`GUARD DEMONSTRATION: bad suite exited ${child.status}\n${child.stderr}`);
});

test('temporary outputs reject checkout paths and symlink escapes before writing', async t => {
  assert.throws(() => temporaryPath('/outside-test-root/new.json'), /under \/tmp/);
  const work = await temporaryDirectory('path-proof-');
  t.after(() => rm(work, { recursive: true, force: true }));
  await symlink('/etc', join(work, 'escape'));
  assert.throws(() => temporaryPath(join(work, 'escape', 'new.json')), /under \/tmp/);
  assert.equal(temporaryPath(join(work, 'new.json')), join(work, 'new.json'));
});

