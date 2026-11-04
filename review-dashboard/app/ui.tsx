import {formatCalendarDate} from './date-time';
import { accountMetadata } from './account-metadata';
import { accountLabel as metadataLabel } from '../scripts/account-metadata.mjs';
// Shared formatting, icons, page chrome and keyboard behavior for every dashboard view.
import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState, type ComponentPropsWithRef, type KeyboardEvent, type MouseEvent as ReactMouseEvent, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';

import { count as formatCount } from './formatting';
export { money, moneyWhole, compactMoney, axisMoney, currencySymbol, count, units, moneyTotal, sumCurrencies, currencyOrder } from './formatting';
const change = (value: number, base: number) => base ? (value - base) / base * 100 : null;
/** Change against a comparison, with an arrow and words. More spending reads as negative. */
export function Delta({ value, base, suffix }: { value: number; base: number; suffix?: string }) {
  const pct = change(value, base);
  if (pct === null) return <span className="delta flat">No comparison{suffix && <span className="delta-suffix"> {suffix}</span>}</span>;
  const direction = Math.abs(pct) < 0.05 ? 'flat' : pct > 0 ? 'up' : 'down';
  return <span className={`delta ${direction}`}>{direction === 'up' ? '↑ ' : direction === 'down' ? '↓ ' : ''}{direction === 'flat' ? 'No change' : percent(pct)}{suffix && <span className="delta-suffix"> {suffix}</span>}</span>;
}
export const percent = (value: number) => `${Math.abs(value).toFixed(Math.abs(value) >= 10 ? 0 : 1)}%`;

// Calendar labels use the ledger or viewer locale without interpreting dates as UTC.
export const dayLabel = (key: string) => formatCalendarDate(key);
export const dayShort = (key: string) => formatCalendarDate(key, { day: 'numeric', month: 'short' });
export const monthLong = (key: string) => formatCalendarDate(key.slice(0, 7), { month: 'long', year: 'numeric' });
export const monthShort = (key: string) => formatCalendarDate(key.slice(0, 7), { month: 'short', year: 'numeric' });
export const monthName = (key: string) => formatCalendarDate(key.slice(0, 7), { month: 'long' });
export const monthAbbr = (key: string) => formatCalendarDate(key.slice(0, 7), { month: 'short' });
export const daysInMonth = (key: string) => new Date(Number(key.slice(0, 4)), Number(key.slice(5, 7)), 0).getDate();
export const plural = (count: number, one: string, many = `${one}s`) => `${formatCount(count)} ${count === 1 ? one : many}`;

export type IconName = 'explorer' | 'atlas' | 'journal' | 'forecast' | 'accounts' | 'review' | 'search' | 'source' | 'calendar'
  | 'chevron-left' | 'chevron-right' | 'close' | 'postings' | 'integrity' | 'copy' | 'pending' | 'flag' | 'check'
  | 'import' | 'export' | 'fuel' | 'keyboard' | 'filter' | 'stack' | 'pencil' | 'alert' | 'help' | 'chevron-up-down'
  | 'sun' | 'moon' | 'appearance';
const ICONS: Record<IconName, ReactNode> = {
  explorer: <><rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M3.5 14.5h17M9 9.5v10"/></>,
  atlas: <><rect x="3" y="2" width="18" height="20" rx="2"/><path d="M3 7h18M9 7v15M15 7v15M3 12h18M3 17h18"/></>,
  journal: <><path d="M6 3.5h12a1.5 1.5 0 0 1 1.5 1.5v15.5H7a2.5 2.5 0 0 1-2.5-2.5V5A1.5 1.5 0 0 1 6 3.5Z"/><path d="M8.5 8h7M8.5 11.5h7M8.5 15h4.5"/></>,
  forecast: <><path d="M3.5 20h17"/><path d="m4.5 16 4-4.5 3.5 2.5 3.5-4.5"/><path d="m15.5 9.5 4-4.5" strokeDasharray="1.6 2.4"/></>,
  accounts: <><path d="M3.5 9.5 12 4l8.5 5.5"/><path d="M6 10v7.5M10 10v7.5M14 10v7.5M18 10v7.5M3.5 20h17"/></>,
  review: <><circle cx="12" cy="12" r="8.5"/><path d="m8.2 12.3 2.6 2.6 5-5.3"/></>,
  search: <><circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/></>,
  source: <><path d="M6.5 3.5h7.5l4.5 4.5v12.5h-12z"/><path d="M13.5 3.5V8h5M9 12.5h6.5M9 16h4.5"/></>,
  calendar: <><rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M16 3v4M8 3v4M3.5 10h17"/></>,
  'chevron-left': <path d="m14.5 6-6 6 6 6"/>,
  'chevron-right': <path d="m9.5 6 6 6-6 6"/>,
  close: <path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>,
  postings: <><path d="M5 8h13.5l-3-3"/><path d="M19 16H5.5l3 3"/></>,
  integrity: <><path d="M12 3.5 5 6.2v5.6c0 4.2 3 7.6 7 8.7 4-1.1 7-4.5 7-8.7V6.2z"/><path d="m9 12.2 2.2 2.2 4-4.3"/></>,
  copy: <><rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/></>,
  pending: <><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></>,
  flag: <><path d="M6 20.5V4"/><path d="M6 4.5h10.5l-2 3.8 2 3.7H6"/></>,
  check: <path d="m5.5 12.5 4.2 4.2 8.8-9.2"/>,
  import: <><path d="M12 3.5v11M8 10.5l4 4 4-4"/><path d="M4.5 14v4.5a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V14"/></>,
  export: <><path d="M12 14.5v-11M8 7.5l4-4 4 4"/><path d="M4.5 14v4.5a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V14"/></>,
  fuel: <><path d="M5.5 20.5v-15A1.5 1.5 0 0 1 7 4h6a1.5 1.5 0 0 1 1.5 1.5v15M4 20.5h12M5.5 10h9"/><path d="M14.5 8.5h2a1.5 1.5 0 0 1 1.5 1.5v6a1.5 1.5 0 0 0 3 0V8.5L18.5 6"/></>,
  keyboard: <><rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M7.5 14h9"/></>,
  filter: <path d="M4 6h16M7 12h10M10 18h4"/>,
  stack: <><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M6 6.5h12M8.5 3.5h7"/></>,
  pencil: <><path d="M15.5 5.5l3 3L9 18l-4 1 1-4z"/><path d="m13.5 7.5 3 3"/></>,
  alert: <><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5.5M12 16.2v.3"/></>,
  help: <><circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.6a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .8-1 1.5v.4M12 16.6v.3"/></>,
  'chevron-up-down': <path d="m8.5 9.5 3.5-3.5 3.5 3.5M8.5 14.5l3.5 3.5 3.5-3.5"/>,
  sun: <><circle cx="12" cy="12" r="3.8"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4"/></>,
  moon: <path d="M19.5 14.5A7.5 7.5 0 0 1 9.5 4.5a7.5 7.5 0 1 0 10 10Z"/>,
  appearance: <><circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17a8.5 8.5 0 0 0 0-17Z" fill="currentColor"/></>,
};
export function Icon({ name, label }: { name: IconName; label?: string }) {
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true}>{ICONS[name]}</svg>;
}

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children?: ReactNode }) {
  return <header className="page-header">
    <div className="page-heading"><h1 tabIndex={-1}>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
    {children && <div className="page-tools">{children}</div>}
  </header>;
}

export function PanelHeader({ title, meta, children, level = 2, headingRef }: { title: ReactNode; meta?: ReactNode; children?: ReactNode; level?: 2 | 3; headingRef?: React.Ref<HTMLHeadingElement> }) {
  const Heading = level === 2 ? 'h2' : 'h3';
  return <div className="panel-header"><div><Heading ref={headingRef} tabIndex={headingRef ? -1 : undefined}>{title}</Heading>{meta && <p>{meta}</p>}</div>{children && <div className="panel-header-tools">{children}</div>}</div>;
}

/** A search field whose scope stays visible after typing, so the field never loses its label. */
export function SearchField({ value, onChange, label, scope, placeholder, id }: { value: string; onChange: (value: string) => void; label: string; scope?: string; placeholder?: string; id?: string }) {
  return <label className="search-field">
    <Icon name="search"/>
    <span className="sr-only">{label}</span>
    <input id={id} type="search" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder || 'Search'} autoComplete="off" spellCheck={false}
      onKeyDown={(event) => { if (event.key === 'Escape' && value) { event.stopPropagation(); onChange(''); } }}/>
    {scope && <span className="search-scope" aria-hidden="true">{scope}</span>}
  </label>;
}

/** Previous / month pop-up / next. */
export function MonthStepper({ months, value, onChange, format = monthLong }: { months: string[]; value: string; onChange: (value: string) => void; format?: (key: string) => string }) {
  const index = months.indexOf(value);
  return <div className="segmented month-stepper" role="group" aria-label="Month">
    <button type="button" className="icon-button" aria-label="Previous month" title="Previous month ([)" disabled={index <= 0} onClick={() => onChange(months[index - 1])}><Icon name="chevron-left"/></button>
    <label className="month-select"><Icon name="calendar"/><span className="sr-only">Month</span><select value={value} onChange={(event) => onChange(event.target.value)}>{months.map((key) => <option key={key} value={key}>{format(key)}</option>)}</select></label>
    <button type="button" className="icon-button" aria-label="Next month" title="Next month (])" disabled={index >= months.length - 1} onClick={() => onChange(months[index + 1])}><Icon name="chevron-right"/></button>
  </div>;
}

/** Text in a text field, select or editable area keeps its own keys; list and app shortcuts stand aside. */
export function isTyping(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

/**
 * Keyboard model for a list of rows, as in Mail: one row is in the Tab order, the arrow keys,
 * Home and End move between rows, typing a row's first letters jumps to it, and moving calls
 * `onMove` so a detail pane can follow. With `columns`, Left and Right move by one and Up and
 * Down by a row of the grid. Pass 'grid' to read the rendered row width on each keypress.
 */
export function useRowNavigation<T>(rows: readonly T[], idOf: (row: T) => string, activeId: string | null, onMove: (row: T, mode: 'replace') => void, columns: number | 'grid' = 0, textOf?: (row: T) => string) {
  const elements = useRef(new Map<string, HTMLElement>());
  const typed = useRef({ text: '', at: 0 });
  const tabStop = activeId && rows.some((row) => idOf(row) === activeId) ? activeId : rows[0] ? idOf(rows[0]) : null;
  const moveTo = useCallback((row: T) => { onMove(row, 'replace'); elements.current.get(idOf(row))?.focus(); }, [idOf, onMove]);
  const onKeyDown = useCallback((event: KeyboardEvent<HTMLElement>) => {
    if (event.metaKey || event.altKey || event.ctrlKey) return;
    const current = (event.target as HTMLElement).closest('[data-row-id]')?.getAttribute('data-row-id');
    if (!current) return;
    const index = rows.findIndex((row) => idOf(row) === current);
    // Type-select: letters typed within a second of each other find the next row starting with them.
    if (textOf && event.key.length === 1 && event.key !== ' ' && !isTyping(event.target)) {
      const now = Date.now();
      typed.current = { text: (now - typed.current.at < 1000 ? typed.current.text : '') + event.key.toLowerCase(), at: now };
      const needle = typed.current.text;
      const order = [...rows.slice(index + (needle.length === 1 ? 1 : 0)), ...rows.slice(0, index + (needle.length === 1 ? 1 : 0))];
      const match = order.find((row) => textOf(row).toLowerCase().startsWith(needle));
      if (match) { event.preventDefault(); moveTo(match); }
      return;
    }
    // Read the rendered first row at the keypress, including after resizing or zooming.
    const first = rows[0] && elements.current.get(idOf(rows[0]));
    const top = first?.getBoundingClientRect().top;
    const columnCount = columns === 'grid' ? Math.max(1, [...elements.current.values()].filter(element => Math.abs(element.getBoundingClientRect().top - (top ?? 0)) < 2).length) : columns;
    const steps: Record<string, number> = { ArrowDown: columnCount || 1, ArrowUp: -(columnCount || 1), ...(columns ? { ArrowRight: 1, ArrowLeft: -1 } : {}) };
    if (!(event.key in steps) && event.key !== 'Home' && event.key !== 'End') return;
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1 : index + steps[event.key];
    if (next < 0 || next >= rows.length || next === index) return;
    event.preventDefault();
    moveTo(rows[next]);
  }, [rows, idOf, columns, textOf, moveTo]);
  const rowProps = (row: T) => {
    const id = idOf(row);
    return { 'data-row-id': id, tabIndex: id === tabStop ? 0 : -1, ref: (element: HTMLElement | null) => { if (element) elements.current.set(id, element); else elements.current.delete(id); } };
  };
  const focusRow = useCallback((id: string, preventScroll = false) => elements.current.get(id)?.focus({ preventScroll }), []);
  return { onKeyDown, rowProps, focusRow };
}

/** A selected cell fragment should finish its browser gesture without opening details. */
export function hasSelectedText(event: ReactMouseEvent<HTMLElement>) {
  const selection = window.getSelection();
  return event.detail > 0 && !!selection?.toString() && !!selection.rangeCount && selection.getRangeAt(0).intersectsNode(event.currentTarget);
}

/** Drag only from a deliberate title, with the same text as keyboard Copy. */
export const entryDragProps = {
  draggable: true,
  onDragStart: (event: React.DragEvent<HTMLElement>) => {
    const element = event.currentTarget;
    const text = element.closest<HTMLElement>('[data-copy]')?.dataset.copy;
    if (!text) { event.preventDefault(); return; }
    event.dataTransfer.effectAllowed = 'copy';
    event.dataTransfer.setData('text/plain', text);
    if (element instanceof HTMLAnchorElement) event.dataTransfer.setData('text/uri-list', element.href);
  },
};

export type MenuItem = { label: string; action: () => void; disabled?: boolean; checked?: boolean } | 'separator';
type MenuState = { x: number; y: number; above?: number; items: MenuItem[]; target: HTMLElement | null; restore: HTMLElement | null };
/**
 * A context menu for a row: secondary click, Control-click, Shift-F10 or the Menu key open it at
 * the row; it outlines the clicked row without changing the selection, and the arrow keys,
 * Return and Esc work as in a Mac menu.
 */
export function useContextMenu() {
  const [menu, setMenu] = useState<MenuState | null>(null);
  const close = useCallback((restoreFocus = true) => {
    setMenu((current) => {
      current?.target?.removeAttribute('data-context-target');
      current?.restore?.removeAttribute('aria-expanded');
      if (restoreFocus) requestAnimationFrame(() => current?.restore?.focus());
      return null;
    });
  }, []);
  const open = useCallback((event: ReactMouseEvent | MouseEvent | KeyboardEvent<HTMLElement>, items: MenuItem[], target?: HTMLElement | null) => {
    if (event.type === 'contextmenu' && event.shiftKey) { close(false); return; }
    event.preventDefault();
    const row = target || (event.currentTarget as HTMLElement);
    const bounds = row.getBoundingClientRect();
    const point = 'clientX' in event && event.clientX ? { x: event.clientX, y: event.clientY } : { x: bounds.left + 24, y: bounds.bottom - 4 };
    row.setAttribute('data-context-target', 'true');
    setMenu({ ...point, items, target: row, restore: document.activeElement as HTMLElement | null });
  }, [close]);
  /** Opens the menu below a pull-down button. */
  const openBelow = useCallback((button: HTMLElement, items: MenuItem[]) => {
    const bounds = button.getBoundingClientRect();
    button.setAttribute('aria-expanded', 'true');
    setMenu({ x: bounds.left, y: bounds.bottom + 4, above: bounds.top - 4, items, target: null, restore: button });
  }, []);
  /** Opens the menu from the keyboard (Shift-F10 or the Menu key) for the focused row. */
  const keyOpens = (event: KeyboardEvent<HTMLElement>) => event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10');
  const element = menu && <ContextMenu {...menu} onClose={close}/>;
  return { open, openBelow, close, element, keyOpens };
}
function ContextMenu({ x, y, above, items, onClose }: MenuState & { onClose: (restoreFocus?: boolean) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: x, top: y });
  useEffect(() => {
    const element = ref.current; if (!element) return;
    const { width, height } = element.getBoundingClientRect();
    // Without room below, a pull-down opens above its button and a context menu above the pointer.
    setPosition({ left: Math.min(x, window.innerWidth - width - 8), top: y + height > window.innerHeight - 8 ? Math.max(8, (above ?? y) - height) : y });
    (element.querySelector<HTMLElement>('[aria-checked="true"]') || element.querySelector<HTMLElement>('[role^="menuitem"]:not([aria-disabled="true"])'))?.focus();
    const outside = (event: MouseEvent) => { if (!element.contains(event.target as Node)) onClose(false); };
    const away = () => onClose(false);
    window.addEventListener('mousedown', outside, true);
    window.addEventListener('blur', away);
    window.addEventListener('scroll', away, true);
    return () => { window.removeEventListener('mousedown', outside, true); window.removeEventListener('blur', away); window.removeEventListener('scroll', away, true); };
  }, [x, y, above, onClose]);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const entries = [...(ref.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([aria-disabled="true"])') || [])];
    const index = entries.indexOf(document.activeElement as HTMLElement);
    if (event.key === 'Escape' || event.key === 'Tab') { event.preventDefault(); event.stopPropagation(); onClose(); }
    else if (event.key === 'ArrowDown') { event.preventDefault(); entries[(index + 1) % entries.length]?.focus(); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); entries[(index - 1 + entries.length) % entries.length]?.focus(); }
    else if (event.key === 'Home') { event.preventDefault(); entries[0]?.focus(); }
    else if (event.key === 'End') { event.preventDefault(); entries.at(-1)?.focus(); }
  };
  const checkable = items.some((item) => item !== 'separator' && item.checked !== undefined);
  return createPortal(<div ref={ref} className={`context-menu ${checkable ? 'checkable' : ''}`} role="menu" style={position} onKeyDown={onKeyDown} onContextMenu={(event) => event.preventDefault()}>
    {items.map((item, index) => item === 'separator' ? <div key={`separator-${index}`} role="separator" className="menu-separator"/>
      : <button key={item.label} type="button" role={item.checked === undefined ? 'menuitem' : 'menuitemradio'} aria-checked={item.checked} tabIndex={-1} aria-disabled={item.disabled || undefined}
          onMouseEnter={(event) => (event.currentTarget as HTMLElement).focus()}
          onClick={() => { if (item.disabled) return; onClose(); item.action(); }}>{checkable && <span className="menu-check" aria-hidden="true">{item.checked ? '✓' : ''}</span>}{item.label}</button>)}
  </div>, document.body);
}

export type Appearance = 'light' | 'dark';
let memoryAppearance: Appearance | undefined;
const widthMemory = new Map<string, Record<string, number>>();
const APPEARANCE_KEY = 'ledger-appearance';
const readAppearance = (): Appearance => {
  try { const value = window.localStorage.getItem(APPEARANCE_KEY); return value === 'light' || value === 'dark' ? value : window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'; } catch { return memoryAppearance || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); }
};
const applyAppearance = (appearance: Appearance) => {
  document.documentElement.dataset.theme = appearance;
};
/** Start with the system appearance; remember a toggled choice and follow other tabs. */
export function useAppearance(): [Appearance, (appearance: Appearance) => void] {
  const [appearance, setAppearance] = useState<Appearance>(() => typeof window === 'undefined' ? 'light' : readAppearance());
  useEffect(() => {
    const onStorage = (event: StorageEvent) => { if (event.key === APPEARANCE_KEY || event.key === null) { try { const value = localStorage.getItem(APPEARANCE_KEY); memoryAppearance = value === 'light' || value === 'dark' ? value : undefined; } catch { /* Keep the in-memory choice. */ } const next = readAppearance(); applyAppearance(next); setAppearance(next); } };
    const system = window.matchMedia('(prefers-color-scheme: dark)');
    const onSystem = () => {
      let chosen = memoryAppearance;
      try { const value = localStorage.getItem(APPEARANCE_KEY); if (value === 'light' || value === 'dark') chosen = value; } catch { /* An in-memory choice still wins. */ }
      const next = chosen || (system.matches ? 'dark' : 'light');
      applyAppearance(next); setAppearance(next);
    };
    onSystem(); system.addEventListener('change', onSystem);
    window.addEventListener('storage', onStorage);
    return () => { window.removeEventListener('storage', onStorage); system.removeEventListener('change', onSystem); };
  }, []);
  const choose = useCallback((next: Appearance) => {
    memoryAppearance = next;
    try { window.localStorage.setItem(APPEARANCE_KEY, next); } catch { /* still applies to this page */ }
    applyAppearance(next);
    setAppearance(next);
  }, []);
  return [appearance, choose];
}

/** Adds `window-inactive` to the page while the browser window is in the background, so selection turns gray. */
export function useWindowActivity() {
  useEffect(() => {
    const update = () => document.documentElement.classList.toggle('window-inactive', !document.hasFocus());
    update();
    window.addEventListener('focus', update);
    window.addEventListener('blur', update);
    return () => { window.removeEventListener('focus', update); window.removeEventListener('blur', update); };
  }, []);
}

/** Column widths a person can drag, remembered in this browser. */
export function useColumnWidths(storageKey: string, defaults: Record<string, number>, minimums: number | Record<string, number> = 64) {
  const validate = (value: unknown) => Object.fromEntries(Object.entries(defaults).map(([key, fallback]) => {
    const width = value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>)[key] : undefined;
    const minimum = typeof minimums === 'number' ? minimums : minimums[key] || 64;
    return [key, typeof width === 'number' && Number.isFinite(width) ? Math.max(minimum, Math.min(600, width)) : fallback];
  }));
  const [widths, setWidths] = useState<Record<string, number>>(() => {
    if (typeof window === 'undefined') return defaults;
    try { return validate(JSON.parse(window.localStorage.getItem(storageKey) || '{}')); } catch { return validate(widthMemory.get(storageKey)); }
  });
  const menu = useContextMenu();
  useEffect(() => { widthMemory.set(storageKey, widths); try { window.localStorage.setItem(storageKey, JSON.stringify(widths)); } catch { /* widths are a convenience */ } }, [storageKey, widths]);
  useEffect(() => {
    const headingMenu = (event: MouseEvent) => {
      const heading = (event.target as Element).closest('th');
      if (!heading?.closest('table')?.querySelector(`[data-width-store="${storageKey}"]`)) return;
      menu.open(event, [{ label: 'Reset Column Widths', action: () => setWidths(defaults) }], heading);
    };
    document.addEventListener('contextmenu', headingMenu);
    return () => document.removeEventListener('contextmenu', headingMenu);
  });
  /**
   * A grip on a column's trailing edge. A flexible column (one that takes the remaining width) passes
   * the `neighbor` it trades width with: widening it narrows the next column.
   */
  const resizer = (column: string, label: string, neighbor?: string) => {
    const target = neighbor || column;
    const minimum = typeof minimums === 'number' ? minimums : minimums[target] || 64;
    const sign = neighbor ? -1 : 1;
    return <span><span data-width-store={storageKey} className="column-resizer" role="separator" aria-orientation="vertical" aria-label={`${label} column width`} title="Drag to resize; double-click to reset"
    tabIndex={0} aria-valuenow={widths[target]} aria-valuemin={minimum} aria-valuemax={600}
    onKeyDown={(event) => {
      const step = event.key === 'ArrowRight' ? 8 : event.key === 'ArrowLeft' ? -8 : 0;
      if (step) { event.preventDefault(); setWidths((current) => ({ ...current, [target]: Math.max(minimum, Math.min(600, current[target] + sign * step)) })); }
      else if (event.key === 'Home') { event.preventDefault(); setWidths((current) => ({ ...current, [target]: defaults[target] })); }
    }}
    onClick={(event) => event.stopPropagation()}
    onDoubleClick={(event) => { event.stopPropagation(); setWidths((current) => ({ ...current, [target]: defaults[target] })); }}
    onPointerDown={(event) => {
      event.preventDefault(); event.stopPropagation();
      const startX = event.clientX; const start = widths[target];
      const move = (moveEvent: PointerEvent) => setWidths((current) => ({ ...current, [target]: Math.max(minimum, Math.min(600, Math.round(start + sign * (moveEvent.clientX - startX)))) }));
      const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.classList.remove('resizing-column'); };
      document.body.classList.add('resizing-column');
      window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
    }}/>{column === Object.keys(defaults)[0] ? menu.element : null}</span>;
  };
  return { widths, resizer };
}

/** A pane's leading-edge grip, using the same pointer and keyboard behavior as columns. */
export function usePaneWidth(key: string, initial: number, minimum: number, label: string) {
  const { widths, resizer } = useColumnWidths(key, { pane: initial }, minimum);
  return { width: Math.max(minimum, Math.min(600, Number(widths.pane) || initial)),
    grip: <span className="pane-resizer">{resizer('edge', label, 'pane')}</span> };
}

/** Status shown in the sidebar until the next one: confirmations people don't need to act on. */
export function announce(text: string) { if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('ledger-status', { detail: text })); }
export async function copyAndAnnounce(text: string, what: string) { announce(await copyText(text) ? `Copied ${what}` : `The browser blocked copying the ${what}.`); }

/** Modal Tab order includes controls even when Safari's system Tab mode skips them. */
export function trapModalTab(event: KeyboardEvent<HTMLElement>, root: HTMLElement | null) {
  if (event.key !== 'Tab' || !root) return;
  const controls = [...root.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]')]
    .filter((node) => node.getClientRects().length && !node.closest('[inert]'));
  event.preventDefault();
  const index = controls.indexOf(document.activeElement as HTMLElement);
  const next = index < 0 ? (event.shiftKey ? controls.length - 1 : 0) : (index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length;
  (controls[next] || root).focus();
}

/** A modal dialog: focus moves in, Tab stays inside, Esc or a click outside closes it, and focus returns. */
const modalLocks=new WeakMap<HTMLElement,{count:number;original:boolean}>();
export function lockModalPage(){
  const page=document.querySelector<HTMLElement>('main.app');
  if(!page)return ()=>{};
  const state=modalLocks.get(page)||{count:0,original:page.inert};state.count++;modalLocks.set(page,state);page.inert=true;
  return ()=>{state.count--;if(!state.count){page.inert=state.original;modalLocks.delete(page);}};
}

export function Dialog({ title, subtitle, onClose, children, labelId, role = 'dialog', initialFocus }: { initialFocus?: string; title: string; subtitle?: string; onClose: () => void; children: ReactNode; labelId: string; role?: 'dialog' | 'alertdialog' }) {
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const unlock=lockModalPage();
    const target=dialog.current?.querySelector<HTMLElement>(initialFocus || 'button');
    target?.focus();
    if(target instanceof HTMLInputElement)target.select();
    return () => { unlock();opener?.focus(); };
  }, []);
  useEscape(true, onClose);
  const trap = (event: KeyboardEvent<HTMLElement>) => {
    trapModalTab(event, dialog.current);
  };
  return createPortal(<div className="dialog-layer" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialog} className={`dialog ${role === 'alertdialog' ? 'alert' : ''}`} role={role} aria-modal="true" aria-labelledby={labelId} onKeyDown={trap}>
      {role === 'alertdialog' ? <h2 id={labelId} className="alert-title">{title}</h2>
        : <div className="panel-header"><div><h2 id={labelId}>{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button type="button" className="icon-button dialog-close" aria-label={`Close ${title}`} data-tooltip="Close (Esc)" onClick={onClose}><Icon name="close"/></button></div>}
      {children}
    </section>
  </div>, document.body);
}

/** True while a modal dialog is open, so window-level shortcuts don't act on the page behind it. */
export const modalOpen = () => typeof document !== 'undefined' && !!document.querySelector('[aria-modal="true"]');

/** Metadata labels for balance-sheet accounts; category paths retain their context. */
export const accountLabel = (path: string) => { if (accountMetadata()[path]?.kind) return metadataLabel(path, accountMetadata()); const parts = path.split(':'); return (parts.length > 1 ? parts.slice(1) : parts).join(' · '); };

/** A permalink retains this view's filters, sort and account, replacing only the entry. */
export function entryHref(id: string) {
  const url = new URL(typeof window === 'undefined' ? `${import.meta.env.BASE_URL}explorer/` : window.location.href, 'http://localhost');
  url.searchParams.set('entry', id);
  return `${url.pathname}${url.search}${url.hash}`;
}
export const entryUrl = (id: string) => new URL(entryHref(id), window.location.href).href;

/** Ordinary activation opens the panel; modified and middle clicks retain native link behavior. */
export function EntryLink({ entryId, onOpen, onKeyDown, ...props }: Omit<ComponentPropsWithRef<'a'>, 'href' | 'onClick'> & { entryId: string; onOpen: () => void }) {
  return <a {...props} {...entryDragProps} href={entryHref(entryId)} onClick={(event) => {
    event.stopPropagation();
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); if (!hasSelectedText(event)) onOpen();
  }} onKeyDown={(event) => {
    onKeyDown?.(event);
    // Keep the lists' existing Space activation alongside native Return activation.
    if (!event.defaultPrevented && event.key === ' ' && !event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey) {
      event.preventDefault(); event.currentTarget.click();
    }
  }}/>;
}

const scrollMemory = new Map<string, string>();
export function sessionRead(key: string) {
  try { return window.sessionStorage.getItem(key) ?? scrollMemory.get(key) ?? null; } catch { return scrollMemory.get(key) ?? null; }
}
export function sessionWrite(key: string, value: string) {
  scrollMemory.set(key, value);
  try { window.sessionStorage.setItem(key, value); } catch { /* Keep scroll position in memory. */ }
}

/**
 * Keeps a view's scroll position across reloads and returns to it, and brings the selected row
 * into view when the page opens on one.
 */
export function useScrollMemory(key: string, selectedSelector?: string) {
  useLayoutEffect(() => {
    if (typeof window === 'undefined') return;
    const storageKey = `ledger-scroll:${key}`;
    const saved = Number(sessionRead(storageKey) || 0);
    const selected = selectedSelector ? document.querySelector<HTMLElement>(selectedSelector) : null;
    if (selected) selected.scrollIntoView({ block: 'center' }); else if (saved) window.scrollTo(0, saved);
    let frame = 0;
    const save = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => sessionWrite(storageKey, String(Math.round(window.scrollY)))); };
    window.addEventListener('scroll', save, { passive: true });
    return () => { window.removeEventListener('scroll', save); cancelAnimationFrame(frame); };
  }, [key]); // Once per view: later selection changes scroll themselves into view.
}

/** Keeps a scrolling pane's position across reloads (a list, an inspector), restored once on open. */
export function useElementScroll(ref: RefObject<HTMLElement | null>, key: string) {
  useLayoutEffect(() => {
    const element = ref.current; if (!element || typeof window === 'undefined') return;
    const storageKey = `ledger-scroll:${key}`;
    const saved = Number(sessionRead(storageKey) || 0);
    if (saved) element.scrollTop = saved;
    let frame = 0;
    const save = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => sessionWrite(storageKey, String(Math.round(element.scrollTop)))); };
    element.addEventListener('scroll', save, { passive: true });
    return () => { element.removeEventListener('scroll', save); cancelAnimationFrame(frame); };
  }, [ref, key]);
}

/** A source that names a file, as opposed to a note such as "user-provided fuel log". */
export const isFileReference = (source: string) => source.startsWith('Bills/') || /^[^\s/]+\.[A-Za-z0-9]{2,5}$/.test(source);
/** The address that opens a piece of evidence, or null for sources the dashboard doesn't serve. */
export function evidenceHref(source: string) {
  if (source.startsWith('Bills/')) return `${import.meta.env.BASE_URL}bills/${source.slice('Bills/'.length).split('/').map(encodeURIComponent).join('/')}`;
  if (/^[^\s/]+\.xlsx?$/.test(source)) return `${import.meta.env.BASE_URL}statements/${encodeURIComponent(source)}`;
  return null;
}

/** Lets a path wrap only after its separators, never mid-word: "Liabilities:Plastic:Main…". */
export function breakable(path: string) {
  const parts = path.split(/(?<=[:/])/);
  return <>{parts.map((part, index) => <span key={index}>{part}{index < parts.length - 1 && <wbr/>}</span>)}</>;
}

/** Calls `handler` on Escape while `active`, unless a text field handled it first. */
export function useEscape(active: boolean, handler: () => void) {
  useEffect(() => {
    if (!active) return;
    const listener = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape' && !event.defaultPrevented) handler(); };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [active, handler]);
}

let queryBatch = false;
const anyValue = () => true;
const canUseUrl = () => typeof window !== 'undefined' && typeof window.history !== 'undefined';
/** State mirrored in the address's query string, so reloads, bookmarks and new tabs keep the context. */
export function useQueryState(name: string, fallback: string, isValid: (value: string) => boolean = anyValue): [string, (value: string, mode?: 'push' | 'replace') => void] {
  const read = useCallback(() => {
    if (!canUseUrl()) return fallback;
    const fromUrl = new URLSearchParams(window.location.search).get(name);
    return fromUrl !== null && isValid(fromUrl) ? fromUrl : fallback;
  }, [name, fallback, isValid]);
  const [value, setValue] = useState(read);
  useEffect(() => {
    const sync = () => setValue(read());
    window.addEventListener('popstate', sync);
    window.addEventListener('ledger-navigate', sync);
    return () => { window.removeEventListener('popstate', sync); window.removeEventListener('ledger-navigate', sync); };
  }, [read]);
  const update = useCallback((next: string, mode?: 'push' | 'replace') => {
    setValue(next);
    if (!canUseUrl()) return;
    const url = new URL(window.location.href);
    if (next === fallback || next === '') url.searchParams.delete(name); else url.searchParams.set(name, next);
    const address = `${url.pathname}${url.search}${url.hash}`;
    if (address === `${window.location.pathname}${window.location.search}${window.location.hash}`) return;
    const replace = mode === 'replace' || name === 'q' || name === 'find' || queryBatch;
    window.history[replace ? 'replaceState' : 'pushState'](window.history.state, '', address);
    if (!replace) { queryBatch = true; queueMicrotask(() => { queryBatch = false; }); }
    window.dispatchEvent(new Event('ledger-query-change'));
  }, [name, fallback]);
  return [value, update];
}

/** Copies text and reports what actually happened. */
export async function copyText(text: string) {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

/** A readable evidence name; the full path remains available on hover and through Copy. */
export function EvidenceName({ path }: { path: string }) {
  const name = path.split('/').at(-1) || path;
  // Prefer separators to arbitrary stem/tail slices; never hide any part of an evidence name.
  return <span className="evidence-name" title={path}>{name.split(/(?<=[._\- ])/).map((part, index) => <Fragment key={index}>{part}<wbr/></Fragment>)}</span>;
}

/** Remove repeated payment context only in list presentation. */
export function rowTitle(description: string) {
  const split = description.lastIndexOf(' — ');
  if (split < 0) return description;
  const tail = description.slice(split + 3);
  return Object.entries(accountMetadata()).some(([account, meta]) => meta.kind && [metadataLabel(account, accountMetadata()), ...(meta.aliases || []), ...(meta.kind === 'card' ? ['Card'] : [])].some(label => label.toLowerCase() === tail.toLowerCase())) ? description.slice(0, split) : description;
}
/** File labels describe only what the file's own name or type establishes. */
export function evidenceKind(path: string) {
  const name = path.split('/').at(-1) || path;
  if (/\.(xlsx?|csv)$/i.test(name)) return 'Statement';
  if (path.startsWith('Bills/') && /\.md$/i.test(name)) return 'Confirmation';
  if (/^CardStatement-\d{4}-\d{2}-\d{2}\.pdf$/i.test(name)) return 'Statement';
  if (/invoice/i.test(name)) return 'Invoice';
  if (/receipt/i.test(name)) return 'Receipt';
  if (/\.pdf$/i.test(name)) return 'PDF';
  if (/\.(jpe?g|png|heic|webp)$/i.test(name)) return 'Image';
  return 'File';
}
export const evidenceAction = (path: string) => {
  const kind = evidenceKind(path);
  return `Open ${kind === 'PDF' ? kind : kind.toLowerCase()}`;
};

/** Retains a closing pane until its exit animation finishes. Selection changes stay in place. */
export function useSlidingPane<T>(value: T | null) {
  const [retained, setRetained] = useState(value);
  useEffect(() => {
    if (value) { setRetained(value); return; }
    const timer = window.setTimeout(() => setRetained(null), window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 200);
    return () => window.clearTimeout(timer);
  }, [value]);
  return { value: value || retained, closing: !value };
}

/** Freeze the document through the modal's exit, retaining its viewport and scrollbar space. */
export function useDocumentScrollLock() {
  useLayoutEffect(() => {
    const root = document.documentElement;
    const body = document.body;
    const x = window.scrollX, y = window.scrollY;
    const width = root.clientWidth;
    const properties = ['position', 'top', 'left', 'width', 'max-width'] as const;
    const previous = properties.map(property => [property, body.style.getPropertyValue(property), body.style.getPropertyPriority(property)]);
    const overflow = [root.style.getPropertyValue('overflow'), root.style.getPropertyPriority('overflow')];
    root.style.setProperty('overflow', 'hidden');
    body.style.setProperty('position', 'fixed');
    body.style.setProperty('top', `${-y}px`);
    body.style.setProperty('left', `${-x}px`);
    body.style.setProperty('width', `${width}px`);
    body.style.setProperty('max-width', '100%');
    // Rotation and window resizing must not retain the old fixed body width.
    const resize = () => body.style.setProperty('width', `${root.clientWidth}px`);
    window.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('resize', resize);
      for (const [property, value, priority] of previous) {
        if (value) body.style.setProperty(property, value, priority); else body.style.removeProperty(property);
      }
      if (overflow[0]) root.style.setProperty('overflow', overflow[0], overflow[1]); else root.style.removeProperty('overflow');
      window.scrollTo({ left: x, top: y, behavior: 'instant' });
    };
  }, []);
}
