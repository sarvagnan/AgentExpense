import { summarizeAccounts } from './account-labels';

type Posting = { account: string; amount: number; label: string };
type PaymentEntry = { postings: Posting[]; displayType: string };
const personal = (account: string) => /(^|:)Accounts (Payable|Receivable):/.test(account);
const paymentAccount = (account: string) => /^(Assets|Liabilities):/.test(account) || personal(account);
const settlement = (account: string) => personal(account) || account.startsWith('Assets:Reimbursements:');
const wallet = (account: string) => paymentAccount(account) && !settlement(account) && !account.startsWith('Assets:Investments:');

/** Money accounts only. Prefer the paying wallet, or name the receiving wallet
 * for income, refunds and repayments. Settlement accounts remain valid funding. */
export function paymentSide(item: PaymentEntry) {
  if (item.displayType === 'Balance adjustment') return { short: '—', full: '—' };
  const eligible = item.postings.filter(p => paymentAccount(p.account));
  const paying = eligible.filter(p => wallet(p.account) && p.amount < 0);
  const receiving = eligible.filter(p => wallet(p.account) && p.amount > 0);
  const outgoing = paying.length || !receiving.length ? eligible.filter(p => p.amount < 0) : [];
  const incoming = !outgoing.length;
  const summary = summarizeAccounts(incoming ? (receiving.length ? receiving : eligible.filter(p => p.amount > 0)) : outgoing);
  return incoming && summary.short !== '—'
    ? { short: `Into ${summary.short}`, full: `Into ${summary.full}` }
    : summary;
}
