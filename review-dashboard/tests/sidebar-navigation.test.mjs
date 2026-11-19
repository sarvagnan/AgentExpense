import test from 'node:test';
import assert from 'node:assert/strict';
import * as jsx from 'react/jsx-runtime';
import * as editNavigation from '../scripts/edit-navigation.mjs';
import {sidebarSearch} from '../scripts/sidebar-navigation.mjs';
import {hookHarness} from './hook-harness.mjs';

const base = 'http://localhost/expenses/';

function navigation(start) {
  let app, location = new URL(start, base);
  const window = new EventTarget(), document = new EventTarget(), storage = new Map();
  const go = path => { location = new URL(path, base); };
  class Anchor {
    constructor(path, area) { this.href = new URL(path, base).href; this.area = area; }
    closest(selector) { return selector === 'a[href]' || selector.split(', ').includes(this.area) ? this : null; }
    hasAttribute() { return false; }
  }
  const history = {pushState: (_state, _title, path) => go(path)};
  Object.defineProperty(window, 'location', {get: () => location});
  Object.assign(window, {history, scrollTo() {}});
  document.getElementById = () => ({});
  const harness = hookHarness('../app/main.tsx', {
    'react/jsx-runtime': jsx,
    'react-dom/client': {createRoot: () => ({render: root => { app = root.props.children.type; }})},
    '../scripts/edit-navigation.mjs': editNavigation,
    './live/pending-actions': {},
    './dashboard': {},
    './ui': {sessionRead: key => storage.get(key) ?? null, sessionWrite: (key, value) => storage.set(key, value), useAppearance() {}},
    './data': {loadData: () => new Promise(() => {})},
    './first-run': {},
    './globals.css': {},
    '../scripts/sidebar-navigation.mjs': {sidebarSearch},
  }, {location, history, window, document, URL, performance, Element: Anchor, HTMLAnchorElement: Anchor});
  harness.render(() => app());
  const click = (path, area = '.sidebar') => {
    const event = new Event('click', {cancelable: true});
    Object.defineProperties(event, {target: {value: new Anchor(path, area)}, button: {value: 0}});
    document.dispatchEvent(event);
    assert.equal(event.defaultPrevented, true);
    return location.search;
  };
  return {click, storage, pop: path => { go(path); window.dispatchEvent(new Event('popstate')); return location.search; }};
}

test('sidebar search ignores saved and link queries for live views', () => {
  for (const view of ['add', 'queue', 'statements', 'trash', 'settings']) {
    assert.equal(sidebarSearch(view, '?item=b2', () => '?item=a1'), '');
  }
  assert.equal(sidebarSearch('explorer', '?month=2026-11', () => null), 'month=2026-11');
});

test('sidebar returns to browsing context without replaying a one-time address', () => {
  const {click, storage} = navigation('explorer/?q=lunch&entry=a1&edit_changed=2026-11&edited=1&edit_op=2&settings=1');
  assert.equal(click('add/'), '');
  assert.equal(click('explorer/?month=2026-11'), '?q=lunch&entry=a1');
  for (const [view, path, query] of [
    ['atlas', 'atlas', 'month=2026-10'], ['journal', 'journal', 'q=club'],
    ['forecast', 'forecast', 'section=rules'], ['accounts', 'account-summary', 'find=club'],
    ['review', 'review', 'entry=a1'],
  ]) {
    storage.set('ledger-view:' + view, '?' + query);
    assert.equal(click(path + '/'), '?' + query);
  }
  storage.set('ledger-view:atlas', '?month=2026-09');
  assert.equal(click('atlas/', '#phone-more-views'), '?month=2026-09');
});

test('sidebar opens live views plain while explicit links and history keep their address', () => {
  const {click, storage, pop} = navigation('explorer/');
  storage.set('ledger-view:add', '?draft=a1&settings=1');
  storage.set('ledger-view:queue', '?correct=a1');
  storage.set('ledger-view:trash', '?item=a1');
  assert.equal(click('add/'), '');
  assert.equal(click('queue/?correct=b2', null), '?correct=b2');
  assert.equal(click('queue/'), '');
  assert.equal(pop('queue/?draft=b2'), '?draft=b2');
  assert.equal(click('trash/', '.sidebar'), '');
  assert.equal(click('add/?entry=c3', null), '?entry=c3');
});
