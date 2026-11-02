import type {DashboardData} from './data-types';

export type Point = {day: number; value: number};
export function cumulative(increments: Map<number, number>, from: number, to: number, start = 0): Point[] {
  const points = [{day: from, value: start}];
  for (let day = from + 1; day <= to; day += 1) points.push({day, value: points.at(-1)!.value + (increments.get(day) || 0)});
  return points;
}

const daysInMonth = (month: string) => new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0).getDate();
function variableOn(forecast: DashboardData['forecast'], day: number, days: number) {
  const portion = forecast.variable.portions.find(portion => day >= portion.from && day <= portion.to)!;
  return portion.average / (Math.min(portion.to, days) - portion.from + 1);
}
export function variableAfter(forecast: DashboardData['forecast'], month: string, afterDay: number) {
  const days = daysInMonth(month);
  let sum = 0;
  for (let day = afterDay + 1; day <= days; day += 1) {
    sum += variableOn(forecast, day, days);
  }
  return sum;
}

// The list, month table and chart use the same still-expected amounts. Overdue
// payments and today's unconsumed variable estimate sit at the start of the line.
export function forecastProjection(forecast: DashboardData['forecast'], months: DashboardData['months'], today: string, focus: string, includeVariable: boolean) {
  const isCurrent = focus === forecast.currentMonth;
  const asOfDay = isCurrent ? Number(today.slice(8, 10)) : 0;
  const focusDays = daysInMonth(focus);
  const items = forecast.items.filter(item => item.month === focus);
  const increments = new Map<number, number>();
  for (const item of items) {
    const day = Math.max(asOfDay, Number(item.date.slice(8, 10)));
    increments.set(day, (increments.get(day) || 0) + item.signedExpense);
  }
  const afterDay = isCurrent ? forecast.variableAfterDay : 0;
  if (includeVariable) for (let day = afterDay + 1; day <= focusDays; day += 1) {
    const amount = variableOn(forecast, day, focusDays);
    const on = Math.max(asOfDay, day);
    increments.set(on, (increments.get(on) || 0) + amount);
  }
  const daily = months.find(row => row.month === focus && row.currency === forecast.currency)?.daily || [];
  const recorded = isCurrent ? cumulative(new Map(daily.map(row => [row.day, row.amount])), 0, asOfDay) : [];
  const recordedTotal = recorded.at(-1)?.value || 0;
  const expected = cumulative(increments, asOfDay, focusDays, recordedTotal + (increments.get(asOfDay) || 0));
  const committed = items.reduce((sum, item) => sum + item.signedExpense, 0);
  const variable = includeVariable ? variableAfter(forecast, focus, afterDay) : 0;
  return {isCurrent, asOfDay, focusDays, items, recorded, expected, recordedTotal, committed, variable,
    total: expected.at(-1)!.value, remainingDays: focusDays - afterDay};
}
