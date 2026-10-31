/** Shared account behavior. Paths are identities; metadata supplies their meaning. */
export const paymentKinds = ['bank', 'card', 'cash', 'wallet', 'loan', 'person'];
export const groupNames = {bank:'Banks', card:'Credit cards', wallet:'Prepaid', investment:'Investments', person:'People', loan:'Loans', cash:'Cash'};
export const accountLabel = (account, accounts = {}) => accounts[account]?.label || account.split(':').at(-1) || account;
export const accountKind = (account, accounts = {}) => accounts[account]?.kind;
export function expenseCategory(account, accounts = {}) {
  const parts = account.split(':');
  if (parts[0] === 'Expenses') {
    for (let length = parts.length; length > 0; length--) {
      const category = accounts[parts.slice(0, length).join(':')]?.category;
      if (category) return category;
    }
  }
  const first = parts[1] || 'Other';
  return first === 'Food' ? 'Food & dining' : first;
}
export const hasSegment = (account, words) => account.split(':').some(segment => words.some(word => segment.toLowerCase() === word.toLowerCase()));
export const paymentAccounts = (accounts = {}) => Object.fromEntries(Object.entries(accounts).filter(([, meta]) => paymentKinds.includes(meta.kind)));
export const defaultAccount = (accounts = {}) => Object.keys(accounts).find(account => accounts[account].kind === 'bank') || Object.keys(accounts).find(account => accounts[account].kind) || '';
export function reviewKeys(ledger) {
  const legacy = ledger.review?.storageKey;
  const decisions = legacy || `agentexpense-review-v2:${ledger.id}`;
  return {decisions, drafts: legacy ? `${legacy.replace(/-v2$/, '')}-drafts-v1` : `agentexpense-review-drafts-v1:${ledger.id}`,
    exported: legacy ? `${legacy.replace(/-v2$/, '')}-exported-at` : `agentexpense-review-exported-at:${ledger.id}`};
}

// A complete runtime snapshot publishes metadata together with the dashboard.
/** @type {Record<string, any>} */
let selectedAccounts = {};
export const currentAccounts = () => selectedAccounts;
export const setCurrentAccounts = accounts => { selectedAccounts = accounts || {}; };
