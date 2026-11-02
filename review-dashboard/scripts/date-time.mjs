/** Calendar dates stay local; instants display in the viewer's zone. */
/** @type {string | undefined} */
let currentLocale;
/** @param {string | undefined} [locale] */
export const setDateLocale = (locale) => { currentLocale = locale; };
/** @param {number} value */
const pad = (value) => String(value).padStart(2, '0');
export const localDate = (now = new Date()) => `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;

export function clientTime(now = new Date()) {
  const minutes = -now.getTimezoneOffset();
  const offset = `${minutes < 0 ? '-' : '+'}${pad(Math.floor(Math.abs(minutes) / 60))}:${pad(Math.abs(minutes) % 60)}`;
  return {
    client_time: `${localDate(now)}T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}${offset}`,
    client_zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
}

/** @param {string} value @param {Intl.DateTimeFormatOptions} [options] @param {string} [locale] */
export function formatTimestamp(value, options = {
  day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit',
}, locale = currentLocale) {
  return new Date(value).toLocaleString(locale, options);
}

/** @param {string} value @param {Intl.DateTimeFormatOptions} [options] @param {string} [locale] */
export function formatCalendarDate(value, options = {
  day: 'numeric', month: 'short', year: 'numeric',
}, locale = currentLocale) {
  const [year, month, day = 1] = value.split('-').map(Number);
  const date = new Date(0);
  date.setFullYear(year, month - 1, day);
  date.setHours(12, 0, 0, 0);
  return date.toLocaleDateString(locale, options);
}

/** Today's local calendar date, shared by the builder and the page. */
export function today(now = new Date()) {
  const pinned = (typeof process !== 'undefined' ? process.env.DASHBOARD_TODAY : '')
    || import.meta.env?.DASHBOARD_TODAY;
  if (!pinned) return localDate(now);
  const date = new Date(`${pinned}T12:00:00`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(pinned) || Number.isNaN(date.getTime()) || localDate(date) !== pinned) {
    throw new Error('DASHBOARD_TODAY must be a calendar date (YYYY-MM-DD)');
  }
  return pinned;
}

/** Month navigation includes today's month and its immediate predecessor. */
export function calendarMonths(months, current = today().slice(0, 7)) {
  const date = new Date(`${current}-01T12:00:00`);
  date.setMonth(date.getMonth() - 1);
  return [...new Set([...months, current, localDate(date).slice(0, 7)])].sort();
}
