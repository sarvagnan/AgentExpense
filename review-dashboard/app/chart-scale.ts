/**
 * Chart scales and labels shared by Explorer and Forecast. Pure functions, so the
 * unit tests check them without a browser.
 */

export type Scale = { low: number; top: number; step: number; ticks: number[] };

/**
 * A value axis from the data: steps of 1, 2 or 5 × 10ⁿ, at most `maxTicks` ticks, always
 * including zero, ending on the first step at or beyond the data. Steps of 2.5 are left
 * out because the lakh axis prints tenths (0.25L would read as 0.3L).
 */
export function niceScale(min: number, max: number, maxTicks = 5): Scale {
  const from = Math.min(0, Number.isFinite(min) ? min : 0);
  const to = Math.max(0, Number.isFinite(max) ? max : 0);
  if (to - from <= 0) return { low: 0, top: 1, step: 1, ticks: [0, 1] };
  const intervals = Math.max(1, maxTicks - 1);
  let magnitude = 10 ** Math.floor(Math.log10((to - from) / intervals));
  for (;;) {
    for (const factor of [1, 2, 5]) {
      const step = factor * magnitude;
      const low = Math.floor(from / step) * step;
      const top = Math.ceil(to / step) * step;
      const count = Math.round((top - low) / step);
      if (count <= intervals) return { low, top, step, ticks: Array.from({ length: count + 1 }, (_, index) => clean(low + index * step)) };
    }
    magnitude *= 10;
  }
}
// Keeps 0.1 + 0.2 style sums from printing as 0.30000000000000004.
const clean = (value: number) => Number(value.toPrecision(12));

export type Label = { id: string; y: number; height: number };
/**
 * Places labels in one column without overlap: each keeps its own centre `y` where it can,
 * moves down just enough to clear the label above it, and the column is pushed back up from
 * `bottom` if it runs past it. Returns the placed centres by id.
 */
export function placeLabels(labels: Label[], top: number, bottom: number, gap = 2): Record<string, number> {
  const sorted = labels.slice().sort((a, b) => a.y - b.y || a.id.localeCompare(b.id));
  const placed = sorted.map((label) => label.y);
  sorted.forEach((label, index) => {
    const floor = index ? placed[index - 1] + sorted[index - 1].height / 2 + gap + label.height / 2 : top + label.height / 2;
    placed[index] = Math.max(placed[index], floor);
  });
  for (let index = sorted.length - 1; index >= 0; index -= 1) {
    const ceiling = index === sorted.length - 1 ? bottom - sorted[index].height / 2 : placed[index + 1] - sorted[index + 1].height / 2 - gap - sorted[index].height / 2;
    placed[index] = Math.min(placed[index], ceiling);
  }
  return Object.fromEntries(sorted.map((label, index) => [label.id, placed[index]]));
}

/**
 * The comparison averages Forecast offers for a currency with `full` complete months: 3, 6 and
 * 12 where there are that many, plus every full month when that is fewer than 12 and not
 * already offered. The default window, `min(6, full)`, is always one of them.
 */
export function comparisonWindows(full: number): { value: number; label: string }[] {
  const options = [3, 6, 12].filter((count) => count <= full).map((count) => ({ value: count, label: `Last ${count} full months` }));
  if (full > 0 && full < 12 && !options.some((option) => option.value === full)) options.push({ value: full, label: full === 1 ? 'The one full month' : `All ${full} full months` });
  return options;
}
export const defaultComparisonWindow = (full: number) => Math.min(6, full);

const shiftMonth = (month: string, by: number) => {
  const date = new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)) - 1 + by, 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`;
};
/**
 * Explorer's twelve-month range: it ends at the last complete month the ledger has (the month
 * in progress is never plotted), or is empty when there is none.
 */
export function trendMonths(months: string[], today: string): string[] {
  const current = today.slice(0, 7);
  const last = months.filter((month) => month < current).sort().at(-1);
  return last ? Array.from({ length: 12 }, (_, index) => shiftMonth(last, index - 11)) : [];
}
