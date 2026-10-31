/** Evidence labels come from explicit membership and source fields. */
export function sourceKind(transaction) {
  const source = transaction.sources?.[0] || '';
  if (transaction['statement-end']) return 'Statement';
  if (transaction['source-row']) return 'Bank import';
  if (/^Bills\/.*\.md$/i.test(source) || /user-(?:provided|confirmed)/i.test(source)) return 'User confirmed';
  if (source.startsWith('Bills/')) return 'Receipt';
  return 'Derived';
}

/** Card membership takes priority; bank labels follow the actual paying account. */
export function sourceType(transaction) {
  const postings = transaction.postings;
  if (postings.some(posting => posting.account.startsWith('Liabilities:Credit Cards:'))) return 'Credit card';
  // A refund or income has an incoming bank leg; use it when no paying bank leg exists.
  const banks = postings.filter(posting => posting.account.startsWith('Assets:Bank:'));
  const payment = banks.find(posting => posting.amount < 0) || banks[0];
  if (payment?.account === 'Assets:Bank:Savings Account') return 'Savings account';
  if (payment?.account === 'Assets:Bank:Salary Account') return 'Salary account';
  return 'Derived';
}
