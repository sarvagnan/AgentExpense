// Shared helpers for the timing tests: held and forwarding routes, and paused clocks.
//
// Routes. Playwright rethrows an error from a route handler as an unhandled rejection,
// which ends the whole browser run, not just its test (CI, 2026-11-06: a held
// `route.fetch` met a reset connection). Every handler that fetches, waits or fulfils
// goes through these helpers, so a late request can fail only its own test.

const RESET = /ECONNRESET|EPIPE|socket hang up/;
const GONE = /Target page, context or browser has been closed|Route is already handled|Request context disposed/i;

/** route.fetch, sent once more when a reused keep-alive connection was reset (GET and HEAD only). */
export async function fetchRoute(route, options) {
  try { return await route.fetch(options); }
  catch (error) {
    if (!['GET', 'HEAD'].includes(route.request().method()) || !RESET.test(String(error?.message))) throw error;
    return await route.fetch(options);
  }
}

/** True when an error only says the page, its context or the request is gone. */
export const gone = (page, error) => page.isClosed() || GONE.test(String(error?.message));

/**
 * Run a route handler's work and never throw. On failure the route is aborted (ignored
 * once the page is gone) and the error returned, for the test to report where it waits.
 */
export async function guard(route, work) {
  try { await work(); return null; }
  catch (error) { await route.abort().catch(() => {}); return error; }
}

/**
 * Hold the first `count` matching responses until `release()`. `transform(body, response)`
 * may return a replacement body. `ready` resolves once every held response has arrived and
 * rejects if one cannot be fetched (or none arrives within `limit` ms); `release()` lets
 * them through and rethrows a failure, unless it only says the page or request is gone.
 */
export async function hold(page, pattern, {method = 'GET', count = 1, transform = null, accept = null, limit = 120000} = {}) {
  let resolveReady, rejectReady, open;
  const ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
  const gate = new Promise(resolve => open = resolve);
  let seen = 0, completed = 0; const work = [], failures = [];
  const handler = async route => {
    if ((method && route.request().method() !== method) || seen >= count) return route.fallback();
    seen++;
    let fetched = false;
    const task = guard(route, async () => {
      const response = await fetchRoute(route); let body = await response.body();
      if (accept && !accept(body,response)) {seen--;fetched=true;await route.fulfill({response,body});return;}
      if (transform) body = await transform(body, response);
      fetched = true; if (++completed === count) resolveReady();
      await gate; await route.fulfill({response, body});
    }).then(error => {
      if (!error) return;
      if (!fetched) { failures.push(error); rejectReady(new Error(`Held ${method} ${pattern} could not be fetched: ${error.message}`, {cause:error})); }
      else if (!gone(page, error)) failures.push(error);
    });
    work.push(task); await task;
  };
  await page.route(pattern, handler);
  let timer;
  const limited = Promise.race([ready, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`No ${method} response to hold for ${pattern}`)), limit); timer.unref?.(); })]);
  limited.then(() => clearTimeout(timer), () => clearTimeout(timer));
  limited.catch(() => {}); // The test awaits it; a failure never becomes an unhandled rejection.
  let released = false;
  return {ready:limited, async release() {
    open(); await Promise.all(work);
    if (!released) { released = true; await page.unroute(pattern, handler).catch(error => { if (!gone(page, error)) throw error; }); }
    if (failures.length) { const [failure] = failures.splice(0); if (!gone(page, failure)) throw failure; }
  }};
}

/**
 * A route handler that forwards the request to the server and answers with
 * `respond(response, route)`, which fulfils it. A failure (server closed, connection
 * reset, a failed check in `respond`) aborts the request; `failures` collects those the
 * page did not end, for the test to check. `fetch` holds route.fetch options.
 */
export function forward(page, respond, {failures = [], fetch} = {}) {
  return async route => {
    const error = await guard(route, async () => respond(await fetchRoute(route, fetch), route));
    if (error && !gone(page, error)) failures.push(error);
  };
}

// Paused clocks. With `page.clock` paused only real-time tasks run. React commits a render
// and then runs its effects, where the app sets its timers, in a later MessageChannel task.
// Before advancing a paused clock past a timer that a render has just started, let those
// queued tasks run, or the timer starts after the advance and is due an interval late
// (CI WebKit, 2026-11-06: a status poll never came). Several rounds, then a short real wait,
// because message order across channels is not guaranteed.
export async function settleTasks(page, rounds = 3, wait = 250) {
  await page.evaluate(rounds => new Promise(resolve => {
    const channel = new MessageChannel(); let left = rounds;
    channel.port1.onmessage = () => { if (--left > 0) channel.port2.postMessage(0); else { channel.port1.close(); resolve(); } };
    channel.port2.postMessage(0);
  }), rounds);
  await page.waitForTimeout(wait);
}
