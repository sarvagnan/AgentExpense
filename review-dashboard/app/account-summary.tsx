import { AccountDialog } from './settings-dialog';
import { isCurrency } from '../scripts/formatting.mjs';
import { moduleSettings } from '../scripts/ledger-modules.mjs';
import {money as formatMoney} from './formatting';
import { displayAccountLabel } from './account-labels';
import { defaultAccount } from '../scripts/account-metadata.mjs';
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react';
import { dashboard as data, type DashboardData } from './data';
import { accountActivity, balanceAccounts, groups, isFund, marketValue, priceOn, valueSeries } from './account-balances';
import { TransactionDetails, entryMenu, navigate } from './dashboard';
import { EntryLink, Icon, PageHeader, announce, hasSelectedText, breakable, copyAndAnnounce, useColumnWidths, type MenuItem, PanelHeader, SearchField, dayLabel, dayShort, moneyWhole, money, moneyTotal, sumCurrencies, currencyOrder, plural, units, useContextMenu, useEscape, useQueryState, useRowNavigation } from './ui';

type Transaction = DashboardData['transactions'][number];
const short = displayAccountLabel;
const GROUP_LABEL: Record<string, { total: string; note: string }> = {
  Banks: { total: 'In bank accounts', note: 'In each account’s currency' },
  Prepaid: { total: 'In prepaid balances', note: 'In each account’s currency' },
  'Credit cards': { total: 'Owed on cards', note: 'Ledger balance, not the statement amount due' },
  Investments: { total: 'In investments', note: 'Mutual funds at market value, others at contributions' },
};
const PERIODS: Record<string, string> = { month: 'This month to date', '30': 'Last 30 days', all: 'All recorded history' };
// The Investments heading in the account list opens every holding together.
const PORTFOLIO = 'Investments';
const nav = (value: number, currency?: string) => money(value, currency, 4);
const gain = (value: number, currency?: string) => `${value >= 0.005 ? '+' : ''}${money(value, currency)}`;
const percent = (value: number, base: number) => base ? `${value >= 0 ? '+' : '−'}${Math.abs(value / base * 100).toFixed(2)}%` : '';

type ValuePoint = { day: string; value: number; cost: number; navNote?: string };

/** Market value against cost, with a crosshair and tooltip for the nearest NAV date. */
function ValueChart({ points, label, caption, currency, showCost = true }: { points: ValuePoint[]; label: string; caption: string; currency: string; showCost?: boolean }) {
  const money = (value: number) => formatMoney(value, currency);
  const moneyWhole = (value: number) => formatMoney(value, currency, 0);
  const gain = (value: number) => `${value >= 0.005 ? '+' : ''}${formatMoney(value, currency)}`;
  const [keyboard, setKeyboard] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const selectedPoint = useRef(points.length - 1);
  const plot = useRef<HTMLDivElement>(null);
  const tooltip = useRef<HTMLDivElement>(null);
  const control = useRef<SVGRectElement>(null);
  const instructionsId = useId();
  const revealPending = useRef(false);
  const pointerFocus = useRef(false);
  const pointerScroll = useRef<number | null>(null);
  const [revealRequest, setRevealRequest] = useState(0);
  const reveal = () => { revealPending.current = true; setRevealRequest(value => value + 1); };
  const [tooltipLeft, setTooltipLeft] = useState(0);
  useEscape(hover !== null, () => setHover(null));
  // Measure both boxes: text, zoom and a resized panel can all change the available width.
  useLayoutEffect(() => {
    if (hover === null || !plot.current || !tooltip.current) return;
    const place = () => {
      const width = plot.current!.getBoundingClientRect().width;
      const tipWidth = tooltip.current!.getBoundingClientRect().width;
      const crosshair = x(points[hover].day) / 1000 * width;
      const preferred = crosshair > width / 2 ? crosshair - tipWidth - 12 : crosshair + 12;
      setTooltipLeft(Math.max(0, Math.min(preferred, width - tipWidth)));
    };
    place();
    let revealFrame = 0;
    if (pointerScroll.current !== null) {
      // WebKit can adjust document scroll when an in-flow readout grows.
      // Preserve the pointer's viewport coordinates as well as the plot's layout.
      const top = pointerScroll.current;
      pointerScroll.current = null;
      const restore = () => { if (window.scrollY !== top) window.scrollTo({ top, behavior: 'instant' }); };
      restore();
      revealFrame = requestAnimationFrame(restore);
    }
    if (revealPending.current) {
      revealPending.current = false;
      const revealReadout = () => {
        if (!tooltip.current) return;
        tooltip.current.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        // WebKit can reveal only the containing SVG's box after a native tap.
        // Correct against the actual readout and the visible viewport.
        const box = tooltip.current.getBoundingClientRect();
        const top = (window.visualViewport?.offsetTop ?? 0) + 8;
        const bottom = top + (window.visualViewport?.height ?? window.innerHeight) - 16;
        const delta = box.bottom > bottom ? box.bottom - bottom : box.top < top ? box.top - top : 0;
        if (delta) window.scrollBy({ top: delta, behavior: 'instant' });
      };
      revealReadout();
      // Native touch focus can settle after layout; reveal the final box as well.
      revealFrame = requestAnimationFrame(revealReadout);
    }
    const observer = new ResizeObserver(place);
    observer.observe(plot.current);
    observer.observe(tooltip.current);
    return () => { observer.disconnect(); cancelAnimationFrame(revealFrame); };
  }, [hover, points, revealRequest]);
  const first = points[0].day;
  const last = points.at(-1)!.day;
  const values = points.flatMap((point) => showCost ? [point.value, point.cost] : [point.value]);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const time = (day: string) => new Date(`${day}T12:00:00Z`).getTime();
  const x = (day: string) => 8 + (time(day) - time(first)) / Math.max(1, time(last) - time(first)) * 984;
  const y = (value: number) => 150 - (value - low) / Math.max(1, high - low) * 136;
  // Cost changes only on a purchase or sale, so it steps; market value moves with each NAV.
  const costSteps = points.flatMap((point, index) => index && point.cost !== points[index - 1].cost ? [{ ...point, cost: points[index - 1].cost }, point] : [point]);
  const move = (event: ReactPointerEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const target = ((event.clientX - box.left) / box.width) * 1000;
    let nearest = 0;
    points.forEach((point, index) => { if (Math.abs(x(point.day) - target) < Math.abs(x(points[nearest].day) - target)) nearest = index; });
    if (matchMedia('(max-width: 760px)').matches) pointerScroll.current = window.scrollY;
    selectedPoint.current = nearest;
    setHover(nearest);
  };
  const keyPoint = (event: KeyboardEvent<SVGRectElement>) => {
    pointerScroll.current = null;
    setKeyboard(true);
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopPropagation(); setHover(null); return;
    }
    const moves: Record<string, number> = { ArrowLeft: selectedPoint.current - 1, ArrowRight: selectedPoint.current + 1, ArrowUp: selectedPoint.current - 1, ArrowDown: selectedPoint.current + 1, Home: 0, End: points.length - 1 };
    if (!(event.key in moves)) return;
    event.preventDefault();
    selectedPoint.current = Math.max(0, Math.min(points.length - 1, moves[event.key]));
    setHover(selectedPoint.current);
    reveal();
  };
  const readout = (point: (typeof points)[number]) => `${dayLabel(point.day)}. Market value ${money(point.value)}. ${showCost ? `Cost ${money(point.cost)}. Unrealised gain ${gain(point.value - point.cost)}.` : 'Cost is recorded in another currency.'}${point.navNote ? ` ${point.navNote}.` : ''}`;
  const point = hover === null ? null : points[hover];
  const readoutContent = (item: ValuePoint) => <>
    <p className="chart-tooltip-title">{dayLabel(item.day)}</p>
    {item.navNote && <p className="chart-tooltip-nav">{item.navNote}</p>}
    <p><svg width="30" height="8" aria-hidden="true"><path d="M1 4H29" style={{ stroke: 'var(--series-focus)' }} strokeWidth="2" strokeLinecap="round"/></svg><strong>{money(item.value)}</strong><span>Market value</span></p>
    {showCost && <p><svg width="30" height="8" aria-hidden="true"><path d="M1 4H29" style={{ stroke: 'var(--series-compare)' }} strokeWidth="2" strokeDasharray="5 4"/></svg><strong>{money(item.cost)}</strong><span>Cost</span></p>}
    {showCost && <p><span/><strong>{gain(item.value - item.cost)}</strong><span>Unrealised</span></p>}
  </>;
  return <>
    <div className="account-chart-plot value-chart-plot">
    <p id={instructionsId} className="sr-only">Use Left and Right to move between NAV dates (Up and Down also work), Home and End for the first and last date. Click or tap to select the nearest date. Escape hides the tooltip and keeps focus and the selected date.</p>
    <div className="account-chart-scale"><span>{moneyWhole(high)}</span><span>{moneyWhole(low)}</span></div>
    <div className="value-chart" data-keyboard={keyboard} ref={plot} onPointerUp={(event) => { if (event.pointerType !== 'mouse' && !matchMedia('(max-width: 760px)').matches) reveal(); }} onPointerCancel={() => setHover(null)} onPointerLeave={(event) => { if (event.pointerType === 'mouse' && document.activeElement !== control.current) setHover(null); }}>
      <svg viewBox="0 0 1000 160" preserveAspectRatio="none" role="group" aria-label={label}>
        <path d="M0 14H1000 M0 82H1000 M0 150H1000" className="chart-grid" vectorEffect="non-scaling-stroke"/>
        {showCost && <polyline points={costSteps.map((item) => `${x(item.day)},${y(item.cost)}`).join(' ')} fill="none" style={{ stroke: 'var(--series-compare)' }} strokeWidth="2" strokeDasharray="5 4" vectorEffect="non-scaling-stroke"/>}
        <polyline points={points.map((item) => `${x(item.day)},${y(item.value)}`).join(' ')} fill="none" style={{ stroke: 'var(--series-focus)' }} strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke"/>
        {point && <path d={`M${x(point.day)},4V156`} className="chart-crosshair" vectorEffect="non-scaling-stroke"/>}
        <rect ref={control} x="0" y="0" width="1000" height="160" fill="transparent" vectorEffect="non-scaling-stroke" tabIndex={0} className="chart-focus" role="slider"
          aria-label="Mutual fund values by NAV date" aria-describedby={instructionsId} aria-valuemin={1} aria-valuemax={points.length} aria-valuenow={selectedPoint.current + 1} aria-valuetext={readout(points[selectedPoint.current])}
          onPointerMove={(event) => { if (event.pointerType === 'mouse') move(event); }} onPointerDown={(event) => {
            // Suppress compatibility mouse focus after a tap scrolls the readout.
            if (event.pointerType !== 'mouse') event.preventDefault();
            setKeyboard(false);
            pointerFocus.current = true;
            event.currentTarget.focus({ preventScroll: true });
            pointerFocus.current = false;
            move(event);
          }}
          onFocus={() => { setHover(selectedPoint.current); if (!pointerFocus.current) { setKeyboard(true); reveal(); } }} onBlur={() => { setHover(null); setKeyboard(false); }} onKeyDown={keyPoint}/>
      </svg>
      {points.length === 1 && <>
        {showCost && <span className="value-chart-dot value-chart-single cost-point" style={{ left: `${x(first) / 10}%`, top: `${y(points[0].cost)}px` }} aria-hidden="true"/>}
        <span className="value-chart-dot value-chart-single" style={{ left: `${x(first) / 10}%`, top: `${y(points[0].value)}px` }} aria-hidden="true"/>
      </>}
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">{point ? readout(point) : ''}</p>
      {point && <>
        <span className="value-chart-dot" style={{ left: `${x(point.day) / 10}%`, top: `${y(point.value)}px` }} aria-hidden="true"/>
      </>}
      <div className="account-chart-dates"><span>{dayLabel(first)}</span><span>{caption}</span><span>{dayLabel(last)}</span></div>
      <p className="chart-key-hint" aria-hidden="true">← / ↑ Previous · → / ↓ Next · Home / End First / last · Esc Hide</p>
      <div className="value-chart-readout">
        {!point && <p className="chart-tap-prompt">Tap the chart to inspect a date</p>}
        {point && <div ref={tooltip} className="chart-tooltip" style={{ left: tooltipLeft }} aria-hidden="true" onPointerDown={(event) => event.preventDefault()}>{readoutContent(point)}</div>}
      </div>
    </div>
  </div></>;
}

const openingOnly = () => data.transactions.every(entry=>entry.postings.some(posting=>posting.account==='Equity:Opening Balances'));
export default function AccountSummary() {
  const [account]=useQueryState('account','');
  const native=balanceAccounts(data.transactions,'9999-12-31').filter(account=>account.group!=='Other accounts' && !isFund(account.currency));
  const mixed=currencyOrder(native.map(account=>account.currency)).length>1 && moduleSettings(data.meta.ledger).investments===false;
  const unitHoldings=moduleSettings(data.meta.ledger).investments!==false && data.transactions.some(entry=>entry.postings.some(posting=>isFund(posting.currency) && data.meta.accounts[posting.account]?.kind==='investment'));
  return openingOnly() && !unitHoldings ? <OpeningAccounts/> : mixed && !account ? <CurrencyAccounts/> : <DetailedAccounts/>;
}
function CurrencyAccounts() {
  const [detailsAccount,setDetailsAccount]=useState<string|null>(null);
  const latest=data.transactions.reduce((last,entry)=>entry.date>last?entry.date:last,'');
  const earliest=data.transactions.reduce((first,entry)=>entry.date<first?entry.date:first,latest);
  const [asOf,setAsOf]=useQueryState('asOf',latest,value=>/^\d{4}-\d{2}-\d{2}$/.test(value) && value>=earliest && value<=latest);
  const accounts=balanceAccounts(data.transactions,asOf).filter(account=>account.group!=='Other accounts' && account.group!=='Investments');
  return <section className="page accounts"><PageHeader title="Accounts" subtitle="Balances in each account’s own currency."><label className="compact-field">Balances as of <input className="field" type="date" min={earliest} max={latest} value={asOf} onChange={e=>{if(e.target.value)setAsOf(e.target.value);}}/></label></PageHeader>
    {currencyOrder(accounts.map(account=>account.currency)).length>1 && <section className="panel net-worth"><h2>Net worth</h2><strong>{moneyTotal(sumCurrencies(accounts,account=>account.balance))}</strong></section>}
    <CurrencyGroups accounts={accounts} onDetails={setDetailsAccount} activity/>
    <p className="panel-note">Each currency has its own total. Card debt is subtracted within that currency.</p>
    {detailsAccount && <AccountDialog account={detailsAccount} onClose={()=>setDetailsAccount(null)}/>}
  </section>;
}
function CurrencyGroups({accounts,onDetails,activity=false,children}: {children?:ReactNode;accounts:ReturnType<typeof balanceAccounts>;onDetails:(account:string)=>void;activity?:boolean}) {
  const currencies=currencyOrder(accounts.map(account=>account.currency));
  return <div className={`currency-groups${currencies.length===1 ? ' single-currency' : ''}`}>{currencies.map(currency=>{
    const members=accounts.filter(account=>account.currency===currency);
    return <section className="panel" key={currency} aria-label={currencies.length>1 ? `${currency} accounts` : 'Net balance'}><PanelHeader title={currencies.length>1 ? `${currency} accounts` : 'Net balance'}><strong>{money(members.reduce((sum,account)=>sum+account.balance,0),currency)}</strong></PanelHeader>{members.map(account=><div className="currency-account-row" key={account.key}><div><button type="button" className="account-link" onClick={e=>{e.currentTarget.focus();onDetails(account.account);}}>{short(account.account)}</button><small>{account.group==='Credit cards'?'Card · '+(account.balance===0?'paid off':'owed, including unbilled charges'):((kind)=>kind ? kind[0].toUpperCase()+kind.slice(1) : account.group)(data.meta.accounts[account.account]?.kind)}</small>{activity && <a className="account-activity-link" href={`${import.meta.env.BASE_URL}account-summary/?account=${encodeURIComponent(account.account+'|'+account.currency)}`}>View activity</a>}</div><span>{money(account.group==='Credit cards'?Math.abs(account.balance):account.balance,currency)}{account.group==='Credit cards' && account.balance<0?' owed':''}</span></div>)}{currencies.length===1 && children}</section>;
  })}</div>;
}
function OpeningAccounts() {
  const [detailsAccount,setDetailsAccount]=useState<string|null>(null);
  const date=data.transactions.reduce((last,entry)=>entry.date>last?entry.date:last,'') || data.meta.ledger.startDate;
  const accounts=balanceAccounts(data.transactions,date || '9999-12-31').filter(account=>account.group!=='Other accounts' && (moduleSettings(data.meta.ledger).investments!==false || account.group!=='Investments'));
  const mixed=currencyOrder(accounts.map(account=>account.currency)).length>1;
  const invitation=<section className="empty viewing-empty opening-invitation"><Icon name="accounts"/><strong>{accounts.length?'Only opening balances so far.':'No opening balances yet.'}</strong><p>Your account activity will appear as you add expenses and check statements.</p><a className="button primary" href={`${import.meta.env.BASE_URL}add/`}>Add your first expense</a></section>;
  return <section className="page accounts opening-accounts"><PageHeader title="Accounts" subtitle={accounts.length?`Opening balances · ${dayLabel(date!)}`:'Balances in each account’s own currency.'}/>
    {accounts.length ? <>{mixed && <section className="panel net-worth"><PanelHeader title="Net balance"><strong>{moneyTotal(sumCurrencies(accounts,account=>account.balance))}</strong></PanelHeader></section>}<CurrencyGroups accounts={accounts} onDetails={setDetailsAccount}>{!mixed && invitation}</CurrencyGroups>{mixed && <><p className="panel-note">Card debt is subtracted within its currency.</p><div className="panel">{invitation}</div></>}</> : <div className="panel">{invitation}</div>}
    {detailsAccount && <AccountDialog account={detailsAccount} onClose={()=>setDetailsAccount(null)}/>}
  </section>;
}

function DetailedAccounts() {
  const [detailsAccount,setDetailsAccount]=useState<string|null>(null);
  const investmentsEnabled = moduleSettings(data.meta.ledger).investments !== false;
  const selectableAccounts = Object.fromEntries(Object.entries(data.meta.accounts).filter(([, meta]) => investmentsEnabled || meta.kind !== 'investment'));
  const priced = investmentsEnabled && data.prices.length > 0;
  const visibleGroups = groups.filter(group => investmentsEnabled || group !== 'Investments');
  const recorded = data.transactions.reduce((last, entry) => entry.date > last ? entry.date : last, '');
  // A NAV published after the last recorded entry still moves fund values, so the date can reach it.
  const latest = data.prices.reduce((last, series) => series.dates.at(-1)! > last ? series.dates.at(-1)! : last, recorded);
  const earliest = data.transactions.reduce((first, entry) => entry.date < first ? entry.date : first, latest);
  const [asOf, setAsOf] = useQueryState('asOf', latest, (value) => /^\d{4}-\d{2}-\d{2}$/.test(value) && value >= earliest && value <= latest);
  // The address names the account, adding its currency when it differs from the default.
  const [accountParam, setAccountParam] = useQueryState('account', defaultAccount(selectableAccounts));
  const [query, setQuery] = useQueryState('find', '');
  const [period, setPeriod] = useQueryState('period', 'month', (value) => value in PERIODS);
  const [selectedId, setSelectedId] = useQueryState('entry', '');
  const accounts = balanceAccounts(data.transactions, asOf).filter((account) => account.group !== 'Other accounts' && (investmentsEnabled || account.group !== 'Investments'));
  const portfolio = investmentsEnabled && accountParam === PORTFOLIO;
  const [paramAccount, paramCurrency] = accountParam.split('|');
  const matches = accounts.filter((account) => account.account === paramAccount && (!paramCurrency || account.currency === paramCurrency));
  const selected = !portfolio && matches.length === 1 ? matches[0] : undefined;
  useEffect(() => {
    if (selected && !paramCurrency && selected.currency !== data.meta.ledger.defaultCurrency) setAccountParam(`${selected.account}|${selected.currency}`, 'replace');
  }, [selected?.account, selected?.currency, paramCurrency, setAccountParam]);
  const [contextExpanded, setContextExpanded] = useState(false);
  const holdingsScroll = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const scroller = holdingsScroll.current;
    if (!scroller) return;
    const measure = () => {
      const cells = scroller.querySelectorAll('thead th');
      const sticky = cells[0].getBoundingClientRect().width;
      const last = cells[cells.length - 1].getBoundingClientRect().width;
      scroller.style.setProperty('--sticky-width', `${sticky}px`);
      scroller.style.setProperty('--numeric-width', `${Math.max(1, scroller.clientWidth - sticky)}px`);
      const overflow = scroller.querySelector('table')!.getBoundingClientRect().width > scroller.clientWidth + 1;
      scroller.style.setProperty('--scroll-tail', `${overflow && !matchMedia('(max-width: 760px)').matches ? Math.max(0, scroller.clientWidth - sticky - last) : 0}px`);
    };
    measure();
    const observer = new ResizeObserver(measure); observer.observe(scroller);
    return () => observer.disconnect();
  }, [portfolio]);
  // Fund units at the latest NAV on or before the date, against the cost of the units held.
  const holdings = accounts.filter((account) => isFund(account.currency)).map((account) => {
    const activity = accountActivity(data.transactions, account.account, account.currency, asOf);
    const series = data.prices.find(item => item.commodity === account.currency && item.currency === data.meta.accounts[account.account]?.currency) || data.prices.find(item => item.commodity === account.currency);
    const price = priceOn(series, asOf);
    const costs = activity.at(-1)?.costs || {};
    const declaredCurrency = data.meta.accounts[account.account]?.currency;
    const costCurrency = declaredCurrency && isCurrency(declaredCurrency) ? declaredCurrency : Object.keys(costs)[0] || data.meta.ledger.defaultCurrency;
    const currency = series?.currency || costCurrency;
    return { account, activity, prices: series, price, currency, costCurrency, costs, value: price ? marketValue(account.balance, price.nav) : costs[currency] || 0, cost: costs[currency] || 0 };
  });
  const holding = holdings.find((item) => item.account.key === selected?.key);
  const valueOf = (account: (typeof accounts)[number]) => holdings.find((item) => item.account.key === account.key)?.value ?? account.balance;
  const activity = holding ? holding.activity : selected ? accountActivity(data.transactions, selected.account, selected.currency, asOf) : [];
  const start = period === 'all' ? earliest : period === 'month' ? `${asOf.slice(0, 7)}-01` : (() => { const date = new Date(`${asOf}T12:00:00Z`); date.setUTCDate(date.getUTCDate() - 29); return date.toISOString().slice(0, 10); })();
  const visible = activity.filter((row) => row.date >= start);
  const showActivityYear = visible.some(row => row.date.slice(0, 4) !== asOf.slice(0, 4));
  const opening = activity.filter((row) => row.date < start).at(-1)?.balance || 0;
  const card = selected?.group === 'Credit cards';
  // A card's ledger balance is negative while money is owed; show what's owed as a positive amount.
  const shown = (balance: number) => card ? -balance : balance;
  const points = [{ date: start, balance: shown(opening) }, ...visible.map((row) => ({ date: row.date, balance: shown(row.balance) })), { date: asOf, balance: shown(selected?.balance || 0) }];
  const low = Math.min(...points.map((point) => point.balance));
  const high = Math.max(...points.map((point) => point.balance));
  const flat = high === low;
  const time = (day: string) => new Date(`${day}T12:00:00Z`).getTime();
  const steps = points.flatMap((point, index) => index ? [{ date: point.date, balance: points[index - 1].balance }, point] : [point]);
  const coords = steps.map((point) => `${8 + (time(point.date) - time(start)) / Math.max(1, time(asOf) - time(start)) * 984},${flat ? 82 : 150 - (point.balance - low) / (high - low) * 136}`).join(' ');
  const needle = query.trim().toLowerCase();
  const listed = groups.flatMap((group) => accounts.filter((account) => account.group === group && account.account.toLowerCase().includes(needle)));
  type ActivityKey = 'date' | 'description' | 'in' | 'out' | 'balance';
  const [sort, setSort] = useQueryState('sort', 'date-desc', (value) => /^(date|description|in|out|balance)-(asc|desc)$/.test(value));
  const [sortKey, sortDirection] = sort.split('-') as [ActivityKey, 'asc' | 'desc'];
  const cashColumns = useColumnWidths('ledger-activity-columns-v2', { date: 76, in: 104, out: 104, balance: 120 });
  // A fund's activity counts units: Units and Cost take In and Out's places, Units held the balance's.
  const fundColumns = useColumnWidths('fund-activity-columns', { date: 76, in: 104, price: 120, out: 120, balance: 112 });
  const { widths, resizer } = holding ? fundColumns : cashColumns;
  const factor = sortDirection === 'asc' ? 1 : -1;
  // Date order keeps the ledger's own sequence; the running balance always follows that order.
  const rows = sortKey === 'date' ? (sortDirection === 'desc' ? [...visible].reverse() : visible)
    : [...visible].sort((a, b) => factor * (sortKey === 'description' ? a.entry.description.localeCompare(b.entry.description)
      : holding ? (sortKey === 'in' ? a.change - b.change : sortKey === 'out' ? a.costChange - b.costChange : a.balance - b.balance)
      : sortKey === 'in' ? Math.max(0, a.change) - Math.max(0, b.change) : sortKey === 'out' ? Math.max(0, -a.change) - Math.max(0, -b.change) : a.balance - b.balance));
  const heading = (key: ActivityKey, label: string, numeric = false) => <th scope="col" className={numeric ? 'th-num' : ''} aria-label={label} aria-sort={sortKey === key ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}>
    <button type="button" className="sort-button" title={`Sort by ${label.toLowerCase()}`} onClick={() => setSort(`${key}-${sortKey === key ? (sortDirection === 'asc' ? 'desc' : 'asc') : key === 'description' ? 'asc' : 'desc'}`)}>{label}<span className="sort-indicator" aria-hidden="true">{sortKey === key ? (sortDirection === 'asc' ? '▲' : '▼') : ''}</span></button>
    {key === 'description' ? resizer('description', label, 'in') : resizer(key, label)}</th>;
  const menu = useContextMenu();
  const datePicker = useRef<HTMLInputElement>(null);
  const selectionHeading = useRef<HTMLHeadingElement>(null);
  const focusDrill = useRef(false);
  const returnHolding = useRef<string | null>(null);
  const previousAccount = useRef(accountParam);
  const announceDate = useRef<string | null>(null);
  const changeDate = (next: string) => {
    if (next === asOf || next < earliest || next > latest) return;
    announceDate.current = next;
    setAsOf(next); setSelectedId('');
  };
  const shiftDay = (days: number) => { const date = new Date(`${asOf}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + days); const next = date.toISOString().slice(0, 10); if (next >= earliest && next <= latest) { changeDate(next); } };
  const selectedEntry = rows.find((row) => row.entry.id === selectedId)?.entry as Transaction | undefined;

  const accountId = useCallback((account: { key: string }) => account.key, []);
  const chooseAccount = useCallback((account: { account: string; currency: string }, mode: 'push' | 'replace' = 'push') => { setAccountParam(account.currency === data.meta.ledger.defaultCurrency ? account.account : `${account.account}|${account.currency}`, mode); setSelectedId('', 'replace'); }, [setAccountParam, setSelectedId]);
  const accountNav = useRowNavigation(listed, accountId, selected?.key || null, chooseAccount, 0, (account) => short(account.account));
  const accountMenu = (account: (typeof accounts)[number]): MenuItem[] => [
    { label: 'Show Activity', action: () => { chooseAccount(account); accountNav.focusRow(account.key); } },
    { label: 'Find Its Entries in Explorer', action: () => navigate(`explorer/?q=${encodeURIComponent(short(account.account).split(' · ').at(-1) || '')}`) },
    'separator',
    { label: 'Copy Account Name', action: () => copyAndAnnounce(short(account.account), 'account name') },
    { label: 'Copy Ledger Account', action: () => copyAndAnnounce(account.account, 'ledger account') },
    ...(isFund(account.currency) ? [
      { label: priced ? 'Copy Market Value' : 'Copy Recorded Cost', action: () => copyAndAnnounce(priced ? valueOf(account).toFixed(2) : moneyTotal(holdings.find(item => item.account.key === account.key)?.costs || {}), priced ? 'market value' : 'recorded cost') },
      { label: 'Copy Units', action: () => copyAndAnnounce(account.balance.toFixed(3), 'units') },
    ] : [{ label: 'Copy Balance', action: () => copyAndAnnounce((account.group === 'Credit cards' ? Math.abs(account.balance) : account.balance).toFixed(2), 'balance') }]),
  ];
  const rowId = useCallback((row: { entry: { id: string } }) => row.entry.id, []);
  const follow = useCallback((row: { entry: { id: string } }) => { if (selectedId) setSelectedId(row.entry.id, 'replace'); }, [selectedId, setSelectedId]);
  const entryNav = useRowNavigation(rows, rowId, selectedId || null, follow, 0, (row) => row.entry.description);
  const close = useCallback(() => { const id = selectedId; setSelectedId(''); if (id) requestAnimationFrame(() => entryNav.focusRow(id, true)); }, [selectedId, setSelectedId, entryNav]);
  useEscape(!!selectedEntry, close);

  // Funds first, then the accounts valued at what's been paid in.
  const investments = accounts.filter((account) => account.group === 'Investments')
    .sort((a, b) => Number(isFund(b.currency)) - Number(isFund(a.currency)));
  const fundValue = sumCurrencies(holdings, (item: typeof holdings[number]) => item.value);
  const fundCost: Record<string, number> = {};
  for (const item of holdings) for (const [currency, cost] of Object.entries(item.costs)) fundCost[currency] = (fundCost[currency] || 0) + cost;
  const contributions = sumCurrencies(investments.filter(account => !isFund(account.currency)), (account: typeof accounts[number]) => account.balance);
  const combined = (...maps: Record<string, number>[]) => maps.reduce((sum, map) => { for (const [code, value] of Object.entries(map)) sum[code] = (sum[code] || 0) + value; return sum; }, {});
  const gainTotals = sumCurrencies(holdings.filter(item => Object.keys(item.costs).every(code => code === item.currency)), (item: typeof holdings[number]) => item.value - item.cost);
  const investmentTotals = combined(priced ? fundValue : fundCost, contributions);
  const valueCurrency = (account: typeof accounts[number]) => holdings.find(item => item.account.key === account.key)?.currency || account.currency;
  const netWorth = combined(!priced ? fundCost : {}, sumCurrencies(accounts.filter(account => priced || !isFund(account.currency)), (account: typeof accounts[number]) => valueOf(account), (account: typeof accounts[number]) => valueCurrency(account)));
  useLayoutEffect(() => {
    const changedAccount = previousAccount.current !== accountParam;
    previousAccount.current = accountParam;
    // A history return removes the old heading/link. Stable picker and list
    // controls retain focus; only replace a disappearing drill-down opener.
    const returning = changedAccount && document.activeElement === document.body;
    if (focusDrill.current || (returning && selected?.key === returnHolding.current)) {
      focusDrill.current = false;
      selectionHeading.current?.focus();
      announce(`${selected ? short(selected.account) : 'Investments'}. Balances as of ${dayLabel(asOf)}.`);
    } else if (returning && portfolio && returnHolding.current) {
      const link = [...document.querySelectorAll<HTMLAnchorElement>('.holdings-table a[data-account]')].find(link => link.dataset.account === returnHolding.current);
      link?.focus();
    }
  }, [accountParam, portfolio, selected?.account, asOf]);
  useEffect(() => {
    if (announceDate.current !== asOf) return;
    announceDate.current = null;
    const balance = portfolio ? `Investments ${moneyTotal(investmentTotals)}`
      : selected ? `${short(selected.account)} ${holding ? priced ? 'market value' : 'recorded cost' : 'balance'} ${holding ? priced ? money(holding.value, holding.currency) : moneyTotal(holding.costs) : money(shown(selected.balance), selected.currency)}`
      : 'Account not found';
    announce(`Balances as of ${dayLabel(asOf)}; ${balance}.`);
  }, [asOf, portfolio, selected, holding, fundValue, contributions]);
  const accountTitle = portfolio ? 'Investments' : selected ? short(selected.account) : 'Account not found';
  useEffect(() => { document.title = `${accountTitle} — Accounts — Ledger`; }, [accountTitle]);
  const drillInto = (account: (typeof accounts)[number]) => {
    returnHolding.current = account.key;
    focusDrill.current = true;
    chooseAccount(account);
  };
  const accountHref = (account: (typeof accounts)[number]) => {
    const params = new URLSearchParams(typeof window === 'undefined' ? '' : window.location.search);
    params.set('account', account.currency === data.meta.ledger.defaultCurrency ? account.account : `${account.account}|${account.currency}`);
    params.delete('entry');
    return `?${params}`;
  };
  const periodSelect = <label className="compact-field"><span>Period</span><select className="field" value={period} onChange={(event) => { setPeriod(event.target.value); setSelectedId(''); }}>{Object.entries(PERIODS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>;
  const legend = <span className="value-chart-legend"><span><svg width="30" height="8" aria-hidden="true"><path d="M1 4H29" className="value-key"/></svg>Market value</span><span><svg width="30" height="8" aria-hidden="true"><path d="M1 4H29" className="cost-key" strokeDasharray="5 4"/></svg>Cost</span></span>;
  const series = (items: typeof holdings): ValuePoint[] => valueSeries(items.map(item => ({...item, activity: item.activity.map(row => ({...row, cost: row.costs[item.currency] || 0}))})), start < earliest ? earliest : start, asOf).map(point => {
    const carriedDates = [...new Set(items.filter(item => item.activity.some(row => row.date <= point.day && row.balance !== 0)).map(item => priceOn(item.prices, point.day)?.date).filter((date): date is string => !!date && date !== point.day))];
    return { ...point, navNote: carriedDates.length ? `NAV of ${carriedDates.map(dayLabel).join(' and ')} (carried forward)` : undefined };
  });
  const describe = (name: string, items: { day: string; value: number; cost: number }[], currency: string, showCost = true) => `${name} from ${dayLabel(items[0].day)} to ${dayLabel(items.at(-1)!.day)}: market value ${money(items[0].value, currency)} to ${money(items.at(-1)!.value, currency)}, ${showCost ? `against the cost recorded in ${currency}, ${money(items.at(-1)!.cost, currency)}.` : 'Cost remains in its recorded currency.'}`;
  const navNote = (price: { date: string } | null) => price ? `NAV of ${dayLabel(price.date)}` : 'At recorded cost';

  // The account list is already a column, so entry details float over the page instead of adding a third.
  return <div className="accounts-container">{detailsAccount && <AccountDialog account={detailsAccount} onClose={()=>setDetailsAccount(null)}/>}<div className="split"><section className="page accounts" data-context-expanded={contextExpanded} data-portfolio={portfolio}>
    <PageHeader title="Accounts" subtitle={`Balances as of ${dayLabel(asOf)}, from every recorded entry including opening balances and pending entries. ${investmentsEnabled ? priced ? 'Mutual funds are valued at the latest NAV on or before that day. ' : 'Investments show recorded cost; no recorded prices available. ' : ''}Forecasts are left out.`}>
      {selected && <button type="button" className="button" onClick={e=>{e.currentTarget.focus();setDetailsAccount(selected.account);}}>Account details</button>}
      <span className="compact-field" id="as-of-label">Balances as of</span>
      <div className="segmented month-stepper" role="group" aria-labelledby="as-of-label">
        <button type="button" className="icon-button" aria-label="Previous day" title="Previous day" disabled={asOf <= earliest} onClick={() => shiftDay(-1)}><Icon name="chevron-left"/></button>
        <span className="month-select date-select"><Icon name="calendar"/>
          <button type="button" className="date-button" aria-label={`Balances as of ${dayLabel(asOf)}. Choose a date`} onClick={() => { try { datePicker.current?.showPicker(); } catch { datePicker.current?.focus(); } }}>{dayLabel(asOf)}</button>
          <input ref={datePicker} className="date-input" type="date" tabIndex={-1} aria-hidden="true" min={earliest} max={latest} value={asOf} onChange={(event) => { if (event.target.value) { changeDate(event.target.value); } }}/></span>
        <button type="button" className="icon-button" aria-label="Next day" title="Next day" disabled={asOf >= latest} onClick={() => shiftDay(1)}><Icon name="chevron-right"/></button>
      </div>
      {asOf !== latest && <button type="button" className="button" onClick={() => { changeDate(latest); }}>Latest</button>}
    </PageHeader>
    <button type="button" className="button plain accounts-context-toggle" aria-expanded={contextExpanded} aria-controls="account-totals account-context" onClick={() => setContextExpanded(value => !value)}>{contextExpanded ? 'Less account context' : 'More account context'}<Icon name="chevron-up-down"/></button>
    <section className="panel"><PanelHeader title="Net worth"><span className="summary-total">{moneyTotal(netWorth)}</span></PanelHeader></section>
    {currencyOrder(accounts.filter(account=>!isFund(account.currency)).map(account=>account.currency)).length>1 && <><CurrencyGroups accounts={accounts.filter(account=>!isFund(account.currency))} onDetails={setDetailsAccount}/><p className="panel-note">Each currency has its own total. Card debt is subtracted within that currency.</p></>}
    <section className="metrics" id="account-totals" aria-label="Totals by kind of account">{['Banks', 'Prepaid', 'Credit cards', ...(investmentsEnabled ? ['Investments'] : [])].map((group) => {
      const members = accounts.filter((account) => account.group === group);
      const total = group === 'Investments' && !priced ? investmentTotals : sumCurrencies(members, (account: typeof accounts[number]) => valueOf(account) * (group === 'Credit cards' ? -1 : 1), (account: typeof accounts[number]) => valueCurrency(account));
      const note = group === 'Investments' && !priced ? 'Recorded cost and contributions' : group === 'Investments' && !holdings.length ? 'Contributions since the ledger began, not market value' : GROUP_LABEL[group].note;
      return <div className="metric" key={group} data-selected={portfolio && group === PORTFOLIO}><p className="metric-label">{group === 'Credit cards' && Object.values(total).every(value => (value as number) < 0) ? 'Credit on cards' : GROUP_LABEL[group].total}</p><p className="metric-value">{moneyTotal(total)}</p><p className="metric-note" title={note}>{plural(members.length, 'account')} · {note}</p></div>;
    })}</section>
    <label className="compact-field accounts-picker"><span>Account</span><select className="field" aria-label="Choose account" value={portfolio ? PORTFOLIO : selected ? (selected.currency === data.meta.ledger.defaultCurrency ? selected.account : `${selected.account}|${selected.currency}`) : ''} onChange={(event) => { setAccountParam(event.target.value); setSelectedId('', 'replace'); }}>
      {!portfolio && !selected && <option value="" disabled>Account not found</option>}
      {investmentsEnabled && <option value={PORTFOLIO}>Investments · portfolio</option>}
      {visibleGroups.map(group => <optgroup key={group} label={group}>{accounts.filter(account => account.group === group).map(account => <option key={account.key} value={account.currency === data.meta.ledger.defaultCurrency ? account.account : `${account.account}|${account.currency}`}>{short(account.account)}{account.currency !== data.meta.ledger.defaultCurrency && !isFund(account.currency) ? ` (${account.currency})` : ''}</option>)}</optgroup>)}
    </select></label>
    <div className="accounts-workspace">
      <nav className="panel account-list" id="account-context" aria-label="Accounts">
        <div className="account-list-search"><SearchField id="account-search" value={query} onChange={setQuery} label="Find an account" placeholder="Find" scope="Accounts"/></div>
        <div onKeyDown={accountNav.onKeyDown}>{visibleGroups.map((group) => {
          const members = listed.filter((account) => account.group === group);
          const totals:Record<string,number>={};
          for(const account of members){const fund=holdings.find(item=>item.account.key===account.key);if(fund){if(priced)totals[fund.currency]=(totals[fund.currency]||0)+fund.value;else for(const [code,value] of Object.entries(fund.costs))totals[code]=(totals[code]||0)+Number(value);}else totals[account.currency]=(totals[account.currency]||0)+(group==='Credit cards'?Math.abs(account.balance):account.balance);}
          const heading=<><span className="account-group-name"><strong>{group}</strong><small>{members.length}</small>{group===PORTFOLIO&&<Icon name="chevron-right"/>}</span><span className="account-group-total">{Object.entries(totals).map(([code,value])=>money(value,code)).join(' + ')}{group==='Credit cards'?' owed':''}</span></>;
          return members.length ? <section key={group} aria-label={group}>{group === PORTFOLIO
            ? <h3><button type="button" className="account-group-open" aria-current={portfolio ? 'true' : undefined} title="Show every investment together" onClick={() => { setAccountParam(PORTFOLIO); setSelectedId('', 'replace'); }}>{heading}</button></h3>
            : <h3>{heading}</h3>}{members.map((account) => {
            const fund = holdings.find((item) => item.account.key === account.key);
            const amount = fund ? priced ? money(fund.value, fund.currency) : moneyTotal(fund.costs) : money(group === 'Credit cards' ? Math.abs(account.balance) : account.balance, account.currency);
            return <button key={account.key} type="button" className="account-row" {...accountNav.rowProps(account)} aria-current={account.key === selected?.key ? 'true' : undefined} onClick={() => chooseAccount(account)}
            data-copy={[short(account.account), amount].join('\t')}
            onContextMenu={(event) => menu.open(event, accountMenu(account), event.currentTarget)} onKeyDownCapture={(event) => { if (menu.keyOpens(event)) menu.open(event, accountMenu(account), event.currentTarget); }}>
            <span title={account.account}>{short(account.account)}{account.currency !== data.meta.ledger.defaultCurrency && !fund && <span className="row-sub">{account.currency}</span>}</span>
            <span className="num">{amount}{fund && <span className="row-sub">{units(account.balance)} units</span>}{group === 'Credit cards' && <span className="row-sub">{account.balance <= 0 ? 'Owed' : 'In credit'}</span>}</span>
          </button>;
          })}</section> : null;
        })}{!listed.length && <div className="empty"><strong>No accounts match</strong><span>Check the spelling, or clear the search.</span><button type="button" className="button" onClick={() => { setQuery(''); requestAnimationFrame(() => document.getElementById('account-search')?.focus({ preventScroll: true })); }}>Clear Search</button></div>}</div>
      </nav>
      {!portfolio && !selected && <section className="panel account-not-found"><PanelHeader title={matches.length > 1 ? 'Choose an account currency' : 'Account not found'}/><div className="empty"><p>{matches.length > 1 ? 'This account has more than one commodity. Choose the account and currency from the list.' : <>No account matches <code>{breakable(accountParam)}</code> on {dayLabel(asOf)}. Choose another account or a later date.</>}</p>{investmentsEnabled && <button type="button" className="button" onClick={() => { focusDrill.current = true; setAccountParam(PORTFOLIO); setSelectedId('', 'replace'); }}>Back to Investments</button>}</div></section>}
      {portfolio && <div className="stack">
        <section className="panel">
          <PanelHeader title="Investments" headingRef={selectionHeading} meta={priced ? "Mutual funds at market value; other investments at what's been paid in since the ledger began" : 'At recorded cost; no recorded prices available'}><span className="summary-total">{moneyTotal(investmentTotals)}</span></PanelHeader>
          <dl className="fund-stats">
            <div><dt>{priced ? 'Mutual funds' : 'Fund cost'}</dt><dd>{moneyTotal(fundValue)}<span className="row-sub">{plural(holdings.length, 'fund')} {priced ? 'at market value' : 'at cost'}</span></dd></div>
            <div><dt>Fund cost</dt><dd>{moneyTotal(fundCost)}<span className="row-sub">Of the units held</span></dd></div>
            {priced && <div><dt>Unrealised gain</dt><dd>{holdings.every(item => Object.keys(item.costs).every(code => code === item.currency)) ? moneyTotal(gainTotals) : 'Different cost currencies'}<span className="row-sub">{'Per currency'} over cost</span></dd></div>}
            <div><dt>Other investments</dt><dd>{moneyTotal(contributions)}<span className="row-sub">Contributions</span></dd></div>
          </dl>
          {priced && holdings.length ? currencyOrder(holdings.map(item => item.currency)).map(currency => { const items = series(holdings.filter(item => item.currency === currency)); return <div className="account-chart" key={currency}>
            <div className="account-chart-head"><h3>Mutual funds over time · {currency}</h3>{legend}{periodSelect}</div>
            <ValueChart key={`${accountParam}-${start}-${asOf}-${items.length}`} points={items} showCost={holdings.filter(item => item.currency === currency).every(item => Object.keys(item.costs).every(code => code === currency))} currency={currency} label={describe('All mutual funds', items, currency, holdings.filter(item => item.currency === currency).every(item => Object.keys(item.costs).every(code => code === currency)))} caption="Units at the latest available NAV on each date, against the cost of the units held"/>
          </div>; }) : null}
        </section>
        <section className="panel">
          <PanelHeader title="Holdings" meta={`As of ${dayLabel(asOf)} · ${priced ? 'Latest recorded prices' : 'Recorded cost'}`}/>
          <p className="panel-note holdings-scroll-hint">{priced ? 'Scroll across to compare units, NAV, cost, value and gain.' : 'Compare units and recorded cost.'} Account names stay in view.</p>
          <div ref={holdingsScroll} className="table-scroll holdings-scroll" onScroll={(event) => { event.currentTarget.dataset.scrolled = String(event.currentTarget.scrollLeft > 1); }} tabIndex={0} role="region" aria-label="Investment holdings"><table className="data-table holdings-table" role="table">
            <thead><tr><th>Account</th><th className="th-num">Units</th>{priced && <th className="th-num">NAV</th>}<th className="th-num">Cost</th><th className="th-num">Value</th>{priced && <th className="th-num">Gain</th>}</tr></thead>
            <tbody>{investments.map((account) => {
              const fund = holdings.find((item) => item.account.key === account.key);
              return <tr key={account.key} className="interactive" onClick={(event) => { if (!(event.target instanceof Element && event.target.closest('a[href]')) && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey && !hasSelectedText(event)) drillInto(account); }}
                onContextMenu={(event) => menu.open(event, [{ label: 'Show Activity', action: () => drillInto(account) }, ...accountMenu(account).slice(1)], event.currentTarget)}
                onKeyDownCapture={(event) => { if (menu.keyOpens(event)) menu.open(event, [{ label: 'Show Activity', action: () => drillInto(account) }, ...accountMenu(account).slice(1)], event.currentTarget); }}>
                <td><a className="row-open" href={accountHref(account)} data-account={account.key} onAuxClick={(event) => event.stopPropagation()} onClick={(event) => {
                  event.stopPropagation();
                  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                  event.preventDefault(); if (!hasSelectedText(event)) drillInto(account);
                }}><span className="row-title">{short(account.account)}</span><span className="row-sub">{fund ? navNote(fund.price) : 'At contributions'}</span></a></td>
                <td className="num" data-label="Units">{fund ? units(account.balance) : ''}</td>
                {priced && <td className="num" data-label="NAV">{fund?.price ? nav(fund.price.nav, fund.currency) : ''}</td>}
                <td className="num" data-label="Cost">{fund ? moneyTotal(fund.costs) : money(account.balance, account.currency)}</td>
                <td className="num" data-label="Value">{fund && !priced ? moneyTotal(fund.costs) : money(fund ? fund.value : account.balance, fund?.currency || account.currency)}</td>
                {priced && <td className="num" data-label="Gain">{fund ? <>{Object.keys(fund.costs).every(code => code === fund.currency) ? gain(fund.value - fund.cost, fund.currency) : 'Different cost currency'}<span className="row-sub">{Object.keys(fund.costs).every(code => code === fund.currency) ? percent(fund.value - fund.cost, fund.cost) : ''}</span></> : ''}</td>}
              </tr>;
            })}</tbody>
            <tfoot><tr><td>Total</td><td/>{priced && <td/>}<td className="num">{moneyTotal(combined(fundCost, contributions))}</td><td className="num">{moneyTotal(investmentTotals)}</td>{priced && <td className="num">{holdings.length ? moneyTotal(gainTotals) : ''}</td>}</tr></tfoot>
          </table></div>
        </section>
      </div>}
      {selected && <div className="stack">
        <section className="panel">
          <PanelHeader title={short(selected.account)} headingRef={selectionHeading} meta={<>{holding ? 'Mutual fund' : selected.group} · <code>{breakable(selected.account)}</code></>}><span className={holding ? 'fund-headline-group' : undefined}>{holding && <span className="summary-total-label">{priced ? 'Market value' : 'Recorded cost'}</span>}<span className={`summary-total${holding ? ' fund-headline' : ''}`}>{holding ? priced ? money(holding.value, holding.currency) : moneyTotal(holding.costs) : money(card ? Math.abs(selected.balance) : selected.balance, selected.currency)}</span></span></PanelHeader>
          {holding && <dl className="fund-stats">
            <div><dt>Units</dt><dd>{units(selected.balance)}</dd></div>
            {priced && <div><dt>NAV</dt><dd>{holding.price ? nav(holding.price.nav, holding.currency) : '—'}<span className="row-sub">{holding.price ? dayLabel(holding.price.date) : 'At recorded cost'}</span></dd></div>}
            <div><dt>Cost</dt><dd>{moneyTotal(holding.costs)}<span className="row-sub">{selected.balance ? `${moneyTotal(Object.fromEntries(Object.entries(holding.costs).map(([code, value]) => [code, value / selected.balance])), 4)} a unit` : ''}</span></dd></div>
            {priced && <div><dt>Unrealised gain</dt><dd>{Object.keys(holding.costs).every(code => code === holding.currency) ? gain(holding.value - holding.cost, holding.currency) : 'Different cost currency'}<span className="row-sub">{Object.keys(holding.costs).every(code => code === holding.currency) ? percent(holding.value - holding.cost, holding.cost) : ''} over cost</span></dd></div>}
          </dl>}
          {holding && !priced && <p className="panel-note">Showing recorded cost. No recorded prices available.</p>}
          {holding ? priced ? (() => { const items = series([holding]); return <div className="account-chart">
            <div className="account-chart-head"><h3>Market value over time</h3>{legend}{periodSelect}</div>
            <ValueChart key={`${accountParam}-${start}-${asOf}-${items.length}`} points={items} showCost={Object.keys(holding.costs).every(code => code === holding.currency)} currency={holding.currency} label={describe(short(selected.account), items, holding.currency, Object.keys(holding.costs).every(code => code === holding.currency))} caption="Units held at the latest available NAV on each date"/>
          </div>; })() : null : <div className="account-chart">
            <div className="account-chart-head"><h3>{card ? (selected.balance <= 0 ? 'Owed' : 'In credit') : selected.group === 'Investments' ? 'Contributions' : 'Balance'} over time</h3>
              {periodSelect}</div>
            <div className={`account-chart-plot${flat ? ' account-chart-flat' : ''}`}>
              <div className="account-chart-scale"><span>{money(high, selected.currency)}</span>{!flat && <span>{money(low, selected.currency)}</span>}</div>
              <svg viewBox="0 0 1000 160" preserveAspectRatio="none" role="img" aria-label={`${short(selected.account)} from ${dayLabel(start)} to ${dayLabel(asOf)}: opening ${money(opening, selected.currency)}, closing ${money(selected.balance, selected.currency)}${card ? '; a card balance is negative while money is owed' : ''}.`}>
                <path d={flat ? "M0 82H1000" : "M0 14H1000 M0 82H1000 M0 150H1000"} className="chart-grid" vectorEffect="non-scaling-stroke"/>
                <polyline points={coords} fill="none" style={{ stroke: 'var(--series-focus)' }} strokeWidth="2" vectorEffect="non-scaling-stroke"/>
              </svg>
            </div>
            {flat && <p className="chart-flat-note">No change in this period</p>}
            <div className="account-chart-dates"><span>{dayLabel(start)}</span><span>{card ? 'Amount owed: charges raise it, payments lower it' : 'Running ledger balance'}</span><span>{dayLabel(asOf)}</span></div>
          </div>}
        </section>
        <section className="panel">
          <PanelHeader title="Activity" meta={`${plural(visible.length, 'entry', 'entries')} · ${PERIODS[period].toLowerCase()} · ${holding ? `${units(opening)} units held at the start` : `${card ? 'owed at the start' : 'opening balance'} ${money(shown(opening), selected.currency)}`}`}/>
          {visible.length ? <div className="table-scroll"><table className={`data-table activity-table${holding ? ' fund-activity-table' : ''}`} role="table">
            {holding
              ? <colgroup><col style={{ width: showActivityYear ? Math.max(108, widths.date) : widths.date }}/><col/><col style={{ width: widths.in }}/><col style={{ width: widths.price }}/><col style={{ width: widths.out }}/><col style={{ width: widths.balance }}/></colgroup>
              : <colgroup><col style={{ width: showActivityYear ? Math.max(108, widths.date) : widths.date }}/><col/><col style={{ width: widths.in }}/><col style={{ width: widths.out }}/><col style={{ width: widths.balance }}/></colgroup>}
            <thead><tr>{heading('date', 'Date')}{heading('description', 'Description')}{holding
              ? <>{heading('in', 'Units', true)}<th scope="col" className="th-num" title="Cost of each unit bought, or the average cost of each unit sold">Cost per unit{resizer('price', 'Cost per unit')}</th>{heading('out', 'Cost', true)}{heading('balance', 'Units held', true)}</>
              : <>{heading('in', card ? 'Paid' : 'In', true)}{heading('out', card ? 'Charged' : 'Out', true)}{heading('balance', card ? 'Owed' : 'Balance', true)}</>}</tr></thead>
            <tbody onKeyDown={entryNav.onKeyDown}>{rows.map((row) => <tr key={`${row.entry.id}-${row.date}`} className="interactive" data-selected={selectedId === row.entry.id} onClick={(event) => { if (hasSelectedText(event)) return; setSelectedId(row.entry.id); entryNav.focusRow(row.entry.id); }}
              onContextMenu={(event) => menu.open(event, entryMenu(row.entry as Transaction, () => setSelectedId(row.entry.id), 'journal', row.entry.date.slice(0, 7)), event.currentTarget)}>
              <td className="date"><time dateTime={row.date} aria-label={dayLabel(row.date)}>{showActivityYear ? dayLabel(row.date) : dayShort(row.date)}</time></td>
              <td><EntryLink entryId={row.entry.id} className="row-open" {...entryNav.rowProps(row)} aria-current={selectedId === row.entry.id ? 'true' : undefined} aria-controls="transaction-inspector" data-copy={[dayLabel(row.date), row.entry.description, money(row.change, selected.currency), selected.account].join('\t')} onOpen={() => { setSelectedId(row.entry.id); entryNav.focusRow(row.entry.id); }}
                onKeyDown={(event) => { if (menu.keyOpens(event)) menu.open(event, entryMenu(row.entry as Transaction, () => setSelectedId(row.entry.id), 'journal', row.entry.date.slice(0, 7)), event.currentTarget.closest('tr')); }}><span className="row-title">{row.entry.description}</span><span className="row-sub">{row.entry.status === 'Pending' ? 'Unclassified' : 'Cleared'}</span></EntryLink></td>
              {holding ? <>
                <td className={`num${row.change > 0 ? ' credit' : ''}`} data-label="Units">{row.change > 0 ? '+' : ''}{units(row.change)}</td>
                <td className="num" data-label="Cost per unit">{row.change ? moneyTotal(Object.fromEntries(Object.entries(row.costChanges).map(([code, value]) => [code, Math.abs(value / row.change)])), 4) : ''}</td>
                <td className="num" data-label="Cost">{moneyTotal(row.costChanges)}</td>
                <td className="num activity-balance" data-label="Units held">{units(row.balance)}</td>
              </> : <>
                <td className="num credit" data-label={card ? 'Paid' : 'In'}>{row.change > 0 ? money(row.change, selected.currency) : ''}</td>
                <td className="num" data-label={card ? 'Charged' : 'Out'}>{row.change < 0 ? money(-row.change, selected.currency) : ''}</td>
                <td className="num activity-balance" data-label={card ? 'Owed' : 'Balance'}>{money(shown(row.balance), selected.currency)}</td>
              </>}
            </tr>)}</tbody></table></div>
            : <div className="empty"><strong>No activity in this period</strong><span>{holding ? 'The units carry forward from earlier entries; their value still moves with the NAV.' : 'The balance carries forward from earlier entries.'} Choose a longer period to see them.</span></div>}
        </section>
      </div>}
    </div>
  </section>
  <TransactionDetails transaction={selectedEntry || null} onClose={close} entries={rows.map((row) => row.entry as Transaction)} onStep={(id) => setSelectedId(id, 'replace')}/>
  {menu.element}
  </div></div>;
}
