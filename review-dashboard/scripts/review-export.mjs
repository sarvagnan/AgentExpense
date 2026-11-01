/** Preserve version-2 native-currency rows while the server reviews whole entries. */
export function reviewExportRows(entries, decisions = {}, legacy = {}) {
  return Object.values(entries).flatMap(entry => {
    const amounts = entry.amounts || {[entry.currency]: entry.amount};
    const codes = Object.keys(amounts);
    return codes.map(currency => {
      const id = codes.length > 1 ? `${entry.id}-${currency}` : entry.id;
      const amount = Number(amounts[currency]);
      const signedAmount = entry.signedAmounts?.[currency] ?? (Number(entry.signedAmount) < 0 ? -amount : amount);
      return {...entry, id, ...(codes.length > 1 ? {storeId: entry.id} : {}), amount, signedAmount: Number(signedAmount), currency,
        decision: legacy[id] || legacy[entry.id] || decisions[entry.id] || {status:'unreviewed', account:'', label:'', notes:'', updatedAt:''}};
    });
  });
}
