import type { Accounts } from './account-metadata';
import { currentAccounts as accountMetadata } from '../scripts/account-metadata.mjs';
import { groupNames } from '../scripts/account-metadata.mjs';
export type Posting = { account: string; currency: string; amount: number; cost?: number };
export type Entry = { id: string; date: string; sequence: number; description: string; postings: Posting[] };
export type PriceSeries = { commodity: string; dates: string[]; navs: number[] };
export const groups = Object.values(groupNames);
export function accountGroup(account: string, metadata: Accounts = accountMetadata()) {
  return groupNames[metadata[account]?.kind as keyof typeof groupNames] || 'Other accounts';
}
/** Mutual fund units are a ledger commodity such as DEMOFUND, not an ISO currency code. */
export const isFund = (currency: string) => !/^[A-Z]{3}$/.test(currency);
// Balances are summed in whole hundredths of a currency, or thousandths of a fund unit, to avoid float drift.
const scale = (currency: string) => isFund(currency) ? 1000 : 100;
export function balanceAccounts(entries: Entry[], asOf: string, metadata: Accounts = accountMetadata()) {
  const accounts = new Map<string, { key: string; account: string; currency: string; balance: number; group: string }>();
  for (const entry of entries) {
    for (const posting of entry.postings) {
      if (entry.date > asOf) continue;
      if (/^(Expenses|Income|Equity):/.test(posting.account)) continue;
      const key = `${posting.account}\0${posting.currency}`;
      const value = accounts.get(key) || { key, account: posting.account, currency: posting.currency, balance: 0, group: accountGroup(posting.account, metadata) };
      value.balance += Math.round(posting.amount * scale(posting.currency));
      accounts.set(key, value);
    }
  }
  return [...accounts.values()].map(value => ({ ...value, balance: value.balance / scale(value.currency) })).sort((a, b) => a.account.localeCompare(b.account) || a.currency.localeCompare(b.currency));
}
/**
 * An account's entries with the running balance. For fund units, `cost` is the INR cost of the units
 * still held: a purchase adds what it cost, and a sale removes its share of the average cost.
 */
export function accountActivity<T extends Entry>(entries: T[], account: string, currency: string, asOf: string) {
  const factor = scale(currency);
  let held = 0;
  let paise = 0;
  const events = entries.filter(entry => entry.date <= asOf).map(entry => ({
    entry, date: entry.date,
    postings: entry.postings.filter(posting => posting.account === account && posting.currency === currency),
  })).filter(event => event.postings.length)
    .sort((a, b) => a.date.localeCompare(b.date) || a.entry.sequence - b.entry.sequence);
  return events.map(({ entry, date, postings }) => {
    const before = { held, paise };
    for (const posting of postings) {
      const change = Math.round(posting.amount * factor);
      paise += change >= 0 ? Math.round((posting.cost || 0) * 100) : held ? Math.round(paise * change / held) : 0;
      held += change;
    }
    return { entry, date, change: (held - before.held) / factor, balance: held / factor, costChange: (paise - before.paise) / 100, cost: paise / 100 };
  });
}
/** The latest NAV on or before a day. */
export function priceOn(series: PriceSeries | undefined, day: string) {
  if (!series) return null;
  let low = 0;
  let high = series.dates.length - 1;
  let found = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (series.dates[middle] <= day) { found = middle; low = middle + 1; } else high = middle - 1;
  }
  return found < 0 ? null : { date: series.dates[found], nav: series.navs[found] };
}
// Statements value a holding to the paisa, units times NAV.
export const marketValue = (units: number, nav: number) => Math.round(units * nav * 100) / 100;
/**
 * Market value and cost of fund holdings on each NAV date from start to asOf, plus both ends.
 * Each holding is its activity (from accountActivity) and the fund's prices.
 */
export function valueSeries(holdings: { activity: { entry: { date: string }; balance: number; cost: number }[]; prices: PriceSeries | undefined }[], start: string, asOf: string) {
  const days = [...new Set([start, ...holdings.flatMap(({ prices }) => prices?.dates.filter(day => day > start && day < asOf) || []), asOf])].sort();
  return days.map(day => holdings.reduce((point, { activity, prices }) => {
    const row = activity.filter(item => item.entry.date <= day).at(-1);
    const price = priceOn(prices, day);
    return { day, value: point.value + (row && price ? marketValue(row.balance, price.nav) : 0), cost: point.cost + (row?.cost || 0) };
  }, { day, value: 0, cost: 0 }));
}
