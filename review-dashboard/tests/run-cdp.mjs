import { temporaryDirectory, artifactPath, temporaryPath } from './test-isolation.mjs';
// Launch an explicitly supplied fictional CDP suite with managed Chromium.
// Usage: node tests/run-cdp.mjs behavior [base-url]
//        node tests/run-cdp.mjs capture <output-under-/tmp> [base-url] [views] [schemes]
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { mkdir, rm, realpath, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, join } from 'node:path';
process.env.TMPDIR = process.env.EXPENSES_TEST_TMP;
const require = createRequire(process.env.HOME + '/.cache/expense-playwright/');
const { chromium } = require('playwright');
const root = fileURLToPath(new URL('../../', import.meta.url));
const cdpScript = process.env.CDP_SCRIPT;
assert.ok(cdpScript, 'Set CDP_SCRIPT to a fictional CDP suite or capture script');
const [mode, ...args] = process.argv.slice(2);
assert.ok(['behavior', 'capture'].includes(mode), 'Choose behavior or capture');
const work = await temporaryDirectory('expense-cdp-');
let context, child;
let stopping = false;
async function stop(signal) {
  if (stopping) return;
  stopping = true;
  child?.kill(signal);
  await context?.close().catch(() => {});
  await rm(work, { recursive: true, force: true });
  process.exit(signal === 'SIGINT' ? 130 : 143);
}
process.on('SIGINT', () => { void stop('SIGINT'); });
process.on('SIGTERM', () => { void stop('SIGTERM'); });
async function safeExecutable(path) {
  const canonical = await realpath(path);
  if (canonical.includes('/Google Chrome.app/') || canonical.endsWith('/Google Chrome.app')) throw Error('The everyday Google Chrome.app must never be used for tests');
  return path;
}
const counts = [];
async function run(label, full, env, scriptArgs) {
  const profile = join(work, label), downloads = join(work, `${label}-downloads`);
  await mkdir(downloads, { recursive: true });
  const port = mode === 'behavior' ? 9341 : 9340;
  const options = { headless: true, acceptDownloads: true, downloadsPath: downloads, viewport: null,
    args: [`--remote-debugging-port=${port}`, '--disable-gpu', ...(mode === 'capture' ? ['--hide-scrollbars'] : [])] };
  if (full) options.channel = 'chromium'; // Playwright's separate Chrome for Testing app.
  else if (process.env.CHROME) options.executablePath = await safeExecutable(process.env.CHROME);
  console.log(`Launching ${label}: ${full ? chromium.executablePath() : process.env.CHROME || 'Playwright-managed Chromium headless shell'}`);
  context = await chromium.launchPersistentContext(profile, options);
  // The raw CDP script owns dialogs on its initial target. Without a listener,
  // Playwright auto-accepts beforeunload and races the script's cancel response.
  const target = context.pages()[0];
  assert.ok(target, 'The raw CDP script needs an initial page target');
  target.on('dialog', () => {});
  context.on('page', page => page.on('download', d => { void d.saveAs(join(downloads, d.suggestedFilename())).catch(() => {}); }));
  for (const page of context.pages()) page.on('download', d => { void d.saveAs(join(downloads, d.suggestedFilename())).catch(() => {}); });
  let output = '';
  try {
    const code = await new Promise((resolveCode, reject) => {
      child = spawn(process.execPath, [resolve(cdpScript), String(port), ...scriptArgs], { env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
      child.stdout.on('data', data => { const text = data.toString(); output += text; process.stdout.write(text); });
      child.stderr.on('data', data => { output += data.toString(); process.stderr.write(data); });
      child.on('error', reject);
      child.on('close', (code, signal) => resolveCode(code ?? (signal ? 1 : 0)));
    });
    await writeFile(join(work, `${label}.log`), output);
    const matches = [...output.matchAll(/^(\d+)\/(\d+) passed$/gm)];
    if (mode === 'behavior') {
      assert.equal(matches.length, 1, `${label} must report one completed check count`);
      counts.push({ pass: Number(matches[0][1]), total: Number(matches[0][2]) });
    }
    return code;
  } finally { child = undefined; await context.close(); context = undefined; }
}
try {
  if (mode === 'capture') {
    const out = temporaryPath(args[0] || artifactPath('capture'));
    await mkdir(out, { recursive: true });
    assert.ok(/^\/(private\/)?tmp\//.test(await realpath(out)), 'Screenshots must stay under /tmp');
    process.exitCode = await run('capture', false, {}, [args[1] || 'http://127.0.0.1:5173/', out, ...args.slice(2)]);
  } else {
    const base = (args[0] || 'http://127.0.0.1:5173/').replace(/\/?$/, '/');
    let code = 0;
    if (!process.env.UI_ZOOM_ONLY) code ||= await run('behavior-shell', false, { UI_SKIP_REAL_ZOOM: '1' }, [base]);
    if (!process.env.UI_G1_ONLY) {
      const zoomCode = await run('behavior-real-zoom', true, { UI_ZOOM_ONLY: '1', UI_G1_ONLY: '', UI_GAPS_ONLY: '' }, [base]);
      code ||= zoomCode;
    }
    const total = counts.reduce((s, c) => ({ pass: s.pass + c.pass, total: s.total + c.total }), { pass: 0, total: 0 });
    console.log(`\nCDP TOTAL: ${total.pass}/${total.total} passed`);
    process.exitCode = code || (total.pass !== total.total ? 1 : 0);
  }
} finally { await rm(work, { recursive: true, force: true }); }
