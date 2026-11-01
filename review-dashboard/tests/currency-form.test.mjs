import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {currenciesLinked,positiveDecimal} from '../app/live/currency-math.ts';
test('linked amount and rate use decimal half-up minor units in both directions',()=>{
 assert.deepEqual(currenciesLinked('46.80','51.48','amount',2),{amount:'51.48',rate:'1.1000'});
 assert.deepEqual(currenciesLinked('46.80','1.1001','rate',2),{amount:'51.48',rate:'1.1001'});
 assert.equal(currenciesLinked('1','1.005','rate',2).amount,'1.01');
 assert.equal(currenciesLinked('1000','1.16','rate',2).amount,'1160.00');
 assert.equal(currenciesLinked('1','1.5','rate',0).amount,'2');
 assert.equal(currenciesLinked('1','1.2345','rate',3).amount,'1.235');
 for(const value of ['','0','-1','Infinity','NaN','bad','1e2']){assert.equal(positiveDecimal(value),false);assert.deepEqual(currenciesLinked('46.80',value,'rate',2),{amount:'',rate:''});}
});
test('server currency minor-unit table matches the browser formatter for all supported codes',async()=>{
 const table=JSON.parse(await readFile(new URL('../../schema/currency-minor-units.json',import.meta.url)));
 for(const [currency,digits] of Object.entries(table))assert.equal(digits,new Intl.NumberFormat('en',{style:'currency',currency}).resolvedOptions().maximumFractionDigits,currency);
});
