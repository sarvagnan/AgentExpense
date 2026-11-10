import test from 'node:test';
import assert from 'node:assert/strict';
import {calendarDayAverage} from '../scripts/daily-average.mjs';
test('calendar day average uses the device date and excludes later spending',()=>{
  const daily=[{day:1,amount:30},{day:10,amount:-10},{day:20,amount:600}];
  assert.deepEqual(calendarDayAverage('2026-11',daily,new Date(2026,10,1,12)),{days:1,average:30,toDate:true});
  assert.deepEqual(calendarDayAverage('2026-11',daily,new Date(2026,10,10,12)),{days:10,average:2,toDate:true});
  assert.deepEqual(calendarDayAverage('2026-11',daily,new Date(2026,10,30,12)),{days:30,average:620/30,toDate:true});
  assert.deepEqual(calendarDayAverage('2026-11',daily,new Date(2026,11,1,12)),{days:30,average:620/30,toDate:false});
  assert.deepEqual(calendarDayAverage('2026-11',daily,new Date(2026,9,31,12)),{days:0,average:null,toDate:false});
});
