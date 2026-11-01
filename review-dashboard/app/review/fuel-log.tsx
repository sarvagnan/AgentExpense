import {useState,useRef,useEffect} from 'react';
import {dashboard as dashboardData,fuel as fuelData} from '../data';
import {fuelLedgerEntry} from '../fuel-links';
import {useColumnWidths,Icon,dayLabel,money} from '../ui';
type FuelEntry = (typeof fuelData.entries)[number];
type FuelSort = 'date' | 'cost' | 'litres' | 'tripKm';
/** The fuel log, as a panel that stays open beside the entries while you classify fuel charges. */
export default function FuelLog({ onClose }: { onClose: () => void }) {
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

