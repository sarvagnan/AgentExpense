// Shared by builders and browser views. No optional-file inference.
export function moduleSettings(manifest) {
  const value = manifest?.modules || {};
  return {investments: value.investments === false ? false : {prices: value.investments?.prices ?? null},
    history: {oldLedger: value.history?.oldLedger ?? null}, weeklyReview: value.weeklyReview ?? false};
}
/** Whether ledger.json lists an extension; fuel's screens switch on with the fuel extension. */
export function hasExtension(manifest, name) {
  return Object.hasOwn(manifest?.extensions || {}, name);
}
export function priceSource(manifest) {
  return moduleSettings(manifest).investments?.prices || null;
}
