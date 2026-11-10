import {localDate} from './date-time.mjs';
/** The calendar stays whole; the average stops at the device's local today.
 * @param {string} month @param {{day:number,amount:number}[]} daily @param {Date} [now]
 */
export function calendarDayAverage(month,daily,now=new Date()) {
  const current=localDate(now).slice(0,7),toDate=month===current;
  const [year,number]=month.split('-').map(Number);
  const days=month>current?0:toDate?now.getDate():new Date(year,number,0).getDate();
  return {days,average:days?daily.filter(row=>row.day<=days).reduce((sum,row)=>sum+row.amount,0)/days:null,toDate};
}
