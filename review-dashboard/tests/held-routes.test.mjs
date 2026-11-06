import './test-isolation.mjs';
// The browser harness's held and forwarding routes, with route and page doubles: a fetch
// that fails or a page that closes fails only the test that waits, and never becomes an
// unhandled rejection that ends the whole browser run (CI, 2026-11-06: ECONNRESET).
import assert from 'node:assert/strict';
import test from 'node:test';
import {fetchRoute, forward, hold} from './browser/held.mjs';

const reset = () => Object.assign(new Error('route.fetch: read ECONNRESET'), {code:'ECONNRESET'});
function fakeRoute(method, fetches) {
  const calls = [];
  return {calls, request:() => ({method:() => method}),
    async fetch() { calls.push('fetch'); const next = fetches.shift(); if (next instanceof Error) throw next; return {body:async () => Buffer.from(next ?? '{}')}; },
    async fulfill({body}) { calls.push('fulfill:' + body); },
    async abort() { calls.push('abort'); },
    async fallback() { calls.push('fallback'); }};
}
function fakePage() {
  const handlers = [];
  return {handlers, closed:false, isClosed() { return this.closed; },
    async route(pattern, handler) { handlers.push(handler); }, async unroute() {}};
}
async function noUnhandledRejection(run) {
  const seen = []; const listener = error => seen.push(error);
  process.on('unhandledRejection', listener);
  try { await run(); await new Promise(resolve => setTimeout(resolve, 20)); }
  finally { process.off('unhandledRejection', listener); }
  assert.deepEqual(seen, [], 'No unhandled rejection');
}

test('a reused connection reset is sent once more for GET, never for POST', async () => {
  const get = fakeRoute('GET', [reset(), 'answer']);
  assert.equal((await (await fetchRoute(get)).body()).toString(), 'answer');
  assert.deepEqual(get.calls, ['fetch', 'fetch']);
  const post = fakeRoute('POST', [reset(), 'answer']);
  await assert.rejects(fetchRoute(post), /ECONNRESET/);
  assert.deepEqual(post.calls, ['fetch']);
});

test('a held fetch that fails rejects ready, aborts the request and fails release, without an unhandled rejection', async () => {
  await noUnhandledRejection(async () => {
    const page = fakePage(), held = await hold(page, '**/api/agent-status');
    const route = fakeRoute('GET', [reset(), reset()]);
    await page.handlers[0](route);
    await assert.rejects(held.ready, /Held GET \*\*\/api\/agent-status could not be fetched: route.fetch: read ECONNRESET/);
    assert.deepEqual(route.calls, ['fetch', 'fetch', 'abort']);
    await assert.rejects(held.release(), /ECONNRESET/);
    await held.release();
  });
});

test('a held response lands after release, and a page that closed while held ends quietly', async () => {
  await noUnhandledRejection(async () => {
    const page = fakePage(), held = await hold(page, '**/data', {transform:body => Buffer.from(body.toString().toUpperCase())});
    const route = fakeRoute('GET', ['data']); const handled = page.handlers[0](route);
    await held.ready; assert.deepEqual(route.calls, ['fetch']);
    await held.release(); await handled;
    assert.deepEqual(route.calls, ['fetch', 'fulfill:DATA']);
    const closing = fakePage(), late = await hold(closing, '**/data');
    const gone = fakeRoute('GET', ['data']); gone.fulfill = async () => { throw new Error('Target page, context or browser has been closed'); };
    const lateHandled = closing.handlers[0](gone); await late.ready; closing.closed = true;
    await late.release(); await lateHandled;
  });
});

test('a forwarded request whose fetch fails is aborted and reported to the test', async () => {
  await noUnhandledRejection(async () => {
    const page = fakePage(), failures = [];
    const route = fakeRoute('POST', [reset()]);
    await forward(page, async () => assert.fail('never answered'), {failures})(route);
    assert.deepEqual(route.calls, ['fetch', 'abort']);
    assert.match(failures[0].message, /ECONNRESET/);
  });
});
