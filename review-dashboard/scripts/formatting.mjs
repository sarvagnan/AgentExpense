import currencyCodes from '../../schema/currencies.json' with {type: 'json'};
let locale;
let defaultCurrency;
export function setMoneySettings(settings = {}) {
  locale = settings.locale || undefined;
  defaultCurrency = settings.defaultCurrency;
}
export const moneySettings = () => ({locale, defaultCurrency});
const currencies = new Set(currencyCodes);
export const isCurrency = currency => currencies.has(currency);
const minus = text => text.replace(/^-/, '−');
export const count = value => new Intl.NumberFormat(locale).format(value);
export const units = value => minus(new Intl.NumberFormat(locale, {minimumFractionDigits: 3, maximumFractionDigits: 3}).format(Math.abs(value) < 0.0005 ? 0 : value));
export function currencySymbol(currency = defaultCurrency) {
  if (!currency || !isCurrency(currency)) return '';
  return new Intl.NumberFormat(locale, {style: 'currency', currency}).formatToParts(0).find(part => part.type === 'currency')?.value || currency;
}
export function money(value, currency = defaultCurrency, digits = 2) {
  if (!currency) throw new Error('A monetary amount needs a currency');
  if (!isCurrency(currency)) return `${units(value)} units`;
  return minus(new Intl.NumberFormat(locale, {style: 'currency', currency, minimumFractionDigits: digits, maximumFractionDigits: digits})
    .format(Math.abs(value) < 0.5 * 10 ** -digits ? 0 : value));
}
export const moneyWhole = (value, currency) => money(value, currency, 0);
const lakhLocale = () => new Intl.NumberFormat(locale).format(100000).replace(/\D/g, '').length === 6
  && new Intl.NumberFormat(locale).format(100000).replace(/\d/g, '').length === 2;
export function compactMoney(value, currency = defaultCurrency) {
  const size = Math.abs(value);
  // Intl's Indian compact notation says T/L instead of the existing K/L. Preserve
  // whole-thousand rounding, tenths of a lakh, and the boundary at 99,950.
  if (lakhLocale()) {
    const prefix = `${value < 0 && Math.round(size) !== 0 ? '−' : ''}${currencySymbol(currency)}`;
    if (size >= 99950) return `${prefix}${(Math.round(size / 10000) / 10).toFixed(1)}L`;
    if (size >= 999.5) return `${prefix}${Math.round(size / 1000)}K`;
    return `${prefix}${Math.round(size)}`;
  }
  return minus(new Intl.NumberFormat(locale, {style: 'currency', currency, notation: 'compact', maximumFractionDigits: 1})
    .format(Math.abs(value) < 0.5 ? 0 : value));
}
export function axisMoney(value, max, currency = defaultCurrency) {
  const symbol = currencySymbol(currency);
  const size = Math.abs(max);
  if (lakhLocale()) {
    if (size >= 100000) return value === 0 ? `${symbol}0` : `${value < 0 ? '−' : ''}${symbol}${(Math.round(Math.abs(value) / 10000) / 10).toFixed(value % 100000 ? 1 : 0)}L`;
    if (size >= 1000) return `${value < 0 ? '−' : ''}${symbol}${Math.round(Math.abs(value) / 1000)}K`.replace(`${symbol}0K`, `${symbol}0`);
    return moneyWhole(value, currency);
  }
  // All ticks use the unit selected by the largest tick.
  const divisor = size >= 1e9 ? 1e9 : size >= 1e6 ? 1e6 : size >= 1e3 ? 1e3 : 1;
  const suffix = divisor === 1 ? '' : new Intl.NumberFormat(locale, {notation: 'compact'}).formatToParts(divisor).find(part => part.type === 'compact')?.value || '';
  return `${money(value / divisor, currency, divisor === 1 ? 0 : 1)}${value ? suffix : ''}`;
}
export function currencyOrder(codes, preferred = defaultCurrency) {
  return [...new Set(codes)].sort((a, b) => a === preferred ? -1 : b === preferred ? 1 : a.localeCompare(b));
}
export function moneyTotal(totals, digits = 2) {
  const codes = currencyOrder(Object.keys(totals));
  return codes.length ? codes.map(code => money(totals[code], code, digits)).join(' + ') : money(0);
}
