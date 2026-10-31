import type { Accounts } from './account-metadata';
import { currentAccounts as accountMetadata } from '../scripts/account-metadata.mjs';
import { summarizeAccounts } from './account-labels';

type Posting = { account: string; amount: number; label: string };
type PaymentEntry = { postings: Posting[]; displayType: string };
/** Money accounts only. Prefer the paying wallet, or name the receiving wallet
 * for income, refunds and repayments. Settlement accounts remain valid funding. */
export function paymentSide(item: PaymentEntry, metadata: Accounts = accountMetadata()) {
  if (item.displayType === 'Balance adjustment') return { short: '—', full: '—' };
  const eligible = item.postings.filter(p => !!metadata[p.account]?.kind);
  const paying = eligible.filter(p => !['person', 'investment'].includes(metadata[p.account]?.kind || '') && p.amount < 0);
  const receiving = eligible.filter(p => !['person', 'investment'].includes(metadata[p.account]?.kind || '') && p.amount > 0);
  const outgoing = paying.length || !receiving.length ? eligible.filter(p => p.amount < 0) : [];
  const incoming = !outgoing.length;
  const summary = summarizeAccounts(incoming ? (receiving.length ? receiving : eligible.filter(p => p.amount > 0)) : outgoing, metadata);
  return incoming && summary.short !== '—'
    ? { short: `Into ${summary.short}`, full: `Into ${summary.full}` }
    : summary;
}
