import type { Accounts } from './account-metadata';
export type LedgerManifest = {format: number; id: string; name: string; defaultCurrency: string; locale?: string; startDate?: string; modules: Record<string, unknown>; review: {storageKey: string|null}};
// Runtime payload contracts must remain stable when a selected ledger has empty arrays.
export type Provenance = {
  auditId: string; text: string; time: string; savedAt: string | null;
  questions: { question: string; answer: string | null }[]; doubts: string[]; models: string[];
};
export type DashboardTransaction = {
  id: string; storeId?: string; provenance: Provenance | null; sequence: number; date: string; originalDate: string;
  month: string; status: string; description: string; kind: string; displayType: string;
  currency: string; amount: number; signedExpense: number; category: string; ledgerAccount: string;
  paymentAccount: string; paymentLabel: string; reimbursementGroup: string;
  reimbursementLabel: string; reimbursementParty: string; reimbursementAmount: number;
  sources: string[]; source: string; sourceKind: string; sourceReference: string;
  classification: string; originalAmount: string;
  fuelLog: { litres: number; tripKm: number | null; confirmed: boolean } | null;
  postings: { account: string; amount: number; currency: string; label: string; cost?: number; costCurrency?: string }[];
};
export type DashboardData = {
  meta: { currencyMinorUnits?: Record<string, number>; ledger: LedgerManifest; accounts: Accounts; generatedAt: string; today: string; defaultMonth: string; firstMonth: string; lastMonth: string; transactionCount: number };
  months: {
    month: string; currency: string; total: number; income: number; investments: number; recurring: number; count: number; pending: number;
    categories: { category: string; amount: number }[]; daily: { day: number; amount: number }[];
    comparison: {month: string; total: number; count: number; pending: number; categories: {category: string; amount: number}[]; daily: {day: number; amount: number}[]} | null;
  }[];
  forecasts: DashboardData['forecast'][];
  forecast: {
    currency: string; asOf: string; currentMonth: string; daysPassed: number; variableAfterDay: number; start: string; end: string; firstFullMonth: string; recurringAccounts: string[];
    variable: {
      months: string[];
      portions: { from: number; to: number; average: number; median: number; min: number; max: number; byMonth: number[] }[];
      excluded: { reason: string; description: string; amount: number; from: string; to: string }[];
    };
    months: { month: string; committed: number; investments: number }[];
    items: { currency: string; date: string; month: string; description: string; rule: string; kind: string; category: string;
      account: string; amount: number; signedExpense: number; paymentLabel: string; sources: string[] }[];
  };
  prices: { commodity: string; currency: string; dates: string[]; navs: number[] }[];
  reimbursementGroups: { id: string; currency: string; label: string; purchases: number; received: number; outstanding: number; paymentDate: string }[];
  transactions: DashboardTransaction[];
};
export type ReviewData = {
  meta: { ledger: string; pendingTransactionCount: number; pendingNetAmount: Record<string, number>; pendingAbsoluteAmount: Record<string, number>;
    byKind: Record<string, number>; bySource: Record<string, number> };
  transactions: {
    id: string; storeId?: string; date: string; originalDate: string; description: string; pattern: string; currency: string; amount: number;
    signedAmount: number; kind: string; currentAccount: string; source: string; sourceType: string;
    sourceReference: string; statementEnd: string; classification: string;
  }[];
};
export type FuelData = {
  meta: Record<string, unknown>;
  entries: { id: string; date: string; ledgerStatus: string; entryId?: string;
    litres: number | null; tripKm: number | null; cost: number | null; currency?: string; payment: string }[];
};
