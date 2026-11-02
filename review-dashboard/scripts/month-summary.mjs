// Whole-month and same-day comparisons share the signed expense/refund rule.
export function expenseSummary(records) {
  const rows = records.filter(row => row.kind === 'Expense' || row.kind === 'Refund');
  const categories = new Map(), daily = new Map();
  let total = 0;
  for (const row of rows) {
    total += row.signedExpense;
    categories.set(row.category, (categories.get(row.category) || 0) + row.signedExpense);
    const day = Number(row.date.slice(8, 10));
    daily.set(day, (daily.get(day) || 0) + row.signedExpense);
  }
  const rounded = value => Number(value.toFixed(2));
  return {
    total: rounded(total), count: rows.length,
    pending: rows.filter(row => row.status === 'Pending').length,
    categories: [...categories].map(([category, amount]) => ({category, amount: rounded(amount)})).sort((a, b) => b.amount - a.amount),
    daily: [...daily].map(([day, amount]) => ({day, amount: rounded(amount)})).sort((a, b) => a.day - b.day),
  };
}

export function previousMonth(month) {
  const [year, number] = month.split('-').map(Number);
  return `${number === 1 ? year - 1 : year}-${String(number === 1 ? 12 : number - 1).padStart(2, '0')}`;
}
