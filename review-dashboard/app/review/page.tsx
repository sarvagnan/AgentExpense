import {formatTimestamp} from '../date-time';
import { reviewKeys } from '../../scripts/account-metadata.mjs';
import { moduleSettings } from '../../scripts/ledger-modules.mjs';
import { fuelLedgerEntry } from '../fuel-links';
import { useWorkStore, useReviewEditor } from './storage';
import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent, type CSSProperties, type ReactNode } from 'react';
import { dashboard as dashboardData, fuel as fuelData, review as reviewData, type DashboardData, type ReviewData } from '../data';
import { EvidenceName, Dialog, entryDragProps, hasSelectedText, Icon, PageHeader, SearchField, breakable, dayShort, evidenceHref, modalOpen, copyAndAnnounce, usePaneWidth, useElementScroll, sessionRead, useColumnWidths, rowTitle, evidenceAction, copyText, dayLabel, money, isTyping, plural, useContextMenu, useQueryState, useRowNavigation, type MenuItem } from '../ui';

type Transaction = ReviewData['transactions'][number];
type LedgerTransaction = DashboardData['transactions'][number];
type ReviewStatus = 'unreviewed' | 'resolved' | 'followup';
type Decision = { status: ReviewStatus; account: string; label: string; notes: string; updatedAt: string };
type Decisions = Record<string, Decision>;
type Draft = Pick<Decision, 'account' | 'label' | 'notes'>;
type Sort = 'amount' | 'recent' | 'name';
type Change = { ids: string[]; before: (Decision | undefined)[]; after: (Decision | undefined)[]; summary: string;
  /** A draft the change consumed (Resolve clears it), so Undo can bring it back. */
  draft?: { key: string; before?: Draft; after?: Draft };
  drafts?: { before: Record<string, Draft>; after: Record<string, Draft> } };
type Notice = { tone: 'success' | 'error' | 'info'; text: string; undo?: boolean; redo?: boolean };

const SUGGESTED_ACCOUNTS = [
  'Expenses:Food:Dining & Delivery', 'Expenses:Food:Groceries', 'Expenses:Housing:Household',
  'Expenses:Housing:Furniture & Appliances', 'Expenses:Utilities', 'Expenses:Transport:Local',
  'Expenses:Transport:Fuel', 'Expenses:Travel', 'Expenses:Shopping', 'Expenses:Health',
  'Expenses:Subscriptions', 'Expenses:Entertainment', 'Expenses:Education', 'Expenses:Insurance',
  'Expenses:Donations', 'Expenses:Fees', 'Expenses:Taxes', 'Expenses:Uncategorized',
  'Income:Reimbursement', 'Income:Refund', 'Income:Employer:Other', 'Income:Other',
];
const EMPTY_DRAFT: Draft = { account: '', label: '', notes: '' };
const STATUS_LABEL: Record<ReviewStatus, string> = { unreviewed: 'Unreviewed', followup: 'Follow-Up', resolved: 'Resolved' };

const isMoneyIn = (transaction: Transaction) => transaction.kind !== 'Expense';
const signed = (transaction: Transaction) => `${isMoneyIn(transaction) ? '+' : ''}${money(transaction.amount, transaction.currency)}`;
const STATUSES: ReviewStatus[] = ['unreviewed', 'resolved', 'followup'];
const KNOWN_IDS = () => new Set(reviewData.transactions.map((item) => item.id));
const isText = (value: unknown) => typeof value === 'string';
/**
 * The one rule for a decision, used when saving, loading and importing: a known status, text fields,
 * and an account whenever the status is Resolved (as the Resolve button requires).
 */
function validDecision(value: unknown): value is Decision {
  const decision = value as Partial<Decision> | null;
  return !!decision && typeof decision === 'object' && STATUSES.includes(decision.status as ReviewStatus)
    && isText(decision.account) && isText(decision.label) && isText(decision.notes) && isText(decision.updatedAt)
    && (decision.status !== 'resolved' || !!decision.account?.trim());
}
function validDraft(value: unknown): value is Draft {
  const draft = value as Partial<Draft> | null;
  return !!draft && typeof draft === 'object' && isText(draft.account) && isText(draft.label) && isText(draft.notes);
}
/** Keeps only well-formed entries for known transactions, so a damaged store can't blank the page. */
function sanitize<T>(value: unknown, valid: (item: unknown) => item is T, allowKey: (key: string) => boolean): Record<string, T> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).filter(([key, item]) => allowKey(key) && valid(item))) as Record<string, T>;
}
// Saved work is never dropped for belonging to an entry this build doesn't list (an entry's id changes
// when the ledger edits it): such decisions stay stored, are exported, and can be discarded on purpose.
const isDecisionKey = (key: string) => key.length > 0;
const isDraftKey = (key: string) => key.length > 0;


function matchKey(transaction: { date: string; description: string; amount: number; sourceReference: string }) {
  return `${transaction.date}|${transaction.description}|${transaction.amount.toFixed(2)}|${transaction.sourceReference}`;
}
function saveBlob(content: string, type: string, filename: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url);
}
const csvCell = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`;

let ledgerIndex: { source: DashboardData; index: Map<string, LedgerTransaction>; accounts: string[] } | undefined;
function ledger() {
  if (ledgerIndex?.source !== dashboardData) {
    const accounts = new Set(SUGGESTED_ACCOUNTS);
    for (const item of dashboardData.transactions) for (const posting of item.postings) if (/^(Expenses|Income):/.test(posting.account)) accounts.add(posting.account);
    ledgerIndex = { source: dashboardData, index: new Map(dashboardData.transactions.map((item) => [matchKey(item), item])), accounts: [...accounts].sort() };
  }
  return ledgerIndex;
}

type FuelEntry = (typeof fuelData.entries)[number];
type FuelSort = 'date' | 'cost' | 'litres' | 'tripKm';
/** The fuel log, as a panel that stays open beside the entries while you classify fuel charges. */
function FuelLog({ onClose }: { onClose: () => void }) {
  const { widths, resizer } = useColumnWidths('ledger-fuel-columns', { cost: 84, litres: 48, tripKm: 56 }, { cost: 80, litres: 44, tripKm: 48 });
  const [sort, setSort] = useState<{ key: FuelSort; ascending: boolean }>({ key: 'date', ascending: false });
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>('button')?.focus();
    // Return focus to the Fuel Log button when the panel closes from inside it.
    return () => { if (!document.activeElement || document.activeElement === document.body || panel.current?.contains(document.activeElement)) opener?.focus(); };
  }, []);
  const value = (entry: FuelEntry) => sort.key === 'date' ? entry.date || '' : entry[sort.key] ?? -1;
  const entries = [...fuelData.entries].sort((a, b) => { const x = value(a), y = value(b); return (x < y ? -1 : x > y ? 1 : 0) * (sort.ascending ? 1 : -1); });
  const heading = (key: FuelSort, label: string, numeric = false) => <th className={numeric ? 'th-num' : ''} aria-label={label} aria-sort={sort.key === key ? (sort.ascending ? 'ascending' : 'descending') : 'none'}>
    <button type="button" className="sort-button" title={`Sort by ${label.toLowerCase()}`} onClick={() => setSort((current) => ({ key, ascending: current.key === key ? !current.ascending : key !== 'date' && !numeric }))}>{label}<span className="sort-indicator" aria-hidden="true">{sort.key === key ? (sort.ascending ? '▲' : '▼') : ''}</span></button>{key === 'date' ? resizer('date', label, 'cost') : resizer(key, label)}
  </th>;
  return <aside ref={panel} id="fuel-log" className="panel fuel-panel" aria-labelledby="fuel-title" onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); onClose(); } }}>
    <div className="panel-header"><div><h2 id="fuel-title">Fuel Log</h2><p>Fills, trips and ledger matches</p></div><button type="button" className="icon-button" aria-label="Close the fuel log" title="Close (Esc)" onClick={onClose}><Icon name="close"/></button></div>
    <div className="table-scroll" tabIndex={0} aria-label="Fuel log entries"><table className="data-table fuel-table"><colgroup><col/><col style={{ width: widths.cost }}/><col style={{ width: widths.litres }}/><col style={{ width: widths.tripKm }}/></colgroup><thead><tr>{heading('date', 'Date / Ledger')}{heading('cost', 'Cost', true)}{heading('litres', 'L', true)}{heading('tripKm', 'km', true)}</tr></thead>
      <tbody>{entries.map((item) => { const linked = fuelLedgerEntry(item, dashboardData.transactions, dashboardData.meta.ledger); return <tr key={item.id}>
        <td className="date">{item.date ? dayLabel(item.date) : 'No date'}<span className="fuel-match">{item.ledgerStatus === 'Linked' ? linked ? <a className="fuel-entry-link" href={`${import.meta.env.BASE_URL}explorer/?month=${linked.month}&entry=${linked.id}`} target="_blank" rel="noopener" title={linked.description} aria-label={`Open linked ledger entry ${linked.sequence + 1}: ${linked.description}`}><Icon name="check"/>Entry {linked.sequence + 1}</a> : <span className="status pending">Marked linked · reference unavailable</span> : <span className="status pending"><Icon name="pending"/>{item.ledgerStatus}</span>}</span><span className="fuel-payment">Paid with {item.payment || 'not recorded'}</span></td>
        <td className="num">{item.cost === null ? '—' : money(item.cost, item.currency)}</td><td className="num">{item.litres === null ? '—' : item.litres.toFixed(2)}</td><td className="num">{item.tripKm === null ? '—' : item.tripKm.toFixed(1)}</td>
      </tr>; })}</tbody></table></div>
  </aside>;
}

function StatusLabel({ status, draft }: { status: ReviewStatus; draft: boolean }) {
  if (draft) return <span className="status pending"><Icon name="pencil"/>Draft</span>;
  if (status === 'resolved') return <span className="status resolved"><Icon name="check"/>Resolved</span>;
  if (status === 'followup') return <span className="status followup"><Icon name="flag"/>Follow-Up</span>;
  return null;
}

export default function Review() {
  const pane = usePaneWidth('ledger-review-detail-width', 440, 300, 'Review detail');
  const editor = useReviewEditor();
  const readOnly = !editor.editable;
  const onAdopt = () => { setHistory({ undo: [], redo: [] }); setNotice({ tone: 'info', text: 'Work changed in another tab; saved work was loaded. Earlier changes here can no longer be undone.' }); };
  const {decisions: STORAGE_KEY, drafts: DRAFTS_KEY, exported: EXPORTED_KEY} = reviewKeys(dashboardData.meta.ledger);
  const decisionStore = useWorkStore(STORAGE_KEY, validDecision, onAdopt, editor.editable);
  const draftStore = useWorkStore(DRAFTS_KEY, validDraft, onAdopt, editor.editable);
  const { data: decisions, setData: setDecisions, saved: decisionsSaved } = decisionStore;
  const { data: drafts, setData: setDrafts, saved: draftsSaved } = draftStore;
  const stores = [decisionStore, draftStore];
  const [query, setQuery] = useQueryState('q', '');
  const [statusFilter, setStatusFilter] = useQueryState('status', 'unreviewed', (value) => ['unreviewed', 'followup', 'resolved', 'all'].includes(value));
  const [sourceFilter, setSourceFilter] = useQueryState('source', 'all');
  const [kindFilter, setKindFilter] = useQueryState('type', 'all');
  const [sortParam, setSort] = useQueryState('sort', 'amount', (value) => ['amount', 'recent', 'name'].includes(value));
  const sort = sortParam as Sort;
  const [selectedId, setSelectedId] = useQueryState('entry', '');
  const [batchIds, setBatchIds] = useState<string[] | null>(null);
  const [fuelParam, setFuelParam] = useQueryState('fuel', '', (value) => value === 'open');
  const fuelEnabled = moduleSettings(dashboardData.meta.ledger).fuel;
  const fuelOpen = fuelEnabled && fuelParam === 'open';
  const setFuelOpen = (open: boolean) => setFuelParam(open ? 'open' : '');
  const listRef = useRef<HTMLDivElement>(null);
  const detailRef = useRef<HTMLElement>(null);
  useElementScroll(listRef, 'review-list');
  useElementScroll(detailRef, 'review-detail');
  const [confirmClear, setConfirmClear] = useState(false);
  const [confirmDiscardRecovery, setConfirmDiscardRecovery] = useState(false);
  const recoveryCount = new Set(stores.flatMap((store) => Object.keys(store.recovery || {}))).size;
  const [lastExport, setLastExport] = useState(() => { try { return window.localStorage.getItem(EXPORTED_KEY) || ''; } catch { return ''; } });
  const workFingerprint = JSON.stringify({ decisions, drafts });
  const [exportedWork, setExportedWork] = useState(() => { try { return localStorage.getItem(`${EXPORTED_KEY}-work`) || ''; } catch { return ''; } });
  const workCount = Object.keys(decisions).length + Object.keys(drafts).length;
  const [dropping, setDropping] = useState(false);
  const menu = useContextMenu();
  const [notice, setNotice] = useState<Notice | null>(null);
  const [accountError, setAccountError] = useState(false);
  const [history, setHistory] = useState<{ undo: Change[]; redo: Change[] }>({ undo: [], redo: [] });
  useEffect(() => { if (!sessionRead('ledger-scroll:review-list')) document.querySelector('.review-row[aria-selected="true"]')?.scrollIntoView({ block: 'center' }); }, []);
  const importRef = useRef<HTMLInputElement>(null);
  const accountRef = useRef<HTMLInputElement>(null);

  const decisionFor = useCallback((id: string): Decision => decisions[id] || { status: 'unreviewed', account: '', label: '', notes: '', updatedAt: '' }, [decisions]);
  const counts = useMemo(() => {
    const result = { unreviewed: 0, followup: 0, resolved: 0, all: reviewData.transactions.length };
    for (const item of reviewData.transactions) result[decisionFor(item.id).status] += 1;
    return result;
  }, [decisionFor]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return reviewData.transactions.filter((item) => {
      const decision = decisionFor(item.id);
      const haystack = `${item.description} ${item.pattern} ${item.sourceReference} ${item.source} ${item.amount.toFixed(2)} ${money(item.amount, item.currency)} ${dayLabel(item.originalDate)} ${decision.account} ${decision.label} ${decision.notes}`.toLowerCase();
      return (!needle || haystack.includes(needle)) && (statusFilter === 'all' || decision.status === statusFilter)
        && (sourceFilter === 'all' || item.sourceType === sourceFilter) && (kindFilter === 'all' || item.kind === kindFilter);
    }).sort((a, b) => sort === 'recent' ? b.originalDate.localeCompare(a.originalDate) : sort === 'name' ? a.description.localeCompare(b.description) : b.amount - a.amount);
  }, [decisionFor, kindFilter, query, sort, sourceFilter, statusFilter]);

  // The detail pane always shows an entry the list shows, so the two never disagree.
  const selected = filtered.find((item) => item.id === selectedId) || filtered[0] || null;
  const targetIds = batchIds && selected && batchIds.includes(selected.id) ? batchIds : selected ? [selected.id] : [];
  const draftKey = targetIds.length > 1 ? `batch:${[...targetIds].sort().join(',')}` : targetIds[0] || '';
  const saved = selected ? decisionFor(selected.id) : null;
  const draft: Draft = drafts[draftKey] || (targetIds.length > 1 ? EMPTY_DRAFT : saved ? { account: saved.account, label: saved.label, notes: saved.notes } : EMPTY_DRAFT);
  const dirty = !!drafts[draftKey];
  const matching = selected ? reviewData.transactions.filter((item) => item.pattern === selected.pattern && item.kind === selected.kind) : [];
  const ledgerTransaction = selected ? ledger().index.get(matchKey(selected)) : undefined;
  const knownAccount = !draft.account.trim() || ledger().accounts.includes(draft.account.trim());

  const editDraft = (patch: Partial<Draft>) => {
    if (!draftKey) return;
    setAccountError(false);
    commit((decisions, drafts) => {
      const base = drafts[draftKey] || (targetIds.length > 1 ? EMPTY_DRAFT : decisions[targetIds[0]]) || EMPTY_DRAFT;
      const value = { account: base.account, label: base.label, notes: base.notes, ...patch };
      record({ ids: [], before: [], after: [], drafts: { before: drafts, after: { ...drafts, [draftKey]: value } }, summary: 'edited draft' });
    });
  };
  const showDetails = () => requestAnimationFrame(() => {
    detailRef.current?.scrollIntoView({ block: 'start' });
    accountRef.current?.focus({ preventScroll: true });
  });
  const select = useCallback((item: Transaction) => { setSelectedId(item.id, 'replace'); setBatchIds(null); setAccountError(false); }, [setSelectedId]);
  const idOf = useCallback((item: Transaction) => item.id, []);
  const describe = useCallback((item: Transaction) => item.description, []);
  const nav = useRowNavigation(filtered, idOf, selected?.id || null, select, 0, describe);

  /** Shows an entry an undo or redo changed: selects it in the list (switching to All if it left this one). */
  const reveal = useCallback((id: string | undefined) => {
    if (!id) return;
    setSelectedId(id, 'replace'); setBatchIds(null);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (!document.querySelector(`.review-row[data-row-id="${id}"]`)) setStatusFilter('all');
      requestAnimationFrame(() => { document.querySelector<HTMLElement>(`.review-row[data-row-id="${id}"]`)?.scrollIntoView({ block: 'nearest' }); });
    }));
  }, [setSelectedId, setStatusFilter]);
  const apply = useCallback((change: Change, direction: 'before' | 'after') => {
    setDecisions((current) => {
      const next = { ...current };
      change.ids.forEach((id, index) => { const value = change[direction][index]; if (value) next[id] = value; else delete next[id]; });
      return next;
    });
    if (change.drafts) setDrafts(change.drafts[direction]);
    if (change.draft) {
      const { key } = change.draft; const value = change.draft[direction];
      setDrafts((current) => { const next = { ...current }; if (value) next[key] = value; else delete next[key]; return next; });
    }
  }, []);
  /** All user mutations read both working stores at commit time, including after file reads. */
  const commit = (operation: (decisions: Decisions, drafts: Record<string, Draft>) => void) => { if (editor.canEdit()) operation(decisionStore.current.current, draftStore.current.current); };
  const record = (change: Change) => {
    change.before = change.ids.map((id) => decisionStore.current.current[id]);
    if (change.draft) change.draft.before = draftStore.current.current[change.draft.key];
    if (change.drafts) change.drafts.before = draftStore.current.current;
    apply(change, 'after'); setHistory((current) => ({ undo: [...current.undo, change].slice(-50), redo: [] })); };
  const undo = useCallback(() => {
    const change = history.undo.at(-1); if (!editor.canEdit() || !change) return;
    apply(change, 'before');
    setHistory((current) => ({ undo: current.undo.slice(0, -1), redo: [...current.redo, change] }));
    setNotice({ tone: 'info', text: `Undid: ${change.summary}`, redo: true });
    reveal(change.ids[0]);
  }, [apply, history.undo, reveal]);
  const redo = useCallback(() => {
    const change = history.redo.at(-1); if (!editor.canEdit() || !change) return;
    apply(change, 'after');
    setHistory((current) => ({ undo: [...current.undo, change], redo: current.redo.slice(0, -1) }));
    setNotice({ tone: 'info', text: `Redid: ${change.summary}`, undo: true });
    reveal(change.ids[0]);
  }, [apply, history.redo, reveal]);

  const save = useCallback((status: ReviewStatus) => commit((decisions, drafts) => {
    const draft = drafts[draftKey] || (targetIds.length > 1 ? EMPTY_DRAFT : decisions[targetIds[0]]) || EMPTY_DRAFT;
    if (!selected) return;
    const account = draft.account.trim();
    if (status === 'resolved' && !account) { setAccountError(true); setNotice(null); accountRef.current?.focus(); return; }
    const updatedAt = new Date().toISOString();
    const after: Decision = { status, account, label: draft.label.trim(), notes: draft.notes.trim(), updatedAt };
    const name = targetIds.length > 1 ? plural(targetIds.length, 'entry', 'entries') : `“${selected.description}”`;
    record({ ids: targetIds, before: targetIds.map((id) => decisions[id]), after: targetIds.map(() => after), summary: status === 'resolved' ? `resolved ${name} as ${account}` : `marked ${name} for follow-up`,
      draft: drafts[draftKey] ? { key: draftKey, before: drafts[draftKey], after: undefined } : undefined });
    setNotice({ tone: 'success', text: status === 'resolved' ? `Resolved ${name} as ${account}.` : `Marked ${name} for follow-up.`, undo: true });
    // Move on to the next entry still in this list, as Mail does after filing a message.
    if (statusFilter !== 'all') {
      const index = filtered.findIndex((item) => item.id === selected.id);
      const next = filtered.slice(index + 1).find((item) => !targetIds.includes(item.id)) || filtered.slice(0, index).reverse().find((item) => !targetIds.includes(item.id));
      if (next) { setSelectedId(next.id, 'replace'); setBatchIds(null); requestAnimationFrame(() => nav.focusRow(next.id)); }
    }
  }), [selected, draft, drafts, targetIds, decisions, draftKey, statusFilter, filtered, setSelectedId, nav]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (modalOpen() || !editor.canEdit()) return;
      if (event.metaKey && event.key === 'Enter') { event.preventDefault(); save(event.shiftKey ? 'followup' : 'resolved'); return; }
      // Match the typed character, not the key's position, so ⌘Z is Undo on every keyboard layout.
      if (event.metaKey && event.key.toLowerCase() === 'z' && !isTyping(event.target)) { event.preventDefault(); if (event.shiftKey) redo(); else undo(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [save, undo, redo, confirmClear]);

  function exportJson() {
    const rows = reviewData.transactions.map((item) => ({ ...item, decision: decisionFor(item.id) }));
    const unmatched = Object.fromEntries(Object.entries(decisions).filter(([id]) => !KNOWN_IDS().has(id)));
    const exportedAt = new Date().toISOString();
    saveBlob(JSON.stringify({ exportedAt, sourceLedger: reviewData.meta.ledger, reviewVersion: 2, transactions: rows, unmatched, drafts, storage: { decisions: decisionStore.state, drafts: draftStore.state } }, null, 2), 'application/json', 'expense-review-decisions.json');
    try { if (editor.canEdit()) localStorage.setItem(EXPORTED_KEY, exportedAt); } catch { /* the reminder is a convenience */ }
    setLastExport(exportedAt); setExportedWork(workFingerprint);
    try { if (editor.canEdit()) localStorage.setItem(`${EXPORTED_KEY}-work`, workFingerprint); } catch { /* Keep the session reminder. */ }
    setNotice({ tone: 'success', text: 'Download requested: expense-review-decisions.json. Keep it to back up decisions and drafts, or move work to another browser with Import.' });
  }
  function exportRecovery() {
    const recoveredDecisions = decisionStore.recoveredData();
    const recoveredDrafts = draftStore.recoveredData();
    const transactions = reviewData.transactions.map((item) => ({ ...item, decision: recoveredDecisions[item.id] || { status: 'unreviewed', ...EMPTY_DRAFT, updatedAt: '' } }));
    const unmatched = Object.fromEntries(Object.entries(recoveredDecisions).filter(([id]) => !KNOWN_IDS().has(id)));
    saveBlob(JSON.stringify({ exportedAt: new Date().toISOString(), sourceLedger: reviewData.meta.ledger, reviewVersion: 2, transactions, unmatched, drafts: recoveredDrafts,
      recovery: { decisions: decisionStore.recovery || {}, drafts: draftStore.recovery || {} } }, null, 2), 'application/json', 'expense-review-unsaved-work.json');
    setNotice({ tone: 'info', text: 'Download requested: expense-review-unsaved-work.json. The recovery copy and departure warning remain until you restore or discard it.' });
  }
  function restoreRecovery() { commit((decisions, drafts) => {
    const ids = Object.keys(decisionStore.recovery || {});
    const recovered = decisionStore.recoveredData();
    record({ ids, before: ids.map((id) => decisions[id]), after: ids.map((id) => recovered[id]),
      drafts: { before: drafts, after: draftStore.recoveredData() }, summary: 'restored unsaved work' });
    stores.forEach((store) => store.discardRecovery());
    setNotice({ tone: 'success', text: `Restored unsaved work for ${plural(recoveryCount, 'entry', 'entries')}. Newer saved work on other entries was kept.`, undo: true });
  }); }
  function exportCsv() {
    const headers = ['id', 'date', 'description', 'amount', 'currency', 'kind', 'source', 'source_reference', 'current_account', 'review_status', 'chosen_account', 'label', 'notes'];
    const rows = reviewData.transactions.map((item) => { const decision = decisionFor(item.id); return [item.id, item.originalDate, item.description, item.signedAmount, item.currency, item.kind, item.source, item.sourceReference, item.currentAccount, decision.status, decision.account, decision.label, decision.notes]; });
    saveBlob([headers, ...rows].map((row) => row.map(csvCell).join(',')).join('\n'), 'text/csv;charset=utf-8', 'expense-review-decisions.csv');
    setNotice({ tone: 'success', text: 'Download requested: expense-review-decisions.csv, a spreadsheet of every decision. Import reads the JSON file, not this one.' });
  }
  function importDecisions(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; if (!file) return;
    importFile(file); event.target.value = '';
  }
  function importFile(file: File) {
    if (!editor.canEdit()) return;
    setNotice(null);
    const reader = new FileReader();
    const readFailed = () => setNotice({ tone: 'error', text: `${file.name} couldn’t be read. Choose it again; current work is unchanged.` });
    reader.onerror = readFailed; reader.onabort = readFailed;
    reader.onload = () => {
      try {
        const payload = JSON.parse(String(reader.result));
        const rows: { id?: unknown; decision?: unknown }[] = Array.isArray(payload?.transactions) ? payload.transactions : [];
        const known = KNOWN_IDS();
        const invalidRows = rows.filter((row) => !row || typeof row.id !== 'string' || !row.id.length || !validDecision(row.decision));
        const invalidMap = (value: unknown, valid: (item: unknown) => boolean) => value === undefined ? []
          : !value || typeof value !== 'object' || Array.isArray(value) ? ['(record map)']
          : Object.entries(value).filter(([key, item]) => !key.length || !valid(item)).map(([key]) => key || '(empty key)');
        const invalidDrafts = invalidMap(payload.drafts, validDraft);
        if (payload.conflicts !== undefined && (!payload.conflicts || typeof payload.conflicts !== 'object' || Object.values(payload.conflicts).some((value) => !Array.isArray(value) || value.length))) {
          setNotice({ tone: 'error', text: 'This file contains unresolved conflicts. Resolve them in the exporting version before importing. Nothing was imported.' }); return;
        }
        const invalidUnmatched = invalidMap(payload.unmatched, validDecision);
        if (!Array.isArray(payload?.transactions) || invalidRows.length || invalidDrafts.length || invalidUnmatched.length) {
          const describe = (kind: string, keys: string[]) => keys.length ? `${plural(keys.length, kind)}${keys.length <= 4 ? ` (${keys.join(', ')})` : ''}` : '';
          const problems = [describe('invalid decision', invalidRows.map((row) => String(row?.id ?? '(missing id)'))), describe('invalid draft', invalidDrafts), describe('invalid unmatched decision', invalidUnmatched)].filter(Boolean).join('; ');
          setNotice({ tone: 'error', text: problems ? `${file.name} has ${problems}, so nothing was imported. Correct those records and try again.` : `${file.name} isn’t a review export. Choose a .json file made with Export.` });
          return;
        }
        const seen = new Map<string, unknown>();
        const conflicting = new Set<string>();
        let duplicates = 0;
        for (const row of rows) {
          const id = row.id as string;
          if (seen.has(id)) { if (JSON.stringify(seen.get(id)) !== JSON.stringify(row)) conflicting.add(id); else duplicates++; }
          else seen.set(id, row);
        }
        for (const [id, decision] of Object.entries(payload.unmatched || {})) {
          const row = rows.find((item) => item.id === id);
          if (row && JSON.stringify(row.decision) !== JSON.stringify(decision)) conflicting.add(id);
        }
        if (conflicting.size) { setNotice({ tone: 'error', text: `Conflicting duplicate IDs: ${[...conflicting].join(', ')}. Nothing was imported.` }); return; }
        const extra = sanitize(payload.unmatched, validDecision, isDecisionKey);
        const imported: Decisions = { ...extra, ...Object.fromEntries(rows.filter((row) => row.decision !== undefined).map((row) => [row.id as string, row.decision as Decision])) };
        const elsewhere = Object.keys(imported).filter((id) => !known.has(id) && imported[id].status !== 'unreviewed').length;
        commit((decisions, drafts) => {
        const ids = Object.keys(imported).filter((id) => imported[id].status !== 'unreviewed' || decisions[id]);
        const importedDrafts = sanitize(payload.drafts, validDraft, isDraftKey);
        const nextDrafts = { ...drafts };
        let adoptedDrafts = 0, skippedDrafts = 0;
        for (const [id, value] of Object.entries(importedDrafts)) {
          if (drafts[id] && JSON.stringify(drafts[id]) !== JSON.stringify(value)) { skippedDrafts++; continue; }
          nextDrafts[id] = value; adoptedDrafts++;
        }
        const adoptedDecisions = ids.length;
        record({ ids, before: ids.map((id) => decisions[id]), after: ids.map((id) => imported[id]?.status === 'unreviewed' ? undefined : imported[id]),
          drafts: { before: drafts, after: nextDrafts },
          summary: `imported ${plural(adoptedDecisions, 'decision')} and ${plural(adoptedDrafts, 'draft')}` });
        setNotice({ tone: 'success', text: `Imported ${plural(adoptedDecisions, 'decision')} and ${plural(adoptedDrafts, 'draft')} from ${file.name}.${skippedDrafts ? ` Skipped ${plural(skippedDrafts, 'differing draft')}; local drafts were kept.` : ''}${duplicates ? ` Collapsed ${plural(duplicates, 'exact duplicate')}.` : ''}${elsewhere ? ` ${elsewhere} kept for entries no longer pending.` : ''}`, undo: true });
        });
      } catch { setNotice({ tone: 'error', text: `${file.name} isn’t a review export. Choose a .json file made with Export.` }); }
    };
    reader.readAsText(file);
  }
  const unmatchedCount = Object.keys(decisions).filter((id) => !KNOWN_IDS().has(id)).length;
  function discardUnmatched() { commit((decisions) => {
    const ids = Object.keys(decisions).filter((id) => !KNOWN_IDS().has(id));
    record({ ids, before: ids.map((id) => decisions[id]), after: ids.map(() => undefined), summary: `discarded ${plural(ids.length, 'decision')} for entries no longer pending` });
    setNotice({ tone: 'info', text: `Discarded ${plural(ids.length, 'decision')} for entries no longer pending.`, undo: true });
  }); }
  /** Sets one entry's status from its context menu, keeping any saved account, label and notes. */
  function setStatusFor(item: Transaction, status: ReviewStatus) { commit((decisions) => {
    const before = decisions[item.id];
    const after: Decision | undefined = status === 'unreviewed' ? undefined : { ...(before || { account: '', label: '', notes: '' }), status, updatedAt: new Date().toISOString() } as Decision;
    record({ ids: [item.id], before: [before], after: [after], summary: `${status === 'followup' ? 'marked' : 'returned'} “${item.description}” ${status === 'followup' ? 'for follow-up' : 'to unreviewed'}` });
    setNotice({ tone: 'success', text: status === 'followup' ? `Marked “${item.description}” for follow-up.` : `Returned “${item.description}” to Unreviewed.`, undo: true });
  }); }
  const rowMenu = (item: Transaction): MenuItem[] => {
    const status = decisionFor(item.id).status;
    return [
      { label: 'Show Details', action: () => { select(item); showDetails(); } },
      { label: 'Open Source', action: () => window.open(evidenceHref(item.source)!, '_blank', 'noopener'), disabled: !evidenceHref(item.source) },
      'separator',
      { label: 'Mark for Follow-Up', action: () => setStatusFor(item, 'followup'), disabled: readOnly || status === 'followup' },
      { label: 'Return to Unreviewed', action: () => setStatusFor(item, 'unreviewed'), disabled: readOnly || status === 'unreviewed' },
      'separator',
      { label: 'Copy Description', action: () => copyAndAnnounce(item.description, 'description') },
      { label: 'Copy Reference', action: () => copyAndAnnounce(item.sourceReference, 'reference'), disabled: !item.sourceReference },
      { label: 'Copy Source Path', action: () => copyAndAnnounce(item.source, 'source path') },
    ];
  };
  function clearAll() { commit((decisions) => {
    const ids = Object.keys(decisions);
    setConfirmClear(false);
    record({ ids, before: ids.map((id) => decisions[id]), after: ids.map(() => undefined), summary: `cleared ${plural(ids.length, 'saved decision')}` });
    setNotice({ tone: 'info', text: `Cleared ${plural(ids.length, 'saved decision')}.`, undo: true });
  }); }

  const resolvedShare = Math.round(counts.resolved / counts.all * 100);
  const listLabel = statusFilter === 'all' ? 'All entries' : `${STATUS_LABEL[statusFilter as ReviewStatus]} entries`;
  const message: ReactNode = notice && <p className={`message ${notice.tone}`} role={notice.tone === 'error' ? 'alert' : 'status'}>
    {notice.tone === 'success' && <Icon name="check"/>}<span>{notice.text}</span>
    {notice.undo && history.undo.length > 0 && <button type="button" className="button plain" disabled={readOnly} onClick={undo}>Undo <kbd>⌘Z</kbd></button>}
    {notice.redo && history.redo.length > 0 && <button type="button" className="button plain" disabled={readOnly} onClick={redo}>Redo <kbd>⇧⌘Z</kbd></button>}
    <button type="button" className="icon-button" aria-label="Dismiss message" title="Dismiss" onClick={() => setNotice(null)}><Icon name="close"/></button>
  </p>;

  const hasFiles = (event: DragEvent) => [...event.dataTransfer.types].includes('Files');
  return <section className="page review"
    onDragOver={(event) => { if (hasFiles(event) && !readOnly) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; setDropping(true); } }}
    onDragLeave={(event) => { if (event.currentTarget === event.target || !event.currentTarget.contains(event.relatedTarget as Node)) setDropping(false); }}
    onDrop={(event) => { if (!hasFiles(event)) return; if (readOnly) { event.preventDefault(); return; } event.preventDefault(); setDropping(false); if (event.dataTransfer.files.length > 1) { setNotice({ tone: 'error', text: 'Drop one export file at a time.' }); return; } const file = event.dataTransfer.files[0]; if (file) importFile(file); }}>
    <PageHeader title="Review" subtitle={`${plural(counts.all, 'ledger entry is', 'ledger entries are')} unclassified (marked pending) and need an account. Decisions are saved in this browser until you export them.`}>
      <div className="review-progress" title={`${counts.resolved} resolved, ${counts.followup} for follow-up`}>
        <span><strong>{counts.resolved}</strong> of {counts.all} resolved</span>
        <progress max={counts.all} value={counts.resolved} aria-label={`${resolvedShare}% resolved`}/>
        <span className={`export-state ${workCount && workFingerprint !== exportedWork ? 'warning' : ''}`}>{lastExport ? `${workFingerprint !== exportedWork ? 'Changes since last export · ' : 'Last exported '}${formatTimestamp(lastExport)}` : workCount ? `${Object.keys(drafts).length ? plural(Object.keys(drafts).length, 'draft') + ' not exported · ' : ''}Not exported from this browser yet` : 'Nothing to export yet'}{unmatchedCount ? ` · ${unmatchedCount} kept for entries no longer pending` : ''}</span>
      </div>
      {fuelEnabled && <button type="button" className="button" aria-pressed={fuelOpen} aria-controls="fuel-log" onClick={() => setFuelOpen(!fuelOpen)}><Icon name="fuel"/>Fuel Log</button>}
      <button type="button" className="button" aria-haspopup="menu" onClick={(event) => menu.openBelow(event.currentTarget, [
        { label: 'Import Decisions…', action: () => importRef.current?.click(), disabled: readOnly },
        'separator',
        { label: 'Export Work…', action: exportJson },
        { label: 'Export as CSV Spreadsheet', action: exportCsv },
        'separator',
        ...(unmatchedCount ? [{ label: `Discard ${plural(unmatchedCount, 'Decision')} for Entries No Longer Pending`, action: discardUnmatched, disabled: readOnly }] : []),
        { label: 'Clear Saved Decisions…', action: () => Object.keys(decisions).length ? setConfirmClear(true) : setNotice({ tone: 'info', text: 'There are no saved decisions to clear.' }), disabled: readOnly || !Object.keys(decisions).length },
      ])}><Icon name="export"/>Import &amp; Export<Icon name="chevron-up-down"/></button>
      <input ref={importRef} className="sr-only" type="file" disabled={readOnly} accept="application/json,.json" tabIndex={-1} aria-hidden="true" onChange={importDecisions}/>
    </PageHeader>
    {readOnly && <div className="message info" role="status"><span>Review is open for editing in another tab.</span><button type="button" className="button" onClick={editor.takeOver}>Edit Here Instead</button></div>}
    {recoveryCount > 0 && <div className="message error recovery-notice" role="alert"><span><strong>Unsaved work kept separately</strong> for {plural(recoveryCount, 'entry', 'entries')}. This view shows the latest saved work. Export before leaving; the departure warning remains active.{!readOnly && ' Restore replaces only the entries you changed here; other saved work stays.'}</span>
      <button type="button" className="button" onClick={exportRecovery}>Export Unsaved Work</button>
      {!readOnly && <button type="button" className="button" onClick={restoreRecovery}>Restore Unsaved Work</button>}
      <button type="button" className="button" onClick={() => setConfirmDiscardRecovery(true)}>Discard</button>
    </div>}
    {!selected && message}

    {stores.some((store) => !store.saved) && !stores.some((store) => store.state === 'unreadable' || store.state === 'damaged') && <div className="message error" role="alert"><span>Work cannot be saved in this browser right now. {!decisionsSaved && 'Decisions are not stored. '}{!draftsSaved && 'Drafts are not stored. '}Keep this page open or export before leaving.</span><button type="button" className="button" onClick={exportJson}>Export Work…</button></div>}
    {stores.some((store) => store.state === 'unreadable' || store.state === 'damaged') && <div className="message error" role="alert"><span>Work cannot be saved in this browser until storage is accessible or you choose replacement. Export keeps your current work.</span>
      {stores.map((store, index) => (store.state === 'unreadable' || store.state === 'damaged') && <div key={index}><span>Saved {index ? 'drafts' : 'decisions'} {store.state === 'unreadable' ? 'could not be read' : 'data is damaged'}. Original storage is preserved until you explicitly replace it.</span>
        <button className="button" onClick={store.retry}>Retry</button><button className="button" onClick={exportJson}>Export Work…</button>
        {store.raw !== undefined && <button className="button" onClick={() => saveBlob(store.raw!, 'application/json', 'damaged-review.json')}>Download damaged {index ? 'drafts' : 'decisions'}</button>}
        <button className="button" disabled={readOnly} onClick={store.replace}>{store.state === 'damaged' ? 'Continue with current work' : 'Replace saved work with current work'}</button></div>)}
    </div>}
    <div className={`review-split ${fuelOpen ? 'with-fuel' : ''}`} style={{ '--review-detail-width': `${pane.width}px` } as CSSProperties}>
      {pane.grip}
      <section className="panel review-queue" aria-label="Entries to review">
        {selected && <button type="button" className="button narrow-details" onClick={showDetails}>Show Details</button>}
        <div className="review-toolbar">
          <SearchField id="review-search" value={query} onChange={setQuery} label="Search entries to review" placeholder="Description, amount or reference" scope={listLabel}/>
          <label className="compact-field"><span>Source</span><select className="field" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}><option value="all">All</option>{Object.entries(reviewData.meta.bySource).map(([source, count]) => <option key={source} value={source}>{source} ({count})</option>)}</select></label>
          <label className="compact-field"><span>Type</span><select className="field" value={kindFilter} onChange={(event) => setKindFilter(event.target.value)}><option value="all">All</option>{Object.entries(reviewData.meta.byKind).map(([kind, count]) => <option key={kind} value={kind}>{kind} ({count})</option>)}</select></label>
          <label className="compact-field"><span>Sort</span><select className="field" value={sort} onChange={(event) => setSort(event.target.value as Sort)}><option value="amount">Largest first</option><option value="recent">Newest first</option><option value="name">Description A–Z</option></select></label>
        </div>
        <div className="segmented-tabs" role="tablist" aria-label="Review status" onKeyDown={(event) => {
          const order = ['unreviewed', 'followup', 'resolved', 'all'];
          const index = order.indexOf(statusFilter);
          const next = event.key === 'ArrowRight' ? index + 1 : event.key === 'ArrowLeft' ? index - 1 : event.key === 'Home' ? 0 : event.key === 'End' ? order.length - 1 : -2;
          if (next === -2 || next < 0 || next >= order.length) return;
          event.preventDefault(); setStatusFilter(order[next]);
          const tablist = event.currentTarget;
          requestAnimationFrame(() => tablist.querySelectorAll<HTMLElement>('[role="tab"]')[next]?.focus());
        }}>{(['unreviewed', 'followup', 'resolved', 'all'] as const).map((value) => <button type="button" role="tab" key={value} aria-selected={statusFilter === value} aria-controls="review-list" tabIndex={statusFilter === value ? 0 : -1} onClick={() => setStatusFilter(value)}>{value === 'all' ? 'All' : STATUS_LABEL[value]} <span className="count">{counts[value]}</span></button>)}</div>
        <div ref={listRef} className="review-list" id="review-list" role="listbox" aria-label={listLabel} aria-describedby={targetIds.length > 1 ? 'review-batch-summary' : undefined} onKeyDown={nav.onKeyDown}>
          {filtered.map((item) => { const status = decisionFor(item.id).status; const isTarget = targetIds.includes(item.id); return <button key={item.id} type="button" role="option" className="review-row" aria-selected={item.id === selected?.id} data-batch-target={targetIds.length > 1 && isTarget ? true : undefined} {...nav.rowProps(item)} data-copy={[dayLabel(item.originalDate), item.description, signed(item), item.sourceReference ? `Ref ${item.sourceReference}` : item.sourceType].join('\t')} onClick={(event) => { if (hasSelectedText(event)) return; select(item); event.currentTarget.focus(); if (window.matchMedia('(max-width: 960px)').matches) showDetails(); }}
            onContextMenu={(event) => menu.open(event, rowMenu(item), event.currentTarget)} onKeyDownCapture={(event) => { if (menu.keyOpens(event)) menu.open(event, rowMenu(item), event.currentTarget); }}>
            <span className="date">{dayLabel(item.originalDate)}</span>
            <span className="review-row-main"><span className="row-title" {...entryDragProps} title={item.description}>{rowTitle(item.description)}</span><span className="row-sub">{item.sourceType}{item.sourceReference ? ` · Ref ${item.sourceReference}` : ''}</span></span>
            <span className="review-row-end"><span className={`num ${isMoneyIn(item) ? 'credit' : ''}`}>{signed(item)}</span><StatusLabel status={status} draft={!!drafts[item.id]}/>{targetIds.length > 1 && isTarget && <span className="status batch-target">Batch target</span>}</span>
          </button>; })}
          {filtered.length === 0 && <div className="empty"><strong>Nothing to show</strong><span>{counts.all && statusFilter !== 'all' && !query ? `No ${STATUS_LABEL[statusFilter as ReviewStatus].toLowerCase()} entries.` : 'Try another status, filter or search.'}</span>{query && <button type="button" className="button" onClick={() => { setQuery(''); requestAnimationFrame(() => document.getElementById('review-search')?.focus({ preventScroll: true })); }}>Clear Search</button>}</div>}
        </div>
        <p className="review-list-foot">{plural(filtered.length, 'entry', 'entries')} · <kbd>↑</kbd> <kbd>↓</kbd> to move</p>
      </section>

      <section ref={detailRef} className="panel review-detail" aria-label="Selected entry">{selected ? <>
        <button type="button" className="button narrow-details" onClick={() => { listRef.current?.scrollIntoView({ block: 'start' }); if (selected) nav.focusRow(selected.id); }}>Back to Queue</button>
        <header className="review-detail-head">
          <div><h2>{selected.description}</h2><p>{dayLabel(selected.originalDate)} · {selected.kind} · {selected.sourceType} statement</p></div>
          <p className={`review-amount ${isMoneyIn(selected) ? 'credit' : ''}`}>{signed(selected)}</p>
        </header>
        {matching.length > 1 && <div className="review-batch"><Icon name="stack"/><span id="review-batch-summary" role="status" aria-live="polite">{targetIds.length > 1 ? `Classifying ${plural(targetIds.length, 'entry', 'entries')} with this description together` : `${plural(matching.length, 'entry', 'entries')} share this description`}</span>
          <button type="button" className="button" disabled={readOnly} onClick={() => setBatchIds(targetIds.length > 1 ? null : matching.map((item) => item.id))}>{targetIds.length > 1 ? 'Classify This One Only' : `Classify All ${matching.length}`}</button></div>}
        <section className="inspector-section review-evidence" aria-label="Entry evidence"><h3><Icon name="source"/>Evidence</h3>
          <ul className="source-list"><li><span className="path"><EvidenceName path={selected.source}/></span><span className="source-actions">{evidenceHref(selected.source) && <a className="button plain" href={evidenceHref(selected.source)!} target="_blank" rel="noopener" aria-label={`${evidenceAction(selected.source)}: ${selected.source}`} title="Open in a new tab">{evidenceAction(selected.source)}</a>}<button type="button" className="icon-button" aria-label="Copy source path" title="Copy path" onClick={async () => setNotice(await copyText(selected.source) ? { tone: 'success', text: 'Source path copied.' } : { tone: 'error', text: 'The browser blocked copying. Select the path and press ⌘C.' })}><Icon name="copy"/></button></span></li></ul>
          <details><summary>Reference and statement end</summary><dl className="facts"><dt>Reference</dt><dd>{selected.sourceReference || 'Not on the statement'}</dd><dt>Statement end</dt><dd>{selected.statementEnd ? dayLabel(selected.statementEnd) : 'Not recorded'}</dd></dl></details>
          <dl className="facts"><dt>Note</dt><dd>{selected.classification || 'Not supplied'}</dd></dl>
        </section>
        <form className="review-form" onSubmit={(event) => { event.preventDefault(); save('resolved'); }}>
          <div className="form-row">
            <label className="field-label" htmlFor="review-account">Ledger account</label>
            <input ref={accountRef} id="review-account" disabled={readOnly} className="field mono-field" list="review-accounts" value={draft.account} placeholder="Type or choose an account" autoComplete="off" spellCheck={false}
              aria-invalid={accountError} aria-describedby="review-account-help" onChange={(event) => editDraft({ account: event.target.value })}/>
            <datalist id="review-accounts">{ledger().accounts.map((account) => <option key={account} value={account}/>)}</datalist>
            {accountError ? <p className="field-error" id="review-account-help"><Icon name="alert"/>Choose or type an account to resolve this entry.</p>
              : <p className="field-help" id="review-account-help">{!knownAccount ? 'New account: it isn’t in the ledger yet.' : 'Suggestions come from accounts already in the ledger.'}</p>}
          </div>
          <div className="review-actions">
            <p className={`draft-state ${dirty && !draftsSaved ? 'warning' : ''}`}>{dirty ? draftsSaved ? 'Draft saved in this browser' : 'Draft not stored — export before leaving; departure warning is active' : saved?.status !== 'unreviewed' ? `${STATUS_LABEL[saved!.status]} ${saved!.updatedAt ? dayLabel(saved!.updatedAt.slice(0, 10)) : ''}` : 'Not reviewed yet'}</p>
            <button type="button" className="button" disabled={readOnly} title="Mark for Follow-Up (⇧⌘↩)" onClick={() => save('followup')}><Icon name="flag"/>Mark for Follow-Up</button>
            <button type="submit" disabled={readOnly} className="button primary" title="Resolve (⌘↩)"><Icon name="check"/>Resolve</button>
          </div>

          <div className="form-row"><label className="field-label" htmlFor="review-label">Label <span className="optional">(optional)</span></label><input id="review-label" disabled={readOnly} className="field" value={draft.label} placeholder="What was this?" onChange={(event) => editDraft({ label: event.target.value })}/></div>
          <div className="form-row"><label className="field-label" htmlFor="review-notes">Notes <span className="optional">(optional)</span></label><textarea id="review-notes" disabled={readOnly} className="field" rows={2} value={draft.notes} placeholder="Anything that will help classify similar entries" onChange={(event) => editDraft({ notes: event.target.value })}/></div>
          <p className="shortcut-hint"><kbd>⌘↩</kbd> Resolve · <kbd>⇧⌘↩</kbd> Mark for Follow-Up · <kbd>⌘Z</kbd> Undo · <kbd>↑</kbd><kbd>↓</kbd> Next entry</p>
          {message}
        </form>

        <section className="inspector-section"><h3><Icon name="postings"/>Postings{draft.account ? ' with this account' : ''}</h3><div className="postings">
          {(ledgerTransaction?.postings || [{ account: selected.currentAccount, amount: selected.amount, currency: selected.currency, label: selected.currentAccount }]).map((posting, index) => { const account = posting.account === selected.currentAccount && draft.account ? draft.account : posting.account; return <div className="posting" key={`${posting.account}-${index}`}>
            <span className="posting-side" title={posting.amount >= 0 ? 'Debit' : 'Credit'}>{posting.amount >= 0 ? 'Dr' : 'Cr'}</span>
            <span><span className="posting-name">{account.split(':').slice(-2).join(' · ')}</span><span className="posting-path">{account}</span></span>
            <span className="posting-amount">{money(Math.abs(posting.amount), posting.currency)}</span>
          </div>; })}
        </div></section>
      </> : <div className="empty"><strong>No entry selected</strong><span>Choose an entry on the left to see its evidence and classify it.</span></div>}</section>
      {fuelOpen && <FuelLog onClose={() => setFuelOpen(false)}/>}
    </div>
    {confirmDiscardRecovery && <Dialog role="alertdialog" title="Discard unsaved recovery copy?" labelId="discard-recovery-title" onClose={() => setConfirmDiscardRecovery(false)}>
      <div className="alert-body"><p>This removes the separately kept work from this tab. Export it first to keep a copy. The latest saved work stays.</p><div className="alert-actions"><button type="button" className="button" onClick={() => setConfirmDiscardRecovery(false)}>Cancel</button><button type="button" className="button destructive-filled" onClick={() => { stores.forEach((store) => store.discardRecovery()); setConfirmDiscardRecovery(false); }}>Discard Unsaved Work</button></div></div>
    </Dialog>}
    {confirmClear && <Dialog role="alertdialog" title={`Clear all ${plural(Object.keys(decisions).length, 'saved decision')}?`} labelId="clear-title" onClose={() => setConfirmClear(false)}>
      <div className="alert-body"><p>They’re removed from this browser. You can undo this with ⌘Z until you leave the page; export first to keep a copy.</p>
        <div className="alert-actions"><button type="button" className="button" onClick={() => setConfirmClear(false)}>Cancel</button><button type="button" className="button destructive-filled" disabled={readOnly} onClick={clearAll}>Clear Decisions</button></div></div>
    </Dialog>}
    {menu.element}
    {dropping && <div className="drop-overlay" aria-hidden="true"><div><Icon name="import"/><strong>Drop to import review decisions</strong><span>A .json file made with Export</span></div></div>}
  </section>;
}
