import {useCallback,useEffect,useRef,useState,useSyncExternalStore,type FormEvent,type ReactNode} from 'react';
import './styles/first-run.css';
import {dashboard,dataVersion,reloadDataAfterWrite,subscribeData} from './data';
import {Icon,dayLabel,money,useAppearance} from './ui';
import {CurrencyOptions} from './settings-dialog';
import {request} from './live/api';
import {statementsApi,type StatementSession} from './live/statements-api';
import {today} from './date-time';
import {minorUnits} from './formatting';

/** The first-run screens of a ledger with no accounts: About you → How to start →
 * opening balances or past statements → Add. Every save is one locked, verified
 * commit on the server; nothing here writes the ledger directly. */
export type Step = 'about' | 'start' | 'today' | 'import';
export type FirstRunDone = {path:'today'|'import'; entries?:number; review?:number; unsaved?:number};
type Kind = 'bank'|'card'|'cash'|'wallet'|'loan'|'investment'|'person';
export type AccountRow = {key:string; kind:Kind; label:string; balance:string; currency:string; existing?:boolean};

// Polls statement readings and saves while any file is being read or saved.
export const POLL_MS = 1500;
const POLLED = new Set(['reading','agent_waiting','applying']);
const SETTLED = new Set(['applied','failed','couldnt_read','wrong_file','already_filed']);
const FLAG = 'ledger-first-run';

let done: FirstRunDone | null = null;
/** What Add shows once, after the first run lands there. */
export const firstRunDone = () => done;
export const clearFirstRunDone = () => { done = null; };

function flagged() { try { return localStorage.getItem(FLAG) === dashboard.meta.ledger.id; } catch { return false; } }
function setFlag(on: boolean) { try { if (on) localStorage.setItem(FLAG, dashboard.meta.ledger.id); else localStorage.removeItem(FLAG); } catch { /* the server state still decides */ } }

/** A ledger with no entries opens the first run while it has no accounts, or while
 * this browser is bringing in statements (which creates accounts before any entry). */
export function needsFirstRun() {
  const accounts = Object.values(dashboard.meta.accounts).some(meta => meta.kind);
  return dashboard.transactions.length === 0 && (!accounts || flagged());
}

const post = (body: unknown): RequestInit => ({method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
const steps: Step[] = ['about','start','today','import'];
const readStep = (): Step | null => { const value = new URLSearchParams(window.location.search).get('setup'); return steps.includes(value as Step) ? value as Step : null; };
let counter = 0, versions = 0;
const nextKey = () => `row-${++counter}`;
// Every local change to a file gets a new version; a response started before it is dropped.
const nextVersion = () => ++versions;
const accountLabel = (account: string) => dashboard.meta.accounts[account]?.label || account.split(':').at(-1) || account;

function useLedgerVersion() { return useSyncExternalStore(subscribeData, dataVersion, dataVersion); }

export default function FirstRun({onDone}: {onDone: (result: FirstRunDone) => void}) {
  useLedgerVersion();
  // Coming back while statements are being brought in (accounts exist, no entries) resumes there.
  const [step, setStep] = useState<Step>(() => readStep() || (flagged() && Object.values(dashboard.meta.accounts).some(meta => meta.kind) ? 'import' : 'about'));
  const [rows, setRows] = useState<AccountRow[]>(() => initialRows());
  const [status, setStatus] = useState('');
  const heading = useRef<HTMLHeadingElement>(null), frame = useRef<HTMLDivElement>(null);
  const first = useRef(true);
  // A row that gets shorter in the background (a form closing, a file read) must not
  // clamp scrollY and move what is under the pointer. Release the reach on scrolling up.
  useEffect(() => {
    const keep = () => { const node = frame.current; if (node) node.style.minHeight = `${Math.ceil(window.scrollY + window.innerHeight) + 1}px`; /* a fractional scroll position must not round down */ };
    frame.current?.style.removeProperty('min-height'); keep();
    window.addEventListener('scroll', keep, {passive:true}); window.addEventListener('resize', keep);
    return () => { window.removeEventListener('scroll', keep); window.removeEventListener('resize', keep); };
  }, [step]);
  const [appearance, setAppearance] = useAppearance();
  useEffect(() => {
    const pop = () => setStep(readStep() || 'about');
    const announce = (event: Event) => setStatus((event as CustomEvent<string>).detail);
    window.addEventListener('popstate', pop); window.addEventListener('ledger-status', announce);
    return () => { window.removeEventListener('popstate', pop); window.removeEventListener('ledger-status', announce); };
  }, []);
  useEffect(() => {
    document.title = 'Set up — Ledger';
    if (first.current) { first.current = false; return; }
    heading.current?.focus({preventScroll:true}); window.scrollTo({top:0, behavior:'instant'});
  }, [step]);
  const go = useCallback((next: Step) => {
    const url = new URL(window.location.href); url.searchParams.set('setup', next);
    window.history.pushState({}, '', `${url.pathname}${url.search}${url.hash}`);
    setStep(next);
  }, []);
  const finish = useCallback((result: FirstRunDone) => { setFlag(false); done = result; onDone(result); }, [onDone]);
  const title = <h1 ref={heading} tabIndex={-1}>{{about:'A little about you', start:'How would you like to start?', today:'Your accounts, as of today', import:'Let’s read your statements'}[step]}</h1>;
  return <div className="setup-frame" ref={frame}>
    <header className="setup-brand"><span className="wordmark"><span className="wordmark-badge" aria-hidden="true">A</span>AgentExpense</span>
      <button type="button" className="appearance" onClick={() => setAppearance(appearance === 'light' ? 'dark' : 'light')}><Icon name={appearance === 'light' ? 'moon' : 'sun'}/>{appearance === 'light' ? 'Dark' : 'Light'} appearance</button></header>
    <main className={`setup-content ${step === 'today' || step === 'import' ? 'wide' : ''}`} id="main-content" data-setup-step={step}>
      {step === 'about' && <About title={title} onNext={() => go('start')}/>}
      {step === 'start' && <Start title={title} onChoose={go}/>}
      {step === 'today' && <Today title={title} rows={rows} setRows={setRows} onBack={() => go('start')} onDone={finish}/>}
      {step === 'import' && <Import title={title} onBack={() => go('start')} onDone={finish}/>}
    </main>
    <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">{status}</p>
  </div>;
}

function initialRows(): AccountRow[] {
  const main = dashboard.meta.ledger.defaultCurrency;
  const declared = Object.entries(dashboard.meta.accounts).filter(([, meta]) => meta.kind && meta.currency);
  if (declared.length) return declared.map(([account, meta]) => ({key:nextKey(), kind:meta.kind as Kind, label:meta.label || accountLabel(account), balance:'', currency:meta.currency!, existing:true}));
  return [{key:nextKey(), kind:'bank', label:'', balance:'', currency:main}];
}

function Footer({children}: {children: ReactNode}) { return <footer className="setup-footer">{children}</footer>; }
function Back({to, disabled=false}: {to: () => void; disabled?: boolean}) {
  return <button type="button" className="button plain" disabled={disabled} onClick={to}><Icon name="chevron-left"/>Back</button>;
}

function About({title, onNext}: {title: ReactNode; onNext: () => void}) {
  const [name, setName] = useState(dashboard.meta.ledger.name);
  const [currency, setCurrency] = useState(dashboard.meta.ledger.defaultCurrency);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !name.trim()) return;
    setBusy(true); setError('');
    try {
      if (name.trim() !== dashboard.meta.ledger.name || currency !== dashboard.meta.ledger.defaultCurrency) {
        await request('setup/about', post({name:name.trim(), currency}), 120000);
        await reloadDataAfterWrite().catch(() => undefined);
      }
      setBusy(false); onNext();
    } catch (e) { setBusy(false); setError((e as Error).message); }
  }
  return <form onSubmit={submit} aria-busy={busy}>
    <p className="setup-step">Step 1 of 3</p>{title}
    <p className="setup-intro">A name for your records and a currency to start with.</p>
    <section className="panel"><div className="panel-body setup-form">
      <label><span className="field-label">Your name</span><input id="setup-name" className="field" autoComplete="off" placeholder="Your name" maxLength={200} value={name} readOnly={busy} onChange={e => setName(e.target.value)}/></label>
      <label><span className="field-label">Main currency</span><select id="setup-currency" className="field" value={currency} disabled={busy} onChange={e => setCurrency(e.target.value)} aria-describedby="setup-currency-help"><CurrencyOptions current={currency}/></select>
        <span className="setup-help" id="setup-currency-help">New accounts start in this currency. Any account can have its own.</span></label>
    </div></section>
    <Footer><span className="setup-footer-note">You can change both later.</span><div className="setup-footer-end"><button type="submit" className="button primary" disabled={busy || !name.trim()}>{busy ? 'Saving…' : 'Continue'}</button>{error && <p className="setup-error" role="alert">{error}</p>}</div></Footer>
  </form>;
}

function Start({title, onChoose}: {title: ReactNode; onChoose: (step: Step) => void}) {
  return <>
    <p className="setup-step">Step 2 of 3</p>{title}
    <p className="setup-intro">Choose what you want in your records. You can bring in older statements later.</p>
    <div className="setup-choices">
      <button type="button" className="setup-choice" onClick={() => onChoose('today')}><Icon name="accounts"/><div><h2>Start from today</h2><p>List your accounts and today’s balances. There will be little to review.</p></div><span className="setup-choice-arrow"><Icon name="chevron-right"/></span></button>
      <button type="button" className="setup-choice" onClick={() => onChoose('import')}><Icon name="export"/><div><h2>Bring in past statements</h2><p>Your statements are read for you. Expect a Review list to work through.</p></div><span className="setup-choice-arrow"><Icon name="chevron-right"/></span></button>
    </div>
    <Footer><Back to={() => onChoose('about')}/></Footer>
  </>;
}

/** Reads an amount as typed in the ledger's locale, checked against the currency's minor units. */
export function parseAmount(text: string, currency: string, locale = dashboard.meta.ledger.locale || navigator.language) {
  const parts = new Intl.NumberFormat(locale).formatToParts(-12345.6);
  const group = parts.find(p => p.type === 'group')?.value || ',', decimal = parts.find(p => p.type === 'decimal')?.value || '.';
  const plain = text.trim().replace(/[\s  ]/g, '').split(group).join('').replace(decimal, '.').replace(/^−/, '-');
  if (!plain) return {value:null as string | null, error:''};
  if (!/^-?\d+(\.\d+)?$/.test(plain)) return {value:null, error:'Enter a number, such as 1840.25.'};
  const places = (plain.split('.')[1] || '').length, allowed = minorUnits(currency);
  if (places > allowed) return {value:null, error:allowed ? `${currency} amounts have at most ${allowed} decimal places.` : `${currency} amounts have no decimal places.`};
  return {value:plain, error:''};
}
const owed = (kind: Kind) => kind === 'card' || kind === 'loan';
const KIND_HINT: Partial<Record<Kind, string>> = {
  card:'Include charges not yet billed, not just your last statement balance.',
  person:'Money owed between you and someone. Use a negative amount when you owe them.',
};

function Today({title, rows, setRows, onBack, onDone}: {title: ReactNode; rows: AccountRow[]; setRows: (update: (rows: AccountRow[]) => AccountRow[]) => void; onBack: () => void; onDone: (result: FirstRunDone) => void}) {
  const main = dashboard.meta.ledger.defaultCurrency, start = today();
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const added = useRef<string | null>(null);
  // Rows nobody has typed in follow a main currency chosen in About you.
  useEffect(() => { setRows(xs => xs.map(row => !row.existing && !row.label && !row.balance ? {...row, currency:main} : row)); }, [main, setRows]);
  useEffect(() => { if (added.current) { document.querySelector<HTMLInputElement>(`[data-row="${added.current}"] .account-name`)?.focus(); added.current = null; } }, [rows.length]);
  const update = (key: string, change: Partial<AccountRow>) => setRows(xs => xs.map(row => row.key === key ? {...row, ...change} : row));
  const checked = rows.map(row => ({row, amount:parseAmount(row.balance, row.currency)}));
  const names = rows.map(row => row.label.trim().toLocaleLowerCase());
  const duplicate = (row: AccountRow) => !!row.label.trim() && names.filter(name => name === row.label.trim().toLocaleLowerCase()).length > 1;
  const valid = checked.every(({row, amount}) => row.label.trim() && amount.value !== null && !duplicate(row));
  async function finish() {
    if (busy || !valid) return;
    setBusy(true); setError('');
    try {
      await request('setup/opening', post({start, accounts:checked.map(({row, amount}) => ({kind:row.kind, label:row.label.trim(), currency:row.currency, balance:amount.value}))}), 180000);
      await reloadDataAfterWrite().catch(() => undefined);
      onDone({path:'today'});
    } catch (e) { setBusy(false); setError((e as Error).message); }
  }
  return <>
    <p className="setup-step">Step 3 of 3 · Start from today</p>{title}
    <p className="setup-intro">Add each account and its balance on {dayLabel(start)}. New accounts start in {main}.</p>
    <fieldset disabled={busy} className="setup-fieldset" aria-busy={busy}>
      <section className="panel" id="setup-accounts" aria-label="Accounts">{checked.map(({row, amount}) => <div className="setup-account" key={row.key} data-row={row.key}>
        <div className="account-fields">
          <label><span className="field-label">Kind</span><select className="field account-kind" value={row.kind} disabled={row.existing} onChange={e => update(row.key, {kind:e.target.value as Kind})}>
            {(['bank','card','cash','wallet','loan'] as Kind[]).map(kind => <option key={kind} value={kind}>{kind[0].toUpperCase() + kind.slice(1)}</option>)}
            <optgroup label="More"><option value="investment">Investment</option><option value="person">Person</option></optgroup></select></label>
          <label><span className="field-label">Account name</span><input className="field account-name" value={row.label} readOnly={row.existing} maxLength={100} autoComplete="off" aria-invalid={duplicate(row) || undefined} onChange={e => update(row.key, {label:e.target.value})}/></label>
          <label><span className="field-label">{owed(row.kind) ? 'Amount owed' : 'Balance'}</span><input className="field account-balance" inputMode="decimal" autoComplete="off" value={row.balance} aria-invalid={!!amount.error || undefined} onChange={e => update(row.key, {balance:e.target.value})}/></label>
          <label><span className="field-label">Currency</span><select className="field account-currency" value={row.currency} disabled={row.existing} aria-label={`Currency for ${row.label.trim() || 'this account'}`} onChange={e => update(row.key, {currency:e.target.value})}><CurrencyOptions current={row.currency}/></select></label>
        </div>
        {KIND_HINT[row.kind] && <p className="setup-hint kind-hint">{KIND_HINT[row.kind]}</p>}
        {row.existing && <p className="setup-hint">Already in your ledger.</p>}
        {amount.error && <p className="setup-error" role="alert">{amount.error}</p>}
        {duplicate(row) && <p className="setup-error">Another account has this name. Give each a different name.</p>}
        <div className="opening-line"><p className="opening-unit">Opening balance: <span>{amount.value !== null ? money(Number(amount.value), row.currency) : amount.error ? '—' : money(0, row.currency)}</span></p>
          {rows.length > 1 && !row.existing && <button type="button" className="icon-button" aria-label={`Remove ${row.label.trim() || 'this account'}`} title="Remove" onClick={() => setRows(xs => xs.filter(x => x.key !== row.key))}><Icon name="close"/></button>}</div>
      </div>)}</section>
      <div className="setup-add-account"><button type="button" className="button plain" onClick={() => { const key = nextKey(); added.current = key; setRows(xs => [...xs, {key, kind:'bank', label:'', balance:'', currency:main}]); }}><Icon name="plus"/>Add account</button></div>
      <p className="setup-more-kinds">More kinds: Investment · Person — money owed between you and someone.</p>
    </fieldset>
    <Footer><Back to={onBack} disabled={busy}/><div className="setup-footer-end">
      <button type="button" className="button primary" disabled={busy || !valid} aria-describedby="setup-finish-hint" onClick={() => void finish()}>{busy ? 'Saving…' : 'Finish setup'}</button>
      {!valid && !busy && <p className="setup-footer-note" id="setup-finish-hint">Enter a name and balance for every account.</p>}
      {error && <p className="setup-error" role="alert">{error}</p>}</div></Footer>
  </>;
}

type FileRow = {key:string; name:string; id?:string; token?:string; session?:StatementSession; uploading?:boolean; busy?:boolean; error?:string; choice?:string; version:number};
const bankOrCard = () => Object.entries(dashboard.meta.accounts).filter(([, meta]) => meta.kind === 'bank' || meta.kind === 'card')
  .sort(([a], [b]) => accountLabel(a).localeCompare(accountLabel(b)));
const fileCurrency = (row: FileRow) => row.session?.file_currency || (row.session && ['found','review','applying','applied'].includes(row.session.state) ? row.session.statement?.currency : undefined);
const chosen = (row: FileRow) => row.choice ?? row.session?.statement?.account ?? row.session?.assigned_account ?? row.session?.suggested_account ?? '';
// A read file stays read while a mismatching account is only chosen on this page (nothing is sent).
const read = (row: FileRow) => !!row.session && ['found','review'].includes(row.session.state) && !!row.session.plan && !row.busy;
const mismatched = (row: FileRow) => { const currency = fileCurrency(row), account = chosen(row); return !!currency && !!account && !!dashboard.meta.accounts[account] && dashboard.meta.accounts[account].currency !== currency; };
const unread = (row: FileRow) => !!row.error && !row.session || !!row.session && SETTLED.has(row.session.state) && row.session.state !== 'applied';
const needsAccount = (row: FileRow) => !!row.session && ['account_needed','currency_mismatch','locked'].includes(row.session.state);
const pendingRows = (session: StatementSession) => session.plan?.items.filter(item => item.needs_user || item.decision?.action === 'pending').length || 0;

function Import({title, onBack, onDone}: {title: ReactNode; onBack: () => void; onDone: (result: FirstRunDone) => void}) {
  useLedgerVersion();
  const [rows, setRowsState] = useState<FileRow[]>([]);
  const rowsRef = useRef<FileRow[]>([]);
  const setRows = useCallback((update: (rows: FileRow[]) => FileRow[]) => { setRowsState(current => { const next = update(current); rowsRef.current = next; return next; }); }, []);
  const [loaded, setLoaded] = useState(false), [listError, setListError] = useState('');
  const [finishing, setFinishing] = useState(false), [finishError, setFinishError] = useState('');
  const [dragging, setDragging] = useState(false), [tick, setTick] = useState(0);
  const picker = useRef<HTMLInputElement>(null);
  const change = useCallback((key: string, values: Partial<FileRow>, version = nextVersion()) => { setRows(xs => xs.map(row => row.key === key ? {...row, ...values, version} : row)); return version; }, [setRows]);
  useEffect(() => {
    setFlag(true);
    let alive = true;
    statementsApi.list().then(list => {
      if (!alive) return;
      const known = new Set(rowsRef.current.map(row => row.id));
      setRows(xs => [...list.sessions.filter(s => !known.has(s.id)).sort((a, b) => a.created_at.localeCompare(b.created_at)).map(session => ({key:nextKey(), name:session.original_name, id:session.id, session, version:0})), ...xs]);
      setLoaded(true);
    }, (e: Error) => { if (alive) { setListError(e.message); setLoaded(true); } });
    return () => { alive = false; };
  }, [setRows]);
  // One poll at a time. A response for a file removed, moved or saved since is dropped.
  const polling = rows.some(row => row.session && POLLED.has(row.session.state));
  useEffect(() => {
    if (!polling) return;
    const timer = window.setTimeout(async () => {
      const targets = rowsRef.current.filter(row => row.id && row.session && POLLED.has(row.session.state));
      await Promise.all(targets.map(async row => {
        try {
          const session = await statementsApi.get(row.id!);
          setRows(xs => xs.map(x => x.key === row.key && x.version === row.version && !session.trashed_at ? {...x, session} : x));
        } catch { /* the next poll retries */ }
      }));
      setTick(value => value + 1);
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [polling, tick, setRows]);
  function add(files: File[]) {
    for (const file of files) {
      const key = nextKey(), token = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
      setRows(xs => [...xs, {key, name:file.name, token, uploading:true, version:0}]);
      statementsApi.upload(file, token).then(session => {
        change(key, {id:session.id, session, uploading:false});
        if (!rowsRef.current.some(row => row.key === key)) void statementsApi.discard(session.id).catch(() => undefined);
      }, (e: Error) => change(key, {uploading:false, error:e.message}));
    }
  }
  async function remove(row: FileRow) {
    setRows(xs => xs.filter(x => x.key !== row.key));
    try {
      if (row.id) await statementsApi.discard(row.id);
      else if (row.token) await statementsApi.cancelUpload(row.token);
    } catch (e) { setRows(xs => [...xs, {...row, error:`Could not remove this file. ${(e as Error).message}`, version:nextVersion()}]); }
  }
  async function choose(row: FileRow, account: string) {
    const current = rowsRef.current.find(x => x.key === row.key); if (!current || !account) return;
    const meta = dashboard.meta.accounts[account], currency = fileCurrency(current);
    if (currency && meta && meta.currency !== currency) { change(row.key, {choice:account, error:''}); return; }
    const version = change(row.key, {choice:account, busy:true, error:''});
    try {
      const session = await request<StatementSession>(`setup/statements/${current.id}/account`, post({account}), 30000);
      setRows(xs => xs.map(x => x.key === row.key && x.version === version ? {...x, session, choice:undefined, busy:false, version:nextVersion()} : x));
    } catch (e) { setRows(xs => xs.map(x => x.key === row.key && x.version === version ? {...x, busy:false, error:(e as Error).message} : x)); }
  }
  async function createAccount(row: FileRow, spec: {kind:string; label:string; currency:string}) {
    const result = await request<{account:string}>('setup/accounts', post(spec), 120000);
    await reloadDataAfterWrite().catch(() => undefined);
    if (rowsRef.current.some(x => x.key === row.key)) await choose(row, result.account);
  }
  async function unlock(row: FileRow, password: string) {
    const version = change(row.key, {busy:true, error:''});
    try { const session = await statementsApi.unlock(row.id!, password); setRows(xs => xs.map(x => x.key === row.key && x.version === version ? {...x, session, busy:false, version:nextVersion()} : x)); }
    catch (e) { setRows(xs => xs.map(x => x.key === row.key && x.version === version ? {...x, busy:false, error:(e as Error).message} : x)); }
  }
  async function finish() {
    const ready = rowsRef.current.filter(read);
    setFinishing(true); setFinishError('');
    try {
      const result = await request<{sessions:StatementSession[]}>('setup/import', post({sessions:ready.map(row => row.id)}), 30000);
      const queued = new Map(result.sessions.map(session => [session.id, session]));
      setRows(xs => xs.map(row => row.id && queued.has(row.id) ? {...row, session:queued.get(row.id), version:nextVersion()} : row));
    } catch (e) { setFinishing(false); setFinishError((e as Error).message); }
  }
  // Finish lands on Add once every queued statement has been saved or refused.
  const saving = rows.filter(row => row.session && ['applying','applied'].includes(row.session.state) || finishing && row.session?.state === 'failed' && row.session.plan);
  useEffect(() => {
    if (!finishing) return;
    const inFlight = rows.some(row => row.session?.state === 'applying');
    if (inFlight) return;
    const applied = rows.filter(row => row.session?.state === 'applied');
    if (!applied.length) { if (saving.length) { setFinishing(false); setFinishError('No statement could be saved. Check each file’s message.'); } return; }
    let alive = true;
    void reloadDataAfterWrite().catch(() => undefined).then(() => {
      if (alive) onDone({path:'import', entries:applied.reduce((sum, row) => sum + (row.session?.statement?.rows.length || 0), 0), review:applied.reduce((sum, row) => sum + pendingRows(row.session!), 0), unsaved:rows.filter(row => row.session?.state === 'failed').length});
    });
    return () => { alive = false; };
  }, [rows, finishing]); // eslint-disable-line react-hooks/exhaustive-deps

  const readRows = rows.filter(read);
  const entries = readRows.reduce((sum, row) => sum + (row.session?.statement?.rows.length || 0), 0);
  const review = readRows.reduce((sum, row) => sum + pendingRows(row.session!), 0);
  const mismatch = rows.some(mismatched), waiting = rows.some(row => row.uploading || row.busy || row.session && ['reading','agent_waiting'].includes(row.session.state));
  const unchosen = rows.some(row => needsAccount(row) && !mismatched(row)), failures = rows.filter(row => !finishing && unread(row)).length;
  const blocked = mismatch || waiting || unchosen;
  const statusText = finishing ? 'Saving each statement and its checks…' : mismatch ? 'Resolve the currency mismatch before finishing. Nothing has been saved.'
    : waiting ? 'Reading your files. Nothing is saved until you finish.' : unchosen ? 'Choose an account for every file, or remove it. Nothing has been saved.'
    : failures ? `${readRows.length ? 'Currencies match. ' : ''}The unread ${failures === 1 ? 'file' : 'files'} won’t be imported; you can try ${failures === 1 ? 'it' : 'them'} again later.` : readRows.length ? 'Currencies match. Nothing is saved until you finish.' : 'Add your statement files to begin.';
  return <>
    <p className="setup-step">Step 3 of 3 · Bring in past statements</p>{title}
    <p className="setup-intro">Bring bank or card statements as PDF or Excel files. Check each account and currency before finishing.</p>
    <section className="panel" aria-label="Statement files"><div className="panel-body">
      <div className={`setup-dropzone ${dragging ? 'is-dragging' : ''}`} onDragOver={e => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setDragging(true); } }} onDragLeave={() => setDragging(false)}
        onDrop={e => { setDragging(false); if (e.dataTransfer.files.length && !finishing) { e.preventDefault(); add(Array.from(e.dataTransfer.files)); } }}>
        <Icon name="export"/><strong>Drop PDF or Excel files here</strong><p>PDF, CSV or Excel. Known formats use a built-in reader; the agent reads other formats.</p>
        <button type="button" className="button" disabled={finishing} onClick={() => picker.current?.click()}>Choose files</button>
        <input ref={picker} className="sr-only" tabIndex={-1} aria-label="Choose statement files" type="file" multiple accept=".pdf,.csv,.xlsx,.xls" onChange={e => { add(Array.from(e.target.files || [])); e.target.value = ''; }}/>
      </div>
      {listError && <p className="setup-error" role="alert">{listError}</p>}
      {rows.length > 0 && <ul className="setup-files" aria-label="Files">{rows.map(row => <FileItem key={row.key} row={row} finishing={finishing} onChoose={account => void choose(row, account)} onCreate={spec => createAccount(row, spec)} onRemove={() => void remove(row)} onUnlock={password => void unlock(row, password)}/>)}</ul>}
    </div>
    <div className="import-summary" data-blocked={blocked || undefined} aria-live="polite"><Icon name={blocked ? 'alert' : 'check'}/><div><strong>{readRows.length ? `${entries} ${entries === 1 ? 'entry' : 'entries'} found, ${review} need${review === 1 ? 's' : ''} your review.` : loaded ? 'No statements read yet.' : 'Loading your files…'}</strong><p id="import-status">{statusText}</p></div></div></section>
    <Footer><Back to={onBack} disabled={finishing}/><div className="setup-footer-end">
      <button type="button" className="button primary" disabled={finishing || blocked || !readRows.length} onClick={() => void finish()}>{finishing ? 'Saving…' : `Finish with ${readRows.length} read ${readRows.length === 1 ? 'file' : 'files'}`}</button>
      {finishError && <p className="setup-error" role="alert">{finishError}</p>}</div></Footer>
  </>;
}

function FileItem({row, finishing, onChoose, onCreate, onRemove, onUnlock}: {row: FileRow; finishing: boolean; onChoose: (account: string) => void; onCreate: (spec: {kind:string; label:string; currency:string}) => Promise<void>; onRemove: () => void; onUnlock: (password: string) => void}) {
  const s = row.session, currency = fileCurrency(row), account = chosen(row), accounts = bankOrCard();
  const [adding, setAdding] = useState(false), [kind, setKind] = useState('bank'), [label, setLabel] = useState('');
  const [newCurrency, setNewCurrency] = useState(currency || dashboard.meta.ledger.defaultCurrency);
  const [creating, setCreating] = useState(false), [createError, setCreateError] = useState(''), [password, setPassword] = useState('');
  useEffect(() => { if (currency) setNewCurrency(currency); }, [currency]);
  const statement = s?.statement, count = statement?.rows.length || 0;
  const agent = s?.agent_verified || s?.reader === 'agent';
  const tag = row.uploading ? ['', 'pending', 'Uploading…'] : row.busy ? ['', 'pending', 'Working…'] : !s ? ['error', 'alert', 'Couldn’t be added']
    : s.state === 'applied' ? ['success', 'check', 'Imported'] : s.state === 'applying' ? ['', 'pending', 'Saving…']
    : ['found','review'].includes(s.state) ? ['success', 'check', agent ? 'Read by the agent' : 'Read by a built-in reader']
    : s.state === 'reading' ? ['', 'pending', 'Reading…'] : s.state === 'agent_waiting' ? ['warning', 'pending', 'Waiting for the agent']
    : s.state === 'locked' ? ['warning', 'alert', 'Needs its password'] : ['account_needed','currency_mismatch'].includes(s.state) ? ['warning', 'alert', 'Choose its account']
    : s.state === 'already_filed' ? ['', 'check', 'Already in your records'] : ['error', 'alert', 'Couldn’t be read'];
  const detail = row.uploading ? 'Sending the file to this ledger’s server.' : !s ? row.error : ['found','review','applying','applied'].includes(s.state) && statement ? `${count} ${count === 1 ? 'entry' : 'entries'} found · ${agent ? 'source values and totals checked' : 'totals checked'}`
    : s.state === 'reading' ? 'Checking its printed totals before anything is saved.' : s.state === 'agent_waiting' ? (s.waiting || 'The agent reads it as soon as it is available.')
    : s.state === 'account_needed' ? 'Choose the account this statement belongs to; then it is read.' : s.state === 'currency_mismatch' ? (currency ? `Printed in ${currency}.` : 'Choose the account this statement belongs to.')
    : s.state === 'locked' ? 'Type the file’s password. It stays in memory only.' : s.error || '';
  const warning = mismatched(row) ? `This file is in ${currency}, but ${accountLabel(account)} is in ${dashboard.meta.accounts[account].currency}. Choose ${/^[AEIOU]/.test(currency || '') ? 'an' : 'a'} ${currency} account or remove the file. Nothing has been saved.` : '';
  const selectable = !!s && !row.uploading && !['applying','applied','locked','reading','agent_waiting','already_filed'].includes(s.state) && !finishing;
  const id = `setup-file-${row.key}`;
  async function create() {
    if (creating || !label.trim()) return;
    setCreating(true); setCreateError('');
    try { await onCreate({kind, label:label.trim(), currency:newCurrency}); setAdding(false); setLabel(''); }
    catch (e) { setCreateError((e as Error).message); }
    finally { setCreating(false); }
  }
  return <li className="setup-file" data-file={row.name} data-session={row.id} data-state={row.uploading ? 'uploading' : s?.state || 'error'}>
    <span className="file-icon" aria-hidden="true"><Icon name="source"/></span>
    <div className="file-text"><strong>{row.name}</strong><span className={`setup-tag ${tag[0]}`}><Icon name={tag[1] as 'check'}/>{tag[2]}</span>{detail && <small className="file-detail">{detail}</small>}
      {s && !row.uploading && s.state !== 'locked' && <label className="import-account"><span className="field-label">{currency ? `Account · file in ${currency}` : 'Account'}</span>
        <select id={id} className="field file-account" value={account} disabled={!selectable || row.busy} aria-invalid={!!warning || undefined} onChange={e => onChoose(e.target.value)}>
          {!account && <option value="">Choose an account</option>}
          {accounts.map(([name, meta]) => <option key={name} value={name}>{accountLabel(name)} ({meta.currency})</option>)}
        </select></label>}
      {warning && <p className="currency-warning" role="alert">{warning}</p>}
      {s?.state === 'locked' && <form className="setup-password" onSubmit={e => { e.preventDefault(); if (password) onUnlock(password); }}><label><span className="field-label">File password</span><input type="password" className="field" autoComplete="off" value={password} onChange={e => setPassword(e.target.value)}/></label><button className="button" disabled={!password || row.busy}>Open</button></form>}
      {row.error && s && <p className="setup-error" role="alert">{row.error}</p>}
      {selectable && !adding && (!account || !!warning || ['account_needed','currency_mismatch'].includes(s!.state)) && <div className="setup-file-actions"><button type="button" className="text-button" onClick={() => setAdding(true)}>{currency ? `Add an account in ${currency}` : 'Add an account'}</button></div>}
      {selectable && adding && <div className="setup-new-account" role="group" aria-label="New account">
        <h3>New account</h3>
        <label><span className="field-label">Kind</span><select className="field" value={kind} onChange={e => setKind(e.target.value)}><option value="bank">Bank</option><option value="card">Card</option></select></label>
        <label><span className="field-label">Account name</span><input className="field" value={label} maxLength={100} autoComplete="off" onChange={e => setLabel(e.target.value)}/></label>
        <label><span className="field-label">Currency</span><select className="field" value={newCurrency} disabled={!!currency} onChange={e => setNewCurrency(e.target.value)}><CurrencyOptions current={newCurrency}/></select></label>
        <div className="setup-new-account-actions"><button type="button" className="button primary" disabled={creating || !label.trim()} onClick={() => void create()}>{creating ? 'Adding…' : 'Add account'}</button><button type="button" className="button" disabled={creating} onClick={() => setAdding(false)}>Cancel</button></div>
        {createError && <p className="setup-error" role="alert">{createError}</p>}
      </div>}
    </div>
    {!finishing && s?.state !== 'applied' && <button type="button" className="icon-button" aria-label={`Remove ${row.name}`} title={`Remove ${row.name}`} onClick={onRemove}><Icon name="close"/></button>}
  </li>;
}
