// Format 2 store values for Node tests (docs/STORE.md): every key present, empty values null or [].
import {CURRENT_FORMAT} from '../scripts/ledger-manifest.mjs';

export const posting = (account, amount, commodity = 'INR', extra = {}) =>
  ({account, amount, commodity, cost: null, assert: null, notes: [], ...extra});

export const entry = (overrides = {}) => ({
  id: '0000000000000001', date: '2026-01-30', status: 'confirmed', description: 'Test purchase', evidence: [], statement: null,
  references: null, classification: null, 'original-amount': null, reimbursement: null, 'excluded-from-estimate': null,
  extensions: {}, rule: null, 'possible-duplicate-of': [], notes: [],
  postings: [posting('Expenses:Test', '100.00'), posting('Assets:Cash', '-100.00')], ...overrides});

export const rule = (overrides = {}) => {
  const date = overrides.date ?? '2026-01-30';
  return {id: 'f000000000000001', date, status: 'confirmed', description: 'Test rule',
    schedule: {every: 'month', from: date, interval: 1, 'anchor-day': null}, amount: 'fixed', lifecycle: 'active',
    origin: null, created: null, removal: null, bill: null, evidence: [], references: null, classification: null,
    'original-amount': null, reimbursement: null, 'excluded-from-estimate': null, notes: [],
    postings: [posting('Expenses:Test', '100.00'), posting('Assets:Cash', '-100.00')], ...overrides};
};

export const statementRow = (values = {}) => ({end: null, serial: null, page: null, 'source-page': null, 'source-row': null, ...values});

/** Account metadata written in full for its root. */
export function metadata(account, values = {}) {
  const root = account.split(':')[0];
  if (root === 'Income' || root === 'Equity') return {...values};
  if (root === 'Expenses') return {category: null, ...values};
  const statement = values.statement ? {reader: values.statement.reader, identifier: values.statement.identifier ?? null, options: values.statement.options ?? null} : null;
  return {kind: values.kind, currency: values.currency, label: values.label ?? null, aliases: values.aliases ?? [], statement};
}

export const accounts = (declared) => Object.fromEntries(Object.entries(declared).map(([name, values]) => [name, metadata(name, values)]));

export const manifest = (overrides = {}) => ({format: CURRENT_FORMAT, id: '00000000-0000-4000-8000-000000000001', name: 'Offline fixture',
  defaultCurrency: 'INR', extensions: {}, ...overrides});
