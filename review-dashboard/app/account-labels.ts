/** Compact list labels only. Full account paths remain in data, search and details. */
export function displayAccountLabel(account: string) {
  const structural = new Set(['Assets', 'Liabilities', 'Expenses', 'Income', 'Equity', 'Bank', 'Credit Cards', 'Accounts Payable', 'Accounts Receivable', 'Investments', 'Mutual Funds']);
  const segments = account.split(/:| · /);
  // Personal payables/receivables put the owner immediately after the relationship.
  const parts = segments.filter((part, index) => !structural.has(part)
    && !['Accounts Payable', 'Accounts Receivable'].includes(segments[index - 1]));
  // Category already has its own line/column; keep merchant and item on expense sides.
  const glance = /^(Expenses|Income|Equity)(:| · )/.test(account) ? parts.slice(-2) : parts;
  return glance.join(' · ') || account.split(/:| · /).at(-1) || account;
}

type Posting = { account: string; amount: number; label: string };
/** Combine repeated item postings before choosing the largest account on a side. */
export function summarizeAccounts(postings: Posting[]) {
  const amounts = new Map<string, number>();
  for (const posting of postings) amounts.set(posting.account, (amounts.get(posting.account) || 0) + Math.abs(posting.amount));
  const accounts = [...amounts].sort((a, b) => b[1] - a[1]).map(([account]) => account);
  return {
    short: accounts.length ? `${displayAccountLabel(accounts[0])}${accounts.length > 1 ? ` + ${accounts.length - 1} more` : ''}` : '—',
    full: accounts.join(' + ') || '—',
  };
}
