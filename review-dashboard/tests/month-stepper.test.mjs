import test from 'node:test';
import assert from 'node:assert/strict';
import * as jsx from 'react/jsx-runtime';
import {hookHarness} from './hook-harness.mjs';

test('month options start at the newest while arrows move to adjacent months', () => {
  const {exports: {MonthStepper}} = hookHarness('../app/ui.tsx', {
    'react/jsx-runtime': jsx, 'react-dom': {}, './date-time': {}, './account-metadata': {},
    '../scripts/account-metadata.mjs': {}, './formatting': {},
  });
  const months = ['2026-09', '2026-10', '2026-11'];
  const chosen = [];
  const render = value => MonthStepper({months, value, format: month => month, onChange: month => chosen.push(month)}).props.children;
  const [previous, label, next] = render('2026-10');
  previous.props.onClick();
  next.props.onClick();
  assert.deepEqual(chosen, ['2026-09', '2026-11']);
  assert.equal(render('2026-09')[0].props.disabled, true);
  assert.equal(render('2026-11')[2].props.disabled, true);
  const select = label.props.children[2];
  assert.equal(select.props.value, '2026-10');
  assert.deepEqual(Array.from(select.props.children, option => option.props.value), ['2026-11', '2026-10', '2026-09']);
  assert.deepEqual(months, ['2026-09', '2026-10', '2026-11']);
});
