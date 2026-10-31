import { useCallback, useEffect, useMemo, useLayoutEffect, useRef, useState, useSyncExternalStore, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { dashboard as data, review as reviewData, type DashboardData } from './data';
import Review from './review/page';
import Add from './live/add';
import {useAnswerGuard} from './live/answers';
import {useComposer} from './live/composer';
import {useLiveFocus} from './live/focus';
import Drafts from './live/drafts';
import Statements from './live/statements';
import { useDrafts } from './live/use-drafts';
import { subscribeData, dataVersion } from './data';
import AccountSummary from './account-summary';
import { displayAccountLabel, summarizeAccounts } from './account-labels';
import { paymentSide } from './payment-side';
import { useEntryTextTooltip } from './entry-text-tooltip';
import Forecast from './forecast';
import { sortTransactions, type SortKey, type SortDirection } from './transaction-sort';
import {
  EntryLink, entryUrl, useSlidingPane, useDocumentScrollLock, Delta, Dialog, Icon, MonthStepper, accountLabel, EvidenceName, evidenceKind, rowTitle, evidenceAction, hasSelectedText, breakable, isTyping, modalOpen, trapModalTab, useScrollMemory, useAppearance, evidenceHref, isFileReference, announce, copyAndAnnounce, useColumnWidths, usePaneWidth, useContextMenu, useWindowActivity, type MenuItem, PageHeader, PanelHeader, SearchField, compactInr, copyText, dayLabel, dayShort, daysInMonth, inr,
  money, monthLong, monthName, monthShort, plural, useEscape, useQueryState, useRowNavigation, type IconName,
} from './ui';

export type View = 'explorer' | 'atlas' | 'journal' | 'forecast' | 'review' | 'accounts' | 'add' | 'drafts' | 'statements';
type Transaction = DashboardData['transactions'][number];
type Month = DashboardData['months'][number];

export const VIEWS: { id: View; label: string; href: string; icon: IconName; carriesMonth: boolean }[] = [
  { id: 'explorer', label: 'Explorer', href: 'explorer/', icon: 'explorer', carriesMonth: true },
  { id: 'atlas', label: 'Atlas', href: 'atlas/', icon: 'atlas', carriesMonth: true },
  { id: 'journal', label: 'Journal', href: 'journal/', icon: 'journal', carriesMonth: true },
  { id: 'forecast', label: 'Forecast', href: 'forecast/', icon: 'forecast', carriesMonth: false },
  { id: 'accounts', label: 'Accounts', href: 'account-summary/', icon: 'accounts', carriesMonth: false },
  { id: 'review', label: 'Review', href: 'review/', icon: 'review', carriesMonth: false },
  { id: 'add', label: 'Add', href: 'add/', icon: 'pencil', carriesMonth: false },
  { id: 'drafts', label: 'Drafts', href: 'drafts/', icon: 'stack', carriesMonth: false },
  { id: 'statements', label: 'Statements', href: 'statements/', icon: 'source', carriesMonth: false },
];

const MONEY_IN = new Set(['Income', 'Refund', 'Reimbursement']);
/** Expenses show plain; money coming in shows "+"; transfers and reimbursable purchases move money without spending it. */
export function signedAmount(transaction: Pick<Transaction, 'kind' | 'amount'>) {
  return `${MONEY_IN.has(transaction.kind) ? '+' : ''}${inr(transaction.amount)}`;
}
const amountClass = (transaction: Pick<Transaction, 'kind'>) => MONEY_IN.has(transaction.kind) ? 'credit' : transaction.kind === 'Transfer' || transaction.kind === 'Reimbursable purchase' ? 'neutral-amount' : '';
const monthKeys = () => data.months.map((month) => month.month);
const describe = (item: { description: string }) => item.description;
const fullEntryText = (item: Transaction) => `${item.description}. ${item.category}. ${item.displayType}. ${signedAmount(item)}. Payment account: ${paymentSide(item).full}. From ${routeSide(item, -1).full}. To ${routeSide(item, 1).full}. ${item.status === 'Pending' ? 'Unclassified' : 'Cleared'}`;
const searchText = (item: Transaction) => `${item.description} ${item.ledgerAccount} ${item.paymentAccount} ${item.paymentLabel} ${item.postings.map(p => `${p.account} ${p.label} ${displayAccountLabel(p.account)}`).join(' ')} ${item.amount.toFixed(2)}`;
/** What ⌘C copies for a selected entry: one tab-separated line. */
const copyLine = (item: Transaction) => [dayLabel(item.date), item.description, signedAmount(item), item.ledgerAccount].join('\t');
/** Opens another view through the app's own link handling, so the page doesn't reload. */
export function navigate(path: string) {
  const link = document.createElement('a');
  link.href = `${import.meta.env.BASE_URL}${path}`;
  document.body.append(link); link.click(); link.remove();
}
/** Context menu commands for a ledger entry. */
export function entryMenu(item: Transaction, showDetails: () => void, other: 'explorer' | 'journal' | null, month: string): MenuItem[] {
  const evidence = item.sources.find(isFileReference);
  const href = item.sources.map(evidenceHref).find(Boolean);
  return [
    { label: 'Show Details', action: showDetails },
    { label: 'Open in New Tab', action: () => window.open(entryUrl(item.id), '_blank', 'noopener') },
    { label: 'Open Evidence', action: () => window.open(href!, '_blank', 'noopener'), disabled: !href },
    ...(other ? [{ label: other === 'journal' ? 'Show in Journal' : 'Show in Explorer', action: () => navigate(`${other}/?month=${month}&entry=${item.id}`) }] : []),
    'separator',
    { label: 'Copy Entry Link', action: () => copyAndAnnounce(entryUrl(item.id), 'entry link') },
    { label: 'Copy Description', action: () => copyAndAnnounce(item.description, 'description') },
    { label: 'Copy Amount', action: () => copyAndAnnounce(item.amount.toFixed(2), 'amount') },
    { label: 'Copy Ledger Account', action: () => copyAndAnnounce(item.ledgerAccount, 'ledger account') },
    { label: 'Copy Evidence Path', action: () => copyAndAnnounce(evidence || '', 'evidence path'), disabled: !evidence },
  ];
}
const latestEntryDate = () => data.transactions.reduce((latest, item) => item.date > latest ? item.date : latest, '');
const isMonth = (value: string) => data.months.some((month) => month.month === value);

const SHORTCUTS: [string, [string, string][]][] = [
  ['Everywhere', [['⌥⌘F', 'Search this view'], ['Esc', 'Close details, a menu or a dialog; clear a search field'], ['Tab', 'Move between controls (turn on Keyboard navigation in System Settings to reach every control)']]],
  ['Explorer and Journal', [['[  ]', 'Previous or next month']]],
  ['Entry links in Explorer, Journal and Accounts', [['⌘/Ctrl-click', 'Open the entry in a new tab (middle-click also works)']]],
  ['Lists of entries and accounts', [['↑  ↓', 'Previous or next entry; open details follow'], ['Home  End', 'First or last entry'], ['A–Z', 'Jump to an entry by its first letters'], ['Return  Space', 'Show the entry’s details'], ['⇧F10', 'Show the entry’s commands (or Control-click it)']]],
  ['Calendar, Atlas and month buttons', [['←  →  ↑  ↓', 'Move between days or months']]],
  ['Forecast chart', [['←  →', 'Move between days (click, tap or Tab to the chart first)'], ['Home  End', 'First or last day'], ['Esc', 'Hide the tooltip; focus and the selected day stay on the chart']]],
  ['Investment value charts', [['←  →', 'Move between NAV dates (click, tap or Tab to the chart first)'], ['Home  End', 'First or last NAV date'], ['Esc', 'Hide the tooltip; focus and the selected date stay on the chart']]],
  ['Review', [['⌘↩', 'Resolve'], ['⇧⌘↩', 'Mark for Follow-Up'], ['⌘Z', 'Undo the last decision'], ['⇧⌘Z', 'Redo']]],
];
const VIEW_GUIDE: [string, string][] = [
  ['Explorer', 'One month: what was spent, by day and by category, and every ledger entry with its postings and evidence. On phones, More month figures expands the average, expense count and largest expense below Spent.'],
  ['Atlas', 'Twelve months of daily spending side by side; choose a month for its busiest days and latest entries. Up/Down follows the visible card rows as the grid changes width.'],
  ['Journal', 'Every entry of a month by day, with the day’s net expenses and where each payment came from and went.'],
  ['Forecast', 'This month so far, then recurring commitments and the usual variable spending to month end, against earlier months.'],
  ['Accounts', 'Balances on any date, each account’s history, and the entries that moved it. Investments opens the portfolio: mutual funds at the latest NAV on or before that date, with cost and unrealised gain; other investments at contributions. Click, tap or use the value chart’s arrow keys to inspect NAV dates. On phones, Choose account switches accounts and More account context expands the totals and full account list.'],
  ['Review', 'Check the evidence → choose an account → Resolve. Work is saved in this browser; use Export Work… to back it up.'],
  ['Appearance', 'The appearance toggle in the sidebar, or Views on phones, switches between Light and Dark. Your first visit starts with the system appearance; your choice is remembered across reloads and tabs.'],
];
function HelpDialog({ onClose }: { onClose: () => void }) {
  return <Dialog title="Help" subtitle="What each view is for, and its keyboard shortcuts" labelId="help-title" onClose={onClose}>
    <div className="help" tabIndex={0} aria-label="Help">
      <section><h3>Views</h3><dl className="help-views">{VIEW_GUIDE.map(([view, text]) => <div key={view}><dt>{view}</dt><dd>{text}</dd></div>)}</dl></section>
      <section className="help-review"><h3>Review</h3>
        <details><summary>Backup and moving browsers</summary><p>Export Work… saves decisions and drafts to a file. Import that file in the other browser or at a new dashboard address.</p></details>
        <details><summary>Multiple tabs</summary><p>One tab edits; others follow saved work read-only. Use Edit Here Instead to take over.</p></details>
        <details><summary>Recovery</summary><p>If work cannot be saved, keep the tab open and export it. A recovery notice offers Export Unsaved Work and Discard. Take editing back to Restore Unsaved Work; Undo and Redo are available. Keep the tab open until the work is saved or deliberately discarded.</p></details>
        <details><summary>Fuel Log</summary><p>Open Fuel Log beside the entry to compare its payment and readings. Numbered Entry links open the confirmed ledger entries in a new tab.</p></details>
      </section>
      <section><h3>Keyboard shortcuts</h3><div className="shortcuts">{SHORTCUTS.map(([group, items]) => <section key={group}><h4>{group}</h4><dl>{items.map(([keys, action]) => <div key={keys}><dt><kbd>{keys}</kbd></dt><dd>{action}</dd></div>)}</dl></section>)}</div>
        <p className="help-note">In entry details, ↑/↓ or Previous/Next steps through the list. The title, amount and tools stay visible above the scrolling detail body. Esc, Close, or the dimmed page exits. Tab stays inside until you close it. The page behind stays still and returns to its exact scroll position on close.</p>
        <p className="help-note">Entry titles are links: click for details or ⌘/Ctrl-click or middle-click for a new tab. Drag the title to copy the entry to another app; title links include its URL. Select text in other cells to copy just that part. Right-click anywhere on a row for its commands; Shift+right-click opens the browser’s menu.</p>
        <p className="help-note">Show All clears Explorer filters. Clear Search in Journal, Accounts and Review keeps other filters. Explorer’s matching subtotal excludes transfers; the month summary shows the whole month.</p>
        <p className="help-note">Right-click a column heading for Reset Column Widths. Browser shortcuts still work, including ⌘F. If data cannot load, use Retry; Details explains the problem. A successful Retry focuses the loaded view’s heading.</p></section>
    </div>
  </Dialog>;
}

const REVIEW_KEY = 'agentexpense-review-v2';
/** Entries still unreviewed in this browser: the ledger's pending count less saved decisions. */
function unreviewedCount() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(REVIEW_KEY) || '{}') as Record<string, { status: string }>;
    const ids = new Set(reviewData.transactions.map((item) => item.id));
    return reviewData.meta.pendingTransactionCount - Object.entries(saved).filter(([id, decision]) => ids.has(id) && decision.status !== 'unreviewed').length;
  } catch { return reviewData.meta.pendingTransactionCount; }
}

function DataBuilt() {
  const generated = new Date(data.meta.generatedAt);
  return <p className="data-built"><strong>{data.meta.ledger}</strong>Data built {generated.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}, {generated.toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' })}</p>;
}

function Sidebar({ active, month, attention, status, unreviewed, onHelp }: { active: View; month: string; attention: number; status: string; unreviewed: number; onHelp: () => void }) {
  const [appearance, setAppearance] = useAppearance();
  const nextAppearance = appearance === 'light' ? 'dark' : 'light';
  const appearanceLabel = `Switch to ${nextAppearance === 'light' ? 'Light' : 'Dark'} appearance`;
  const query = month !== data.meta.defaultMonth ? `?month=${month}` : '';
  return <aside className="sidebar">
    <a className="wordmark" href={`${import.meta.env.BASE_URL}explorer/${query}`} title="Ledger"><span className="wordmark-badge" aria-hidden="true">₹</span><span className="sidebar-label">Ledger</span></a>
    <nav aria-label="Views">{VIEWS.map((view) => <a key={view.id} href={`${import.meta.env.BASE_URL}${view.href}${view.carriesMonth ? query : ''}`} aria-current={view.id === active ? 'page' : undefined} title={view.label}>
      <Icon name={view.icon}/><span className="sidebar-label">{view.label}</span>{view.id === 'review' && unreviewed > 0 && <span className="nav-count"><span className="sr-only">, </span>{unreviewed}<span className="sr-only"> to review</span></span>}
      {view.id === 'drafts' && attention > 0 && <span className="nav-count"><span className="sr-only">, </span>{attention}<span className="sr-only"> need attention</span></span>}
    </a>)}</nav>
    <div className="sidebar-footer">
      <p className="sidebar-status" aria-hidden="true">{status}</p>
      <button type="button" className="sidebar-button" aria-label={appearanceLabel} title={appearanceLabel} onClick={() => setAppearance(nextAppearance)}>
        <Icon name={nextAppearance === 'light' ? 'sun' : 'moon'}/><span className="sidebar-label">{nextAppearance === 'light' ? 'Light' : 'Dark'} appearance</span></button>
      <button type="button" className="sidebar-button" title="Help & Shortcuts" onClick={(event) => { event.currentTarget.focus(); onHelp(); }}><Icon name="help"/><span className="sidebar-label">Help &amp; Shortcuts</span></button>
      <DataBuilt/>
    </div>
  </aside>;
}

/** One piece of evidence: a file opens in a new tab and its path can be copied; a note is shown as written. */
function SourceItem({ path, onCopied }: { path: string; onCopied: (ok: boolean) => void }) {
  if (!isFileReference(path)) return <li className="source-note">{path.charAt(0).toUpperCase() + path.slice(1)}</li>;
  const href = evidenceHref(path);
  return <li><span className="path"><EvidenceName path={path}/><span className="row-sub">{evidenceKind(path)}</span></span><span className="source-actions">{href && <a className="button plain" href={href} target="_blank" rel="noopener" aria-label={`${evidenceAction(path)}: ${path}`} title="Open in a new tab">{evidenceAction(path)}</a>}<button type="button" className="icon-button" aria-label={`Copy ${path}`} title="Copy path" onClick={async () => onCopied(await copyText(path))}><Icon name="copy"/></button></span></li>;
}

type EntryPanelProps = { transaction: Transaction | null; onClose: () => void; entries?: Transaction[]; onStep?: (id: string) => void };
export function TransactionDetails({ transaction, onClose, entries = [], onStep }: EntryPanelProps) {
  const pane = useSlidingPane(transaction);
  return pane.value ? <EntryDetails transaction={pane.value} onClose={onClose} closing={pane.closing} entries={entries} onStep={onStep}/> : null;
}

function EntryDetails({ transaction, onClose, closing, entries = [], onStep }: Omit<EntryPanelProps, 'transaction'> & { transaction: Transaction; closing: boolean }) {
  useDocumentScrollLock();
  const dialog = useRef<HTMLElement>(null);
  const body = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    body.current?.scrollTo({ top: 0, left: 0, behavior: 'instant' });
    dialog.current?.querySelector<HTMLElement>('#entry-title')?.focus({ preventScroll: true });
  }, [transaction.id]);
  const lastId = useRef(transaction.id);
  lastId.current = transaction.id;
  useEffect(() => {
    const page = document.querySelector<HTMLElement>('main.app');
    if (page) page.inert = true;
    dialog.current?.querySelector<HTMLElement>('#entry-title')?.focus({ preventScroll: true });
    return () => {
      if (page) page.inert = false;
      const row = document.querySelector<HTMLElement>(`[data-row-id="${CSS.escape(lastId.current)}"]`);
      row?.focus({ preventScroll: true });
    };
  }, []);
  const index = entries.findIndex((entry) => entry.id === transaction.id);
  const step = (delta: number) => { const next = entries[index + delta]; if (next && index >= 0) onStep?.(next.id); };
  const pane = usePaneWidth('ledger-inspector-width', 360, 300, 'Entry inspector');
  useEffect(() => { document.documentElement.style.setProperty('--inspector-width', `${pane.width}px`); }, [pane.width]);
  const group = data.reimbursementGroups.find((item) => item.id === transaction.reimbursementGroup);
  const reimbursable = transaction.kind === 'Reimbursable purchase';
  const [copied, setCopied] = useState<boolean | null>(null);
  const [linkCopied, setLinkCopied] = useState<boolean | null>(null);
  useEffect(() => { setCopied(null); setLinkCopied(null); }, [transaction.id]);
  const closeRef = useRef<HTMLButtonElement>(null);
  const linked = transaction.source.startsWith('Bills/');
  const content = <div className="entry-layer" onMouseDown={(event) => { if (event.target === event.currentTarget && !closing) onClose(); }}><aside ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="entry-title" onKeyDown={(event) => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); }
    else if ((event.key === 'ArrowUp' || event.key === 'ArrowDown') && !isTyping(event.target)) { event.preventDefault(); step(event.key === 'ArrowUp' ? -1 : 1); }
    else trapModalTab(event, dialog.current);
  }} className={`inspector ${closing ? 'closing' : ''}`} inert={closing} id="transaction-inspector" aria-label="Entry details">
    {pane.grip}
    <div className="inspector-header">
      <div><h2 id="entry-title" tabIndex={-1}>{transaction.description}</h2><p>{dayLabel(transaction.date)} · {transaction.displayType}{transaction.status === 'Pending' ? ' · unclassified' : ''}</p>
        <p className={`amount-hero ${amountClass(transaction)}`}>{signedAmount(transaction)}</p>
        {reimbursable && <p>Purchase amount{transaction.originalAmount ? ` · ${transaction.originalAmount} billed` : ''}</p>}</div>
      <div className="entry-header-tools"><button type="button" className="button plain" onClick={async () => setLinkCopied(await copyText(entryUrl(transaction.id)))}><Icon name="copy"/>Copy Link</button><span className={`entry-copy-status ${linkCopied === false ? 'error' : ''}`} role="status">{linkCopied === null ? '' : linkCopied ? 'Link copied' : 'Copy blocked'}</span><div className="entry-navigation"><button type="button" className="icon-button" aria-label="Previous entry" title="Previous entry (↑)" disabled={index <= 0} onClick={() => step(-1)}>↑</button><button type="button" className="icon-button" aria-label="Next entry" title="Next entry (↓)" disabled={index < 0 || index >= entries.length - 1} onClick={() => step(1)}>↓</button><button ref={closeRef} type="button" className="icon-button" onClick={onClose} aria-label="Close details" title="Close (Esc)"><Icon name="close"/></button></div></div>
    </div>
    <div className="inspector-body" ref={body}>
    <dl className="facts">
      <dt>Category</dt><dd>{transaction.category}</dd>
      {transaction.kind === 'Transfer' ? <>
        <dt>From</dt><dd>{transaction.postings.filter((posting) => posting.amount < 0).map((posting) => posting.label).join(' + ')}</dd>
        <dt>To</dt><dd>{transaction.postings.filter((posting) => posting.amount > 0).map((posting) => posting.label).join(' + ')}</dd>
        <dt>Personal expense impact</dt><dd>{inr(transaction.signedExpense)}</dd>
      </> : <><dt>{transaction.kind === 'Reimbursement' ? 'Received in' : 'Paid using'}</dt><dd>{transaction.paymentLabel}</dd></>}
      {transaction.reimbursementParty && <><dt>{reimbursable ? 'Reimbursable from' : 'Reimbursement from'}</dt><dd>{transaction.reimbursementParty}</dd><dt>Personal expense impact</dt><dd>{inr(transaction.signedExpense)}</dd></>}
      <dt>Classification</dt><dd>{transaction.classification || 'Not supplied'}</dd>
    </dl>
    {group && <section className="inspector-section" aria-label={group.label}><h3>{group.label}</h3><dl className="facts">
      <dt>Matched purchases</dt><dd>{inr(group.purchases)}</dd>
      <dt>Received{group.paymentDate ? ` ${dayShort(group.paymentDate)}` : ''}</dt><dd>{inr(group.received)}</dd>
      <dt>{group.outstanding >= 0 ? 'Still owed' : 'Credit remaining'}</dt><dd>{inr(Math.abs(group.outstanding))}<span className="row-sub">Across this group</span></dd>
    </dl></section>}
    <section className="inspector-section" aria-label="Double-entry postings"><h3><Icon name="postings"/>Postings</h3><div className="postings">
      {transaction.postings.map((posting, index) => <div className="posting" key={`${posting.account}-${index}`}>
        <span className="posting-side" title={posting.amount >= 0 ? 'Debit' : 'Credit'}>{posting.amount >= 0 ? 'Dr' : 'Cr'}</span>
        <span><span className="posting-name">{posting.label}</span><span className="posting-path">{breakable(posting.account)}</span></span>
        <span className="posting-amount">{money(Math.abs(posting.amount), posting.currency)}{'cost' in posting && posting.cost !== undefined && <span className="row-sub" title="Cost in INR">Cost {inr(Math.abs(posting.cost))}</span>}</span>
      </div>)}
    </div></section>
    {transaction.provenance && <section className="inspector-section" aria-label="Added from the live page"><h3>Added from the live page</h3>{transaction.provenance.text.length>240?<details className="provenance-original"><summary><span className="provenance-preview">{transaction.provenance.text}</span><span className="provenance-disclose">Show full typed words</span></summary><p className="provenance-words">{transaction.provenance.text}</p></details>:<p className="provenance-words">{transaction.provenance.text || 'Files only — no typed words.'}</p>}<p className="live-hint">{new Date(transaction.provenance.time).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'})} · Asia/Kolkata<br/>{transaction.provenance.models.join(', ') || 'No agent run'}</p>{transaction.provenance.questions.map((q,i)=><div className="earlier-answer" key={i}><p>{q.question}</p><p><strong>You:</strong> {q.answer || 'Unanswered'}</p></div>)}<p><strong>Doubts:</strong> {transaction.provenance.doubts.join('; ') || 'None reported'}</p></section>}
    <section className="inspector-section" aria-label="Source evidence"><h3><Icon name="source"/>Evidence</h3>
      <ul className="source-list">{transaction.sources.map((source) => <SourceItem key={source} path={source} onCopied={setCopied}/>)}</ul>
      {transaction.sourceReference && <dl className="facts"><dt>Reference</dt><dd>{transaction.sourceReference}</dd></dl>}
      <p className="message">{linked ? <><Icon name="check"/>Filed under Bills/</> : <><Icon name="pending"/>Recorded source, not filed under Bills/</>}</p>
      {copied !== null && <p className={`message ${copied ? 'success' : 'error'}`} role="status">{copied ? <><Icon name="check"/>Path copied</> : 'The browser blocked copying. Select the path and press ⌘C.'}</p>}
    </section>
    </div>
  </aside></div>;
  return typeof document === 'undefined' ? content : createPortal(content, document.body);
}

/** One calendar, beside wide lists and disclosed above narrower lists. */
function ExplorerDayFilter({ month, current, day, onDay }: Parameters<typeof SpendingCalendar>[0]) {
  const [wide, setWide] = useState(() => typeof matchMedia === 'undefined' || matchMedia('(min-width: 1281px)').matches);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    const media = matchMedia('(min-width: 1281px)');
    const update = () => setWide(media.matches);
    update(); media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return <details className="explorer-day-filter" open={wide || expanded} onToggle={(event) => { if (!wide) setExpanded(event.currentTarget.open); }}>
    <summary>Filter by day{day ? ` · ${dayShort(`${month}-${String(day).padStart(2, '0')}`)}` : ''}</summary>
    <PanelHeader title="Daily spending" meta={day ? `Listing ${dayShort(`${month}-${String(day).padStart(2, '0')}`)} only` : 'Select a day to list only its entries'}/>
    <SpendingCalendar month={month} current={current} day={day} onDay={onDay}/>
  </details>;
}

/** Mon-first calendar of the month's spending; a day filters the entries below. */
function SpendingCalendar({ month, current, day, onDay }: { month: string; current: Month; day: number | null; onDay: (day: number | null) => void }) {
  const days = daysInMonth(month);
  const amounts = new Map(current.daily.map((item) => [item.day, item.amount]));
  const max = Math.max(1, ...current.daily.map((item) => item.amount));
  const lead = (new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)) - 1, 1).getDay() + 6) % 7;
  const level = (amount: number) => amount <= 0 ? 0 : Math.min(4, Math.ceil(amount / max * 4));
  const dates = useMemo(() => Array.from({ length: days }, (_, index) => index + 1), [days]);
  const idOf = useCallback((date: number) => String(date), []);
  const nav = useRowNavigation(dates, idOf, day ? String(day) : null, useCallback(() => undefined, []), 7);
  return <div className="calendar">
    <div className="calendar-grid" role="group" aria-label={`Spending by day, ${monthLong(month)}. Use the arrow keys to move between days.`} onKeyDown={nav.onKeyDown}>
      {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((name) => <span key={name} className="calendar-weekday" aria-hidden="true">{name}</span>)}
      {Array.from({ length: lead }, (_, index) => <span key={`lead-${index}`}/>)}
      {Array.from({ length: days }, (_, index) => {
        const date = index + 1; const amount = amounts.get(date) || 0;
        return <button key={date} type="button" className={`calendar-day level-${level(amount)}`} aria-pressed={day === date} {...nav.rowProps(date)}
          aria-label={`${date} ${monthName(month)}: ${amount < 0 ? `net refund ${inr(amount)}` : amount !== 0 ? inr(amount) : 'no spending'}`} title={amount !== 0 ? `${dayShort(`${month}-${String(date).padStart(2, '0')}`)}: ${inr(amount)}${amount < 0 ? ' net refund' : ''}` : undefined}
          onClick={() => onDay(day === date ? null : date)}>
          <span className="calendar-date">{date}</span><span className={`calendar-amount${amount < 0 ? ' credit' : ''}`}>{amount !== 0 ? compactInr(amount).replace('₹', '') : ''}</span>
        </button>;
      })}
    </div>
    <div className="calendar-legend" aria-hidden="true"><span>Amounts in ₹ · Less</span>{[1, 2, 3, 4].map((step) => <i key={step} className={`level-${step}`}/>)}<span>More</span></div>
  </div>;
}

function CategoryComparison({ current, previous }: { current: Month; previous?: Month }) {
  const before = new Map(previous?.categories.map((item) => [item.category, item.amount]) || []);
  const rows = current.categories.slice(0, 8);
  const max = Math.max(1, ...rows.map((item) => Math.max(Math.abs(item.amount), Math.abs(before.get(item.category) || 0))));
  return <div className="category-list">
    {rows.map((item) => { const old = before.get(item.category) || 0; return <div key={item.category} className="category-row">
      <span className="category-name">{item.category}</span>
      <span className="category-bars" aria-hidden="true"><i className={`bar-current${item.amount < 0 ? ' net-refund' : ''}`} style={{ width: `${item.amount ? Math.max(1, Math.abs(item.amount) / max * 100) : 0}%` }}/>{previous && <i className={`bar-previous${old < 0 ? ' net-refund' : ''}`} style={{ width: `${old ? Math.max(1, Math.abs(old) / max * 100) : 0}%` }}/>}</span>
      <span className={`category-amount${item.amount < 0 ? ' credit' : ''}`}>{inr(item.amount)}</span>
      <span className="category-change">{!previous ? '' : old ? <Delta value={item.amount} base={old}/> : <span className="delta flat">New</span>}</span>
    </div>; })}
  </div>;
}

export function Explorer({ month, setMonth }: { month: string; setMonth: (month: string, mode?: 'push' | 'replace') => void }) {
  const [summaryExpanded, setSummaryExpanded] = useState(false);
  const [query, setQuery] = useQueryState('q', '');
  const [sort, setSort] = useQueryState('sort', 'date-desc', (value) => /^(date|description|category|paymentLabel|status|amount)-(asc|desc)$/.test(value));
  const [sortKey, sortDirection] = sort.split('-') as [SortKey, SortDirection];
  const [category, setCategory] = useQueryState('category', '');
  const [dayParam, setDayParam] = useQueryState('day', '', (value) => /^\d{1,2}$/.test(value));
  const day = dayParam ? Number(dayParam) : null;
  const setDay = (value: number | null) => setDayParam(value ? String(value) : '');
  const [selectedId, setSelectedId] = useQueryState('entry', '');
  const menu = useContextMenu();
  useScrollMemory(`explorer:${month}`, 'tbody tr[data-selected="true"]');
  const { widths, resizer } = useColumnWidths('ledger-explorer-columns', { date: 84, category: 120, payment: 152, amount: 112 });
  const [cursor, setCursor] = useState<string | null>(null);
  const textTooltip = useEntryTextTooltip();
  const current = data.months.find((item) => item.month === month) as Month;
  const previous = data.months[data.months.findIndex((item) => item.month === month) - 1];
  const monthTransactions = useMemo(() => data.transactions.filter((item) => item.month === month), [month]);
  const selected = monthTransactions.find((item) => item.id === selectedId) || null;
  const categories = [...new Set(monthTransactions.map((item) => item.category))].sort();
  const needle = query.trim().toLowerCase();
  const filtered = sortTransactions(monthTransactions.filter((item) => (!category || item.category === category) && (!day || Number(item.date.slice(8, 10)) === day)
    && (!needle || searchText(item).toLowerCase().includes(needle))), sortKey, sortDirection, sortKey === 'paymentLabel' ? item => paymentSide(item).short : undefined);
  const expenses = monthTransactions.filter((item) => item.kind === 'Expense');
  const largest = [...expenses].sort((a, b) => b.amount - a.amount)[0];
  const days = daysInMonth(month);
  const filtersActive = !!(category || day || needle);
  const matchingExpenses = filtered.reduce((sum, item) => sum + item.signedExpense, 0);
  const showAll = () => { setQuery(''); setCategory(''); setDay(null); requestAnimationFrame(() => document.getElementById('explorer-search')?.focus({ preventScroll: true })); };
  const changeMonth = (value: string) => { setMonth(value); setCategory(''); setDay(null); setSelectedId(''); };
  const select = useCallback((item: Transaction) => { setSelectedId(item.id); setCursor(item.id); }, [setSelectedId]);
  const idOf = useCallback((item: Transaction) => item.id, []);
  const follow = useCallback((item: Transaction) => { setCursor(item.id); if (selectedId) setSelectedId(item.id, 'replace'); }, [selectedId, setSelectedId]);
  const rows = useRowNavigation(filtered, idOf, cursor || selectedId || null, follow, 0, describe);
  const close = useCallback(() => { const id = selectedId; setSelectedId(''); if (id) requestAnimationFrame(() => rows.focusRow(id, true)); }, [selectedId, setSelectedId, rows]);
  useEscape(!!selected, close);
  function sortBy(key: SortKey) {
    setSort(`${key}-${key === sortKey ? (sortDirection === 'asc' ? 'desc' : 'asc') : key === 'amount' || key === 'date' ? 'desc' : 'asc'}`);
  }
  const columns: { key: SortKey; label: string; numeric?: boolean; className?: string; width?: string }[] = [
    { key: 'date', label: 'Date', width: 'date' }, { key: 'description', label: 'Description' }, { key: 'category', label: 'Category', className: 'col-category', width: 'category' },
    { key: 'paymentLabel', label: 'Payment account', className: 'col-payment', width: 'payment' }, { key: 'amount', label: 'Amount', numeric: true, width: 'amount' },
  ];
  const openMenu = (event: Parameters<typeof menu.open>[0], item: Transaction, row: HTMLElement | null) => menu.open(event, entryMenu(item, () => select(item), 'journal', month), row);

  return <div className="split">
    {textTooltip.element}
    <section className="page explorer">
      <PageHeader title="Explorer" subtitle={`${monthLong(month)} · ${month === data.meta.defaultMonth ? `recorded through ${dayShort(latestEntryDate())}` : 'complete month'}`}>
        <MonthStepper months={monthKeys()} value={month} onChange={changeMonth}/>
        <SearchField id="explorer-search" value={query} onChange={setQuery} label="Search this month's entries" placeholder="Search entries" scope={monthShort(month)}/>
      </PageHeader>
      <section className="metrics" data-expanded={summaryExpanded} aria-label={filtersActive ? 'Whole month summary' : 'Month summary'}>
        <div className="metric"><p className="metric-label">{filtersActive ? 'Spent · whole month' : 'Spent'}</p><p className="metric-value">{inr(current.total)}</p><p className="metric-note">{previous ? <Delta value={current.total} base={previous.total} suffix={`vs ${monthName(previous.month)}`}/> : 'First recorded month'}</p></div>
        <div className="metric" id="explorer-summary-average"><p className="metric-label">Average per calendar day</p><p className="metric-value">{inr(current.total / days)}</p><p className="metric-note">Total ÷ {days} days</p></div>
        <div className="metric" id="explorer-summary-count"><p className="metric-label">Expenses</p><p className="metric-value">{current.count}</p><p className="metric-note">{current.pending ? <a href={`${import.meta.env.BASE_URL}review/`}>{current.pending} unclassified</a> : 'All classified'} · {plural(monthTransactions.length, 'ledger entry', 'ledger entries')}</p></div>
        <div className="metric" id="explorer-summary-largest"><p className="metric-label">Largest expense</p><p className="metric-value">{largest ? inr(largest.amount) : '—'}</p><p className="metric-note" title={largest?.description}>{largest?.description || 'No expenses'}</p></div>
        <button type="button" className="button plain summary-toggle" aria-expanded={summaryExpanded} aria-controls="explorer-summary-average explorer-summary-count explorer-summary-largest" onClick={() => setSummaryExpanded(value => !value)}>{summaryExpanded ? 'Fewer month figures' : 'More month figures'}<Icon name="chevron-up-down"/></button>
      </section>
      <div className="explorer-body with-aside">
      <section className="panel entries-panel">
        <PanelHeader title="Ledger entries" meta={filtersActive ? `${plural(filtered.length, 'entry', 'entries')} of ${monthTransactions.length} shown` : undefined}>
          {filtersActive && <button type="button" className="button plain" onClick={showAll}>Show All</button>}
          <label className="sr-only" htmlFor="explorer-category">Category</label>
          <select id="explorer-category" className="field" value={category} onChange={(event) => setCategory(event.target.value)}><option value="">All categories</option>{categories.map((item) => <option key={item} value={item}>{item}</option>)}</select>
        </PanelHeader>
        {filtersActive && <p className="matching-total" role="status">{filtered.length} matching · <strong>{inr(matchingExpenses)}</strong> net expenses <span>· transfers excluded</span></p>}
        <div className="table-scroll"><table className="data-table entries-table" role="table">
          <colgroup><col className="col-date" style={{ width: widths.date }}/><col/><col className="col-category" style={{ width: widths.category }}/><col className="col-payment" style={{ width: widths.payment }}/><col className="col-amount" style={{ width: widths.amount }}/></colgroup>
          <thead><tr>{columns.map(({ key, label, numeric, className, width }) => <th key={key} className={`${numeric ? 'th-num' : ''} ${className || ''}`} aria-label={label} aria-sort={sortKey === key ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}>
            <button type="button" className="sort-button" title={`Sort by ${label.toLowerCase()}`} onClick={() => sortBy(key)}>{label}<span className="sort-indicator" aria-hidden="true">{sortKey === key ? (sortDirection === 'asc' ? '▲' : '▼') : ''}</span></button>
            {width ? resizer(width, label) : key === 'description' && resizer('description', label, 'category')}
          </th>)}</tr></thead>
          <tbody onKeyDown={rows.onKeyDown}>{filtered.map((item) => <tr key={item.id} className="interactive" data-selected={item.id === selectedId} onClick={(event) => { if (hasSelectedText(event)) return; select(item); rows.focusRow(item.id); }}
            onContextMenu={(event) => openMenu(event, item, event.currentTarget)}>
            <td className="date">{dayShort(item.date)}</td>
            <td className="col-description"><EntryLink entryId={item.id} className="row-open" {...rows.rowProps(item)} aria-label={fullEntryText(item)} title={item.description} aria-current={item.id === selectedId ? 'true' : undefined} aria-controls="transaction-inspector" data-copy={copyLine(item)} onOpen={() => { select(item); rows.focusRow(item.id); }} onFocus={(event) => { setCursor(item.id); textTooltip.show(event, fullEntryText(item)); }} onBlur={textTooltip.hide}
              onKeyDown={(event) => { if (menu.keyOpens(event)) openMenu(event, item, event.currentTarget.closest('tr')); }}>
              <span className="row-title" title={item.description}>{rowTitle(item.description)}</span>
              <span className="row-sub-line">{item.status === 'Pending' && <span className="status pending"><Icon name="pending"/>Unclassified</span>}</span></EntryLink></td>
            <td className="col-category" title={item.category}>{item.category}</td>
            <td className="col-payment" title={paymentSide(item).full} aria-label={paymentSide(item).full}><span className="payment-name">{paymentSide(item).short}</span></td>
            <td className={`num ${amountClass(item)}`}>{signedAmount(item)}</td>
          </tr>)}</tbody>
        </table>{filtered.length === 0 && <div className="empty"><strong>No matching entries</strong><span>Try another search or category, or show all entries.</span><button type="button" className="button" onClick={showAll}>Show All</button></div>}</div>
      </section>
      <aside className="explorer-aside panel" aria-label="The month at a glance">
        <ExplorerDayFilter month={month} current={current} day={day} onDay={setDay}/>
        <section className="aside-section"><div className="aside-section-head"><h3>Categories</h3><p>{previous ? <><span className="key key-current"/>{monthName(month)} <span className="key key-previous"/>{monthName(previous.month)}</> : monthLong(month)}</p></div><CategoryComparison current={current} previous={previous}/></section>
      </aside>
      </div>
    </section>
    <TransactionDetails transaction={selected} onClose={close} entries={filtered} onStep={(id) => setSelectedId(id, 'replace')}/>
    {menu.element}
  </div>;
}

function Atlas({ month, setMonth }: { month: string; setMonth: (month: string, mode?: 'push' | 'replace') => void }) {
  const keys = monthKeys();
  const selectedIndex = keys.indexOf(month);
  const [windowEnd, setWindowEnd] = useState(() => Math.max(Math.min(11, keys.length - 1), selectedIndex));
  const windowStart = Math.max(0, windowEnd - 11);
  const months = data.months.slice(windowStart, windowEnd + 1);
  const current = data.months[selectedIndex];
  const days = daysInMonth(month);
  const daily = Array.from({ length: days }, (_, index) => current.daily.find((item) => item.day === index + 1)?.amount || 0);
  const maxDay = Math.max(1, ...daily);
  const minDay = Math.min(0, ...daily);
  const barStyle = (amount: number, low: number, high: number, minimum: number) => ({ height: `${amount ? Math.max(minimum, Math.abs(amount) / (high - low) * 100) : 0}%`, bottom: `${(Math.min(0, amount) - low) / (high - low) * 100}%` });
  const average = current.total / days;
  const monthEntries = data.transactions.filter((item) => item.month === month);
  const topDays = daily.map((amount, index) => ({ day: index + 1, amount })).filter((item) => item.amount > 0).sort((a, b) => b.amount - a.amount).slice(0, 5).map((item) => {
    const key = `${month}-${String(item.day).padStart(2, '0')}`;
    const entries = monthEntries.filter((entry) => entry.date === key && entry.kind === 'Expense').sort((a, b) => b.amount - a.amount);
    return { ...item, key, entries };
  });
  const recent = monthEntries.filter((item) => item.kind !== 'Transfer').sort((a, b) => b.date.localeCompare(a.date) || b.sequence - a.sequence).slice(0, 6);
  const monthId = useCallback((item: Month) => item.month, []);
  const pick = useCallback((item: Month, mode: 'push' | 'replace' = 'push') => setMonth(item.month, mode), [setMonth]);
  const cards = useRowNavigation(months, monthId, month, pick, 'grid');
  const shift = (direction: -1 | 1) => {
    const end = Math.min(keys.length - 1, Math.max(Math.min(11, keys.length - 1), windowEnd + direction));
    const start = Math.max(0, end - 11);
    setWindowEnd(end);
    if (selectedIndex < start) setMonth(keys[start]);
    if (selectedIndex > end) setMonth(keys[end]);
  };
  return <section className="page atlas">
    <PageHeader title="Atlas" subtitle="Daily spending across twelve months. Bars are scaled within each month. Choose a month to see its detail.">
      <div className="segmented" role="group" aria-label="Twelve-month window">
        <button type="button" className="icon-button" aria-label="Earlier months" title="Earlier months" disabled={windowEnd <= Math.min(11, keys.length - 1)} onClick={() => shift(-1)}><Icon name="chevron-left"/></button>
        <span className="segmented-label">{monthShort(months[0].month)} – {monthShort(months.at(-1)!.month)}</span>
        <button type="button" className="icon-button" aria-label="Later months" title="Later months" disabled={windowEnd >= keys.length - 1} onClick={() => shift(1)}><Icon name="chevron-right"/></button>
      </div>
    </PageHeader>
    <div className="atlas-grid" role="group" aria-label="Months. Use the arrow keys to move between months." onKeyDown={cards.onKeyDown}>{months.map((item) => {
      const localMax = Math.max(1, ...item.daily.map((entry) => entry.amount));
      const localMin = Math.min(0, ...item.daily.map((entry) => entry.amount));
      return <button key={item.month} type="button" className="atlas-card" aria-label={`${monthLong(item.month)} · ${inr(item.total)} · ${plural(item.count, 'expense')}`} title={`${monthLong(item.month)} · ${inr(item.total)}`} aria-pressed={item.month === month} {...cards.rowProps(item)} onClick={() => setMonth(item.month)}>
        <span className="atlas-card-head"><span>{monthLong(item.month)}</span><span>{compactInr(item.total)}</span></span>
        <span className="mini-bars" aria-hidden="true">{Array.from({ length: daysInMonth(item.month) }, (_, index) => { const amount = item.daily.find((entry) => entry.day === index + 1)?.amount || 0; return <i key={index} className={amount < 0 ? 'net-refund' : undefined} style={barStyle(amount, localMin, localMax, 4)}/>; })}{localMin < 0 && <b className="net-zero" style={{ bottom: `${-localMin / (localMax - localMin) * 100}%` }}/>}</span>
        <span className="atlas-card-foot">{plural(item.count, 'expense')}</span>
      </button>;
    })}</div>
    <section className="panel atlas-detail">
      <PanelHeader title={monthLong(month)} meta={`${plural(current.count, 'expense')} · ${days} days · ${inr(average)} per calendar day (total ÷ ${days})`}><span className="atlas-total">{inr(current.total)}</span></PanelHeader>
      <div className="atlas-detail-grid">
        <section><h3>Daily spending</h3>
          <div className="day-chart" role="img" aria-label={`Daily spending in ${monthLong(month)}; highest ${inr(maxDay)}, average ${inr(average)}${minDay < 0 ? `; net refunds below zero, lowest ${inr(minDay)}` : ''}`}>
            <span className="day-chart-max">{compactInr(maxDay)}</span>
            <div className="day-chart-bars">{daily.map((amount, index) => <i key={index} className={amount < 0 ? 'net-refund' : undefined} title={`${index + 1} ${monthName(month)}: ${inr(amount)}${amount < 0 ? ' net refund' : ''}`} style={barStyle(amount, minDay, maxDay, 2)}/>)}
              {minDay < 0 && <b className="net-zero" style={{ bottom: `${-minDay / (maxDay - minDay) * 100}%` }}><span>0</span></b>}
              <b className="day-chart-average" style={{ bottom: `${(average - minDay) / (maxDay - minDay) * 100}%` }}><span>Average</span></b></div>
            <div className="day-chart-axis"><span>1</span><span>8</span><span>15</span><span>22</span><span>{days}</span></div>
          </div>
        </section>
        <section><h3>Highest-spending days</h3><ol className="ranked-list">{topDays.map((item) => <li key={item.day}><a className="ranked-link" href={`${import.meta.env.BASE_URL}explorer/?month=${month}&day=${item.day}`} title={`List ${dayShort(item.key)} in Explorer`}>
          <span className="date">{dayShort(item.key)}</span>
          <span><span className="row-title">{plural(item.entries.length, 'expense')}</span><span className="row-sub" title={item.entries[0]?.description}>{item.entries.length > 1 ? 'Largest: ' : ''}{item.entries[0]?.description}</span></span>
          <span className="num">{inr(item.amount)}</span>
        </a></li>)}</ol></section>
        <section><h3>Latest entries</h3><ol className="ranked-list">{recent.map((item) => <li key={item.id}><a className="ranked-link" href={`${import.meta.env.BASE_URL}explorer/?month=${month}&entry=${item.id}`} title="Show in Explorer">
          <span className="date">{dayShort(item.date)}</span>
          <span><span className="row-title" title={item.description}>{item.description}</span><span className="row-sub">{item.category}</span></span>
          <span className={`num ${amountClass(item)}`}>{signedAmount(item)}</span>
        </a></li>)}</ol>
          <a className="more-link" href={`${import.meta.env.BASE_URL}explorer/${month !== data.meta.defaultMonth ? `?month=${month}` : ''}`}>All {plural(monthEntries.length, 'entry', 'entries')} in Explorer</a></section>
      </div>
    </section>
  </section>;
}

function JournalSummary({ month, rows }: { month: string; rows: Transaction[] }) {
  const current = data.months.find((item) => item.month === month) as Month;
  const days = daysInMonth(month);
  const cumulative: number[] = [];
  for (let day = 1; day <= days; day += 1) cumulative.push((cumulative.at(-1) || 0) + (current.daily.find((item) => item.day === day)?.amount || 0));
  const lineMax = Math.max(1, ...cumulative);
  const lineMin = Math.min(0, ...cumulative);
  const latest = latestEntryDate();
  const recordedDays = latest.startsWith(month) ? Number(latest.slice(8, 10)) : days;
  const points = cumulative.slice(0, recordedDays).map((value, index) => `${index / (days - 1) * 300},${96 - (value - lineMin) / (lineMax - lineMin) * 88}`).join(' ');
  const maxCategory = Math.max(1, ...current.categories.map((item) => Math.abs(item.amount)));
  const all = data.transactions.filter((item) => item.month === month);
  const withSource = all.filter((item) => item.sources.length > 0).length;
  return <aside className="journal-summary panel" aria-label="Month summary">
    <section><PanelHeader title={monthLong(month)} meta="Spent this month"><span className="summary-total">{inr(current.total)}</span></PanelHeader>
      <div className="panel-body"><svg className="sparkline" viewBox="0 0 300 100" preserveAspectRatio="none" role="img" aria-label={`Cumulative spending reached ${inr(cumulative[recordedDays - 1] || 0)} by day ${recordedDays}`}><polyline points={points} fill="none" vectorEffect="non-scaling-stroke"/></svg>
        <div className="sparkline-axis"><span>1 {monthName(month).slice(0, 3)}</span><span>Cumulative</span><span>{days} {monthName(month).slice(0, 3)}</span></div></div></section>
    <section className="summary-section"><h3>{current.categories.length > 8 ? `Top 8 of ${current.categories.length} categories` : 'By category'}</h3>
      <div className="summary-categories">{current.categories.slice(0, 8).map((item) => <div key={item.category}><span>{item.category}</span><span className={`num${item.amount < 0 ? ' credit' : ''}`}>{inr(item.amount)}</span><i className={item.amount < 0 ? 'net-refund' : undefined} style={{ width: `${Math.abs(item.amount) / maxCategory * 100}%` }}/></div>)}</div></section>
    <section className="summary-section"><h3>Ledger integrity</h3><p>Every entry keeps its postings and its source.</p>
      <dl className="facts"><dt>Entries</dt><dd className="num">{all.length}</dd><dt>Unclassified</dt><dd className="num">{current.pending}</dd><dt>With source evidence</dt><dd className="num">{withSource} of {all.length}</dd>{rows.length !== all.length && <><dt>Shown by search</dt><dd className="num">{rows.length}</dd></>}</dl></section>
  </aside>;
}

/** One side of an entry's movement: its accounts, summarized when an order has many item postings. */
function routeSide(item: Transaction, sign: 1 | -1) {
  return summarizeAccounts(item.postings.filter(posting => sign > 0 ? posting.amount > 0 : posting.amount < 0));
}

type JournalKey = 'description' | 'type' | 'amount';
export function Journal({ month, setMonth }: { month: string; setMonth: (month: string, mode?: 'push' | 'replace') => void }) {
  const [query, setQuery] = useQueryState('q', '');
  const [order, setOrder] = useQueryState('order', 'newest', (value) => value === 'newest' || value === 'oldest');
  const [sort, setSort] = useQueryState('sort', 'ledger', (value) => value === 'ledger' || /^(description|type|amount)-(asc|desc)$/.test(value));
  const [sortKey, sortDirection] = (sort === 'ledger' ? [null, 'asc'] : sort.split('-')) as [JournalKey | null, 'asc' | 'desc'];
  const [selectedId, setSelectedId] = useQueryState('entry', '');
  const [cursor, setCursor] = useState<string | null>(null);
  const textTooltip = useEntryTextTooltip();
  const { widths, resizer } = useColumnWidths('ledger-journal-columns', { type: 120, route: 260, amount: 128 });
  const needle = query.trim().toLowerCase();
  // Days follow the chosen order; within a day, entries keep the ledger's sequence unless a column is sorted.
  const grouped = useMemo(() => {
    const matches = data.transactions.filter((item) => item.month === month && (!needle || `${searchText(item)} ${item.source} ${item.sources.join(' ')}`.toLowerCase().includes(needle)));
    const days = [...new Set(matches.map((item) => item.date))].sort((a, b) => order === 'newest' ? b.localeCompare(a) : a.localeCompare(b));
    const factor = sortDirection === 'asc' ? 1 : -1;
    return days.map((date) => [date, matches.filter((item) => item.date === date).sort((a, b) => !sortKey ? (order === 'newest' ? b.sequence - a.sequence : a.sequence - b.sequence)
      : factor * (sortKey === 'amount' ? a.amount - b.amount : String(sortKey === 'type' ? a.displayType : a.description).localeCompare(String(sortKey === 'type' ? b.displayType : b.description))))] as [string, Transaction[]]);
  }, [month, needle, order, sortKey, sortDirection]);
  const rows = useMemo(() => grouped.flatMap(([, entries]) => entries), [grouped]);
  const selected = rows.find((item) => item.id === selectedId) || null;
  const idOf = useCallback((item: Transaction) => item.id, []);
  const follow = useCallback((item: Transaction) => { setCursor(item.id); if (selectedId) setSelectedId(item.id, 'replace'); }, [selectedId, setSelectedId]);
  const nav = useRowNavigation(rows, idOf, cursor || selectedId || null, follow, 0, describe);
  const close = useCallback(() => { const id = selectedId; setSelectedId(''); if (id) requestAnimationFrame(() => nav.focusRow(id, true)); }, [selectedId, setSelectedId, nav]);
  useEscape(!!selected, close);
  const menu = useContextMenu();
  useScrollMemory(`journal:${month}`, '.journal-table tbody tr[data-selected="true"]');
  const openMenu = (event: Parameters<typeof menu.open>[0], item: Transaction, row: HTMLElement | null) => menu.open(event, entryMenu(item, () => { setSelectedId(item.id); setCursor(item.id); }, 'explorer', month), row);
  const choose = (item: Transaction) => { setSelectedId(item.id); setCursor(item.id); nav.focusRow(item.id); };
  const heading = (key: JournalKey, label: string, width?: string, numeric = false) => <th className={numeric ? 'th-num' : ''} aria-label={label} aria-sort={sortKey === key ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}>
    <button type="button" className="sort-button" title={`Sort each day by ${label.toLowerCase()}`} onClick={() => setSort(sortKey === key ? (sortDirection === (key === 'amount' ? 'desc' : 'asc') ? `${key}-${key === 'amount' ? 'asc' : 'desc'}` : 'ledger') : `${key}-${key === 'amount' ? 'desc' : 'asc'}`)}>{label}<span className="sort-indicator" aria-hidden="true">{sortKey === key ? (sortDirection === 'asc' ? '▲' : '▼') : ''}</span></button>
    {width && resizer(width, label)}</th>;
  return <div className="split">
    {menu.element}{textTooltip.element}
    <section className="page journal">
      <PageHeader title="Journal" subtitle={`Every ledger entry in ${monthLong(month)}, by day`}>
        <MonthStepper months={monthKeys()} value={month} onChange={(value) => { setMonth(value); setSelectedId(''); }}/>
        <label className="compact-field"><span className="sr-only">Day order</span><select className="field" value={order} onChange={(event) => setOrder(event.target.value)}><option value="newest">Newest day first</option><option value="oldest">Oldest day first</option></select></label>
        <SearchField id="journal-search" value={query} onChange={setQuery} label="Search this month's entries and evidence" placeholder="Search entries" scope={monthShort(month)}/>
      </PageHeader>
      <div className="journal-layout with-summary">
        <section className="panel journal-book" aria-label={`Journal for ${monthLong(month)}`}>
          <div className="table-scroll"><table className="data-table journal-table" role="table">
            <colgroup><col/><col style={{ width: widths.type }}/><col style={{ width: widths.route }}/><col style={{ width: widths.amount }}/></colgroup>
            <thead><tr>{heading('description', 'Description')}{heading('type', 'Type', 'type')}<th aria-label="From → To">From → To{resizer('route', 'From → To')}</th>{heading('amount', 'Amount', 'amount', true)}</tr></thead>
            {grouped.map(([date, entries]) => { const net = entries.filter((item) => item.kind === 'Expense' || item.kind === 'Refund').reduce((sum, item) => sum + item.signedExpense, 0); return <tbody key={date} onKeyDown={nav.onKeyDown}>
              <tr className="journal-day-row"><th colSpan={4} scope="rowgroup"><span className="journal-day-name">{dayLabel(date)}</span><span className="journal-day-total">{plural(entries.length, 'entry', 'entries')} · Net expenses for the day <strong>{inr(net)}</strong></span></th></tr>
              {entries.map((item) => { const from = routeSide(item, -1); const to = routeSide(item, 1);
                return <tr key={item.id} className="interactive" data-selected={item.id === selectedId} onClick={(event) => { if (!hasSelectedText(event)) choose(item); }} onContextMenu={(event) => openMenu(event, item, event.currentTarget)}>
                  <td><EntryLink entryId={item.id} className="row-open" {...nav.rowProps(item)} aria-label={fullEntryText(item)} title={item.description} aria-current={item.id === selectedId ? 'true' : undefined} aria-controls="transaction-inspector" data-copy={copyLine(item)}
                    onFocus={(event) => { setCursor(item.id); textTooltip.show(event, fullEntryText(item)); }} onBlur={textTooltip.hide} onOpen={() => choose(item)} onKeyDown={(event) => { if (menu.keyOpens(event)) openMenu(event, item, event.currentTarget.closest('tr')); }}>
                    <span className="row-title" title={item.description}>{rowTitle(item.description)}</span><span className="row-sub" title={item.category}>{item.category}{item.status === 'Pending' && <span className="status pending"> · <Icon name="pending"/>Unclassified</span>}</span></EntryLink></td>
                  <td className="journal-type" title={item.displayType}>{item.displayType}</td>
                  <td className="journal-route" title={`${from.full} → ${to.full}`}><span className="route-line">{from.short}</span><span className="route-line"><span className="journal-arrow" aria-hidden="true">→ </span><span className="sr-only">to </span>{to.short}</span></td>
                  <td className={`num ${amountClass(item)}`}>{signedAmount(item)}</td>
                </tr>; })}
            </tbody>; })}
          </table>
          {rows.length === 0 && <div className="empty"><strong>No matching entries</strong><span>Try another search, or clear it to see the whole month.</span><button type="button" className="button" onClick={() => { setQuery(''); requestAnimationFrame(() => document.getElementById('journal-search')?.focus({ preventScroll: true })); }}>Clear Search</button></div>}</div>
        </section>
        <JournalSummary month={month} rows={rows}/>
      </div>
    </section>
    <TransactionDetails transaction={selected} onClose={close} entries={rows} onStep={(id) => setSelectedId(id, 'replace')}/>
  </div>;
}

const TITLES: Record<View, string> = { explorer: 'Explorer', atlas: 'Atlas', journal: 'Journal', forecast: 'Forecast', accounts: 'Accounts', review: 'Review', add: 'Add', drafts: 'Drafts', statements: 'Statements' };

export default function Dashboard({ view }: { view: View }) {
  const [month, setMonth] = useQueryState('month', data.meta.defaultMonth, isMonth);
  const version = useSyncExternalStore(subscribeData, dataVersion, dataVersion);
  const live = useDrafts();
  const composer = useComposer(view==='add');
  useAnswerGuard();
  useLiveFocus(view);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [status, setStatus] = useState('');
  const [unreviewed, setUnreviewed] = useState(() => typeof window === 'undefined' ? reviewData.meta.pendingTransactionCount : unreviewedCount());
  useEffect(() => {
    const onStatus = (event: Event) => setStatus((event as CustomEvent<string>).detail);
    const onProgress = () => setUnreviewed(unreviewedCount());
    const unsubscribe = subscribeData(onProgress);
    window.addEventListener('ledger-status', onStatus);
    window.addEventListener('review-progress', onProgress);
    window.addEventListener('storage', onProgress);
    return () => { unsubscribe(); window.removeEventListener('ledger-status', onStatus); window.removeEventListener('review-progress', onProgress); window.removeEventListener('storage', onProgress); };
  }, []);
  const [more, setMore] = useState(false);
  const viewsButton=useRef<HTMLButtonElement>(null);
  const closeViews=()=>{setMore(false);viewsButton.current?.focus();};
  useEffect(()=>{
    if(!more)return;
    const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();closeViews();}};
    const outside=(e:PointerEvent)=>{if(!(e.target as Element).closest('#phone-more-views,.phone-live-nav button')){e.preventDefault();closeViews();}};
    document.addEventListener('keydown',key);document.addEventListener('pointerdown',outside);
    return()=>{document.removeEventListener('keydown',key);document.removeEventListener('pointerdown',outside);};
  },[more]);
  const [appearance, setAppearance] = useAppearance();
  useEffect(() => setMore(false), [view]);
  useWindowActivity();
  useEffect(() => { if (view !== 'accounts') document.title = `${TITLES[view]} — Ledger`; }, [view]);
  useEffect(() => {
    // ⌥⌘F is the Mac shortcut for focusing a window's search field; ⌘F stays the browser's Find.
    const onKey = (event: KeyboardEvent) => {
      const findKey = event.code === 'KeyF' || event.key.toLowerCase() === 'f' || event.key === 'ƒ';
      if (!(event.metaKey && event.altKey && findKey) || modalOpen() || (isTyping(event.target) && (event.target as HTMLElement).closest('.search-field'))) return;
      const field = document.querySelector<HTMLInputElement>('.page .search-field input');
      if (field) { event.preventDefault(); field.focus(); field.select(); }
    };
    // [ and ] step the month in views that have a month, unless a text field has focus.
    const onStep = (event: KeyboardEvent) => {
      if ((event.key !== '[' && event.key !== ']') || event.metaKey || event.ctrlKey || isTyping(event.target) || modalOpen() || document.querySelector('[role="menu"]')) return;
      const button = document.querySelector<HTMLButtonElement>(`.page .month-stepper .icon-button[aria-label="${event.key === '[' ? 'Previous' : 'Next'} month"]`);
      if (button && !button.disabled) { event.preventDefault(); button.click(); }
    };
    const onCopy = (event: ClipboardEvent) => {
      const row = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>('[data-copy]');
      const selection = window.getSelection();
      if (!row || (selection && !selection.isCollapsed && selection.toString().trim()) || !event.clipboardData) return;
      event.clipboardData.setData('text/plain', row.dataset.copy || '');
      event.preventDefault();
      announce('Copied the selected entry');
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('keydown', onStep);
    document.addEventListener('copy', onCopy);
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('keydown', onStep); document.removeEventListener('copy', onCopy); };
  }, []);
  return <main className={`app ${view === 'add' || view === 'drafts' || view === 'statements' ? 'live-view' : ''}`}><a className="skip-link" href="#main-content" onClick={(event) => { event.preventDefault(); document.querySelector<HTMLElement>('.content h1')?.focus({ preventScroll: true }); }}>Skip to content</a><Sidebar active={view} month={month} attention={live.attention} status={status} unreviewed={unreviewed} onHelp={() => setShortcutsOpen(true)}/><p className="sr-only navigation-status" role="status">{status}</p>
    <div className="content" id="main-content" tabIndex={-1}>{view === 'add' ? <Add live={live} composer={composer}/> : view === 'drafts' ? <Drafts live={live}/> : view === 'statements' ? <Statements/> : <div key={version}>{view === 'explorer' ? <Explorer month={month} setMonth={setMonth}/> : view === 'atlas' ? <Atlas month={month} setMonth={setMonth}/> : view === 'journal' ? <Journal month={month} setMonth={setMonth}/> : view === 'forecast' ? <Forecast/> : view === 'accounts' ? <AccountSummary/> : <Review/>}</div>}</div>
    {!!live.undo.length && <div className="draft-undo panel" role="status">{live.undo.map(item=><div key={item.id}>Draft discarded. <button className="button" onClick={()=>void live.restore(item.id)}>Undo</button></div>)}{live.undoError && <p role="alert" className="live-error">{live.undoError}</p>}</div>}
    <nav className="phone-live-nav" aria-label="Quick navigation"><a href={`${import.meta.env.BASE_URL}add/`} aria-current={view==='add'?'page':undefined}><Icon name="pencil"/>Add</a><a href={`${import.meta.env.BASE_URL}drafts/`} aria-current={view==='drafts'?'page':undefined}><Icon name="stack"/>Drafts{live.attention>0 && <span aria-label={`${live.attention} need attention`}>{live.attention}</span>}</a><a href={`${import.meta.env.BASE_URL}statements/`} aria-current={view==='statements'?'page':undefined}><Icon name="source"/>Statements</a><button ref={viewsButton} type="button" aria-current={!(['add', 'drafts', 'statements'] as View[]).includes(view) ? 'page' : undefined} aria-expanded={more} aria-controls="phone-more-views" onClick={()=>setMore(!more)}><Icon name="explorer"/>Views</button></nav>
    {more && <div className="phone-more-views panel" id="phone-more-views"><nav aria-label="Other views">{VIEWS.filter(v=>v.id!=='add' && v.id!=='drafts' && v.id!=='statements').map(v=><a key={v.id} href={`${import.meta.env.BASE_URL}${v.href}`} aria-current={v.id === view ? 'page' : undefined}><Icon name={v.icon}/>{v.label}{v.id === 'review' && unreviewed > 0 && <span className="nav-count"><span className="sr-only">, </span>{unreviewed}<span className="sr-only"> to review</span></span>}</a>)}</nav><button className="button" onClick={()=>setAppearance(appearance==='light'?'dark':'light')}>Switch to {appearance==='light'?'Dark':'Light'} appearance</button><button className="button" onClick={() => { closeViews(); setShortcutsOpen(true); }}><Icon name="help"/>Help &amp; Shortcuts</button><DataBuilt/><button className="button" onClick={closeViews}>Close views</button></div>}
    {shortcutsOpen && <HelpDialog onClose={() => setShortcutsOpen(false)}/>}
  </main>;
}
