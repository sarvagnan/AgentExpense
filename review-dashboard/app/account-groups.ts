import { minorUnits, isCurrency } from '../scripts/formatting.mjs';
import { groupNames } from '../scripts/account-metadata.mjs';
import type { Entry } from './account-balances';
import type { Accounts } from './account-metadata';

/**
 * One kind of account seen together: the running total from a period's start to the as-of
 * date, a row of figures per account and the combined activity. Every sum is in whole minor
 * units of its currency (the schema's minor-unit table), and currencies are never added.
 */
export type ConfirmedBalance = { account: string; date: string; amount: string; currency: string; entryId: string };
export type CardStatement = { account: string; start: string; end: string; totalDue: string; currency: string };
export type GroupRow = {
  key: string; account: string; currency: string;
  /** In the group's own sign: cards and loans owed are positive. */
  opening: number; balance: number;
  /** Money into and out of the account in the period, as positive ledger amounts. */
  inflow: number; outflow: number;
  lastConfirmed: { date: string; amount: number } | null;
  lastStatement: { end: string; totalDue: number } | null;
  /** The last payment from the account (Prepaid, Cash) on or before the as-of date. */
  lastUsed: string | null;
  /** The last day a balance returned to zero (People, Loans). */
  lastSettled: string | null;
};
export type GroupActivity<T extends Entry = Entry> = {
  entry: T; date: string;
  /** The entry's change to the group, per currency, in the group's sign. */
  changes: Record<string, number>;
  accounts: string[];
  /** Every posting is in the group and each currency nets to zero: a transfer within it. */
  internal: boolean;
};
export type GroupTotal = { currency: string; opening: number; closing: number; increase: number; decrease: number; points: { date: string; total: number }[] };

const scale = (currency: string) => 10 ** (isCurrency(currency) ? minorUnits(currency) : 3);
export const toMinor = (amount: number | string, currency: string) => Math.round(Number(amount) * scale(currency));
export const fromMinor = (value: number, currency: string) => value / scale(currency) || 0;
/** Cards, and loans you owe, read as the amount owed. */
export const groupSign = (group: string, account: string) => group === 'Credit cards' || (group === 'Loans' && account.startsWith('Liabilities:')) ? -1 : 1;
/** An entry belongs to the group's activity only through postings that move money. */
const moving = (amount: number) => amount !== 0;

export function groupView<T extends Entry>({ entries, group, start, asOf, metadata, confirmedBalances = [], cardStatements = [] }: {
  entries: readonly T[]; group: string; start: string; asOf: string; metadata: Accounts;
  confirmedBalances?: readonly ConfirmedBalance[]; cardStatements?: readonly CardStatement[];
}) {
  const inGroup = new Map<string, boolean>();
  const member = (account: string) => {
    if (!inGroup.has(account)) inGroup.set(account, !/^(Expenses|Income|Equity):/.test(account) && (groupNames[metadata[account]?.kind as keyof typeof groupNames] || 'Other accounts') === group);
    return inGroup.get(account)!;
  };
  const ordered = entries.filter(entry => entry.date <= asOf).sort((a, b) => a.date.localeCompare(b.date) || a.sequence - b.sequence);
  type Running = { key: string; account: string; currency: string; opening: number; balance: number; inflow: number; outflow: number; lastUsed: string | null; lastSettled: string | null };
  const rows = new Map<string, Running>();
  const totals = new Map<string, { opening: number; balance: number; increase: number; decrease: number; points: { date: string; total: number }[] }>();
  const activity: GroupActivity<T>[] = [];
  for (const entry of ordered) {
    const inside = entry.postings.filter(posting => member(posting.account));
    if (!inside.length) continue;
    const period = entry.date >= start;
    const changes: Record<string, number> = {};
    for (const posting of inside) {
      const key = `${posting.account}\0${posting.currency}`;
      const row = rows.get(key) || { key, account: posting.account, currency: posting.currency, opening: 0, balance: 0, inflow: 0, outflow: 0, lastUsed: null, lastSettled: null };
      const amount = toMinor(posting.amount, posting.currency);
      const before = row.balance;
      row.balance += amount;
      if (!period) row.opening = row.balance;
      else if (amount > 0) row.inflow += amount;
      else row.outflow -= amount;
      if (amount < 0) row.lastUsed = entry.date;
      if (moving(amount) && before !== 0 && row.balance === 0) row.lastSettled = entry.date;
      rows.set(key, row);
      changes[posting.currency] = (changes[posting.currency] || 0) + groupSign(group, posting.account) * amount;
    }
    for (const [currency, change] of Object.entries(changes)) {
      const total = totals.get(currency) || { opening: 0, balance: 0, increase: 0, decrease: 0, points: [] };
      total.balance += change;
      if (!period) total.opening = total.balance;
      else if (change > 0) total.increase += change;
      else total.decrease -= change;
      if (period) {
        const last = total.points.at(-1);
        if (last?.date === entry.date) last.total = total.balance; else total.points.push({ date: entry.date, total: total.balance });
      }
      totals.set(currency, total);
    }
    if (!period || !inside.some(posting => moving(posting.amount))) continue;
    const internal = inside.length === entry.postings.length && Object.values(changes).every(value => value === 0)
      && new Set(inside.map(posting => posting.account)).size > 1;
    activity.push({ entry, date: entry.date, changes: Object.fromEntries(Object.entries(changes).map(([currency, value]) => [currency, fromMinor(value, currency)])), accounts: [...new Set(inside.map(posting => posting.account))], internal });
  }
  const latest = <F extends { account: string; currency: string }>(facts: readonly F[], row: Running, date: (fact: F) => string) =>
    facts.filter(fact => fact.account === row.account && fact.currency === row.currency && date(fact) <= asOf).reduce<F | null>((last, fact) => !last || date(fact) >= date(last) ? fact : last, null);
  const accounts: GroupRow[] = [...rows.values()].sort((a, b) => a.account.localeCompare(b.account) || a.currency.localeCompare(b.currency)).map(row => {
    const sign = groupSign(group, row.account);
    const confirmed = latest(confirmedBalances, row, fact => fact.date);
    const statement = latest(cardStatements, row, fact => fact.end);
    return {
      key: row.key, account: row.account, currency: row.currency,
      opening: fromMinor(sign * row.opening, row.currency), balance: fromMinor(sign * row.balance, row.currency),
      inflow: fromMinor(row.inflow, row.currency), outflow: fromMinor(row.outflow, row.currency),
      lastConfirmed: confirmed ? { date: confirmed.date, amount: fromMinor(sign * toMinor(confirmed.amount, row.currency), row.currency) } : null,
      lastStatement: statement ? { end: statement.end, totalDue: fromMinor(toMinor(statement.totalDue, row.currency), row.currency) } : null,
      lastUsed: row.lastUsed, lastSettled: row.lastSettled,
    };
  });
  const groupTotals: GroupTotal[] = [...totals.entries()].map(([currency, total]) => ({
    currency, opening: fromMinor(total.opening, currency), closing: fromMinor(total.balance, currency),
    increase: fromMinor(total.increase, currency), decrease: fromMinor(total.decrease, currency),
    // A step chart: the opening total at the start, each day's closing total, then the as-of date.
    points: [{ date: start, total: fromMinor(total.opening, currency) }, ...total.points.map(point => ({ date: point.date, total: fromMinor(point.total, currency) })), { date: asOf, total: fromMinor(total.balance, currency) }],
  }));
  return { group, totals: groupTotals, accounts, activity: activity.reverse() };
}
