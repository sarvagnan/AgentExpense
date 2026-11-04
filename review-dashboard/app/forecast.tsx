import Rules from './rules';
import { moduleSettings } from '../scripts/ledger-modules.mjs';
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { dashboard as data, type DashboardData } from './data';
import { moneyWhole as formatWhole, axisMoney as formatAxis } from './formatting';
import {cumulative, forecastProjection, variableAfter as estimateAfter, type Point} from './forecast-projection';
import {previousMonth as calendarPreviousMonth} from '../scripts/month-summary.mjs';
import { Delta, Icon, PageHeader, money, useColumnWidths, useQueryState, useRowNavigation, PanelHeader, dayShort, daysInMonth, monthAbbr, monthLong, monthShort, plural } from './ui';

const mean = (values: number[]) => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
// Earlier months take a fixed slot while selected, so adding or removing another month never repaints them.
const SLOTS = ['var(--compare-1)', 'var(--compare-2)', 'var(--compare-3)', 'var(--compare-4)', 'var(--compare-5)'];
const slotName = (slot: number) => String.fromCharCode(65 + slot);
const FOCUS = 'var(--series-focus)';

type Portion = DashboardData['forecast']['variable']['portions'][number];
const portionLabel = (portion: Portion, days = 31) => `${portion.from}–${Math.min(portion.to, days)}`;

export default function Forecast({online,version=0}:{online:boolean|null;version?:number}) {
  const [section,setSection]=useQueryState('section','overview',v=>v==='overview'||v==='rules');
  return <><nav className="forecast-sections" aria-label="Forecast views"><button className={`button ${section==='overview'?'active':''}`} aria-current={section==='overview'?'page':undefined} onClick={()=>setSection('overview')}>Overview</button><button className={`button ${section==='rules'?'active':''}`} aria-current={section==='rules'?'page':undefined} onClick={()=>setSection('rules')}>Rules</button></nav>{section==='rules'?<Rules online={online}/>:<ForecastOverview key={version}/>}</>;
}
function ForecastOverview() {
  if(!data.months.some(month=>month.count>0) && !data.forecasts.some(f=>f.items.length))return <section className="page"><PageHeader title="Forecast"/><div className="panel empty viewing-empty"><strong>No expenses yet.</strong><p>Your forecast will appear as you add expenses.</p><a className="button primary" href={`${import.meta.env.BASE_URL}add/`}>Add your first expense</a></div></section>;
  const active = data.forecasts.filter(forecast => forecast.items.length || data.months.some(month => month.currency === forecast.currency && month.count));
  return <>{(active.length ? active : [data.forecast]).map(forecast => <CurrencyForecast key={forecast.currency} forecast={forecast} mixed={active.length > 1}/>)}</>;
}
function CurrencyForecast({forecast, mixed}: {forecast: DashboardData['forecast']; mixed: boolean}) {
  const moneyWhole = (value: number) => formatWhole(value, forecast.currency);
  const axisMoney = (value: number, max: number) => formatAxis(value, max, forecast.currency);
  const months = data.months.filter(item => item.currency === forecast.currency);
  const currentMonth = forecast.currentMonth;
  const asOfDay = Number(data.meta.today.slice(8, 10));
  const hasCurrentRecords = data.transactions.some(row => row.currency === forecast.currency && row.month === currentMonth && row.date <= data.meta.today);
  const complete = months.filter((item) => item.month >= forecast.firstFullMonth && item.month < currentMonth);
  const earlier = complete.slice(-12).reverse();
  const forecastMonths = forecast.months.map((item) => item.month);
  // Every choice is kept in the address, so a reload or a bookmark shows the same chart.
  const [compareParam, setCompareParam] = useQueryState('average', String(Math.min(6, complete.length)), (value) => ['3', '6', '12'].includes(value) && Number(value) <= complete.length);
  const compare = Number(compareParam);
  const setCompare = (value: number) => setCompareParam(String(value));
  const [variableParam, setVariableParam] = useQueryState('variable', 'on', (value) => value === 'on' || value === 'off');
  const includeVariable = variableParam === 'on';
  const setIncludeVariable = (value: boolean) => setVariableParam(value ? 'on' : 'off');
  const [focus, setFocus] = useQueryState('focus', currentMonth, (value) => forecastMonths.includes(value));
  const latest = (count: number) => Object.fromEntries(earlier.slice(0, count).map((item, slot) => [item.month, slot]));
  const defaultCompared = earlier.slice(0, 3).map((item) => item.month).join(',');
  // The address lists months by color slot ("2026-09,,2026-07"), so a month keeps its color while selected.
  const [comparedParam, setComparedParam] = useQueryState('compare', defaultCompared, (value) => value === 'none' || (value.split(',').length <= 5 && value.split(',').every((month) => !month || earlier.some((item) => item.month === month))));
  const assigned: Record<string, number> = Object.fromEntries((comparedParam === 'none' ? '' : comparedParam).split(',').map((month, slot) => [month, slot] as const).filter(([month]) => month));
  const setAssigned = (update: Record<string, number> | ((current: Record<string, number>) => Record<string, number>)) => {
    const next = typeof update === 'function' ? update(assigned) : update;
    const slots = Array.from({ length: 5 }, () => '');
    for (const [month, slot] of Object.entries(next)) slots[slot] = month;
    while (slots.length && !slots.at(-1)) slots.pop();
    setComparedParam(slots.join(',') || 'none');
  };
  const [highlight, setHighlight] = useState<string | null>(null);
  const [hoverDay, setHoverDay] = useState<number | null>(null);
  const selectedDay = useRef(asOfDay);
  const [chartWidth, setChartWidth] = useState(1000);
  const chartRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = chartRef.current; if (!element) return;
    const observer = new ResizeObserver(([entry]) => setChartWidth(Math.max(1, entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const window = complete.slice(-compare);
  const average = mean(window.map((item) => item.total));
  // Variable spending is estimated per portion of the month from every full recorded month, and spread
  // evenly over that portion's days in the month being forecast (so a 30-day month keeps the full last portion).
  const { variable } = forecast;
  const averageVariable = variable.portions.reduce((sum, portion) => sum + portion.average, 0);
  const projection = forecastProjection(forecast, months, data.meta.today, focus, includeVariable);
  const {focusDays, isCurrent, remainingDays} = projection;
  const focusItems = projection.items.filter(item => item.kind !== 'Investment' || moduleSettings(data.meta.ledger).investments !== false);
  const recordedDaily = (month: string) => new Map((months.find((item) => item.month === month)?.daily || []).map((item) => [item.day, item.amount]));
  const {recorded: focusRecorded, expected: focusForecast, total: focusTotal, recordedTotal: focusRecordedTotal, committed: focusCommitted, variable: focusVariable} = projection;
  const focusRows = focusItems.slice().sort((a, b) => a.date.localeCompare(b.date));
  const stripTop = Math.max(0, ...variable.portions.map((portion) => portion.max));
  const stripLow = Math.min(0, ...variable.portions.map((portion) => portion.min));
  const stripPosition = (value: number) => `${(value - stripLow) / (stripTop - stripLow || 1) * 100}%`;

  const lines = earlier.map((item) => ({
    month: item.month,
    slot: assigned[item.month],
    color: item.month in assigned ? SLOTS[assigned[item.month]] : undefined,
    points: cumulative(recordedDaily(item.month), 0, daysInMonth(item.month)),
    total: item.total,
  }));
  const visibleLines = lines.filter((line) => line.month in assigned) as (typeof lines[number] & { color: string })[];
  const full = Object.keys(assigned).length >= SLOTS.length;
  const previousMonth = lines.find(line => line.month === calendarPreviousMonth(currentMonth));
  const previousAtDay = previousMonth?.points[Math.min(asOfDay, previousMonth.points.length - 1)]?.value || 0;

  const allValues = [...visibleLines.flatMap((line) => line.points.map((point) => point.value)), ...focusRecorded.map((point) => point.value), ...focusForecast.map((point) => point.value)];
  const step = 50000;
  const top = Math.max(step, Math.ceil(Math.max(...allValues) / step) * step);
  const low = Math.min(0, Math.floor(Math.min(...allValues) / step) * step);
  const ticks = Array.from({ length: (top - low) / step + 1 }, (_, index) => low + index * step).filter((tick, index, all) => all.length <= 7 || index % 2 === 0 || tick === 0);
  const compactChart = chartWidth < 520;
  const [W, H, left, right, topPad, bottom] = [chartWidth, 340, compactChart ? 46 : 60, compactChart ? 14 : 92, 14, 30];
  const dayTicks = chartWidth < 360 ? [1, 15, 31] : chartWidth < 600 ? [1, 8, 15, 22, 31] : [1, 8, 15, 22, 29, 31];
  const tooltipWidth = Math.min(300, W);
  const plotW = W - left - right;
  const plotH = H - topPad - bottom;
  const x = (day: number) => left + day / 31 * plotW;
  const y = (value: number) => topPad + plotH - (value - low) / (top - low) * plotH;
  const path = (points: Point[]) => points.map((point, index) => `${index ? 'L' : 'M'}${x(point.day).toFixed(1)},${y(point.value).toFixed(1)}`).join('');
  // Past a shorter month's last day, its month-end total carries forward.
  const valueAt = (points: Point[], day: number) => points.find((point) => point.day === day)?.value ?? (day > points.at(-1)!.day ? points.at(-1)!.value : undefined);
  const endNote = (points: Point[], day: number) => day > points.at(-1)!.day ? ` · month end (${points.at(-1)!.day} days)` : '';
  // Direct labels at line ends. Where two would collide they're pushed apart (at least 15 units)
  // rather than dropped, keeping clear of the projected total's two-line label.
  const reserved = { top: y(focusTotal) - 14, bottom: y(focusTotal) + 17 };
  const endLabels = visibleLines.map((line) => ({ month: line.month, x: x(line.points.at(-1)!.day) + 9, y: y(line.points.at(-1)!.value), text: monthAbbr(line.month) }))
    .sort((a, b) => a.y - b.y)
    .reduce<{ month: string; x: number; y: number; text: string }[]>((placed, label) => {
      let position = Math.max(label.y, (placed.at(-1)?.y ?? -Infinity) + 15);
      if (position > reserved.top - 8 && position < reserved.bottom + 8) position = label.y < y(focusTotal) ? Math.min(position, reserved.top - 8) : reserved.bottom + 8;
      if (placed.length && position - placed.at(-1)!.y < 15) position = placed.at(-1)!.y + 15;
      return [...placed, { ...label, y: position }];
    }, []);

  const toggle = (month: string) => setAssigned((current) => {
    const next = { ...current };
    if (month in next) delete next[month];
    else {
      const free = SLOTS.findIndex((_, slot) => !Object.values(next).includes(slot));
      if (free >= 0) next[month] = free;
    }
    return next;
  });
  const lineId = useCallback((line: { month: string }) => line.month, []);
  const toggles = useRowNavigation(visibleLines, lineId, null, useCallback(() => undefined, []), 1);
  const pointerDay = (event: PointerEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.ownerSVGElement!.getBoundingClientRect();
    const svgX = (event.clientX - bounds.left) / bounds.width * W;
    selectedDay.current = Math.max(1, Math.min(31, Math.round((svgX - left) / plotW * 31)));
    setHoverDay(selectedDay.current);
  };
  const keyDay = (event: KeyboardEvent<SVGRectElement>) => {
    if (event.key === 'Escape' && hoverDay !== null) {
      event.preventDefault();
      event.stopPropagation();
      setHoverDay(null);
      return;
    }
    const moves: Record<string, (day: number) => number> = { ArrowLeft: (day) => day - 1, ArrowRight: (day) => day + 1, Home: () => 1, End: () => 31 };
    if (!moves[event.key]) return;
    event.preventDefault();
    selectedDay.current = Math.max(1, Math.min(31, moves[event.key](selectedDay.current)));
    setHoverDay(selectedDay.current);
  };
  const hoverFocus = hoverDay === null ? undefined : hoverDay <= (isCurrent ? asOfDay : 0) ? valueAt(focusRecorded, hoverDay) : valueAt(focusForecast, hoverDay);

  const tableRows = [
    ...earlier.slice().reverse().map((item) => ({ month: item.month, phase: 'actual' as const, recorded: item.total, committed: 0, variable: 0 })),
    ...forecast.months.slice(0, Math.max(7, forecastMonths.indexOf(focus) + 1)).map((item) => {
      const current = item.month === currentMonth;
      return { month: item.month, phase: current ? 'current' as const : 'forecast' as const, recorded: current ? months.find((row) => row.month === currentMonth)?.total || 0 : 0, committed: item.committed, variable: includeVariable ? estimateAfter(forecast, item.month, current ? forecast.variableAfterDay : 0) : 0 };
    }),
  ].map((row) => ({ ...row, total: row.recorded + row.committed + row.variable }))
    .map((row, index, all) => ({ ...row, previousTotal: index ? all[index - 1].total : null }));
  // The table sorts by any figure; "vs previous" always compares with the calendar month before.
  type TableKey = 'month' | 'recorded' | 'committed' | 'variable' | 'total';
  const [tableSort, setTableSort] = useQueryState('table', 'month-asc', (value) => /^(month|recorded|committed|variable|total)-(asc|desc)$/.test(value));
  const [tableKey, tableDirection] = tableSort.split('-') as [TableKey, 'asc' | 'desc'];
  const sortedRows = tableKey === 'month' ? (tableDirection === 'asc' ? tableRows : [...tableRows].reverse())
    : [...tableRows].sort((a, b) => (a[tableKey] - b[tableKey]) * (tableDirection === 'asc' ? 1 : -1));
  const { widths, resizer } = useColumnWidths('ledger-forecast-columns-v2', { month: 128, recorded: 104, committed: 108, variable: 116, total: 104, previous: 96 });
  const tableHeading = (key: TableKey | null, label: string, width: string, numeric = true) => <th className={numeric ? 'th-num' : ''} aria-label={label} aria-sort={key && tableKey === key ? (tableDirection === 'asc' ? 'ascending' : 'descending') : key ? 'none' : undefined}>
    {key ? <button type="button" className="sort-button" title={`Sort by ${label.toLowerCase()}`} onClick={() => setTableSort(`${key}-${tableKey === key ? (tableDirection === 'asc' ? 'desc' : 'asc') : key === 'month' ? 'asc' : 'desc'}`)}>{label}<span className="sort-indicator" aria-hidden="true">{tableKey === key ? (tableDirection === 'asc' ? '▲' : '▼') : ''}</span></button> : label}
    {width && resizer(width, label)}</th>;


  return <section className="page forecast">
    <PageHeader title={`Forecast${mixed ? ` · ${forecast.currency}` : ''}`} subtitle="Cumulative expenses by day · amounts rounded to whole currency units.">
      <label className="compact-field"><span>Month</span><select className="field" value={focus} onChange={(event) => setFocus(event.target.value)}>{forecastMonths.map((month) => <option key={month} value={month}>{monthLong(month)}{month === currentMonth ? ' (current)' : ''}</option>)}</select></label>
      <label className="compact-field"><span>Comparison average</span><select className="field" value={compare} onChange={(event) => setCompare(Number(event.target.value))}>{[3, 6, 12].filter((n) => n <= complete.length).map((n) => <option key={n} value={n}>Last {n} full months</option>)}</select></label>
      <label className="checkbox"><input type="checkbox" checked={includeVariable} onChange={(event) => setIncludeVariable(event.target.checked)}/>Include estimated variable spending</label>
    </PageHeader>

    <section className="metrics" aria-label="Forecast summary">
      <div className="metric"><p className="metric-label">{monthLong(focus)} {isCurrent ? 'projected' : 'forecast'}</p><p className="metric-value">{moneyWhole(focusTotal)}</p><p className="metric-note"><Delta value={focusTotal} base={average} suffix={`vs ${compare}-month average`}/></p></div>
      {isCurrent ? <div className="metric"><p className="metric-label">Recorded through {dayShort(data.meta.today)}</p><p className="metric-value">{moneyWhole(focusRecordedTotal)}</p><p className="metric-note">{!hasCurrentRecords ? 'Nothing recorded this month yet' : previousMonth ? <Delta value={focusRecordedTotal} base={previousAtDay} suffix={`vs ${monthAbbr(previousMonth.month)} by day ${asOfDay}`}/> : 'No earlier month'}</p></div>
        : <div className="metric"><p className="metric-label">Committed in {monthShort(focus)}</p><p className="metric-value">{moneyWhole(focusCommitted)}</p><p className="metric-note">{plural(focusItems.length, 'forecast entry', 'forecast entries')}</p></div>}
      <div className="metric"><p className="metric-label">{compare}-month average</p><p className="metric-value">{moneyWhole(average)}</p><p className="metric-note">{window.length ? `${monthShort(window[0].month)} – ${monthShort(window.at(-1)!.month)}, recorded` : 'No full months yet'}</p></div>
      <div className="metric"><p className="metric-label">Variable spending still to come</p><p className="metric-value">{includeVariable ? moneyWhole(focusVariable) : 'Not included'}</p><p className="metric-note">Based on {variable.months.length} full months · {moneyWhole(averageVariable)} in a typical month</p></div>
    </section>

    <section className="panel forecast-chart-panel">
      <PanelHeader title={`${monthLong(focus)} against earlier months`}>
        <span className="legend-item"><svg width="22" height="6" aria-hidden="true"><path d="M1 3H21" style={{ stroke: FOCUS }} strokeWidth="2.5" strokeLinecap="round"/></svg>Recorded</span>
        <span className="legend-item"><svg width="22" height="6" aria-hidden="true"><path d="M1 3H21" style={{ stroke: FOCUS }} strokeWidth="2.5" strokeLinecap="round" strokeDasharray="0.1 5"/></svg>Forecast</span>
      </PanelHeader>
      <div className="forecast-compare-row">
        <span className="forecast-compare-label" id="compare-label">Compared with</span>
        <div className="month-chips" role="group" aria-labelledby="compare-label" onKeyDown={toggles.onKeyDown}>
          {visibleLines.map((line) => <button key={line.month} type="button" className="month-toggle" aria-pressed="true" {...toggles.rowProps(line)} aria-label={`${monthLong(line.month)}, shown. Remove from the chart`} title="Remove from the chart"
            onClick={() => toggle(line.month)} onPointerEnter={() => setHighlight(line.month)} onPointerLeave={() => setHighlight(null)} onFocus={() => setHighlight(line.month)} onBlur={() => setHighlight(null)}>
            <svg className="compare-key" data-compare-slot={line.slot} width="30" height="8" aria-hidden="true"><path d="M1 4H29" style={{ stroke: line.color }} strokeWidth="2"/></svg><span>{slotName(line.slot)} · {monthShort(line.month)}</span><Icon name="close"/></button>)}
          {!visibleLines.length && <span className="forecast-compare-note">No earlier months</span>}
        </div>
        <label className="compact-field"><span className="sr-only">Add a month to the chart</span><select className="field add-month" value="" disabled={full} title={full ? `Up to ${SLOTS.length} months at once. Remove one to add another.` : undefined} onChange={(event) => { if (event.target.value) toggle(event.target.value); }}>
          <option value="">{full ? `${SLOTS.length} months shown` : 'Add a month…'}</option>{lines.filter((line) => !line.color).map((line) => <option key={line.month} value={line.month}>{monthLong(line.month)}</option>)}</select></label>
        {visibleLines.length > 0 ? <button type="button" className="button plain" onClick={() => setAssigned({})}>Remove All</button> : <button type="button" className="button plain" onClick={() => setAssigned(latest(3))}>Show Latest 3</button>}
      </div>
      {compactChart && <p className="forecast-chart-caption"><strong>{moneyWhole(focusTotal)}</strong> · {monthShort(focus)} {isCurrent ? 'projected' : 'forecast'}</p>}
      <div className="forecast-chart" onPointerLeave={(event) => { if (event.pointerType !== 'touch') setHoverDay(null); }}><div className="forecast-chart-inner" ref={chartRef}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Cumulative expenses by day for ${monthLong(focus)}, ${isCurrent ? `recorded through day ${asOfDay}, then forecast` : 'forecast'} to ${moneyWhole(focusTotal)}, compared with ${visibleLines.length} earlier months. Monthly totals are in the table below. For daily values, Tab to Chart values by day and use the left and right arrow keys; Home and End select the first and last day. Escape hides the tooltip.`}>
          {ticks.map((tick) => <g key={tick}><path d={`M${left},${y(tick)}H${W - right}`} className="chart-grid"/><text x={left - 8} y={y(tick) + 4} textAnchor="end" className="chart-axis">{axisMoney(tick, top)}</text></g>)}
          {dayTicks.map((day) => <text key={day} x={x(day)} y={H - 10} textAnchor="middle" className="chart-axis">{day}</text>)}
          {visibleLines.slice().reverse().map((line) => <path key={line.month} className="comparison-line" data-compare-slot={line.slot} d={path(line.points)} fill="none" style={{ stroke: line.color }} strokeWidth={highlight === line.month ? 3 : 2} strokeLinejoin="round" strokeLinecap="round" opacity={highlight && highlight !== line.month ? 0.25 : 1}/>)}
          {visibleLines.map((line) => { const end = line.points.at(-1)!; return <circle key={line.month} cx={x(end.day)} cy={y(end.value)} r="4" style={{ fill: line.color, stroke: 'var(--surface)' }} strokeWidth="2" opacity={highlight && highlight !== line.month ? 0.25 : 1}/>; })}
          {endLabels.map((label) => <text key={label.month} x={compactChart ? label.x - 6 : label.x} y={label.y + 4} className="chart-label comparison-label">{compactChart ? slotName(assigned[label.month]) : `${slotName(assigned[label.month])} · ${label.text}`}</text>)}
          {isCurrent && <path d={path(focusRecorded)} fill="none" style={{ stroke: FOCUS }} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round"/>}
          <path d={path(focusForecast)} fill="none" style={{ stroke: FOCUS }} strokeWidth="2.5" strokeLinecap="round" strokeDasharray="0.1 6" className="forecast-dotted"/>
          {isCurrent && <circle cx={x(asOfDay)} cy={y(focusRecordedTotal)} r="4.5" style={{ fill: FOCUS, stroke: 'var(--surface)' }} strokeWidth="2"/>}
          <circle cx={x(focusDays)} cy={y(focusTotal)} r="4.5" style={{ fill: 'var(--surface)', stroke: FOCUS }} strokeWidth="2"/>
          {!compactChart && <><text x={x(focusDays) + 9} y={y(focusTotal) - 2} className="chart-label strong">{axisMoney(Math.round(focusTotal / 1000) * 1000, focusTotal)}</text>
          <text x={x(focusDays) + 9} y={y(focusTotal) + 13} className="chart-axis">{monthAbbr(focus)} {isCurrent ? 'projected' : 'forecast'}</text></>}
          {hoverDay !== null && <path d={`M${x(hoverDay)},${topPad}V${topPad + plotH}`} className="chart-crosshair"/>}
          <rect x={left} y={topPad} width={plotW} height={plotH} fill="transparent" tabIndex={0} className="chart-focus" aria-label="Chart values by day. Use the left and right arrow keys to move between days, Home and End for the first and last day, and Escape to hide the tooltip."
            onPointerMove={pointerDay} onPointerDown={(event) => { event.currentTarget.focus({ preventScroll: true }); pointerDay(event); }}
            onFocus={() => setHoverDay((day) => day ?? selectedDay.current)} onBlur={() => setHoverDay(null)} onKeyDown={keyDay}/>
        </svg>
        {hoverDay !== null && <div className="chart-tooltip" style={{ left: Math.max(0, Math.min(x(hoverDay) + 12, W - tooltipWidth)), width: tooltipWidth }} role="status">
          <p className="chart-tooltip-title">Day {hoverDay}, cumulative</p>
          {hoverFocus !== undefined && <p><svg width="14" height="4" aria-hidden="true"><path d="M1 2H13" style={{ stroke: FOCUS }} strokeWidth="2.5" strokeLinecap="round" strokeDasharray={isCurrent && hoverDay <= asOfDay ? undefined : '0.1 4'}/></svg><strong>{moneyWhole(hoverFocus)}</strong><span>{monthShort(focus)} {isCurrent && hoverDay <= asOfDay ? 'recorded' : 'forecast'}{endNote(focusForecast, hoverDay)}</span></p>}
          {isCurrent && hoverDay === asOfDay && focusForecast[0].value !== focusRecordedTotal && <p><svg width="14" height="4" aria-hidden="true"><path d="M1 2H13" style={{stroke: FOCUS}} strokeWidth="2.5" strokeDasharray="0.1 4"/></svg><strong>{moneyWhole(focusForecast[0].value)}</strong><span>Expected at today</span></p>}
          {visibleLines.map((line) => { const value = valueAt(line.points, hoverDay); return value === undefined ? null : <p key={line.month}><svg className="compare-key" data-compare-slot={line.slot} width="30" height="8" aria-hidden="true"><path d="M1 4H29" style={{ stroke: line.color }} strokeWidth="2" strokeLinecap="round"/></svg><strong>{moneyWhole(value)}</strong><span>{slotName(line.slot)} · {monthShort(line.month)}{endNote(line.points, hoverDay)}</span></p>; })}
        </div>}
      </div></div>
      <p className="panel-note">The solid line is recorded spending. The dotted line adds the recurring commitments the ledger schedules (rent, subscriptions, insurance) on their due dates, with foreign-currency commitments at their recorded planning cost{includeVariable ? <>, and estimated variable spending: what earlier months spent outside those commitments, week by week ({moneyWhole(averageVariable)} a month on average across {variable.months.length} full months)</> : '. Variable spending is not included, so the line rises only on commitment dates'}. Overdue expected payments are added at today. {includeVariable && 'Today’s variable estimate remains until an entry dated today is recorded. '}{moduleSettings(data.meta.ledger).investments !== false && 'Investments are left out.'}</p>
    </section>

    <div className="forecast-detail-grid">
      <section className="panel forecast-month">
        <PanelHeader title={isCurrent ? `Rest of ${monthLong(focus)}` : monthLong(focus)} meta={isCurrent ? 'Recorded so far, then what is still expected' : 'What is expected this month'}><span className="summary-total">{moneyWhole(focusTotal)}</span></PanelHeader>
        <dl className="facts panel-body forecast-breakdown">
          {isCurrent && <><dt>Recorded so far</dt><dd className="num">{moneyWhole(focusRecordedTotal)}</dd></>}
          <dt>Commitments</dt><dd className="num">{moneyWhole(focusCommitted)}</dd>
          {includeVariable && <><dt>Estimated variable</dt><dd className="num">{moneyWhole(focusVariable)}</dd></>}
          <dt>Against the {compare}-month average</dt><dd className="num"><Delta value={focusTotal} base={average}/></dd>
        </dl>
        <ol className="ranked-list forecast-items">{focusRows.map((item, index) =>
          <li key={`${item.date}-${index}`} className={item.kind === 'Investment' ? 'excluded' : ''}><span className="date">{dayShort(item.date)}</span><span><span className="row-title">{item.description}{item.varying?.bill&&<> <span className="bill-badge">bill</span></>}</span><span className="row-sub">{item.kind === 'Investment' ? 'Investment, not counted as spending' : `${item.category} · ${item.paymentLabel}`}</span></span><span className="num">{item.varying&&!item.varying.bill?'≈ ':''}{item.varying?money(item.amount,forecast.currency):moneyWhole(item.amount)}{item.varying&&!item.varying.bill&&item.varying.min!=null&&<span className="row-sub">{money(Number(item.varying.min),forecast.currency)}–{money(Number(item.varying.max),forecast.currency)} last 3</span>}</span></li>)}
          {includeVariable && <li className="estimate"><span className="date">{monthAbbr(focus)}</span><span><span className="row-title">Estimated variable spending</span><span className="row-sub">{!isCurrent || remainingDays === focusDays ? 'The whole month' : `The ${remainingDays} days left`} · {variable.months.length}-month average {moneyWhole(averageVariable)} a month</span></span><span className="num">{moneyWhole(focusVariable)}</span></li>}
        </ol>
        {!focusItems.length && <p className="panel-note">No recurring commitments fall in this month.</p>}
      </section>

      <section className="panel forecast-table-panel">
        <PanelHeader title="Month by month" meta="Tick a recorded month to draw it on the chart; choose Show to chart a later month’s forecast"/>
        <div className="table-scroll"><table className="data-table forecast-table">
          <colgroup><col style={{ width: widths.month }}/><col style={{ width: widths.recorded }}/><col style={{ width: widths.committed }}/><col style={{ width: widths.variable }}/><col style={{ width: widths.total }}/><col style={{ width: widths.previous }}/><col/></colgroup>
          <thead><tr>{tableHeading('month', 'Month', 'month', false)}{tableHeading('recorded', 'Recorded', 'recorded')}{tableHeading('committed', 'Commitments', 'committed')}{tableHeading('variable', 'Variable Estimate', 'variable')}{tableHeading('total', 'Total', 'total')}{tableHeading(null, 'vs Previous', 'previous')}{tableHeading(null, 'vs Average', '')}</tr></thead>
          <tbody>{sortedRows.map((row) => <tr key={row.month} className={row.phase} data-selected={row.month === focus || row.month in assigned}>
            <td>{row.phase === 'actual'
              ? <label className="month-check" title={!(row.month in assigned) && full ? `Up to ${SLOTS.length} months at once` : undefined}><input type="checkbox" checked={row.month in assigned} disabled={!(row.month in assigned) && full} onChange={() => toggle(row.month)}/><span className="row-title">{monthShort(row.month)}</span>{row.month in assigned && <i className="swatch" style={{ background: SLOTS[assigned[row.month]] }}/>}</label>
              : <span className="month-forecast"><span><span className="row-title">{monthShort(row.month)}</span><span className="row-sub">{row.phase === 'current' ? 'In progress' : 'Forecast'}</span></span>{row.month === focus ? <span className="row-sub">Charted</span> : <button type="button" className="button plain" aria-label={`Show the ${monthLong(row.month)} forecast on the chart`} onClick={() => setFocus(row.month)}>Show</button>}</span>}</td>
            <td className="num">{row.recorded ? moneyWhole(row.recorded) : '—'}</td><td className="num">{row.committed ? moneyWhole(row.committed) : '—'}</td><td className="num">{row.variable ? moneyWhole(row.variable) : '—'}</td>
            <td className="num"><strong>{moneyWhole(row.total)}</strong></td><td className="num">{row.previousTotal !== null ? <Delta value={row.total} base={row.previousTotal}/> : '—'}</td><td className="num"><Delta value={row.total} base={average}/></td>
          </tr>)}</tbody></table></div>
      </section>
    </div>

    <section className="panel forecast-variable">
      <PanelHeader title="How the variable estimate is built" meta={variable.months.length ? `Spending outside recurring commitments, by week of the month, ${monthShort(variable.months[0])} – ${monthShort(variable.months.at(-1)!)}` : 'No full recorded months yet'}><span className="summary-total">{moneyWhole(averageVariable)} a month</span></PanelHeader>
      <div className="table-scroll"><table className="data-table forecast-variable-table">
        <thead><tr><th>Days</th><th><span className="sr-only">Each month's spending in these days</span><span aria-hidden="true">Each dot is one month · <i className="forecast-variable-key"/> average</span></th><th className="th-num">Average</th><th className="th-num">Median</th><th className="th-num">Lowest – Highest</th><th className="th-num">Share</th></tr></thead>
        <tbody>{variable.portions.map((portion) => <tr key={portion.from}>
          <td>{portionLabel(portion)}{portion.to === 31 && <span className="row-sub">to month end</span>}</td>
          <td><div className="forecast-strip" role="img" aria-label={`Days ${portionLabel(portion)}: ${variable.months.length} months from ${moneyWhole(portion.min)} to ${moneyWhole(portion.max)}, average ${moneyWhole(portion.average)}`}>
            {portion.byMonth.map((value, index) => <i key={variable.months[index]} className={value < 0 ? 'net-refund' : undefined} style={{ left: stripPosition(value) }} title={`${monthShort(variable.months[index])}: ${moneyWhole(value)}`}/>)}
            <b style={{ left: stripPosition(portion.average) }}/>
          </div></td>
          <td className="num"><strong>{moneyWhole(portion.average)}</strong></td><td className="num">{moneyWhole(portion.median)}</td><td className="num">{moneyWhole(portion.min)} – {moneyWhole(portion.max)}</td><td className="num">{averageVariable ? `${(portion.average / averageVariable * 100).toFixed(0)}%` : '—'}</td>
        </tr>)}</tbody>
      </table></div>
      <p className="panel-note">Each week’s average comes from all {variable.months.length} full months and is spread evenly over that week’s days in the forecast month; the last portion runs to the month’s end. Occasional large purchases raise the average, so most months come in below it (compare the median).</p>
      {variable.excluded.length > 0 && <section className="excluded"><h3>Left out of the estimate</h3><p>Card instalment plans, and entries the ledger tags <code>variable-estimate: excluded</code>.</p>
        <div className="table-scroll"><table className="data-table"><thead><tr><th>Entry</th><th>Reason</th><th>Months</th><th className="th-num">Amount</th></tr></thead>
          <tbody>{variable.excluded.map((plan) => <tr key={plan.description}><td>{plan.description}</td><td>{plan.reason.charAt(0).toUpperCase() + plan.reason.slice(1)}</td><td className="date">{plan.from === plan.to ? monthShort(plan.from) : `${monthShort(plan.from)} – ${monthShort(plan.to)}`}</td><td className="num">{moneyWhole(plan.amount)}</td></tr>)}</tbody></table></div></section>}
    </section>
  </section>;
}
