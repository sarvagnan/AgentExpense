import {isCurrency} from '../scripts/formatting.mjs';
import type { Accounts } from './account-metadata';
import { currentAccounts as accountMetadata } from '../scripts/account-metadata.mjs';
import { groupNames } from '../scripts/account-metadata.mjs';
export type Posting = { account: string; currency: string; amount: number; cost?: number; costCurrency?: string };
export type Entry = { id: string; date: string; sequence: number; description: string; postings: Posting[] };
export type PriceSeries = { commodity: string; currency?: string; dates: string[]; navs: number[] };
export const groups = Object.values(groupNames);
export function accountGroup(account: string, metadata: Accounts = accountMetadata()) {
  return groupNames[metadata[account]?.kind as keyof typeof groupNames] || 'Other accounts';
}
/** Mutual fund units are a ledger commodity such as DEMOFUND, not an ISO currency code. */
export const isFund = (currency: string) => !isCurrency(currency);
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
 * An account's entries with the running balance. For fund units, `cost` is the account-currency cost of the units
 * still held: a purchase adds what it cost, and a sale removes its share of the average cost.
 */
export function accountActivity<T extends Entry>(entries: T[], account: string, currency: string, asOf: string) {
  const factor = scale(currency);
  let held = 0;
  let paise = 0;
  const declaredCurrency = accountMetadata()[account]?.currency;
  const costCurrency = (isCurrency(declaredCurrency) ? declaredCurrency : undefined) || entries.flatMap(entry => entry.postings).find(posting => posting.account === account && posting.costCurrency)?.costCurrency;
  const costs: Record<string, number> = {};
  const events = entries.filter(entry => entry.date <= asOf).map(entry => ({
    entry, date: entry.date,
    postings: entry.postings.filter(posting => posting.account === account && posting.currency === currency),
  })).filter(event => event.postings.length)
    .sort((a, b) => a.date.localeCompare(b.date) || a.entry.sequence - b.entry.sequence);
  return events.map(({ entry, date, postings }) => {
    const before = { held, paise, costs: {...costs} };
    for (const posting of postings) {
      if (posting.amount === 0) continue;
      const change = Math.round(posting.amount * factor);
      const code = posting.costCurrency || costCurrency || currency;
      if (change >= 0) costs[code] = (costs[code] || 0) + Math.round((posting.cost || 0) * 100);
      else if (held) for (const key of Object.keys(costs)) costs[key] += Math.round(costs[key] * change / held);
      paise = costs[costCurrency || code] || 0;
      held += change;
    }
    return { entry, date, change: (held - before.held) / factor, balance: held / factor, costChange: (paise - before.paise) / 100, cost: paise / 100, costCurrency, costs: Object.fromEntries(Object.entries(costs).map(([code, amount]) => [code, amount / 100])), costChanges: Object.fromEntries(Object.entries(costs).map(([code, amount]) => [code, (amount - (before.costs[code] || 0)) / 100])) };
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
  return found < 0 ? null : { currency: series.currency, date: series.dates[found], nav: series.navs[found] };
}
// Statements value a holding to the smallest currency unit, units times NAV.
export const marketValue = (units: number, nav: number) => Math.round(units * nav * 100) / 100;
/**
 * Market value and cost of fund holdings on each NAV date from start to asOf, plus both ends.
 * Each holding is its activity (from accountActivity) and the fund's prices.
 */
export function valueSeries(holdings: { activity: { entry: { date: string }; balance: number; cost: number }[]; prices: PriceSeries | undefined }[], start: string, asOf: string) {
  const currencies = new Set(holdings.map(item => item.prices?.currency).filter(Boolean));
  if (currencies.size > 1) throw new Error('Value series must contain one quote currency');
  const days = [...new Set([start, ...holdings.flatMap(({ prices }) => prices?.dates.filter(day => day > start && day < asOf) || []), asOf])].sort();
  return days.map(day => holdings.reduce((point, { activity, prices }) => {
    const row = activity.filter(item => item.entry.date <= day).at(-1);
    const price = priceOn(prices, day);
    return { day, value: point.value + (row && price ? marketValue(row.balance, price.nav) : 0), cost: point.cost + (row?.cost || 0) };
  }, { day, value: 0, cost: 0 }));
}
