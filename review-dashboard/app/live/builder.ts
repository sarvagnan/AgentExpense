import { hasExtension } from '../../scripts/ledger-modules.mjs';
import { money, currencySymbol, moneySettings } from '../../scripts/formatting.mjs';
import type { Accounts } from '../account-metadata';
import { formatCalendarDate, localDate } from '../../scripts/date-time.mjs';
import { accountLabel, paymentAccounts } from '../../scripts/account-metadata.mjs';
/** Pure sentence builder. Values are suggestions for the draft agent, never ledger writes. */
export type Source = 'typed' | 'usual' | 'agent' | 'missing';
export type Piece = { value: string; source: Source; shown: string };
export type PatternStats = { count: number; last: { date: string; description: string; amount: string; currency?: string | null; litres?: string }[]; usualPay: string | null; usualShare: number; usualExpense: string | null; usualCategory?: string | null; known?: string[] };
export type PatternData = { extensions?: Record<string, unknown>; patterns: Record<string, PatternStats>; people: string[]; accounts: string[]; payments?: Accounts; merchants?: Record<string,{name:string;expense:string;category:string;pay:string;amount:string;currency:string;pattern:string|null}> };
export const LABELS: Record<string, string> = { amount: 'Amount', currency: 'Currency', litres: 'Litres', km: 'Trip', outlet: 'Outlet', pay: 'Paid with', date: 'Date', vehicle: 'Vehicle', service: 'Service', route: 'Route', plan: 'Plan', charged: 'Charged amount', items: 'Items', place: 'Place', person: 'From', purpose: 'For', what: 'What', category: 'Category' };
const UPLOAD_READS: Record<string, string> = { what: 'description', pay: 'payment account', amount: 'amount', place: 'place', outlet: 'outlet', service: 'service', purpose: 'purpose', items: 'items', person: 'person', plan: 'plan', route: 'route', km: 'distance' };
export const SOURCES: Record<Source, string> = { typed: 'You typed it', usual: 'Usual for this', agent: 'The agent reads it', missing: 'Missing' };
export const payLabel = (value: string, accounts: Accounts = {}) => accountLabel(value, accounts);
type Pattern = { id: string; name: string; keys?: string[]; pieces: string[]; sentence: string };
export const PATTERN_DEFS: Pattern[] = [
  {id:'fuel', name:'Fuel', keys:['petrol','fuel','diesel','bunk'], pieces:['amount','litres','km','outlet','pay','date','vehicle'], sentence:'Fuel at {outlet} for {amount}: {litres} over {km}, paid with {pay}, {date}, for {vehicle}.'},
  {id:'ride', name:'Auto or cab', keys:['auto','cab','taxi','ride'], pieces:['amount','service','route','pay','date'], sentence:'{service} {route} for {amount}, paid with {pay}, {date}.'},
  {id:'sub', name:'Subscription renewal', keys:['renewed','renewal','subscription'], pieces:['service','amount','plan','pay','charged','date'], sentence:'{service} renewed for {amount}, plan {plan}, on {pay}, {charged} charged to the account, {date}.'},
  {id:'dining', name:'Eating out', keys:['dinner','lunch','breakfast','snacks','coffee'], pieces:['amount','place','pay','date'], sentence:'Eating out at {place} for {amount}, paid with {pay}, {date}.'},
  {id:'payback', name:'Paid back', keys:['payment from','repayment','paid me','my share','paid back','paid me back','repaid','sent me','returned'], pieces:['person','amount','purpose','pay','date'], sentence:'{person} paid you back {amount} for {purpose}, into {pay}, {date}.'},
  {id:'cardbill', name:'Card bill payment', keys:['card bill','bill payment','paid my card','credit card bill'], pieces:['amount','pay','date'], sentence:'Card bill of {amount} paid from {pay}, {date}.'},
  {id:'rewards', name:'Card rewards', keys:['cashback','rewards','reward'], pieces:['amount','pay','date'], sentence:'Card rewards of {amount} credited to {pay}, {date}.'},
];
export const availablePatterns = (manifest: {extensions?: unknown}) => PATTERN_DEFS.filter(p => p.id !== 'fuel' || hasExtension(manifest, 'fuel'));
const NEW: Pattern = { id: 'new', name: 'Something new', pieces: ['what', 'amount', 'pay', 'date'], sentence: '{what} for {amount}, paid with {pay}, {date}.' };
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const words = (s: string) => new RegExp(`(?<![\\p{L}\\p{N}_])${escape(s)}(?![\\p{L}\\p{N}_])`, 'iu');
/** A ride's service as typed: "Cedar Taxi" rather than the matched key "taxi"; the key alone keeps its typed case. */
function rideService(text: string, rest: string, hit?: string) {
  if (!hit) return '';
  const named = rest ? text.match(new RegExp(`(?<![\\p{L}\\p{N}_])(?:${escape(rest)}\\s+${escape(hit)}|${escape(hit)}\\s+${escape(rest)})(?![\\p{L}\\p{N}_])`, 'iu'))?.[0] : undefined;
  return named ?? text.match(words(hit))?.[0] ?? hit;
}
const validDay = (s: string) => /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(s)) && new Date(s).toISOString().slice(0, 10) === s;
export function buildSentence(text: string, data: PatternData, edits: Record<string, string> = {}, hasFiles = false, today = localDate()) {
  const low = text.toLowerCase().replace(/’/g, "'");
  // A repayment mentioning coffee is still a repayment; specific financial patterns win.
  const definitions = availablePatterns(data);
  const priority = [...definitions.filter(p => ['payback', 'cardbill', 'rewards'].includes(p.id)), ...definitions.filter(p => !['payback', 'cardbill', 'rewards'].includes(p.id))];
  const payments = paymentAccounts(data.payments);
  // Remove specific payment names before looking for category trigger words.
  let patternText = low;
  for (const [account, meta] of Object.entries(payments)) for (const word of [account, accountLabel(account, payments), ...(meta.aliases || [])]) {
    if (!['bank','card','cash','wallet','loan','person'].includes(word.toLowerCase())) patternText = patternText.replace(words(word), ' ');
  }
  const merchant = Object.entries(data.merchants || {}).sort(([a],[b])=>b.length-a.length).find(([name])=>words(name).test(patternText))?.[1];
  const pattern = priority.find(p => p.keys?.some(k => words(k).test(patternText)) || merchant?.pattern===p.id) || NEW;
  const stats = data.patterns[pattern.id];
  const known = merchant?.name;
  let rest = low;
  const typed: Record<string, string> = {};
  const take = (re: RegExp, key: string, value: (m: RegExpMatchArray) => string = m => m[1]) => { const m = rest.match(re); if (!m) return false; typed[key] = value(m); rest = rest.replace(m[0], ' '); return true; };
  take(/\b\d{4}-\d{2}-\d{2}\b/, 'date', m => validDay(m[0]) ? m[0] : '') ||
    take(/\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b/, 'date', m=>{const day=`${m[3]}-${m[2].padStart(2,'0')}-${m[1].padStart(2,'0')}`;return validDay(day)?day:'';}) ||
    take(/\byesterday\b/, 'date', () => new Date(Date.parse(today) - 86400000).toISOString().slice(0, 10)) || take(/\btoday\b/, 'date', () => today) ||
    take(/\b(\d{1,2})\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s+(\d{4}))?\b/, 'date', m => { const n = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'].indexOf(m[2].slice(0,3)) + 1; const day = `${m[3] || today.slice(0,4)}-${String(n).padStart(2,'0')}-${m[1].padStart(2,'0')}`; return validDay(day) ? day : ''; });
  if (hasExtension(data, 'fuel')) {
    take(/\b(\d+(?:\.\d+)?)\s*(?:litres?|liters?|ltr|l)\b/, 'litres');
    take(/\b(\d+(?:\.\d+)?)\s*km\b/, 'km');
  }
  // Explicit ISO codes and recognizable currency symbols are evidence. An
  // unmarked amount inherits its selected payment account's currency below.
  const codes = [...new Set([moneySettings().defaultCurrency, ...Object.values(payments).map(meta => meta.currency), ...Intl.supportedValuesOf('currency')].filter((code): code is string => !!code && /^[A-Z]{3}$/.test(code)))];
  for (const code of codes) {
    const symbol = currencySymbol(code);
    const marker = `(?:(?<![a-z])${escape(code)}(?![a-z])${symbol && symbol !== code ? `|${escape(symbol)}` : ''})`;
    if (take(new RegExp(`${marker}\\s*(\\d[\\d,]*(?:\\.\\d+)?)|(?:\\b)(\\d[\\d,]*(?:\\.\\d+)?)\\s*${marker}(?![a-z])`, 'i'), 'amount', m => (m[1] || m[2]).replace(/,/g, ''))) { typed.currency = code; break; }
  }
  if (!typed.amount) take(/(\d[\d,]*(?:\.\d+)?)(?![\d.])/, 'amount', m => m[1].replace(/,/g, ''));
  const generic = new Set(['card','bank','cash','wallet','loan','person']);
  const matches = new Set<string>();
  let ambiguousPay = false;
  const named = Object.entries(payments).filter(([, meta]) => pattern.id !== 'payback' || meta.kind !== 'person').flatMap(([account, meta]) => [account, accountLabel(account, payments), ...(meta.aliases || [])].filter(word => !generic.has(word.toLowerCase())).map(word => ({account, word})));
  const longest = named.filter(({word}) => words(word).test(rest)).sort((a,b) => b.word.length-a.word.length);
  // Prefer a full label over a shorter alias contained in it.
  for (const {word} of longest) if (words(word).test(rest)) {
    for (const hit of named.filter(hit => hit.word.toLowerCase() === word.toLowerCase())) matches.add(hit.account);
    rest = rest.replace(words(word), ' ');
  }
  if (matches.size === 1) typed.pay = [...matches][0];
  else if (matches.size > 1) ambiguousPay = true;
  if (!matches.size) for (const kind of generic) {
    const phrase = kind === 'bank' ? /\bbank(?: transfer)?\b/i : kind === 'card' ? /\b(?:credit )?card\b/i : words(kind);
    if (!phrase.test(rest)) continue;
    const candidates = Object.keys(payments).filter(account => payments[account].kind === kind);
    if (candidates.length === 1) typed.pay = candidates[0]; else ambiguousPay = true;
    rest = rest.replace(phrase, ' ');
    break;
  }
  if (hasExtension(data, 'fuel')) take(/\b(?:other vehicle|rental|rented|scooter)\b/, 'vehicle', () => 'another vehicle');
  for (const person of data.people || []) if (take(words(person), 'person', () => person)) break;
  if (pattern.id === 'payback' && !typed.person) take(/^\s*([\p{L}][\p{L}'-]*(?:\s+[\p{L}'-]+)?)(?=\s+(?:paid|repaid|sent|returned)\b)/u, 'person');
  const hit = 'keys' in pattern ? pattern.keys?.find(k => words(k).test(rest)) : undefined;
  if (hit) rest = rest.replace(words(hit), ' ');
  if (known) rest = rest.replace(words(known), ' ');
  if (pattern.id === 'ride') take(/\b((?:from\s+.+?\s+)?to\s+.+?)\s*$/, 'route');
  // "with receipt" refers to the attached file, never to what was bought.
  rest = rest.replace(/\b(?:with\s+(?:a\s+|the\s+)?)?receipts?\b/gi, ' ');
  rest = rest.replace(/\b(for|on|via|with|paid|the|a|an|at|my|i|just|got|bought|and|of|in|from)\b/g, ' ').replace(/\s+/g, ' ').trim();
  // Taken pieces leave their separators behind ("café, , ,"): keep only the words, in the case they were typed.
  rest = rest.split(/\s*[,;]\s*/).map(part => part.replace(/^[\s.:·–—-]+|[\s.:·–—-]+$/g, '')).filter(Boolean).join(', ')
    .split(' ').map(word => text.match(words(word))?.[0] ?? word).join(' ').replace(/^(?:to\s+)+|(?:\s+to)+$/gi, '');
  const fields: Record<string, Piece> = {};
  const set = (key: string, value: string, source: Source, shown = value) => { fields[key] = { value, source, shown }; };
  // Pieces the agent will read from a file name what they are, so several never read as one repeated chip.
  const vary = (key: string, value = '') => value ? set(key, value, 'typed') : set(key, '', hasFiles ? 'agent' : 'missing', hasFiles ? `${UPLOAD_READS[key] || LABELS[key].toLowerCase()} from your upload` : `${LABELS[key].toLowerCase()}?`);
  for (const key of [...pattern.pieces, 'currency', 'category']) {
    if (typed[key]) set(key, typed[key], 'typed');
    else if (key === 'date') set(key, today, 'usual', 'today');
    else if (key === 'currency') set(key, payments[edits.pay || typed.pay || merchant?.pay || '']?.currency || moneySettings().defaultCurrency || Object.values(payments)[0]?.currency || '', 'usual');
    else if (key === 'pay' && !ambiguousPay && merchant?.pay && payments[merchant.pay]) set(key, merchant.pay, 'usual');
    else if (key === 'category' && merchant?.expense) set(key, merchant.expense, 'usual')
    else if (key === 'amount' && merchant?.amount) set(key, merchant.amount, 'usual');
    else if (key === 'category') set(key, '', 'agent', 'the agent works it out');
    else if (key === 'outlet' || key === 'place') vary(key, known || rest);
    else if (key === 'service') vary(key, known || (pattern.id === 'ride' ? rideService(text, rest, hit) : rest));
    else if (key === 'what' || key === 'purpose' || key === 'items') vary(key, rest);
    else if (key === 'charged' && !typed.currency) set(key, typed.amount || '', typed.amount ? 'typed' : 'agent');
    else vary(key);
  }
  for (const [key, value] of Object.entries({...edits, ...(edits.inr && !edits.charged ? {charged: edits.inr} : {})})) if (fields[key]) set(key, value, value ? 'typed' : hasFiles ? 'agent' : 'missing');
  for (const [key, piece] of Object.entries(fields)) if (piece.value) {
    piece.shown = key === 'amount' ? fields.currency.value ? money(Number(piece.value), fields.currency.value) : piece.value
      : key === 'charged' && payments[fields.pay?.value]?.currency ? money(Number(piece.value), payments[fields.pay.value].currency) : key === 'litres' ? `${piece.value} L` : key === 'km' ? `${piece.value} km` : key === 'pay' ? payLabel(piece.value, payments) : piece.value;
    if (key === 'date') piece.shown = formatCalendarDate(piece.value);
    if (key === 'category' && piece.source === 'usual') piece.shown = merchant?.category || piece.value;
    if (key === 'date' && piece.source === 'usual' && piece.value === today) piece.shown = 'today';
  }
  return { pattern: pattern.id, name: pattern.name, sentence: pattern.sentence, fields, stats, empty: !text.trim() && !hasFiles };
}
export type BuiltSentence = ReturnType<typeof buildSentence>;
export function draftPieces(b: BuiltSentence, edits?: Record<string,string>) { return { ...(edits ? {edits:Object.fromEntries(Object.entries(edits).filter(([key]) => !['litres','km','vehicle','outlet'].includes(key) || key in b.fields))} : {}), pattern: b.pattern, fields: Object.fromEntries(Object.entries(b.fields).map(([k, {value, source}]) => [k, {value, source}])) }; }
