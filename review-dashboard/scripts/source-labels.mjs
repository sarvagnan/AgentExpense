/** Evidence labels come from explicit membership and source fields. */
export function sourceKind(transaction) {
  const source = transaction.sources?.[0] || '';
  if (transaction.statement?.end) return 'Statement';
  if (transaction.statement?.['source-row']) return 'Bank import';
  if (/^Bills\/.*\.md$/i.test(source) || /user-(?:provided|confirmed)/i.test(source)) return 'User confirmed';
  if (source.startsWith('Bills/')) return 'Receipt';
  return 'Derived';
}

/** Card membership takes priority; bank labels follow the actual paying account. */
export function sourceType(transaction, accounts = {}) {
  const cards = transaction.postings.filter(p => accounts[p.account]?.kind === 'card');
  if (cards.length) return accounts[cards[0].account].label || 'Card';
  const banks = transaction.postings.filter(p => accounts[p.account]?.kind === 'bank');
  const payment = banks.find(p => p.amount < 0) || banks[0];
  return payment ? accounts[payment.account].label || payment.account.split(':').at(-1) : 'Derived';
}
