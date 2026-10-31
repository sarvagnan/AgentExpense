import './test-isolation.mjs';
import path from 'node:path';
import { ledgerRoot } from '../../scripts/project-paths.mjs';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { decimalNumber, expandForecast } from '../scripts/expense-store.mjs';

const ledger = path.join(ledgerRoot(), 'expense_ledger.ledger');
const rules = JSON.parse(await readFile(path.join(ledgerRoot(), 'data/forecast.json')));
const quantity = amount => Number(amount.aquantity.decimalMantissa) / 10 ** amount.aquantity.decimalPlaces;
const sort = rows => rows.sort((a, b) => a.date.localeCompare(b.date) || a.id.localeCompare(b.id));

test('every forecast occurrence matches hledger in date, description, posting accounts, currencies and amounts', async () => {
  const data = JSON.parse(await readFile(new URL('../app/data/dashboard.json', import.meta.url)));
  const { start, end } = data.forecast;
  const actual = JSON.parse(execFileSync(process.env.HLEDGER_BIN || 'hledger',
    ['-f', ledger, 'print', `--forecast=${start}..${end}`, `date:${start}..${end}`, '-O', 'json'], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }))
    .filter(row => /^forecast item: /m.test(row.tcomment))
    .map(row => ({
      id: row.tcomment.match(/^id: ([0-9a-f]{16})$/m)?.[1],
      date: row.tdate,
      // Periodic journal templates have no header description; this comment is the stored description.
      description: row.tcomment.match(/^forecast item: (.*)$/m)?.[1],
      postings: row.tpostings.flatMap(posting => posting.pamount.map(amount => ({
        account: posting.paccount, commodity: amount.acommodity, amount: quantity(amount),
      }))),
    }));
  const expected = expandForecast(rules, start, end).map(row => ({
    id: row.id, date: row.date, description: row.description,
    postings: row.postings.map(posting => ({ account: posting.account, commodity: posting.commodity, amount: decimalNumber(posting.amount) })),
  }));
  assert.ok(expected.length > 0);
  assert.deepEqual(sort(actual), sort(expected));
});
