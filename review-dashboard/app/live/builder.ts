/** Pure sentence builder. Values are suggestions for the draft agent, never ledger writes. */
export type Source = 'typed' | 'usual' | 'agent' | 'missing';
export type Piece = { value: string; source: Source; shown: string };
export type PatternStats = { count: number; last: { date: string; description: string; amount: string; litres?: string }[]; usualPay: string | null; usualShare: number; usualExpense: string | null };
export type PatternData = { patterns: Record<string, PatternStats>; people: string[]; accounts: string[] };
export const LABELS: Record<string, string> = { amount: 'Amount', currency: 'Currency', litres: 'Litres', km: 'Trip', outlet: 'Outlet', pay: 'Paid with', date: 'Date', vehicle: 'Vehicle', service: 'Service', route: 'Route', plan: 'Plan', inr: 'In rupees', items: 'Items', place: 'Place', person: 'From', purpose: 'For', what: 'What', category: 'Category' };
export const SOURCES: Record<Source, string> = { typed: 'You typed it', usual: 'Usual for this', agent: 'The agent reads it', missing: 'Missing' };
export const PAYS: Record<string, string> = { card: 'Liabilities:Credit Cards:Rewards Card', savings: 'Assets:Bank:Savings Account', salary: 'Assets:Bank:Salary Account', wallet: 'Assets:Wallets:Shopping Wallet', appstore: 'Assets:Wallets:App Store Balance', cash: 'Assets:Cash' };
export const payLabel = (v: string) => ({ [PAYS.card]: 'Rewards card', [PAYS.savings]: 'Savings account', [PAYS.salary]: 'Salary account', [PAYS.wallet]: 'Shopping wallet', [PAYS.appstore]: 'App store balance', [PAYS.cash]: 'Cash' }[v] || v);
type Pattern = { id: string; name: string; keys?: string[]; known?: string[][]; pieces: string[]; sentence: string };
export const PATTERN_DEFS: Pattern[] = [
  { id: 'fuel', name: 'Fuel', keys: ['petrol', 'fuel', 'diesel', 'bunk'], known: [['Fable Fuels', 'fable'], ['Meadow Motors', 'meadow'], ['Juniper Fuels', 'juniper']], pieces: ['amount', 'litres', 'km', 'outlet', 'pay', 'date', 'vehicle'], sentence: 'Fuel at {outlet} for {amount}: {litres} over {km}, paid with {pay}, {date}, for {vehicle}.' },
  { id: 'ride', name: 'Auto or cab', keys: ['zipcab', 'townride', 'skylane', 'auto', 'cab'], pieces: ['amount', 'service', 'route', 'pay', 'date'], sentence: '{service} {route} for {amount}, paid with {pay}, {date}.' },
  { id: 'sub', name: 'Subscription renewal', keys: ['renewed', 'renewal', 'subscription'], known: [['Paper Lantern', 'paper lantern'], ['Seven Petals', 'seven petals'], ['Toolnest', 'toolnest'], ['Mailnest', 'mailnest'], ['Findnest', 'findnest'], ['Devnest', 'devnest'], ['Meetnest', 'meetnest'], ['Streamnest', 'streamnest']], pieces: ['service', 'amount', 'plan', 'pay', 'inr', 'date'], sentence: '{service} renewed for {amount}, plan {plan}, on {pay}, {inr} in rupees, {date}.' },
  { id: 'mealcart', name: 'Mealcart order', keys: ['mealcart'], pieces: ['amount', 'items', 'pay', 'date'], sentence: 'Mealcart order: {items} for {amount}, on {pay}, {date}.' },
  { id: 'basketdemo', name: 'Basketdemo order', keys: ['basketdemo'], pieces: ['amount', 'items', 'pay', 'date'], sentence: 'Basketdemo order: {items} for {amount}, on {pay}, {date}.' },
  { id: 'dining', name: 'Eating out', keys: ['dinner', 'lunch', 'breakfast', 'chaat', 'snacks', 'coffee'], known: [["Moss Cafe", 'moss'], ['Cloud Bakery', 'cloud'], ['Lantern Cafe', 'lantern cafe'], ['Orchid Kitchen', 'orchid kitchen']], pieces: ['amount', 'place', 'pay', 'date'], sentence: 'Eating out at {place} for {amount}, paid with {pay}, {date}.' },
  { id: 'payback', name: 'Paid back', keys: ['paid back', 'paid me back', 'repaid', 'sent me', 'returned'], pieces: ['person', 'amount', 'purpose', 'pay', 'date'], sentence: '{person} paid you back {amount} for {purpose}, into {pay}, {date}.' },
  { id: 'cardbill', name: 'Card bill payment', keys: ['card bill', 'bill payment', 'paid my card', 'credit card bill'], pieces: ['amount', 'pay', 'date'], sentence: 'Card bill of {amount} paid from {pay}, {date}.' },
  { id: 'rewards', name: 'Card rewards', keys: ['cashback', 'rewards', 'reward'], pieces: ['amount', 'pay', 'date'], sentence: 'Card rewards of {amount} credited to {pay}, {date}.' },
];
const NEW: Pattern = { id: 'new', name: 'Something new', pieces: ['what', 'amount', 'pay', 'date'], sentence: '{what} for {amount}, paid with {pay}, {date}.' };
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const words = (s: string) => new RegExp(`\\b${escape(s)}\\b`, 'i');
const validDay = (s: string) => /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(s)) && new Date(s).toISOString().slice(0, 10) === s;
export function todayInIndia(now = new Date()) {
  const parts=new Intl.DateTimeFormat('en', { timeZone:'Asia/Kolkata', year:'numeric', month:'2-digit', day:'2-digit' }).formatToParts(now);
  return ['year','month','day'].map(key=>parts.find(p=>p.type===key)!.value).join('-');
}
export function buildSentence(text: string, data: PatternData, edits: Record<string, string> = {}, hasFiles = false, today = todayInIndia()) {
  const low = text.toLowerCase().replace(/’/g, "'");
  // A repayment mentioning coffee is still a repayment; specific financial patterns win.
  const priority = [...PATTERN_DEFS.filter(p => ['payback', 'cardbill', 'rewards'].includes(p.id)), ...PATTERN_DEFS.filter(p => !['payback', 'cardbill', 'rewards'].includes(p.id))];
  // The product label is payment context, even though it contains a pattern keyword.
  const patternText = low.replace(/\brewards card\b/g, ' ');
  const pattern = priority.find(p => p.keys?.some(k => words(k).test(patternText)) || p.known?.some(([, k]) => words(k).test(patternText))) || NEW;
  const stats = data.patterns[pattern.id];
  const known = 'known' in pattern ? pattern.known?.find(([, k]) => words(k).test(low)) : undefined;
  let rest = low;
  const typed: Record<string, string> = {};
  const take = (re: RegExp, key: string, value: (m: RegExpMatchArray) => string = m => m[1]) => { const m = rest.match(re); if (!m) return false; typed[key] = value(m); rest = rest.replace(m[0], ' '); return true; };
  take(/\b\d{4}-\d{2}-\d{2}\b/, 'date', m => validDay(m[0]) ? m[0] : '') ||
    take(/\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b/, 'date', m=>{const day=`${m[3]}-${m[2].padStart(2,'0')}-${m[1].padStart(2,'0')}`;return validDay(day)?day:'';}) ||
    take(/\byesterday\b/, 'date', () => new Date(Date.parse(today) - 86400000).toISOString().slice(0, 10)) || take(/\btoday\b/, 'date', () => today) ||
    take(/\b(\d{1,2})\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s+(\d{4}))?\b/, 'date', m => { const n = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'].indexOf(m[2].slice(0,3)) + 1; const day = `${m[3] || today.slice(0,4)}-${String(n).padStart(2,'0')}-${m[1].padStart(2,'0')}`; return validDay(day) ? day : ''; });
  take(/\b(\d+(?:\.\d+)?)\s*(?:litres?|liters?|ltr|l)\b/, 'litres');
  take(/\b(\d+(?:\.\d+)?)\s*km\b/, 'km');
  const usd = take(/\$\s*(\d+(?:\.\d+)?)|\b(\d+(?:\.\d+)?)\s*(?:usd|dollars?)\b/, 'amount', m => m[1] || m[2]);
  if (usd) typed.currency = 'USD';
  if (!typed.amount) take(/(?:₹\s*|\brs\.?\s*|\binr\s*)?(\d[\d,]*(?:\.\d+)?)(?![\d.])/, 'amount', m => m[1].replace(/,/g, ''));
  for (const [key, re] of [['card', /\b(?:(?:rewards|credit) card|card)\b/], ['wallet', /\b(?:shopping wallet|wallet)\b/], ['savings', /\bsavings(?: account)?\b/], ['salary', /\bsalary(?: account)?\b/], ['cash', /\bcash\b/], ['appstore', /\b(?:appstore|app store(?: balance)?)\b/]] as const) if (take(re, 'pay', () => PAYS[key])) break;
  if (!typed.pay) take(/\bupi\b/, 'pay', () => 'UPI — bank to confirm');
  else rest = rest.replace(/\bupi\b/g, ' ');
  take(/\b(?:other vehicle|rental|rented|scooter)\b/, 'vehicle', () => 'another vehicle');
  for (const person of data.people || []) if (take(words(person), 'person', () => person)) break;
  if (pattern.id === 'payback' && !typed.person) take(/^\s*([\p{L}][\p{L}'-]*(?:\s+[\p{L}'-]+)?)(?=\s+(?:paid|repaid|sent|returned)\b)/u, 'person');
  const hit = 'keys' in pattern ? pattern.keys?.find(k => words(k).test(rest)) : undefined;
  if (hit) rest = rest.replace(words(hit), ' ');
  if (known) rest = rest.replace(words(known[1]), ' ').replace(/\bfuels?\b/, ' ');
  if (pattern.id === 'ride') take(/\b((?:from\s+.+?\s+)?to\s+.+?)\s*$/, 'route');
  rest = rest.replace(/\b(for|on|via|with|paid|the|a|an|at|rs|inr|my|i|just|got|bought|and|of|in|from)\b/g, ' ').replace(/\s+/g, ' ').trim();
  const fields: Record<string, Piece> = {};
  const set = (key: string, value: string, source: Source, shown = value) => { fields[key] = { value, source, shown }; };
  const vary = (key: string, value = '') => value ? set(key, value, 'typed') : set(key, '', hasFiles ? 'agent' : 'missing', hasFiles ? 'from your upload' : `${LABELS[key].toLowerCase()}?`);
  for (const key of [...pattern.pieces, 'currency', 'category']) {
    if (typed[key]) set(key, typed[key], 'typed');
    else if (key === 'date') set(key, today, 'usual', 'today');
    else if (key === 'currency') set(key, usd ? 'USD' : 'INR', usd ? 'typed' : 'usual');
    else if (key === 'pay' && stats?.usualPay) set(key, stats.usualPay, 'usual');
    else if (key === 'category' && stats?.usualExpense) set(key, stats.usualExpense, 'usual');
    else if (key === 'category') set(key, '', 'agent', 'the agent works it out');
    else if (key === 'vehicle') set(key, 'my bike', 'usual');
    else if (key === 'outlet' || key === 'place') vary(key, known?.[0] || rest);
    else if (key === 'service') vary(key, known?.[0] || (pattern.id === 'ride' ? hit : rest));
    else if (key === 'what' || key === 'purpose' || key === 'items') vary(key, rest);
    else if (key === 'inr' && !usd) set(key, typed.amount || '', typed.amount ? 'typed' : 'agent');
    else vary(key);
  }
  for (const [key, value] of Object.entries(edits)) if (fields[key]) set(key, value, value ? 'typed' : hasFiles ? 'agent' : 'missing');
  for (const [key, piece] of Object.entries(fields)) if (piece.value) {
    piece.shown = key === 'amount' ? `${fields.currency.value === 'USD' ? '$' : '₹'}${piece.value}` : key === 'litres' ? `${piece.value} L` : key === 'km' ? `${piece.value} km` : key === 'pay' ? payLabel(piece.value) : piece.value;
    if (key === 'date' && piece.source === 'usual' && piece.value === today) piece.shown = 'today';
  }
  return { pattern: pattern.id, name: pattern.name, sentence: pattern.sentence, fields, stats, empty: !text.trim() && !hasFiles };
}
export type BuiltSentence = ReturnType<typeof buildSentence>;
export function draftPieces(b: BuiltSentence, edits?: Record<string,string>) { return { ...(edits ? {edits:{...edits}} : {}), pattern: b.pattern, fields: Object.fromEntries(Object.entries(b.fields).map(([k, {value, source}]) => [k, {value, source}])) }; }
