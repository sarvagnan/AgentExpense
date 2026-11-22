import {mkdir, writeFile} from 'node:fs/promises';
import {join} from 'node:path';

const tolerance = 2;
const inputWindow = 500;

// CRITIQUE.md § Flow run and core-checklist.md § Flow stability.
export function observeInputs() {
  const state = window.flowInput = {last: null, timing: null, events: [], frames: []};
  const observer = globalThis.PerformanceObserver?.supportedEntryTypes?.includes('event')
    ? new PerformanceObserver(list => state.events.push(...list.getEntries().map(entry => entry.toJSON())))
    : null;
  observer?.observe({type: 'event', buffered: true, durationThreshold: 16});
  function signature(timing) {
    return timing.selectors.map(selector => [...document.querySelectorAll(selector)].filter(node =>
      node.getClientRects().length && getComputedStyle(node).visibility === 'visible'
        && !node.closest('.sr-only')
    ).map(node => [node.innerText || '', node.value, node.disabled, node.getAttribute('aria-expanded'),
      node.getAttribute('aria-current'), node.getAttribute('aria-pressed')]));
  }
  window.flowArm = (selectors, busy) => {
    state.timing = {selectors, busy, start: null, first: null, working: null, keys: []};
  };
  for (const type of ['pointerdown', 'keydown', 'input', 'change']) {
    document.addEventListener(type, event => {
      if (!event.isTrusted && !['input', 'change'].includes(type)) return;
      state.last = {time: performance.now(), type, trusted: event.isTrusted};
      const timing = state.timing;
      if (!timing) return;
      if (timing.start === null) {
        timing.start = event.timeStamp;
        timing.before = JSON.stringify(signature(timing));
      }
      if (type === 'keydown') timing.key = event.timeStamp;
      if (type === 'input' && timing.key != null && typeof event.target.value === 'string') {
        const key = timing.key, node = event.target, value = node.value;
        timing.key = null;
        requestAnimationFrame(() => {
          timing.keys.push({start: key, frame: node.isConnected && node.value === value ? performance.now() - key : null});
        });
      }
    }, true);
  }
  function sample(time) {
    if (state.frames.length < 90) state.frames.push(time);
    const timing = state.timing;
    if (timing?.start != null) {
      if (timing.first === null && JSON.stringify(signature(timing)) !== timing.before) timing.first = time - timing.start;
      if (timing.working === null && [...document.querySelectorAll(timing.busy)].some(node =>
        node.getClientRects().length && getComputedStyle(node).visibility === 'visible' && !node.closest('.sr-only')
      )) timing.working = time - timing.start;
    }
    requestAnimationFrame(sample);
  }
  requestAnimationFrame(sample);
}

export function snapshot({core, followups, pairs, sections, blocks}) {
  const box = node => {
    const {x, y, width, height} = node.getBoundingClientRect();
    return {x, y, width, height};
  };
  const visible = node => node && node.getClientRects().length > 0
    && getComputedStyle(node).visibility === 'visible' && !node.closest('[hidden]');
  const find = selector => selector ? [...document.querySelectorAll(selector)].find(visible) : null;
  const key = node => {
    if (!node) return null;
    const control = node.getAttribute('data-testid') || node.id || node.getAttribute('aria-label') || node.getAttribute('aria-labelledby')
      || node.getAttribute('name') || node.labels?.[0]?.textContent?.trim()
      || node.getAttribute('href') || (node.matches('button,summary') ? node.textContent.trim() : '');
    const scope = node.closest('[data-draft-id], [data-result-id], [data-serial], [role=dialog]');
    return [scope?.id || scope?.getAttribute('data-draft-id') || scope?.getAttribute('data-result-id')
      || scope?.getAttribute('data-serial') || '', node.tagName.toLowerCase(), control || node.className || ''].join(':');
  };
  const dialog = find('[role=dialog]');
  const surface = dialog || find('.setup-content') || find('main');
  const setupTitle = surface?.matches('.setup-content') ? surface.querySelector('h1')?.textContent : '';
  const dialogHeading = dialog?.querySelector('h1,h2');
  const focus = document.activeElement;
  const elements = Object.fromEntries(Object.entries(core).map(([name, selector]) => {
    const node = name === 'heading' && dialogHeading ? dialogHeading : find(selector);
    return [name, node ? {selector, key: key(node), box: box(node), disabled: node.matches(':disabled,[aria-disabled=true]'),
      value: 'value' in node ? node.value : undefined,
      selection: typeof node.selectionStart === 'number' ? [node.selectionStart, node.selectionEnd] : undefined} : null];
  }));
  const action = find(core.action);
  const anchor = action?.closest('.add-savebar, .setup-footer, .detail-footer, .dialog-footer, .rule-actions, .review-action-row, .rules-toolbar, .entry-header-tools') || action?.parentElement;
  const actionBox = action ? box(action) : null, anchorBox = anchor ? box(anchor) : null;
  const alignment = actionBox && anchorBox ? {
    region: anchor?.className || anchor?.tagName || '',
    position: getComputedStyle(anchor).position,
    left: actionBox.x - anchorBox.x,
    right: anchorBox.x + anchorBox.width - actionBox.x - actionBox.width,
  } : null;
  if (alignment) alignment.side = Math.abs(alignment.left - alignment.right) <= 2 ? 'center'
    : alignment.left < alignment.right ? 'leading' : 'trailing';
  const visual = window.visualViewport;
  const viewport = {x: visual?.offsetLeft ?? 0, y: visual?.offsetTop ?? 0,
    width: visual?.width ?? innerWidth, height: visual?.height ?? innerHeight, scale: visual?.scale ?? 1};
  const right = rect => rect.x + rect.width;
  const bottom = rect => rect.y + rect.height;
  const fits = (inner, outer) => inner.x >= outer.x - 1 && inner.y >= outer.y - 1
    && right(inner) <= right(outer) + 1 && bottom(inner) <= bottom(outer) + 1;
  const overlays = followups.length ? [...document.querySelectorAll('body *')].filter(node =>
    visible(node) && ['sticky', 'fixed'].includes(getComputedStyle(node).position)).map(node => ({node, box: box(node)})) : [];
  function visibility(selector) {
    const node = find(selector);
    if (!node) return {state: 'absent'};
    const bounds = box(node);
    if (!fits(bounds, viewport)) return {state: 'outside viewport', box: bounds};
    for (let ancestor = node.parentElement; ancestor; ancestor = ancestor.parentElement) {
      const css = getComputedStyle(ancestor), clip = box(ancestor);
      if (/(hidden|clip|auto|scroll)/.test(css.overflowX) && (bounds.x < clip.x - 1 || right(bounds) > right(clip) + 1)
        || /(hidden|clip|auto|scroll)/.test(css.overflowY) && (bounds.y < clip.y - 1 || bottom(bounds) > bottom(clip) + 1)) {
        if (ancestor !== document.documentElement && ancestor !== document.body) return {state: 'clipped', box: bounds, by: key(ancestor)};
      }
    }
    for (const overlay of overlays) {
      if (overlay.node.contains(node) || node.contains(overlay.node)) continue;
      const x1 = Math.max(bounds.x, overlay.box.x), x2 = Math.min(right(bounds), right(overlay.box));
      const y1 = Math.max(bounds.y, overlay.box.y), y2 = Math.min(bottom(bounds), bottom(overlay.box));
      if (x2 - x1 < 1 || y2 - y1 < 1) continue;
      const stack = document.elementsFromPoint((x1 + x2) / 2, (y1 + y2) / 2);
      const covering = stack.findIndex(element => overlay.node.contains(element));
      const target = stack.findIndex(element => node.contains(element));
      if (covering >= 0 && (target < 0 || covering < target)) return {state: 'occluded', box: bounds, by: key(overlay.node)};
    }
    return {state: 'visible', box: bounds};
  }

  const scroll = {page: {x: scrollX, y: scrollY}};
  for (const node of document.querySelectorAll('body *')) {
    if (node === document.scrollingElement || (!node.scrollTop && !node.scrollLeft)) continue;
    const name = key(node);
    scroll[name] = {x: node.scrollLeft, y: node.scrollTop};
  }
  const timing = window.flowInput.timing;
  const frames = window.flowInput.frames;
  return {
    at: performance.now(), lastInput: window.flowInput.last, path: location.pathname,
    focus: key(focus), surface: dialog ? key(dialog) : setupTitle || 'app', scroll, viewport, deviceScaleFactor: devicePixelRatio,
    elements, alignment, followups: Object.fromEntries(followups.map(({name, selector, marker}) =>
      [name, {...visibility(selector), marker: marker ? visibility(marker) : null}]
    )),
    pairs: pairs.map(({name, left, right, equalHeight}) =>
      ({name, left: find(left) ? box(find(left)) : null, right: find(right) ? box(find(right)) : null, equalHeight})),
    sections: sections.flatMap(selector => [...document.querySelectorAll(selector)].filter(visible).map(node => {
      const heading = node.querySelector(':scope > :is(h2,h3), :scope > .panel-header :is(h2,h3)');
      const body = [...node.children].filter(child => visible(child) && child !== heading && !child.contains(heading));
      return {heading: heading?.textContent.trim(), empty: !!heading && !body.some(child =>
        (child.innerText ?? child.textContent ?? '').trim() || child.querySelector('img,svg,input,select,button,textarea'))};
    })),
    blocks: Object.fromEntries(blocks.map(selector => [selector, !!find(selector)])),
    timing: timing?.start == null ? null : {
      first: timing.first, busy: timing.working, settled: performance.now() - timing.start,
      keys: timing.keys.map(key => ({...key, events: window.flowInput.events.filter(entry =>
        ['keydown', 'input'].includes(entry.name) && Math.abs(entry.startTime - key.start) < 8)})),
      frameIntervals: frames.slice(1).map((frame, i) => frame - frames[i]),
    },
  };
}

export function compare({previous, current, direct, preserve, width, typing, returnFocus, viewChange}) {
  const failures = [];
  const fail = (check, element, detail) => failures.push({check, element, detail});
  const inputAge = current.lastInput ? current.at - current.lastInput.time : null;
  const prompted = direct && inputAge !== null && inputAge <= inputWindow;
  const delta = {};
  if (previous) {
    for (const [element, target] of Object.entries(current.elements)) {
      const before = previous.elements[element];
      if (!target || !before) continue;
      delta[element] = Object.fromEntries(Object.keys(target.box).map(axis => [axis, target.box[axis] - before.box[axis]]));
      const {x, y} = delta[element];
      const sameControl = before.key === target.key || before.selector === target.selector;
      if (!prompted && sameControl && (element === 'navigation' || current.surface === previous.surface && current.path === previous.path)
        && (Math.abs(x) > tolerance || Math.abs(y) > tolerance)) {
        fail('FLOW-01', element, `left ${x.toFixed(2)} px, top ${y.toFixed(2)} px`);
      }
    }
    if (!prompted && !viewChange && current.surface === previous.surface && current.path === previous.path) {
      if (current.focus !== previous.focus && !(returnFocus && current.focus === current.elements.input?.key)) fail('FLOW-02', 'focus', `${previous.focus} → ${current.focus}`);
      for (const name of new Set([...Object.keys(previous.scroll), ...Object.keys(current.scroll)])) {
        const before = previous.scroll[name] || {x: 0, y: 0}, after = current.scroll[name] || {x: 0, y: 0};
        if (Math.abs(after.x - before.x) > tolerance || Math.abs(after.y - before.y) > tolerance) {
          fail('FLOW-02', name, `scroll (${before.x}, ${before.y}) → (${after.x}, ${after.y}) CSS px`);
        }
      }
    }
    if (preserve && previous.elements.input && current.elements.input?.value !== previous.elements.input.value) {
      fail('FLOW-03', 'input', `${JSON.stringify(previous.elements.input.value)} → ${JSON.stringify(current.elements.input?.value)}`);
    }
    if (typing) for (const [name, shown] of Object.entries(previous.blocks)) {
      if (shown && !current.blocks[name]) fail('FLOW-06', name, 'visible block disappeared during typing');
    }
  }
  if (width === 390) for (const [name, result] of Object.entries(current.followups)) {
    if (result.state !== 'visible' && result.marker?.state !== 'visible') {
      fail('FLOW-04', name, `${result.state}${result.by ? ` by ${result.by}` : ''}${result.box ? ` at ${JSON.stringify(result.box)}` : ''}`);
    }
  }
  for (const pair of current.pairs) {
    if (!pair.left || !pair.right || pair.left.x + pair.left.width > pair.right.x + tolerance) continue;
    const top = pair.right.y - pair.left.y, height = pair.right.height - pair.left.height;
    if (Math.abs(top) > tolerance || pair.equalHeight && Math.abs(height) > tolerance) {
      fail('FLOW-05', pair.name, `top difference ${top.toFixed(2)} px, height difference ${height.toFixed(2)} px`);
    }
  }
  for (const section of current.sections) {
    if (section.empty) fail('FLOW-06', section.heading, 'heading has no visible content');
  }
  return {failures, delta, prompted, inputAge};
}

const escape = text => String(text).replace(/[&<>"']/g, c =>
  ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const percentile = values => {
  const sorted = values.filter(value => value !== null && Number.isFinite(value)).sort((a, b) => a - b);
  return sorted.length ? sorted[Math.ceil(sorted.length * 0.75) - 1] : null;
};

export async function writeEvidence(root, run) {
  await mkdir(root, {recursive: true});
  const {rows, strips, failures, gaps, errors, machine, cases} = run;
  const groups = new Map();
  for (const row of rows) {
    if (!row.sample || !row.timing || row.element !== 'step') continue;
    const id = `${row.persona}/${row.theme}/${row.flow}/${row.step}`;
    if (!groups.has(id)) groups.set(id, []);
    groups.get(id).push(row.timing);
  }
  const timings = [];
  const ms = value => value === null ? 'unobserved' : `${value.toFixed(1)} ms`;
  for (const [id, samples] of groups) {
    const first = percentile(samples.map(sample => sample.first));
    const busy = percentile(samples.map(sample => sample.busy));
    const settled = percentile(samples.map(sample => sample.settled));
    const intervals = samples.flatMap(sample => sample.frameIntervals ?? []).filter(interval => interval > 0);
    const frame = percentile(intervals);
    const keys = samples.flatMap(sample => sample.keys ?? []);
    const key = percentile(keys.map(entry => entry.frame));
    const problems = [];
    if (!keys.length && first !== null && first > 200) problems.push('RESP-01 first visible response exceeds 200 ms');
    if (!keys.length && first === null) problems.push('RESP-01 first visible response unobserved');
    if (!keys.length && settled > 1000 && percentile(samples.map(sample => sample.busy ?? sample.settled)) > 1000
      && (first === null || settled - first > 20)) problems.push('RESP-01 no timely busy indicator');
    if (key !== null && frame !== null && key > 2 * frame) problems.push('RESP-02 typing exceeds two display frames');
    if (!keys.length && settled > 10000) problems.push('RESP-01 inspect progress and cancellation (CTRL-08)');
    timings.push({id, samples: samples.length, unobserved: samples.filter(sample => sample.unobserved).length, first, busy, settled, key, frame, problems});
  }
  const lines = ['# Browser flows', '', `Machine ${JSON.stringify(machine)}`, '',
    `Cases ${cases.passed} passed, ${cases.failed} failed, ${cases.errors} incomplete.`, '',
    `[Measurements](measurements.json) · [Screenshot strips](index.html)`, '',
    '## FLOW findings', '',
    ...failures.map(f => `- ${f.check} ${f.caseId} / ${f.step} / ${f.element} — ${f.detail}. Row ${f.row}. ${f.before ? `[Before](${f.before}) · ` : ''}[After](${f.after})`),
    ...errors.map(error => `- Incomplete ${error.id} / ${error.step} — ${error.message}`),
    '', '## Coverage notes', '', ...[...new Set(gaps)].map(gap => `- ${gap}`),
    '', '## RESP and timed steps', '',
    ...timings.map(t => `- ${t.id} (n=${t.samples}${t.unobserved ? `, missing ${t.unobserved}` : ''}) — first ${ms(t.first)}, busy ${ms(t.busy)}, settled ${ms(t.settled)}, typing ${ms(t.key)}, frame interval ${ms(t.frame)}.${t.problems.length ? ' ' + t.problems.join('. ') + '.' : ''}`),
  ];
  const html = (title, body) => `<!doctype html><html lang="en"><meta charset="utf-8"><title>${escape(title)}</title><style>body{font:16px system-ui;margin:24px}ol{display:flex;gap:20px;overflow:auto;padding:12px;list-style:none}li{flex:0 0 auto}img{max-width:420px;max-height:480px;display:block}a{overflow-wrap:anywhere}</style><h1>${escape(title)}</h1>${body}</html>\n`;
  for (const [id, shots] of strips) {
    await writeFile(join(root, id, 'index.html'), html(id, '<ol>' + shots.map(({name, file}) =>
      `<li><p>${escape(name)}</p><a href="${escape(file)}"><img src="${escape(file)}" alt="${escape(name)}" loading="lazy"></a></li>`).join('') + '</ol>'));
  }
  await Promise.all([
    writeFile(join(root, 'summary.md'), lines.join('\n') + '\n'),
    writeFile(join(root, 'measurements.json'), JSON.stringify({machine, cases, rows, timings, failures, gaps, errors}, null, 2) + '\n'),
    writeFile(join(root, 'index.html'), html('Browser flows', '<p><a href="summary.md">Summary</a> · <a href="measurements.json">Measurements</a></p><ul>'
      + [...strips.keys()].map(id => `<li><a href="${escape(id)}/index.html">${escape(id)}</a></li>`).join('') + '</ul>')),
  ]);
}
