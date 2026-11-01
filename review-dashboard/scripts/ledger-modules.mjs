// Shared by builders and browser views. No optional-file inference.
export function moduleSettings(manifest) {
  const value = manifest?.modules || {};
  return {fuel: value.fuel ?? false,
    investments: value.investments === false ? false : {prices: value.investments?.prices ?? null},
    history: {oldLedger: value.history?.oldLedger ?? null}, weeklyReview: value.weeklyReview ?? false};
}
export function priceSource(manifest) {
  return moduleSettings(manifest).investments?.prices || null;
}
