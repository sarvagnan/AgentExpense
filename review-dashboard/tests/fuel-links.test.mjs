import './test-isolation.mjs';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { fuelLedgerEntry } from '../app/fuel-links.ts';
const dashboard = JSON.parse(await readFile(new URL('../app/data/dashboard.json', import.meta.url)));
const log = JSON.parse(await readFile(new URL('../app/data/fuel-log.json', import.meta.url)));
test('Every linked fuel-log row has one explicitly confirmed ledger association and physical readings', () => {
  const ids = log.entries.filter(row => row.ledgerStatus === 'Linked').map(row => row.entryId);
  assert.equal(new Set(ids).size, ids.length);
  for (const reading of log.entries.filter((row) => row.ledgerStatus === 'Linked')) {
    const entry = fuelLedgerEntry(reading, dashboard.transactions);
    assert.ok(entry, reading.id);
    assert.equal(entry.fuelLog.confirmed, true);
    assert.equal(entry.fuelLog.litres, reading.litres);
    assert.equal(entry.fuelLog.tripKm, reading.tripKm);
    assert.ok(entry.postings.some((posting) => posting.account === 'Expenses:Transport:Fuel'));
  }
});
test('Amount and date alone never establish an association', () => {
  const reading = log.entries[1];
  const entry = fuelLedgerEntry(reading, dashboard.transactions);
  for (const fuelLog of [null, { ...entry.fuelLog, confirmed: false }, { ...entry.fuelLog, litres: 20 }, { ...entry.fuelLog, tripKm: 20 }]) {
    assert.equal(fuelLedgerEntry(reading, [{ ...entry, fuelLog }]), undefined);
  }
  assert.equal(fuelLedgerEntry({ ...reading, ledgerStatus: 'Unlinked' }, [entry]), undefined);
});
test('Only the stored ID links, and duplicate IDs stay unlinked', () => {
  const reading = log.entries[1];
  const entry = fuelLedgerEntry(reading, dashboard.transactions);
  assert.equal(fuelLedgerEntry(reading, [entry, { ...entry, id: 'another' }]), entry);
  assert.equal(fuelLedgerEntry(reading, [entry, { ...entry }]), undefined);
  assert.equal(fuelLedgerEntry({ ...reading, entryId: undefined }, [entry]), undefined);
  assert.equal(fuelLedgerEntry(reading, [{ ...entry, id: 'another' }]), undefined);
  assert.equal(fuelLedgerEntry(reading, [{ ...entry, status: 'Pending' }]), undefined);
  assert.equal(fuelLedgerEntry(log.entries[0], dashboard.transactions), undefined);
});

test('An ID association survives changes in displayed date and amount', () => {
  const reading = log.entries[1];
  const entry = fuelLedgerEntry(reading, dashboard.transactions);
  const changed = { ...entry, date: '2026-01-01', amount: 1 };
  assert.equal(fuelLedgerEntry(reading, [changed]), changed);
});
