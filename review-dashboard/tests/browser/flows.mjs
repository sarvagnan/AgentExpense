import assert from 'node:assert/strict';
import {mkdir, mkdtemp} from 'node:fs/promises';
import {arch, cpus, loadavg, platform, release, tmpdir, totalmem} from 'node:os';
import {join, resolve} from 'node:path';
import {setTimeout as sleep} from 'node:timers/promises';
import {flowCases} from './flow-cases.mjs';
import {compare, observeInputs, snapshot, writeEvidence} from './flow-evidence.mjs';
import {hold} from './held.mjs';
import {fixtureContext} from './suites.mjs';

const sizes = [{width: 390, height: 844}, {width: 720, height: 450}, {width: 1440, height: 900}];
const fixedTime = new Date('2026-11-02T12:00:00Z');
const delay = () => sleep(2000);
const measuredSize = process.env.BROWSER_CASE;
const timingMode = process.env.FLOW_TIMING;
const commonCore = {heading: '[role=dialog] :is(h1,h2), .setup-content h1, .content h1',
  navigation: '.sidebar nav, nav[aria-label="Quick navigation"]'};
const commonWatch = ['.loading-state', '.action-feedback', '.panel-header [role=status]', '.live-notice', '[role=alert]'];
const commonBusy = '[aria-busy=true], [data-action-state=queued], [data-action-state=saving], [data-action-state=waiting], .pending-upload, .loading-state';
const slug = value => value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

export async function createFlowRun({engines, themes, personas}) {
  const names = process.env.FLOWS ? [...new Set(process.env.FLOWS.split(','))] : Object.keys(flowCases);
  for (const name of names) assert.ok(Object.hasOwn(flowCases, name), `Unknown flow "${name}". Choose from ${Object.keys(flowCases).join(', ')}`);
  if (timingMode === 'only') assert.ok(engines.includes('chromium') && (!measuredSize || measuredSize.endsWith('-1440')),
    'Timed flows require Chromium at 1440 × 900');
  if (measuredSize) assert.ok(themes.some(theme => sizes.some(({width}) => measuredSize === `${theme}-${width}`)),
    'BROWSER_CASE must select a requested theme and one of 390, 720 or 1440');
  const parent = resolve(process.env.FLOW_ARTIFACT_DIR || (platform() === 'win32' ? tmpdir() : '/tmp'));
  await mkdir(parent, {recursive: true});
  const root = await mkdtemp(join(parent, 'agentexpense-flow-run-'));
  console.log(`Flow evidence: ${root}`);
  const evidence = {
    rows: [], strips: new Map(), failures: [], gaps: [], errors: [],
    machine: {platform: platform(), release: release(), architecture: arch(), processor: cpus()[0]?.model,
      logicalCPUs: cpus().length, memoryBytes: totalmem(), node: process.version,
      throttling: 'none', startLoad: loadavg(), delayMs: 2000, browserZoom: '100%', runs: {engines, themes, personas, flows: names}, timedLoads: []},
    cases: {passed: 0, failed: 0, errors: 0},
  };
  const actionRegions = new Map();
  evidence.gaps.push(
    'Headless browser contexts record the visual viewport and overlays. A native on-screen keyboard is unavailable.',
    'Next-item typing is exercised on Add and during the Settings preview. Save dialogs and view transitions with no next-item field record focus, scroll and geometry.',
  );
  async function run({browser, engine, stage, expect: playwrightExpect}) {
    const {base, persona} = stage;
    const expect = playwrightExpect.configure({timeout: 12000});
    const send = async (path, method, body) => {
      const reply = await fetch(base + path, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
      assert.ok(reply.ok, `${path} returned ${reply.status}: ${await reply.clone().text()}`);
      return reply;
    };
    const control = async (path, body) => (await send('__test/' + path, 'POST', body)).json();
    const get = async path => (await send('api/' + path, 'GET')).json();
    const post = async (path, body) => (await send('api/' + path, 'POST', body)).json();
    const file = async (path, name, mimeType) => ({name, mimeType, buffer: Buffer.from(await (await send('__test/' + path, 'POST')).arrayBuffer())});
    evidence.machine[engine] = browser.version();

    async function runCase(name, theme, size, sample = 0) {
      const timed = sample > 0, {width, height} = size;
      const id = `${persona}/${engine}/${theme}-${width}/${name}`;
      const context = await fixtureContext(browser, persona, theme, size);
      const page = await context.newPage();
      let checkpointed = false, previous, previousFile;
      let part = 0, activeStep = 'Fixture setup';
      const shots = [];
      const errorStart = evidence.failures.length;
      if (!timed) {
        await mkdir(join(root, id), {recursive: true});
        evidence.strips.set(id, shots);
      }
      async function checkpoint(label, options = {}) {
        activeStep = label;
        const {core, followups = [], pairs = [], sections = [], blocks = [], direct = false, preserve = false, typing = false, returnFocus = false, viewChange = false} = options;
        const current = await page.evaluate(snapshot, {core: {...commonCore, ...core}, followups, pairs, sections, blocks});
        const comparison = compare({previous, current, direct, preserve, typing, width, returnFocus, viewChange});
        const group = options.actionGroup;
        let priorAction;
        if (!timed && group && current.alignment) {
          const key = [persona, engine, theme, width, group].join('/');
          priorAction = actionRegions.get(key);
          if (!priorAction) actionRegions.set(key, {alignment: current.alignment, caseId: id, step: label});
          else {
            const placement = ({region, position, side}) => [region, position, side].join(' / ');
            if (placement(priorAction.alignment) !== placement(current.alignment)) {
              comparison.failures.push({check: 'FLOW-07', element: 'action',
                detail: `${placement(priorAction.alignment)} at ${priorAction.caseId} / ${priorAction.step} → ${placement(current.alignment)}`,
                before: priorAction.picture});
            }
          }
        }
        let picture = null;
        if (!timed) {
          const image = `${String(++part).padStart(2, '0')}-${slug(label)}.png`;
          await page.screenshot({path: join(root, id, image), animations: 'disabled'});
          shots.push({name: label, file: image});
          picture = `${id}/${image}`;
          if (group && current.alignment && !priorAction) {
            const key = [persona, engine, theme, width, group].join('/');
            actionRegions.get(key).picture = picture;
          }
        }
        const context = {persona, engine, theme, width, height, flow: name, sample, step: label, screenshot: picture,
          prompted: comparison.prompted, inputAgeMs: comparison.inputAge, lastInput: current.lastInput, focus: current.focus,
          scroll: current.scroll, viewport: current.viewport, deviceScaleFactor: current.deviceScaleFactor};
        const stepRow = evidence.rows.push({...context, element: 'step',
          timing: options.timing ? current.timing ?? {unobserved: true} : null});
        const indices = new Map();
        for (const [element, measured] of Object.entries(current.elements)) {
          indices.set(element, evidence.rows.push({...context, element, ...measured, alignment: element === 'action' ? current.alignment : undefined, delta: comparison.delta[element] ?? null}));
        }
        for (const [element, measured] of Object.entries(current.followups)) indices.set(element, evidence.rows.push({...context, element, followup: measured}));
        for (const pair of current.pairs) indices.set(pair.name, evidence.rows.push({...context, element: pair.name, pair}));
        for (const section of current.sections) indices.set(section.heading, evidence.rows.push({...context, element: section.heading, section}));
        for (const [element, visible] of Object.entries(current.blocks)) indices.set(element, evidence.rows.push({...context, element, visible}));
        if (!timed) for (const failure of comparison.failures) {
          evidence.failures.push({...failure, caseId: id, step: label, row: indices.get(failure.element) ?? stepRow, before: failure.before ?? previousFile, after: picture});
        }
        previous = current;
        previousFile = picture;
      }
      const paint = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const step = async (label, action, options = {}) => {
        activeStep = label;
        const watch = Object.values({...commonCore, ...options.core}).concat(commonWatch, options.watch ?? []).filter(Boolean);
        if (timed) await page.evaluate(([watch, busy]) => window.flowArm(watch, busy), [watch, options.busy ?? commonBusy]);
        await action();
        await paint();
        await checkpoint(label, {...options, direct: true, timing: timed && !options.deferTiming});
      };
      const prepare = async ({pattern, ...match}) => timed ? null : hold(page, pattern, {limit: 25000, ...match});
      async function arrival(label, route, action, done, options = {}) {
        activeStep = label;
        const {probe, done: doneScreen = options, prepared, timingFrom, ...working} = options;
        const held = prepared ?? await prepare(route);
        try {
          if (action) await step(label, action, {...working, followups: [], deferTiming: true});
          if (held) {
            await held.ready;
            await checkpoint(`${label} — working`, {...working, followups: [], direct: !!action});
            if (probe) await step(`${label} — type while waiting`, probe, {...working, followups: [], typing: true});
            await delay();
            await held.release();
          }
          activeStep = `${label} — completion`;
          await done();
          await paint();
          await checkpoint(`${label} — done`, {...doneScreen, preserve: !!held && !!probe, timing: timed && (!!action || !!timingFrom)});
        } finally {
          await held?.release();
        }
      }
      // Use a named role to identify controls whose labels are implicit, and a
      // measurement handle local to this run if the rendered control has no ID.
      const selector = async (locator, name) => {
        await locator.evaluate((node, name) => node.setAttribute('data-flow-target', name), name);
        return `[data-flow-target="${name}"]`;
      };
      const go = async (path, heading, options) => {
        activeStep = 'Open ' + heading;
        await page.goto(base + path);
        await expect(page.getByRole('heading', {name: options?.headingName ?? heading, exact: true}).first()).toBeVisible();
        await expect(page.locator('.loading-state')).toHaveCount(0);
        await paint();
        previous = null;
        await checkpoint(activeStep, options);
      };
      const button = name => page.getByRole('button', {name, exact: typeof name === 'string'});
      const type = async (locator, value) => {
        await locator.click();
        await locator.press(process.platform === 'darwin' ? 'Meta+a' : 'Control+a');
        await locator.pressSequentially(value, {delay: timed ? 35 : 0});
        await expect(locator).toHaveValue(value);
      };
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      try {
        page.setDefaultTimeout(12000);
        await control('first-run', {checkpoint: true});
        checkpointed = true;
        await control('gate', {state: 'ready'});
        await page.clock.setFixedTime(fixedTime);
        await page.addInitScript(observeInputs);
        await page.route('**/*', route => /^(?:blob|data):/.test(route.request().url()) || route.request().url().startsWith(base)
          ? route.continue() : route.abort('blockedbyclient'));
        if (timed) evidence.machine.timedLoads.push({persona, theme, name, sample, load: loadavg()});
        await flowCases[name]({page, persona, expect, go, button, type, selector, step, arrival, prepare, checkpoint, control, get, post, file, delay, timed,
          gap: message => evidence.gaps.push(message)});
        assert.deepEqual(errors, [], 'Uncaught browser errors');
        if (!timed) {
          const found = evidence.failures.length - errorStart;
          evidence.cases[found ? 'failed' : 'passed']++;
          console.log(`${found ? 'FLOW' : 'PASS'} ${id} (${found} findings)`);
        }
      } catch (error) {
        const fullId = timed ? `${id}/timed-${sample}` : id;
        if (!timed && !page.isClosed()) {
          await page.screenshot({path: join(root, id, 'incomplete.png')}).then(() =>
            shots.push({name: 'Incomplete: ' + activeStep, file: 'incomplete.png'})).catch(() => {});
        }
        evidence.errors.push({id: fullId, step: activeStep, message: error.message.split('\n')[0]});
        evidence.cases.errors++;
        console.error(`INCOMPLETE ${fullId} / ${activeStep}\n${error.stack}`);
      } finally {
        await context.close();
        if (checkpointed) await control('first-run', {restore: true});
      }
    }
    for (const theme of themes) {
      if (timingMode !== 'only') {
        for (const size of sizes) {
          if (measuredSize && measuredSize !== `${theme}-${size.width}`) continue;
          for (const name of names) await runCase(name, theme, size);
          await writeEvidence(root, evidence);
        }
      }
      if (engine === 'chromium' && timingMode !== '0' && (!measuredSize || measuredSize === `${theme}-1440`)) {
        for (const name of names) {
          for (let sample = 1; sample <= 5; sample++) await runCase(name, theme, sizes.at(-1), sample);
          console.log(`TIMED ${persona}/${theme}/${name}`);
          await writeEvidence(root, evidence);
        }
      }
      await writeEvidence(root, evidence);
    }
  }
  return {run, async finish() {
    await writeEvidence(root, evidence);
    console.log(`Flow results: ${join(root, 'summary.md')}`);
    return evidence.failures.length === 0 && evidence.errors.length === 0;
  }};
}
