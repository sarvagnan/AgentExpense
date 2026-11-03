// Test-only filesystem boundary. Production builders and servers keep their normal paths.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, readlinkSync, realpathSync } from 'node:fs';
import { cp, mkdir, mkdtemp, readdir, symlink } from 'node:fs/promises';
import { delimiter, dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

export const sourceApplication = process.env.EXPENSES_TEST_SOURCE || fileURLToPath(new URL('../../', import.meta.url));
export const sourceDashboard = join(sourceApplication, 'review-dashboard');
process.env.EXPENSES_TEST_SOURCE = sourceApplication;

// Resolve existing ancestors too: /tmp/a-link/child must not escape through a symlink.
export function temporaryPath(path) {
  const target = resolve(path);
  let ancestor = target;
  while (!existsSync(ancestor)) ancestor = dirname(ancestor);
  const canonical = resolve(realpathSync(ancestor), relative(ancestor, target));
  assert.ok(canonical.startsWith('/tmp/') || canonical.startsWith('/private/tmp/'), `Test output must stay under /tmp: ${path}`);
  return target;
}

export const testArtifacts = temporaryPath(process.env.EXPENSES_TEST_TMP || '/tmp/expense-dashboard-tests');
mkdirSync(testArtifacts, { recursive: true });
function cachePath(name, fallback) {
  // npm may inject its ordinary home cache into lifecycle scripts. Redirect it;
  // preserve an explicitly staged cache so offline fixture commands can reuse it.
  try { if (process.env[name]) return temporaryPath(process.env[name]); }
  catch { /* An inherited home cache is not a test output directory. */ }
  return join(testArtifacts, fallback);
}
for (const name of ['STAGE', 'ADD_SHOTS', 'STATEMENTS_FINISH_FIXTURE']) {
  if (process.env[name]) temporaryPath(process.env[name]);
}
Object.assign(process.env, {
  EXPENSES_TEST_TMP: testArtifacts,
  TMPDIR: testArtifacts,
  npm_config_update_notifier: 'false',
  npm_config_cache: cachePath('npm_config_cache', 'npm-cache'),
  UV_CACHE_DIR: cachePath('UV_CACHE_DIR', 'uv-cache'),
  NODE_COMPILE_CACHE: cachePath('NODE_COMPILE_CACHE', 'node-compile-cache'),
  PYTHONDONTWRITEBYTECODE: '1',
});

export const artifactPath = name => temporaryPath(join(testArtifacts, name));
export const temporaryDirectory = prefix => mkdtemp(artifactPath(prefix));

export function snapshotData(application = sourceApplication) {
  const files = new Map();
  function visit(path) {
    if (!existsSync(path)) return;
    const stat = lstatSync(path);
    if (stat.isSymbolicLink()) files.set(relative(application, path), `link:${readlinkSync(path)}`);
    else if (stat.isDirectory()) for (const name of readdirSync(path).sort()) visit(join(path, name));
    else if (stat.isFile()) files.set(relative(application, path), createHash('sha256').update(readFileSync(path)).digest('hex'));
  }
  for (const folder of ['review-dashboard/app/data', 'review-dashboard/public/data', 'review-dashboard/dist', 'ledger/data/expenses']) visit(join(application, folder));
  return files;
}

export function assertDataUnchanged(before, application = sourceApplication) {
  const after = snapshotData(application);
  const changed = [...new Set([...before.keys(), ...after.keys()])].filter(name => before.get(name) !== after.get(name)).sort();
  const expenses = changed.filter(name => name.startsWith('ledger/data/expenses/') && /^[a-f0-9]{64}$/.test(after.get(name) || ''));
  const ledger = join(application, 'ledger');
  const git = (...args) => execFileSync('git', ['--no-optional-locks', '-C', ledger, ...args], {
    stdio: ['ignore', 'pipe', 'ignore'], timeout: 10000, maxBuffer: Infinity,
  });
  let commit;
  if (expenses.length) {
    try {
      // Do not accidentally use the application's enclosing repository. Pin main
      // once so a concurrent bookmark update cannot mix content and commit IDs.
      if (realpathSync(git('rev-parse', '--show-toplevel').toString().trim()) === realpathSync(ledger)) {
        commit = git('rev-parse', '--verify', 'refs/heads/main^{commit}').toString().trim();
      }
    } catch { /* Missing git, repository or main leaves the byte guard strict. */ }
  }
  const unexpected = changed.filter(name => {
    if (commit && expenses.includes(name)) {
      try {
        const hash = createHash('sha256').update(git('show', `${commit}:${name.slice('ledger/'.length)}`)).digest('hex');
        if (hash === after.get(name)) {
          console.log(`${name} changed to ledger main ${commit.slice(0, 7)} during the run; not a test write`);
          return false;
        }
      } catch { /* Unreadable or absent committed files remain failures. */ }
    }
    return true;
  });
  assert.deepEqual(unexpected, [], `Test changed protected application/ledger files:\n${unexpected.join('\n')}`);
}

const before = snapshotData();
process.once('exit', () => {
  try { assertDataUnchanged(before); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
});

export async function disposableDashboard(work = undefined) {
  work = work ? temporaryPath(work) : await temporaryDirectory('dashboard-');
  const application = join(work, 'application'), dash = join(application, 'review-dashboard');
  const filter = path => !['node_modules', 'dist', '.venv', '__pycache__', '.git', '.jj', '.source-ledger.json'].includes(path.split(sep).at(-1));
  await mkdir(application, { recursive: true });
  for (const name of ['scripts', 'schema', 'migrations', 'Makefile', '.node-version']) await cp(join(sourceApplication, name), join(application, name), { recursive: true, filter });
  // Copy the source and published data; a fixture refresh/build overwrites only this copy.
  for (const name of ['app', 'public', 'scripts', 'tests', 'server', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts', 'index.html']) {
    await cp(join(sourceDashboard, name), join(dash, name), { recursive: true, filter });
  }
  if (existsSync(join(sourceDashboard, 'dist'))) await cp(join(sourceDashboard, 'dist'), join(dash, 'dist'), { recursive: true });
  // Keep the node_modules DIRECTORY private so Vite's .vite and .vite-temp writes
  // cannot follow a whole-directory symlink back into the real checkout.
  const modules = join(dash, 'node_modules');
  await mkdir(modules);
  for (const name of await readdir(join(sourceDashboard, 'node_modules'))) {
    if (!name.startsWith('.vite')) await symlink(join(sourceDashboard, 'node_modules', name), join(modules, name));
  }
  // The direct Python launcher must also activate its installed environment for
  // nested `uv run --with jsonschema` in make commit/verify, even offline.
  const venv = join(sourceDashboard, 'server/.venv');
  return { application, dash, env: { ...process.env, VIRTUAL_ENV: venv,
    PATH: `${join(venv, 'bin')}${delimiter}${process.env.PATH || ''}`,
    EXPENSES_TEST_APPLICATION: application, DASHBOARD_DATA_DIR: dash, npm_config_update_notifier: 'false',
    LEDGER_DIR: resolve(process.env.LEDGER_DIR || join(sourceApplication, 'ledger')) } };
}

export async function createTestServer(options = {}) {
  const { createServer } = await import('vite');
  const { dash } = await disposableDashboard();
  return createServer({ ...options, root: dash });
}

export async function stopChildren(children) {
  for (const child of [...children].reverse()) child.kill('SIGTERM');
  await Promise.all(children.map(child => new Promise(done => {
    if (child.exitCode !== null || child.signalCode !== null) return done();
    child.once('exit', done);
    const timer = setTimeout(() => child.kill('SIGKILL'), 5000);
    timer.unref();
    child.once('exit', () => clearTimeout(timer));
  })));
}
