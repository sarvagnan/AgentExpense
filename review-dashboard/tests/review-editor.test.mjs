import './test-isolation.mjs';
// Exercise the actual hook module with controllable lock rejection and task queues.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import {reviewKeys} from '../scripts/account-metadata.mjs';

const source = ts.transpileModule(readFileSync(new URL('../app/review/storage.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
function setup() {
  const requests = [], timers = [];
  const window = new EventTarget();
  window.setTimeout = (fn) => timers.push(fn);
  const hooks = { useState: (initial) => [typeof initial === 'function' ? initial() : initial, () => {}], useRef: (current) => ({ current }), useEffect: (fn) => fn() };
  const exports = {};
  vm.runInNewContext(source, { exports, require: name => name === 'react' ? hooks : name.includes('account-metadata') ? {reviewKeys} : {dashboard:{meta:{ledger:{id:'offline'}}}} , window, Event, navigator: { locks: {
    request: (name, options, callback) => new Promise((resolve, reject) => requests.push({ name, options, callback, reject })),
  } } });
  const editor = exports.useReviewEditor();
  return { editor, requests, timers };
}
const flush = () => new Promise((resolve) => setImmediate(resolve));
{
  const { editor, requests, timers } = setup();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].name, 'agentexpense-review-v2:offline:editor');
  // WebKit rejects the held request on document teardown and any subsequent request.
  requests[0].callback();
  assert.equal(editor.canEdit(), true);
  requests[0].reject(new Error('document is going away'));
  await flush();
  assert.equal(editor.canEdit(), false);
  assert.equal(requests.length, 1, 'rejection must not retry in the same microtask checkpoint');
  assert.equal(timers.length, 1);
  timers.shift()();
  assert.equal(requests.length, 2, 'a live tab requeues after its lock is stolen');
}
{
  const { editor, requests, timers } = setup();
  requests[0].reject(new Error('stolen'));
  await flush();
  editor.takeOver();
  assert.equal(requests[1].options.steal, true);
  timers.shift()();
  assert.equal(requests.length, 2, 'stale queued retry must not override a newer takeover');
  requests[1].callback();
  assert.equal(editor.canEdit(), true);
}
console.log('Review editor retry yields to navigation and preserves takeover ordering');
