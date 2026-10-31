import './test-isolation.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { expandForecast, scheduleText } from '../scripts/expense-store.mjs';

const rule = (from, every = 'month', interval) => ({ id: from, date: from, description: from, schedule: { from, every, ...(interval === undefined ? {} : { interval }) }, postings: [{ amount: 1 }] });
const dates = (rules, start, end) => expandForecast(rules, start, end).map(row => row.date);

test('monthly 29th/30th/31st dates clamp in February and recover the anchor in March', () => {
  for (const day of [29, 30, 31]) assert.deepEqual(dates([rule(`2026-01-${day}`)], '2026-02-10', '2026-05-01'), [
    '2026-02-28', `2026-03-${day}`, `2026-04-${Math.min(30, day)}`,
  ]);
  assert.deepEqual(dates([rule('2027-12-30')], '2028-01-01', '2028-04-01'), ['2028-01-30', '2028-02-29', '2028-03-30']);
});

test('quarterly rules retain their from phase even when the forecast starts mid-quarter', () => {
  assert.deepEqual(dates([rule('2026-11-02', 'month', 3)], '2026-12-01', '2027-11-01'), ['2027-02-02', '2027-05-02', '2027-08-02']);
  assert.deepEqual(dates([rule('2026-12-01', 'month', 3)], '2026-10-29', '2027-11-01'), ['2026-12-01', '2027-03-01', '2027-06-01', '2027-09-01']);
  assert.deepEqual(dates([rule('2026-01-31', 'month', 3)], '2026-02-01', '2027-02-01'), ['2026-04-30', '2026-07-31', '2026-10-31', '2027-01-31']);
});

test('yearly and multi-year schedules preserve leap-day anchors', () => {
  assert.deepEqual(dates([rule('2024-02-29', 'year')], '2025-01-01', '2029-01-01'), ['2025-02-28', '2026-02-28', '2027-02-28', '2028-02-29']);
  assert.deepEqual(dates([rule('2024-02-29', 'year', 2)], '2025-03-01', '2029-01-01'), ['2026-02-28', '2028-02-29']);
});

test('window is start-inclusive/end-exclusive, never before from; dates use UTC', () => {
  assert.deepEqual(dates([rule('2026-11-01')], '2026-10-29', '2026-11-01'), []);
  assert.deepEqual(dates([rule('2026-11-01')], '2026-11-01', '2026-12-01'), ['2026-11-01']);
  assert.deepEqual(dates([rule('2026-03-08')], '2026-03-01', '2026-05-01'), ['2026-03-08', '2026-04-08']);
});

test('same-day occurrences retain source rule order, metadata, IDs and postings', () => {
  const a = { ...rule('2026-11-01'), id: 'a' }, b = { ...rule('2026-11-01'), id: 'b' };
  const before = JSON.stringify([a, b]);
  const rows = expandForecast([a, b], '2026-10-29', '2027-01-01');
  assert.deepEqual(rows.map(row => [row.date, row.id]), [['2026-11-01', 'a'], ['2026-11-01', 'b'], ['2026-12-01', 'a'], ['2026-12-01', 'b']]);
  assert.equal(rows[0].description, a.description);
  assert.deepEqual(rows[0].postings, a.postings);
  assert.equal(rows[0].rule, 'monthly from 2026-11-01');
  assert.equal(JSON.stringify([a, b]), before);
});

test('schedule text matches existing hledger display and invalid schedules/windows fail', () => {
  assert.equal(scheduleText(rule('2026-11-01').schedule), 'monthly from 2026-11-01');
  assert.equal(scheduleText(rule('2026-11-01', 'year').schedule), 'yearly from 2026-11-01');
  assert.equal(scheduleText(rule('2026-11-01', 'month', 3).schedule), 'every 3 months from 2026-11-01');
  assert.equal(scheduleText(rule('2026-11-01', 'year', 2).schedule), 'every 2 years from 2026-11-01');
  for (const invalid of [rule('2026-11-01', 'week'), rule('2026-11-01', 'month', 0), rule('2026-11-01', 'month', 1.5), rule('2026-02-30')]) {
    assert.throws(() => expandForecast([invalid], '2026-01-01', '2027-01-01'), /schedule|date/);
  }
  assert.throws(() => expandForecast([], '2026-01-01', '2026-01-01'), /window/);
});
