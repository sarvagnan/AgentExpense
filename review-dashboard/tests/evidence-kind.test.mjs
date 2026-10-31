import './test-isolation.mjs';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { createTestServer as createServer } from './test-isolation.mjs';

const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
let evidenceKind, evidenceAction, isFileReference, rowTitle;
try {
  (await server.ssrLoadModule('/app/data.ts')).setData({dashboard:{meta:{accounts:{'Assets:Checking:Day':{kind:'bank',label:'Day balance'},'Liabilities:Plastic:Main':{kind:'card',label:'Travel card'}}}},review:{},fuel:{}});
  ({ evidenceKind, evidenceAction, isFileReference, rowTitle } = await server.ssrLoadModule('/app/ui.tsx'));
} finally { await server.close(); }
test('invented combined evidence retains independent kinds and actions', () => {
  const sources = ['Bills/2026-01-28/CardStatement-2026-01-28.pdf', 'Bills/2026-01-12/demo-front.jpg', 'Bills/2026-01-12/demo-back.jpg'];
  assert.deepEqual(sources.map(evidenceKind), ['Statement', 'Image', 'Image']);
  assert.deepEqual(sources.map(evidenceAction), ['Open statement', 'Open image', 'Open image']);
});
test('Bills Markdown confirmations take priority over receipt and invoice names', () => {
  for (const name of ['confirmation.md', 'demo-receipt.md', 'demo-invoice.md']) {
    assert.equal(evidenceKind('Bills/2026-01-12/' + name), 'Confirmation');
    assert.equal(evidenceAction('Bills/2026-01-12/' + name), 'Open confirmation');
  }
});

test('generic card-statement filenames are precise and case insensitive', () => {
  for (const name of ['CardStatement-2026-01-28.pdf', 'cardstatement-2026-12-28.PDF']) {
    assert.equal(evidenceKind(`Bills/2026-01-28/${name}`), 'Statement');
  }
  for (const name of ['CardStatement-Unknown.pdf', 'CardStatement-2026-01-28-extra.pdf', 'card-statement.pdf']) {
    assert.equal(evidenceKind(`Bills/2026-01-28/${name}`), 'PDF', name);
  }
});

test('Type fallbacks and explicit invoice/receipt names never depend on the directory name', () => {
  for (const [path, kind] of [
    ['bank.XLSX', 'Statement'], ['bank.xls', 'Statement'], ['bank.CSV', 'Statement'],
    ['Bills/2026-08-28/INVOICE.jpg', 'Invoice'], ['Bills/2026-08-28/Receipt.pdf', 'Receipt'],
    ['Bills/2026-08-28/order.pdf', 'PDF'], ['Bills/receipt-invoice/photo.jpg', 'Image'],
    ...['jpg', 'jpeg', 'png', 'heic', 'webp'].map(ext => [`Bills/2026-08-28/photo.${ext.toUpperCase()}`, 'Image']),
    ['Bills/2026-08-28/notes.txt', 'File'], ['notes.md', 'File'], ['Bills/2026-08-28/archive.zip', 'File'],
  ]) {
    assert.equal(evidenceKind(path), kind, path);
    assert.equal(evidenceAction(path), `Open ${kind === 'PDF' ? kind : kind.toLowerCase()}`, path);
  }
});

test('generic payment labels are removed only from a repeated title suffix', () => {
 for (const label of ['Day balance','Travel card','Card']) assert.equal(rowTitle(`Fable Shop — ${label}`), 'Fable Shop');
 assert.equal(rowTitle('Fable Shop — original description'), 'Fable Shop — original description');
});
