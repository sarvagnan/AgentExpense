import type { Accounts } from './account-metadata';
import { currentAccounts as accountMetadata } from '../scripts/account-metadata.mjs';
import { accountLabel } from '../scripts/account-metadata.mjs';
/** Compact list labels only. Full account paths remain in data, search and details. */
export function displayAccountLabel(account: string, metadata: Accounts = accountMetadata()) {
  if (metadata[account]?.kind) return accountLabel(account, metadata);
  return account.split(/:| · /).slice(-2).join(' · ');
}

type Posting = { account: string; amount: number; label: string };
/** Combine repeated item postings before choosing the largest account on a side. */
export function summarizeAccounts(postings: Posting[], metadata: Accounts = accountMetadata()) {
  const amounts = new Map<string, number>();
  for (const posting of postings) amounts.set(posting.account, (amounts.get(posting.account) || 0) + Math.abs(posting.amount));
  const accounts = [...amounts].sort((a, b) => b[1] - a[1]).map(([account]) => account);
  return {
    short: accounts.length ? `${displayAccountLabel(accounts[0], metadata)}${accounts.length > 1 ? ` + ${accounts.length - 1} more` : ''}` : '—',
    full: accounts.join(' + ') || '—',
  };
}
