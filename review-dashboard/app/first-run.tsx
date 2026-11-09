import {useWriteCompletion,useCommittedError,writeOwnedBy} from './live/write-completion';
import {subscribeWrites,writeSnapshot,retryWrite,type WriteOwner,type DirectWrite} from './live/direct-writes';
import {useActionGuard,useInteractionSnapshot} from './live/interaction';
import {useRetainedState,clearRetainedPrefix,clearRetained,retainedSnapshot} from './live/retained-state';
import {ActionFeedback,PendingLabel,useWriteOwner} from './live/pending-actions';
import {useCallback,useEffect,useLayoutEffect,useRef,useState,useSyncExternalStore,type FormEvent,type ReactNode} from 'react';
import './styles/first-run.css';
import {dashboard,dataStatus,dataVersion,reloadData,subscribeData} from './data';
import {Icon,dayLabel,money,useAppearance} from './ui';
import {CurrencyOptions} from './settings-dialog';
import {request} from './live/api';
import {statementsApi,type StatementSession} from './live/statements-api';
import {statementNeedsAttention} from './live/use-drafts';
import {ledgerCheckApi,ledgerCheckBlock,useLedgerCheckStatus} from './live/ledger-check';
import {today} from './date-time';
import {minorUnits} from './formatting';

/** The first-run screens of a ledger with no accounts: About you → How to start →
 * opening balances or past statements → Add. Every save is one locked, verified
 * commit on the server; nothing here writes the ledger directly. */
export type Step = 'about' | 'start' | 'today' | 'import';
export type FirstRunDone = {path:'today'|'import'; entries?:number; review?:number; unsaved?:number; unread?:number};
type Kind = 'bank'|'card'|'cash'|'wallet'|'loan'|'investment'|'person';
type AboutValues = {name:string;currency:string};
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
export function needsFirstRun(active=false) {
  if(dataStatus?.data_stale&&!active)return false;
  const accounts = Object.values(dashboard.meta.accounts).some(meta => meta.kind);
  // An address naming a setup step (Accounts’ “Set up opening balances”) opens it while the ledger is fresh.
  return dashboard.transactions.length === 0 && (!accounts || flagged() || readStep() !== null);
}

const post = (body: unknown): RequestInit => ({method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
const steps: Step[] = ['about','start','today','import'];
const readStep = (): Step | null => { const value = new URLSearchParams(window.location.search).get('setup'); return steps.includes(value as Step) ? value as Step : null; };
let counter = 0, versions = 0;
const nextKey = () => `row-${++counter}-${crypto.randomUUID()}`;
// Every local change to a file gets a new version; a response started before it is dropped.
const nextVersion = () => ++versions;
const accountLabel = (account: string) => dashboard.meta.accounts[account]?.label || account.split(':').at(-1) || account;

function useLedgerVersion() { return useSyncExternalStore(subscribeData, dataVersion, dataVersion); }

export default function FirstRun({onDone}: {onDone: (result: FirstRunDone) => void}) {
  useLedgerVersion();
  // Coming back while statements are being brought in (accounts exist, no entries) resumes there.
  const [step, setStep] = useState<Step>(() => readStep() || (flagged() && Object.values(dashboard.meta.accounts).some(meta => meta.kind) ? 'import' : 'about'));
  const [rows, setRows, initializeRows] = useRetainedState<AccountRow[]>('setup-rows',() => initialRows());
  const [about,setAbout]=useRetainedState<AboutValues>('setup-about',()=>({name:dashboard.meta.ledger.name,currency:dashboard.meta.ledger.defaultCurrency}));
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
  const finish = useCallback((result: FirstRunDone) => { clearRetainedPrefix('setup-');setFlag(false); done = result; onDone(result); }, [onDone]);
  const title = <h1 ref={heading} tabIndex={-1}>{{about:'A little about you', start:'How would you like to start?', today:'Your accounts, as of today', import:'Let’s read your statements'}[step]}</h1>;
  return <div className="setup-frame" ref={frame}>
    <header className="setup-brand"><span className="wordmark"><span className="wordmark-badge" aria-hidden="true">A</span>AgentExpense</span>
      <button type="button" className="appearance" onClick={() => setAppearance(appearance === 'light' ? 'dark' : 'light')}><Icon name={appearance === 'light' ? 'moon' : 'sun'}/>{appearance === 'light' ? 'Dark' : 'Light'} appearance</button></header>
    <main className={`setup-content ${step === 'today' || step === 'import' ? 'wide' : ''}`} id="main-content" data-setup-step={step}>
      {step === 'about' && <About title={title} saved={about} onSaved={setAbout} onNext={() => go('start')}/>}
      {step === 'start' && <Start title={title} onChoose={go}/>}
      {step === 'today' && <Today title={title} main={about.currency} rows={rows} setRows={setRows} initializeRows={initializeRows} onBack={() => {clearRetained(['setup-rows']);initializeRows(initialRows());go('start');}} onDone={finish}/>}
      {step === 'import' && <Import title={title} onBack={() => {clearRetainedPrefix('setup-account-');go('start');}} onDone={finish}/>}
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

function About({title, saved, onSaved, onNext}: {title: ReactNode; saved:AboutValues; onSaved:(value:AboutValues)=>void; onNext: () => void}) {
  const guard=useActionGuard();
  const [name, setName] = useRetainedState('setup-name',saved.name);
  const [currency, setCurrency] = useRetainedState('setup-currency',saved.currency);
  const [busy, setBusy] = useState(false), [error, setError] = useCommittedError(write=>write.path==='setup/about');
  const latest=useRef({name,currency});latest.current={name,currency};
  const active=useRef(true);useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
  useWriteCompletion(write=>write.path==='setup/about',write=>{onSaved(JSON.parse(write.body));});
  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (busy || !name.trim()) return;
    const ticket=guard.begin();setBusy(true); setError('');
    try {
      if (name.trim() !== saved.name || currency !== saved.currency) {
        await request('setup/about', {...post({name:name.trim(), currency}),clearRetained:retainedSnapshot(['setup-name','setup-currency'])}, 120000);
        onSaved({name:name.trim(),currency});
        await reloadData().catch(() => undefined);
      }
      setBusy(false); if(active.current&&latest.current.name===name&&latest.current.currency===currency)onNext();
    } catch (e) { setBusy(false); setError((e as Error).message); }
  }
  return <form {...guard.interaction} onSubmit={submit} aria-busy={busy}>
    <p className="setup-step">Step 1 of 3</p>{title}
    <p className="setup-intro">A name for your records and a currency to start with.</p>
    <section className="panel"><div className="panel-body setup-form">
      <label><span className="field-label">Your name</span><input id="setup-name" className="field" autoComplete="off" placeholder="Your name" maxLength={200} value={name} onChange={e => setName(e.target.value)}/></label>
      <label><span className="field-label">Main currency</span><select id="setup-currency" className="field" value={currency} onChange={e => setCurrency(e.target.value)} aria-describedby="setup-currency-help"><CurrencyOptions current={currency}/></select>
        <span className="setup-help" id="setup-currency-help">New accounts start in this currency. Any account can have its own.</span></label>
    </div></section>
    <div className="action-error-slot">{error && <p className="setup-error" role="alert">{error}</p>}</div><ActionFeedback scope="setup" path="setup/about" onRetry={item=>void retryWrite(item.id)?.then(()=>{if(writeSnapshot().find(value=>value.id===item.id)?.state!=='saved')return;const body=JSON.parse(item.body);setError('');onSaved(body);if(active.current&&latest.current.name===body.name&&latest.current.currency===body.currency)onNext();})}/><Footer><span className="setup-footer-note">You can change both later.</span><div className="setup-footer-end"><button type="submit" className="button primary" disabled={busy || !name.trim()}><PendingLabel pending={busy}>Continue</PendingLabel></button></div></Footer>
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
/** A valid typed balance with the currency's full decimal places (75 → 75.00), in the typed locale's decimal sign. */
export function fullAmount(text: string, currency: string, locale = dashboard.meta.ledger.locale || navigator.language) {
  const parsed = parseAmount(text, currency, locale);
  if (parsed.value === null) return text;
  const allowed = minorUnits(currency), [whole, fraction = ''] = parsed.value.split('.');
  if (!allowed) return text;
  const decimal = new Intl.NumberFormat(locale).formatToParts(1.5).find(p => p.type === 'decimal')?.value || '.';
  return `${whole}${decimal}${fraction.padEnd(allowed, '0')}`;
}
const owed = (kind: Kind) => kind === 'card' || kind === 'loan';
const KIND_HINT: Partial<Record<Kind, string>> = {
  card:'Include charges not yet billed, not just your last statement balance.',
  person:'Money owed between you and someone. Use a negative amount when you owe them.',
};

export function Today({title, main, rows, setRows, initializeRows=setRows, onBack, onDone}: {title: ReactNode; main:string; rows: AccountRow[]; setRows: (update: (rows: AccountRow[]) => AccountRow[]) => void; initializeRows?: (update: (rows: AccountRow[]) => AccountRow[]) => void; onBack: () => void; onDone: (result: FirstRunDone) => void}) {
  const guard=useActionGuard();
  const start = today();
  const [busy, setBusy] = useState(false), [error, setError] = useCommittedError(write=>write.path==='setup/opening'&&JSON.parse(write.body).operation_id===attempt?.id);
  const [committed,setCommitted]=useState(false),[savedNote,setSavedNote]=useState(''),[attempt,setAttempt]=useRetainedState<{signature:string;id:string}|null>('setup-opening-write',null);
  const writes=useSyncExternalStore(subscribeWrites,writeSnapshot,writeSnapshot),openingWrite=writes.findLast(item=>{try{return item.path==='setup/opening'&&JSON.parse(item.body).operation_id===attempt?.id;}catch{return false;}}),waitingOpening=!!openingWrite&&['queued','saving','waiting'].includes(openingWrite.state);
  useEffect(()=>{const saved=(event:Event)=>{const detail=(event as CustomEvent<{path:string;body:string}>).detail;if(detail?.path==='setup/opening'&&JSON.parse(detail.body).operation_id===attempt?.id){setCommitted(true);setSavedNote('Your opening balances are saved. Any newer rows are still unsaved; Continue to your ledger.');}};window.addEventListener('direct-write-saved',saved);return()=>window.removeEventListener('direct-write-saved',saved);},[attempt?.id]);
  const added = useRef<string | null>(null);
  // Rows nobody has typed in follow a main currency chosen in About you.
  useEffect(() => { if(rows.some(row=>!row.existing&&!row.label&&!row.balance&&row.currency!==main))initializeRows(xs => xs.map(row => !row.existing && !row.label && !row.balance ? {...row, currency:main} : row)); }, [main, initializeRows]);
  useEffect(() => { if (added.current) { document.querySelector<HTMLInputElement>(`[data-row="${added.current}"] .account-name`)?.focus(); added.current = null; } }, [rows.length]);
  const update = (key: string, change: Partial<AccountRow>) => setRows(xs => xs.map(row => row.key === key ? {...row, ...change} : row));
  const checked = rows.map(row => ({row, amount:parseAmount(row.balance, row.currency)}));
  const names = rows.map(row => row.label.trim().toLocaleLowerCase());
  const duplicate = (row: AccountRow) => !!row.label.trim() && names.filter(name => name === row.label.trim().toLocaleLowerCase()).length > 1;
  const valid = checked.every(({row, amount}) => row.label.trim() && amount.value !== null && !duplicate(row));
  const latestRows=useRef(rows),active=useRef(true);latestRows.current=rows;useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
  async function finish() {
    if(committed){onDone({path:'today'});return;}
    if (busy || waitingOpening || !valid) return;
    setBusy(true); setError('');
    try {
      if(attempt){const known=await request<{committed?:boolean}>('setup/operations/'+attempt.id);if(known.committed!==false){if(openingWrite&&openingWrite.state!=='saved'&&!waitingOpening)await retryWrite(openingWrite.id);setCommitted(true);setSavedNote('Your earlier opening balances are saved. Any newer rows are still unsaved; Continue to your ledger.');setBusy(false);return;}}
      const body={start, accounts:checked.map(({row, amount}) => ({kind:row.kind, label:row.label.trim(), currency:row.currency, balance:amount.value}))},signature=JSON.stringify(body),operation=attempt?.signature===signature?attempt:{signature,id:crypto.randomUUID().replaceAll('-','')};setAttempt(operation);
      await request('setup/opening', post({...body,operation_id:operation.id}), 180000);setCommitted(true);
      await reloadData().catch(() => undefined);
      if(active.current&&JSON.stringify(latestRows.current)===JSON.stringify(rows))onDone({path:'today'});else setBusy(false);
    } catch (e) { setBusy(false); setError((e as Error).message); }
  }
  return <>
    <p className="setup-step">Step 3 of 3 · Start from today</p>{title}
    <p className="setup-intro">Add each account and its balance on {dayLabel(start)}. New accounts start in {main}.</p>
    <fieldset {...guard.interaction} className="setup-fieldset" aria-busy={busy}>
      <section className="panel" id="setup-accounts" aria-label="Accounts">{checked.map(({row, amount}) => <div className="setup-account" key={row.key} data-row={row.key}>
        <div className="account-fields">
          <label><span className="field-label">Kind</span><select className="field account-kind" value={row.kind} disabled={row.existing} onChange={e => update(row.key, {kind:e.target.value as Kind})}>
            {(['bank','card','cash','wallet','loan'] as Kind[]).map(kind => <option key={kind} value={kind}>{kind[0].toUpperCase() + kind.slice(1)}</option>)}
            <optgroup label="More"><option value="investment">Investment</option><option value="person">Person</option></optgroup></select></label>
          <label><span className="field-label">Account name</span><input className="field account-name" value={row.label} readOnly={row.existing} maxLength={100} autoComplete="off" aria-invalid={duplicate(row) || undefined} onChange={e => update(row.key, {label:e.target.value})}/></label>
          <label><span className="field-label">{owed(row.kind) ? 'Amount owed' : 'Balance'}</span><input className="field account-balance" inputMode="decimal" autoComplete="off" value={row.balance} aria-invalid={!!amount.error || undefined} onChange={e => update(row.key, {balance:e.target.value})} onBlur={() => { const shown = fullAmount(row.balance, row.currency); if (shown !== row.balance) update(row.key, {balance:shown}); }}/></label>
          <label><span className="field-label">Currency</span><select className="field account-currency" value={row.currency} disabled={row.existing} aria-label={`Currency for ${row.label.trim() || 'this account'}`} onChange={e => update(row.key, {currency:e.target.value})}><CurrencyOptions current={row.currency}/></select></label>
          {rows.length > 1 && !row.existing ? <button type="button" className="icon-button account-remove" aria-label={`Remove ${row.label.trim() || 'this account'}`} title="Remove" onClick={() => setRows(xs => xs.filter(x => x.key !== row.key))}><Icon name="close"/></button> : <span className="account-remove" aria-hidden="true"/>}
        </div>
        {KIND_HINT[row.kind] && <p className="setup-hint kind-hint">{KIND_HINT[row.kind]}</p>}
        {row.existing && <p className="setup-hint">Already in your ledger.</p>}
        {amount.error && <p className="setup-error" role="alert">{amount.error}</p>}
        {duplicate(row) && <p className="setup-error">Another account has this name. Give each a different name.</p>}
        <div className="opening-line"><p className="opening-unit">Opening balance: <span>{amount.value !== null ? money(Number(amount.value), row.currency) : amount.error ? '—' : money(0, row.currency)}</span></p></div>
      </div>)}</section>
      <div className="setup-add-account"><button type="button" className="button plain" onClick={() => { const key = nextKey(); added.current = key; setRows(xs => [...xs, {key, kind:'bank', label:'', balance:'', currency:main}]); }}><Icon name="plus"/>Add account</button></div>
      <p className="setup-more-kinds">More kinds: Investment · Person — money owed between you and someone.</p>
    </fieldset>
    <div className="action-error-slot">{error && <p className="setup-error" role="alert">{error}</p>}</div><ActionFeedback notice={savedNote} scope="setup" path="setup/opening"/><Footer><Back to={onBack}/><div className="setup-footer-end">
      <button type="button" className="button primary" disabled={busy || waitingOpening || !committed&&!valid} aria-describedby="setup-finish-hint" onClick={() => void finish()}><PendingLabel pending={busy||waitingOpening} text={committed?'Continue':undefined}>Finish setup</PendingLabel></button>
      {!valid && !busy && <p className="setup-footer-note" id="setup-finish-hint">Enter a name and balance for every account.</p>}
      </div></Footer>
  </>;
}

type FileRow = {key:string; name:string; id?:string; token?:string; session?:StatementSession; uploading?:boolean; busy?:boolean; error?:string; choice?:string; accountEditing?:boolean; version:number};
const bankOrCard = (accounts=dashboard.meta.accounts) => Object.entries(accounts).filter(([, meta]) => meta.kind === 'bank' || meta.kind === 'card')
  .sort(([a,ma], [b,mb]) => (ma.label||a.split(':').at(-1)||a).localeCompare(mb.label||b.split(':').at(-1)||b));
const fileCurrency = (row: FileRow) => row.session?.file_currency || (row.session && ['found','review','applying','applied'].includes(row.session.state) ? row.session.statement?.currency : undefined);
const chosen = (row: FileRow) => row.choice ?? row.session?.statement?.account ?? row.session?.assigned_account ?? row.session?.suggested_account ?? '';
// A read file stays read while a mismatching account is only chosen on this page (nothing is sent).
/** The first sentence of a reader's message, and the technical rest. */
export const plainReason = (text: string): [string, string] => { const end = text.search(/[.!?] /); return end > 0 ? [text.slice(0, end + 1), text.slice(end + 2).trim()] : [text, '']; };
const read = (row: FileRow) => !!row.session && ['found','review'].includes(row.session.state) && !!row.session.plan && !row.busy;
const mismatched = (row: FileRow) => { const currency = fileCurrency(row), account = chosen(row); return !!currency && !!account && !!dashboard.meta.accounts[account] && dashboard.meta.accounts[account].currency !== currency; };
const unread = (row: FileRow) => !!row.error && !row.session || !!row.session && SETTLED.has(row.session.state) && row.session.state !== 'applied';
const needsAccount = (row: FileRow) => !!row.session && ['account_needed','currency_mismatch','locked'].includes(row.session.state);
const pendingRows = (session: StatementSession) => session.plan?.items.filter(item => item.needs_user || item.decision?.action === 'pending').length || 0;

export function Import({title, onBack, onDone}: {title: ReactNode; onBack: () => void; onDone: (result: FirstRunDone) => void}) {
  useLedgerVersion();
  const [rows, setRowsState] = useState<FileRow[]>([]);
  const rowsRef = useRef<FileRow[]>([]);
  const mounted = useRef(true);
  const setRows = useCallback((update: (rows: FileRow[]) => FileRow[]) => { setRowsState(current => { const next = update(current); rowsRef.current = next; return next; }); }, []);
  const [visible,filesInteraction,releaseFiles]=useInteractionSnapshot({rows,accounts:{...dashboard.meta.accounts}},rows.length>0);
  const accountEditing=useCallback((key:string,editing:boolean)=>setRows(xs=>xs.some(row=>row.key===key&&!!row.accountEditing!==editing)?xs.map(row=>row.key===key?{...row,accountEditing:editing}:row):xs),[setRows]);
  const [loaded, setLoaded] = useState(false), [listError, setListError] = useState('');
  const [finishing, setFinishing] = useState(false), [finishError, setFinishError] = useState(''),[canContinue,setCanContinue]=useState(false),[attemptLabel,setAttemptLabel]=useState('');
  const [importNotice,setImportNotice]=useState('');
  useEffect(()=>{const complete=(event:Event)=>{
    const detail=(event as CustomEvent<{path:string;body:string;result:{sessions:StatementSession[]}}>).detail;
    if(detail?.path!=='setup/import'||!mounted.current)return;
    const ids=new Set<string>(JSON.parse(detail.body).sessions);
    if(!rowsRef.current.some(row=>row.id&&ids.has(row.id)))return;
    const committed=new Map(detail.result.sessions.map(session=>[session.id,session]));
    const newer=rowsRef.current.some(row=>row.accountEditing||!ids.has(row.id||'')&&(read(row)||row.uploading||['reading','agent_waiting','locked','account_needed','currency_mismatch'].includes(row.session?.state||'')));
    setRows(rows=>rows.map(row=>row.id&&committed.has(row.id)?{...row,session:committed.get(row.id),version:nextVersion()}:row));
    setFinishError('');setFinishing(!newer);
    if(newer){setCanContinue(true);setImportNotice('Earlier files are imported. Newer files are kept in Queue; Continue to your ledger.');}
  };window.addEventListener('direct-write-saved',complete);return()=>window.removeEventListener('direct-write-saved',complete);},[setRows]);
  const [dragging, setDragging] = useState(false), [tick, setTick] = useState(0);
  // hledger's second check: importing waits for the accounts an open problem involves (the server refuses too).
  const [check, setCheck] = useLedgerCheckStatus(), [checking, setChecking] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const change = useCallback((key: string, values: Partial<FileRow>, version = nextVersion()) => { setRows(xs => xs.map(row => row.key === key ? {...row, ...values, version} : row)); return version; }, [setRows]);
  useEffect(() => {
    setFlag(true);
    mounted.current=true;
    let alive = true;
    statementsApi.list().then(list => {
      if (!alive) return;
      const known = new Set(rowsRef.current.map(row => row.id));
      setRows(xs => [...list.sessions.filter(s => !known.has(s.id)).sort((a, b) => a.created_at.localeCompare(b.created_at)).map(session => ({key:nextKey(), name:session.original_name, id:session.id, session, version:0})), ...xs]);
      setLoaded(true);
    }, (e: Error) => { if (alive) { setListError(e.message); setLoaded(true); } });
    return () => { alive = false; mounted.current=false; };
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
    releaseFiles();
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
    releaseFiles();
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
  async function createAccount(row: FileRow, spec: {kind:string; label:string; currency:string},owner?:WriteOwner) {
    const initial=rowsRef.current.find(item=>item.key===row.key),version=initial?.version,account=initial?chosen(initial):'';
    const result = await request<{account:string}>('setup/accounts', {...post(spec),writeOwner:owner,clearRetained:retainedSnapshot([`setup-account-${row.id||row.key}`])}, 120000);
    await reloadData().catch(() => undefined);
    if (mounted.current&&rowsRef.current.some(item=>item.key===row.key&&item.version===version&&chosen(item)===account)) await choose(row, result.account);
  }
  async function unlock(row: FileRow, password: string) {
    releaseFiles();
    const version = change(row.key, {busy:true, error:''});
    try { const session = await statementsApi.unlock(row.id!, password); setRows(xs => xs.map(x => x.key === row.key && x.version === version ? {...x, session, busy:false, version:nextVersion()} : x)); }
    catch (e) { setRows(xs => xs.map(x => x.key === row.key && x.version === version ? {...x, busy:false, error:(e as Error).message} : x)); }
  }
  const importResult=(rows:FileRow[]):FirstRunDone=>({path:'import',entries:rows.filter(row=>row.session?.state==='applied').reduce((sum,row)=>sum+(row.session?.statement?.rows.length||0),0),review:rows.filter(row=>row.session?.state==='applied').reduce((sum,row)=>sum+pendingRows(row.session!),0),unsaved:rows.filter(row=>['failed','found','review'].includes(row.session?.state||'')).length,unread:rows.filter(row=>!!row.session&&['couldnt_read','wrong_file'].includes(row.session.state)&&statementNeedsAttention(row.session)).length});
  async function finish() {
    if(canContinue){onDone(importResult(rowsRef.current));return;}
    if(rowsRef.current.some(row=>row.accountEditing))return;
    releaseFiles();
    const ready = rowsRef.current.filter(read);
    setAttemptLabel(`Finish with ${ready.length} read ${ready.length===1?'file':'files'}`);setFinishing(true); setFinishError('');
    try {
      const result = await request<{sessions:StatementSession[]}>('setup/import', post({sessions:ready.map(row => row.id)}), 30000);
      const queued = new Map(result.sessions.map(session => [session.id, session]));
      setRows(xs => xs.map(row => row.id && queued.has(row.id) ? {...row, session:queued.get(row.id), version:nextVersion()} : row));
    } catch (e) {
      if(!mounted.current)return;setFinishError((e as Error).message);
      try{const [latest,status]=await Promise.all([Promise.all(ready.map(row=>statementsApi.get(row.id!))),request<{fresh:boolean}>('setup')]);if(!mounted.current)return;const byId=new Map(latest.map(session=>[session.id,session]));setRows(rows=>rows.map(row=>row.id&&byId.has(row.id)?{...row,session:byId.get(row.id),version:nextVersion()}:row));if(!status.fresh)setCanContinue(true);}catch{/* The visible reason and server sessions remain available. */}
      if(mounted.current)setFinishing(false);void ledgerCheckApi.status().then(value=>{if(mounted.current)setCheck(value);},()=>undefined);
    }
  }
  async function checkAgain() {
    setChecking(true);
    try { setCheck(await ledgerCheckApi.again()); } catch (e) { setFinishError((e as Error).message); } finally { setChecking(false); }
  }
  useEffect(()=>{if(finishError&&rows.some(row=>row.session?.state==='applied'))setCanContinue(true);},[finishError,rows]);
  // Finish lands on Add once every queued statement has been saved or refused.
  const saving = rows.filter(row => row.session && ['applying','applied'].includes(row.session.state) || finishing && row.session?.state === 'failed' && row.session.plan);
  useEffect(() => {
    if (!finishing) return;
    const inFlight = rows.some(row => row.session?.state === 'applying');
    if (inFlight) return;
    const applied = rows.filter(row => row.session?.state === 'applied');
    if (!applied.length) { if (saving.length) { setFinishing(false); setFinishError('No statement could be saved. Check each file’s message.'); } return; }
    let alive = true;
    void reloadData().catch(() => undefined).then(() => {
      if (alive) onDone({path:'import', entries:applied.reduce((sum, row) => sum + (row.session?.statement?.rows.length || 0), 0), review:applied.reduce((sum, row) => sum + pendingRows(row.session!), 0), unsaved:rows.filter(row => row.session?.state === 'failed').length, unread:rows.filter(row => !!row.session && ['couldnt_read','wrong_file'].includes(row.session.state) && statementNeedsAttention(row.session)).length});
    });
    return () => { alive = false; };
  }, [rows, finishing]); // eslint-disable-line react-hooks/exhaustive-deps

  const readRows = rows.filter(read);
  const entries = readRows.reduce((sum, row) => sum + (row.session?.statement?.rows.length || 0), 0);
  const review = readRows.reduce((sum, row) => sum + pendingRows(row.session!), 0);
  const mismatch = rows.some(mismatched), waiting = rows.some(row => row.uploading || row.busy || row.session && ['reading','agent_waiting'].includes(row.session.state));
  const unchosen = rows.some(row => needsAccount(row) && !mismatched(row)), failures = rows.filter(row => !finishing && unread(row)).length;
  const editingAccount=rows.some(row=>row.accountEditing);
  const checkWait = !finishing && rows.filter(read).some(row => !!ledgerCheckBlock(check, row.session?.statement?.account));
  const blocked = mismatch || waiting || unchosen || checkWait || editingAccount;
  const statusText = finishing ? 'Saving each statement and its checks…' : checkWait ? 'The second ledger check (hledger) found a problem with these accounts, so importing waits until a check passes. Nothing has been saved.' : mismatch ? 'Resolve the currency mismatch before finishing. Nothing has been saved.'
    : waiting ? 'Reading your files. Nothing is saved until you finish.' : unchosen ? 'Choose an account for every file, or remove it. Nothing has been saved.'
    : editingAccount ? 'Save or cancel the open account form before finishing the statements.' : failures ? `${readRows.length ? 'Currencies match. ' : ''}The unread ${failures === 1 ? 'file' : 'files'} won’t be imported; you can try ${failures === 1 ? 'it' : 'them'} again later.` : readRows.length ? 'Currencies match. Nothing is saved until you finish.' : 'Add your statement files to begin.';
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
      {visible.rows.length > 0 && <ul className="setup-files" aria-label="Files" onFocusCapture={filesInteraction.onFocusCapture} onBlurCapture={filesInteraction.onBlurCapture} onPointerLeave={filesInteraction.onPointerLeave} onPointerOverCapture={event=>{if((event.target as Element).closest?.('input,select,textarea,button,summary'))filesInteraction.onPointerEnter();}}>{visible.rows.map(header => {const row=rows.find(item=>item.key===header.key)||header;return <FileItem key={row.key} row={row} header={header} headerAccounts={visible.accounts} onRelease={releaseFiles} finishing={finishing} onChoose={account => void choose(row, account)} onCreate={(spec,owner) => createAccount(row, spec,owner)} onEditing={accountEditing} onRemove={() => void remove(row)} onUnlock={password => void unlock(row, password)}/>;})}</ul>}
    </div>
    <div className="import-summary" data-blocked={blocked || undefined} aria-live="polite"><Icon name={blocked ? 'alert' : 'check'}/><div><strong>{readRows.length ? `${entries} ${entries === 1 ? 'entry' : 'entries'} found, ${review} need${review === 1 ? 's' : ''} your review.` : loaded ? 'No statements read yet.' : 'Loading your files…'}</strong><p id="import-status">{statusText}</p>{checkWait && <button type="button" className="button" disabled={checking} onClick={() => void checkAgain()}>{checking ? 'Checking…' : 'Check again'}</button>}</div></div></section>
    <div className="action-error-slot">{finishError && <p className="setup-error" role="alert">{finishError}</p>}</div><ActionFeedback notice={importNotice} scope="setup" path="setup/import"/><Footer><Back to={onBack}/><div className="setup-footer-end">
      <button type="button" className="button primary" disabled={finishing || !canContinue&&(blocked || !readRows.length)} onClick={() => void finish()}><PendingLabel pending={finishing} text={canContinue?'Continue':undefined}>{(finishing||canContinue)&&attemptLabel?attemptLabel:`Finish with ${readRows.length} read ${readRows.length === 1 ? 'file' : 'files'}`}</PendingLabel></button>
      </div></Footer>
  </>;
}

type StatementAccountSpec = {kind:string; label:string; currency:string};
const sameAccount = (a: StatementAccountSpec | null | undefined, b: StatementAccountSpec | null | undefined) => !!a && !!b && a.kind===b.kind && a.label.trim()===b.label.trim() && a.currency===b.currency;
export function FileItem({row, header=row, headerAccounts=dashboard.meta.accounts, onRelease, finishing, onChoose, onCreate, onEditing, onRemove, onUnlock}: {row: FileRow; header?:FileRow; headerAccounts?:typeof dashboard.meta.accounts; onRelease?:()=>void; finishing: boolean; onChoose: (account: string) => void; onCreate: (spec: StatementAccountSpec,owner?:WriteOwner) => Promise<void>; onEditing?:(key:string,editing:boolean)=>void; onRemove: () => void; onUnlock: (password: string) => void}) {
  const liveCurrency=fileCurrency(row),accounts=bankOrCard(headerAccounts),owner=useWriteOwner(`setup-account:${row.id||row.key}`);
  const guard=useActionGuard(),live=useRef(true),currentRow=useRef(row);currentRow.current=row;
  useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
  const [accountForm,setAccountForm,initializeAccountForm]=useRetainedState<{adding:boolean;kind:string;label:string;currency:string;currencyEdited?:boolean;submitted?:StatementAccountSpec;created?:StatementAccountSpec}>(`setup-account-${row.id||row.key}`,()=>({adding:false,kind:'bank',label:'',currency:liveCurrency||dashboard.meta.ledger.defaultCurrency}));
  const {adding,kind,label,currency:newCurrency}=accountForm;
  const editingAccount=!finishing&&adding&&!sameAccount(accountForm.created,{kind,label,currency:newCurrency});
  useLayoutEffect(()=>{onEditing?.(row.key,editingAccount);},[onEditing,row.key,editingAccount]);
  const currentForm=useRef(accountForm);currentForm.current=accountForm;
  const releaseHeader=()=>onRelease?.();
  const s=header.session,currency=fileCurrency(header),account=chosen(header);
  const labelFor=(name:string)=>headerAccounts[name]?.label||name.split(':').at(-1)||name;
  const edit=(patch:Partial<typeof accountForm>)=>{guard.touch();setAccountForm(current=>({...current,...patch}));};
  const [creating, setCreating] = useState(false), [createError, setCreateError] = useCommittedError(write=>write.path==='setup/accounts'&&writeOwnedBy(write,owner)), [password, setPassword] = useState('');
  useWriteCompletion(write=>write.path==='setup/accounts'&&writeOwnedBy(write,owner),write=>{const spec=JSON.parse(write.body) as StatementAccountSpec;initializeAccountForm(current=>({...current,created:spec}));});
  useEffect(() => { if (liveCurrency) initializeAccountForm(current=>current.currencyEdited||current.currency===liveCurrency?current:{...current,currency:liveCurrency}); }, [liveCurrency]);
  const statement = s?.statement, count = statement?.rows.length || 0;
  const agent = s?.agent_verified || s?.reader === 'agent';
  const tag = header.uploading ? ['', 'pending', 'Uploading…'] : header.busy ? ['', 'pending', 'Working…'] : !s ? ['error', 'alert', 'Couldn’t be added']
    : s.state === 'applied' ? ['success', 'check', 'Imported'] : s.state === 'applying' ? ['', 'pending', 'Saving…']
    : ['found','review'].includes(s.state) ? ['success', 'check', agent ? 'Read by the agent' : 'Read by a built-in reader']
    : s.state === 'reading' ? ['', 'pending', 'Reading…'] : s.state === 'agent_waiting' ? ['warning', 'pending', 'Waiting for the agent']
    : s.state === 'locked' ? ['warning', 'alert', 'Needs its password'] : ['account_needed','currency_mismatch'].includes(s.state) ? ['warning', 'alert', 'Choose its account']
    : s.state === 'already_filed' ? ['', 'check', 'Already in your records'] : ['error', 'alert', 'Couldn’t be read'];
  const detail = header.uploading ? 'Sending the file to this ledger’s server.' : !s ? header.error : ['found','review','applying','applied'].includes(s.state) && statement ? `${count} ${count === 1 ? 'entry' : 'entries'} found · ${agent ? 'source values and totals checked' : 'totals checked'}`
    : s.state === 'reading' ? 'Checking its printed totals before anything is saved.' : s.state === 'agent_waiting' ? (s.waiting || 'The agent reads it as soon as it is available.')
    : s.state === 'account_needed' ? 'Choose the account this statement belongs to; then it is read.' : s.state === 'currency_mismatch' ? (currency ? `Printed in ${currency}.` : 'Choose the account this statement belongs to.')
    : s.state === 'locked' ? 'Type the file’s password. It stays in memory only.' : plainReason(s.error || '')[0];
  // A file that couldn't be read gets a one-line reason; the checker's figures sit behind Details.
  const more = s && !header.uploading && !header.busy && ['failed','couldnt_read','wrong_file'].includes(s.state) ? plainReason(s.error || '')[1] : '';
  const warning = currency&&account&&headerAccounts[account]&&headerAccounts[account].currency!==currency ? `This file is in ${currency}, but ${labelFor(account)} is in ${headerAccounts[account].currency}. Choose ${/^[AEIOU]/.test(currency || '') ? 'an' : 'a'} ${currency} account or remove the file. Nothing has been saved.` : '';
  const selectable = !!row.session && !row.uploading && !['applying','applied','locked','reading','agent_waiting','already_filed'].includes(row.session.state) && !finishing;
  const currencyError=liveCurrency&&newCurrency!==liveCurrency?`This file is in ${liveCurrency}. Choose ${liveCurrency} before creating its account.`:'';
  const id = `setup-file-${row.key}`;
  async function create(retry?:DirectWrite) {
    if (creating || !label.trim() || currencyError) return;
    const spec=retry?JSON.parse(retry.body) as StatementAccountSpec:{kind,label:label.trim(),currency:newCurrency},ticket=guard.begin(),initialAccount=chosen(row);
    setAccountForm(current=>({...current,submitted:spec}));
    setCreating(true); setCreateError('');
    try { if(retry){const result=await retryWrite(retry.id) as {account:string}|undefined;if(writeSnapshot().find(value=>value.id===retry.id)?.state!=='saved')return;await reloadData().catch(()=>undefined);if(result&&live.current&&sameAccount(currentForm.current,spec)&&chosen(currentRow.current)===initialAccount)onChoose(result.account);}else await onCreate(spec,owner); if(live.current){const close=guard.unchanged(ticket)&&sameAccount(currentForm.current,spec);if(close)releaseHeader();initializeAccountForm(current=>({...current,created:spec,...(close?{adding:false,label:''}:{})}));} }
    catch (e) { if(live.current)setCreateError((e as Error).message); }
    finally { if(live.current)setCreating(false); }
  }
  return <li className="setup-file" data-file={row.name} data-session={row.id} data-state={row.uploading ? 'uploading' : row.session?.state || 'error'}>
    <span className="file-icon" aria-hidden="true"><Icon name="source"/></span>
    <div className="file-text"><strong>{row.name}</strong><span className={`setup-tag ${tag[0]}`}><Icon name={tag[1] as 'check'}/>{tag[2]}</span>{detail && <small className="file-detail">{detail}</small>}{more && <details className="file-more"><summary>Details</summary><p>{more}</p></details>}
      {s && !row.uploading && s.state !== 'locked' && <label className="import-account"><span className="field-label">{currency ? `Account · file in ${currency}` : 'Account'}</span>
        <select id={id} className="field file-account" value={account} disabled={!selectable || row.busy} aria-invalid={!!warning || undefined} onChange={e => {releaseHeader();onChoose(e.target.value);}}>
          {!account && <option value="">Choose an account</option>}
          {accounts.map(([name, meta]) => <option key={name} value={name}>{labelFor(name)} ({meta.currency})</option>)}
        </select></label>}
      {warning && <p className="currency-warning" role="alert">{warning}</p>}
      {s?.state === 'locked' && <form className="setup-password" onSubmit={e => { e.preventDefault(); if (password) onUnlock(password); }}><label><span className="field-label">File password</span><input type="password" className="field" autoComplete="off" value={password} onChange={e => setPassword(e.target.value)}/></label><button className="button" disabled={!password || row.busy}>Open</button></form>}
      {header.error && s && <p className="setup-error" role="alert">{header.error}</p>}
      {selectable && !adding && (!account || !!warning || ['account_needed','currency_mismatch'].includes(s!.state)) && <div className="setup-file-actions"><button type="button" className="text-button" onClick={() => {releaseHeader();initializeAccountForm(current=>({...current,adding:true}));}}>{currency ? `Add an account in ${currency}` : 'Add an account'}</button></div>}
      {!finishing && adding && <div className="setup-new-account" role="group" aria-label="New account">
        <h3>New account</h3>
        <label><span className="field-label">Kind</span><select className="field" value={kind} onChange={e => edit({kind:e.target.value})}><option value="bank">Bank</option><option value="card">Card</option></select></label>
        <label><span className="field-label">Account name</span><input className="field" value={label} maxLength={100} autoComplete="off" onChange={e => edit({label:e.target.value})}/></label>
        <label><span className="field-label">Currency</span><select className="field" value={newCurrency} disabled={!!liveCurrency&&!accountForm.currencyEdited} onFocus={()=>initializeAccountForm(current=>({...current,currencyEdited:true}))} onChange={e => edit({currency:e.target.value,currencyEdited:true})}><CurrencyOptions current={newCurrency}/></select></label>
        <div className="action-error-slot">{(currencyError||createError) && <p className="setup-error" role="alert">{currencyError||createError}</p>}</div>
        <ActionFeedback owner={owner} scope="setup" path="setup/accounts" match={item=>{try{return sameAccount(accountForm.submitted,JSON.parse(item.body));}catch{return false;}}} onRetry={item=>void create(item)}/>
        <div className="setup-new-account-actions"><button type="button" className="button primary" disabled={creating || !label.trim() || !!currencyError || sameAccount(accountForm.created,{kind,label,currency:newCurrency})} onClick={() => void create()}><PendingLabel pending={creating}>Add account</PendingLabel></button><button type="button" className="button" onClick={() => {releaseHeader();clearRetained([`setup-account-${row.id||row.key}`]);initializeAccountForm({adding:false,kind:'bank',label:'',currency:liveCurrency||dashboard.meta.ledger.defaultCurrency});}}>Cancel</button></div>
      </div>}
    </div>
    {!finishing && s?.state !== 'applied' && <button type="button" className="icon-button" aria-label={`Remove ${row.name}`} title={`Remove ${row.name}`} onClick={onRemove}><Icon name="close"/></button>}{(finishing||s?.state==='applied')&&<span className="setup-file-remove-space" aria-hidden="true"/>}
  </li>;
}
