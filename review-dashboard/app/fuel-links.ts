type Reading = { entryId?: string; litres: number | null; tripKm: number | null; ledgerStatus: string };
type Entry = { id: string; status: string; fuelLog: { litres: number; tripKm: number | null; confirmed: boolean } | null };
/** Persistent IDs identify entries; explicit confirmation and physical readings verify the association. */
export function fuelLedgerEntry<T extends Entry>(reading: Reading, entries: T[]): T | undefined {
  if (reading.ledgerStatus !== 'Linked' || !reading.entryId || reading.litres === null) return;
  const matches = entries.filter((entry) => entry.id === reading.entryId && entry.status === 'Cleared'
    && entry.fuelLog?.confirmed && entry.fuelLog.litres === reading.litres
    && (reading.tripKm === null || entry.fuelLog.tripKm === reading.tripKm));
  return matches.length === 1 ? matches[0] : undefined;
}
