import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { applicationRoot, ledgerRoot } from '../../scripts/project-paths.mjs';
import { moduleSettings, readManifest, validate } from './ledger-manifest.mjs';
import {complexRule,mainPostings,payer,varyingDetails,periodFor} from './recurring-ui.mjs';

const storeRoot = ledgerRoot();
export const compact = (value) => String(value ?? '').replace(/\s+/g, ' ').trim();
const isoDay = (date) => date.toISOString().slice(0, 10);

// Match the old hledger quantity conversion, including its floating-point rounding.
export function decimalNumber(text) {
  if (typeof text !== 'string' || !/^-?(0|[1-9]\d*)(?:\.\d+)?$/.test(text)) {
    throw new Error(`Expected a decimal string: ${JSON.stringify(text)}`);
  }
  const places = text.split('.')[1]?.length || 0;
  const value = Number(text.replace('.', '')) / (10 ** places);
  if (!Number.isFinite(value)) throw new Error(`Non-finite decimal: ${text}`);
  return value === 0 ? 0 : value;
}

function checkDate(day) {
  if (typeof day !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(day)
      || Number.isNaN(Date.parse(`${day}T00:00:00Z`)) || isoDay(new Date(`${day}T00:00:00Z`)) !== day) {
    throw new Error(`Invalid store date: ${day}`);
  }
}

// A real zero-value entry without assertions still belongs in the dashboard.
export function isAssertionOnly(entry) {
  return entry.postings.length > 0 && entry.postings.every((posting) =>
    decimalNumber(posting.amount) === 0 && posting.assert != null);
}

export function toTransaction(entry, accounts = {}) {
  checkDate(entry.date);
  if (!['confirmed', 'pending'].includes(entry.status)) throw new Error(`Invalid status for ${entry.id}: ${entry.status}`);
  if (entry.sources !== undefined && !Array.isArray(entry.sources)) throw new Error(`Invalid sources for ${entry.id}`);
  if (!Array.isArray(entry.postings) || !entry.postings.length) throw new Error(`Missing postings for ${entry.id}`);
  return {
    ...entry,
    sources: entry.sources ?? [],
    description: compact(entry.description),
    postings: entry.postings.map((posting) => {
      const quantity = decimalNumber(posting.amount);
      const result = { account: compact(posting.account), amount: quantity, currency: posting.commodity };
      // Costs retain their quote currency, signed like the posting quantity.
      if (posting.cost) {
        const value = decimalNumber(posting.cost.amount);
        result.costCurrency = posting.cost.commodity;
        result.cost = Number((Math.sign(quantity) * Math.abs(posting.cost.per === 'total' ? value : value * quantity)).toFixed(2));
      }
      return result;
    }),
  };
}

export function parsePrices(text) {
  const priceMap = new Map();
  for (const line of text.split(/\r?\n/).filter((line) => line.trim() && !/^\s*;/.test(line))) {
    const match = line.match(/^P (\d{4}-\d{2}-\d{2}) "?([^" ]+)"? (\d+(?:\.\d+)?) ([A-Z]{3})$/);
    if (!match) throw new Error(`Unexpected price line: ${line}`);
    const series = priceMap.get(`${match[2]}\0${match[4]}`) || { commodity: match[2], currency: match[4], dates: [], navs: [] };
    series.dates.push(match[1]);
    series.navs.push(Number(match[3]));
    priceMap.set(`${match[2]}\0${match[4]}`, series);
  }
  const prices = [...priceMap.values()].sort((a, b) => a.commodity.localeCompare(b.commodity));
  for (const series of prices) {
    if (series.dates.some((day, index) => index && day <= series.dates[index - 1])) throw new Error(`Prices for ${series.commodity} are not in date order`);
  }
  return prices;
}

export function scheduleText({ every, interval = 1, from }) {
  if (!['month', 'year'].includes(every) || !Number.isSafeInteger(interval) || interval < 1) {
    throw new Error(`Unsupported forecast schedule: ${JSON.stringify({ every, interval, from })}`);
  }
  checkDate(from);
  return `${interval === 1 ? (every === 'month' ? 'monthly' : 'yearly') : `every ${interval} ${every}s`} from ${from}`;
}

// Always offset from the original anchor: Jan 30 -> Feb 28 -> Mar 30, without drift.
export function occurrenceDate(from, months, anchorDay) {
  const [year, month, fromDay] = from.split('-').map(Number);
  const day = anchorDay ?? fromDay;
  const target = new Date(`${from}T00:00:00Z`);
  target.setUTCDate(1);
  target.setUTCFullYear(year, month - 1 + months, 1);
  const end = new Date(target);
  end.setUTCMonth(end.getUTCMonth() + 1, 0);
  target.setUTCDate(Math.min(day, end.getUTCDate()));
  return isoDay(target);
}

/** Expand [start, end), retaining input rule order for occurrences on the same date. */
export function expandForecast(rules, start, end) {
  checkDate(start);
  checkDate(end);
  if (end <= start) throw new Error(`Invalid forecast window: ${start}..${end}`);
  const occurrences = rules.flatMap((rule) => {
    if ((rule.lifecycle ?? 'active') !== 'active') return [];
    const text = scheduleText(rule.schedule);
    const { every, interval = 1, from } = rule.schedule;
    const step = interval * (every === 'year' ? 12 : 1);
    const monthDifference = (Number(start.slice(0, 4)) - Number(from.slice(0, 4))) * 12
      + Number(start.slice(5, 7)) - Number(from.slice(5, 7));
    const result = [];
    if(rule.bill) {
      const date=rule.bill.pay_by||rule.bill.occurrence;
      if(date>=start&&date<end)result.push({...rule,date,rule:text,sources:rule.bill.sources,billOccurrence:true,postings:rule.postings.map(p=>({...p,amount:Math.sign(p.amount)*Number(rule.bill.amount)}))});
    }
    for (let index = Math.max(0, Math.floor(monthDifference / step)); ; index++) {
      const date = occurrenceDate(from, index * step, rule.schedule['anchor-day']);
      if (date >= end) break;
      if (date >= start && (!rule.bill||date>rule.bill.occurrence)) result.push({ ...rule, date, rule: text, ...(rule.bill?{billOccurrence:false}: {}) });
    }
    return result;
  });
  return occurrences.sort((a, b) => a.date.localeCompare(b.date));
}

/** Read the JSON source once for both dashboard and review builders. Never read generated ledger text. */
export async function loadExpenseStore(root = storeRoot) {
  const manifest = await readManifest(root);
  const expenseDir = path.join(root, 'data', 'expenses');
  const files = (await readdir(expenseDir).catch(error => { if (error.code === 'ENOENT') return []; throw error; })).filter((name) => name.endsWith('.json')).sort();
  const readJson = async (file) => JSON.parse(await readFile(file, 'utf8'));
  const [years, rawRules, priceText, declarations] = await Promise.all([
    Promise.all(files.map((name) => readJson(path.join(expenseDir, name)))),
    readJson(path.join(root, 'data', 'forecast.json')),
    moduleSettings(manifest).investments !== false ? readFile(path.join(root, 'data', 'prices', 'mutual-funds.prices'), 'utf8').catch(error => { if (error.code === 'ENOENT') return ''; throw error; }) : '',
    readJson(path.join(root, 'data', 'accounts.json')),
  ]);
  const schema = await readJson(path.join(applicationRoot, 'schema/store.schema.json'));
  const recurringSettings = await readJson(path.join(root, 'data/recurring-settings.json')).catch(error => { if (error.code === 'ENOENT') return {'never-automatic': []}; throw error; });
  const trash = await readJson(path.join(root, 'data/trash.json')).catch(error => { if (error.code === 'ENOENT') return []; throw error; });
  validate(trash, schema.$defs.trash, 'data/trash.json', schema);
  const trashedIds = new Set(trash.map(item => item.entry.id));
  if (trashedIds.size !== trash.length || new Set(trash.map(item=>item.id)).size !== trash.length) throw new Error('Trash IDs must be unique');
  const storedPaths=new Set();
  for (const item of trash) {
    const originals=[...item.files.map(file=>file.original),...item.kept.map(file=>file.path)];
    const sources=(item.entry.sources||[]).filter(source=>source.startsWith('Bills/'));
    if (new Set(originals).size!==originals.length || originals.length!==sources.length || sources.some(source=>!originals.includes(source)) || originals.some(source=>source.split('/').includes('..'))) throw new Error('Trash evidence must map every Bills source exactly once');
    for (const file of item.files) {
      if (!file.stored.startsWith(`data/trash-files/${item.id}/`) || storedPaths.has(file.stored)) throw new Error('Trash files must stay inside their operation folder');
      storedPaths.add(file.stored);
    }
  }
  validate(recurringSettings, schema.$defs['recurring-settings'], 'data/recurring-settings.json', schema);
  validate(declarations, schema.$defs.accounts, 'data/accounts.json', schema);
  for (const metadata of Object.values(declarations.accounts)) {
    if (metadata.aliases?.some(alias => alias !== alias.toLowerCase())) throw new Error('Account aliases must be lowercase.');
  }
  if (years.some((year) => !Array.isArray(year)) || !Array.isArray(rawRules)) throw new Error('Store entries must be arrays');
  if (rawRules.some(rule => Object.hasOwn(rule, 'possible-duplicate-of'))) throw new Error('Forecasts cannot contain possible duplicate links');
  for (const rule of rawRules) {
    for (const field of ['lifecycle', 'origin', 'created', 'removal', 'schedule','amount','bill']) {
      if (Object.hasOwn(rule, field)) validate(rule[field], schema.$defs.rule.properties[field], `rule.${field}`, schema);
    }
    if (Object.hasOwn(rule, 'rule')) throw new Error('Forecasts cannot link rule occurrences');
    if(rule.amount==='varies') {
      const main=mainPostings(rule)[0],pay=payer(rule,declarations.accounts);
      if(complexRule(rule)||!pay||rule.postings.some(p=>p.assert)||main.commodity!==pay.commodity||decimalNumber(main.amount)!==-decimalNumber(pay.amount))throw new Error('Only simple two-posting payments in one currency can vary');
    }
    if(rule.bill&&(rule.amount!=='varies'||decimalNumber(rule.bill.amount)<=0||rule.bill.occurrence!==rule.date||periodFor(rule,rule.bill.occurrence)!==rule.bill.occurrence))throw new Error('Bill must name the current occurrence of a varying rule');
  }
  const actuals = years.flat();
  const ids = new Set();
  for (const entry of [...actuals, ...rawRules]) {
    if (typeof entry.id !== 'string' || !/^[0-9a-f]{16}$/.test(entry.id) || ids.has(entry.id)) throw new Error(`Invalid or duplicate store ID: ${entry.id}`);
    ids.add(entry.id);
  }
  const actualIds = new Set(actuals.map(entry => entry.id));
  if ([...trashedIds].some(id=>actualIds.has(id))) throw new Error('Trash entries must be absent from the ledger');
  for (const entry of actuals) {
    if (entry.rule) {
      validate(entry.rule, schema.$defs.entry.properties.rule, 'entry.rule', schema);
      if (!rawRules.some(rule => rule.id === entry.rule.id)) throw new Error('Unknown rule link');
    }
    if (!entry['possible-duplicate-of']) continue;
    validate(entry['possible-duplicate-of'], schema.$defs.entry.properties['possible-duplicate-of'], 'possible-duplicate-of', schema);
    const targets = entry['possible-duplicate-of'].map(link => link.id);
    if (new Set(targets).size !== targets.length || targets.join() !== [...targets].sort().join()
        || targets.some(id => !actualIds.has(id) || id <= entry.id)) throw new Error('Duplicate links must name unique, sorted, greater actual entry IDs');
  }
  if (actuals.some((entry, index) => index && entry.date < actuals[index - 1].date)) throw new Error('Expenses are not in date order');
  // Normalize before filtering so bad assertion entries cannot silently bypass input checks.
  const transactions = actuals.map(entry => toTransaction(entry, declarations.accounts)).filter((_, index) => !isAssertionOnly(actuals[index]));
  const rules = rawRules.map((rule) => {
    scheduleText(rule.schedule);
    if (occurrenceDate(rule.schedule.from, 0, rule.schedule['anchor-day']) !== rule.date) throw new Error('Rule anchor and date disagree');
    if (rule.lifecycle === 'removed' && !rule.removal) throw new Error('Removed rule needs removal evidence');
    if ([...(rule.origin?.evidence ?? []), ...(rule.removal?.evidence ?? [])].some(id => !actualIds.has(id) && !trashedIds.has(id))) throw new Error('Unknown rule evidence');
    return {...toTransaction(rule, declarations.accounts),...(rule.amount==='varies'?{varying:varyingDetails(rule,actuals,declarations.accounts)}:{})};
  });
  return { transactions, rules, rawRules, recurringSettings, prices: parsePrices(priceText), accounts: declarations.accounts, manifest };
}
