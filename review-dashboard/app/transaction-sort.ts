export type SortKey = 'date' | 'description' | 'category' | 'paymentLabel' | 'status' | 'amount';
export type SortDirection = 'asc' | 'desc';

type SortableTransaction = {
  date: string; description: string; category: string; paymentLabel: string;
  status: string; amount: number; sequence: number;
};

export function sortTransactions<T extends SortableTransaction>(rows: readonly T[], key: SortKey, direction: SortDirection, valueOf?: (item: T) => string | number): T[] {
  const factor = direction === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const primary = key === 'amount' ? a.amount - b.amount : String(valueOf ? valueOf(a) : a[key]).localeCompare(String(valueOf ? valueOf(b) : b[key]), 'en', { numeric: true, sensitivity: 'base' });
    return factor * (primary || a.date.localeCompare(b.date) || a.sequence - b.sequence);
  });
}
