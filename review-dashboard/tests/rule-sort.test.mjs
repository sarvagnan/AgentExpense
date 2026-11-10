import test from 'node:test';
import assert from 'node:assert/strict';
import {sortRules} from '../scripts/rule-sort.mjs';
const rule=(id,name,currency,amount,date='2026-11-01',pay='Assets:Purse')=>({id,description:name,date,postings:[{account:'Expenses:Dues',amount,commodity:currency},{account:pay,amount:String(-Number(amount)),commodity:currency}]});
test('rule sorting keeps currencies separate, uses bills and estimates, and never mutates suggestions',()=>{
 const rules=[rule('a','Willow','USD','2'),rule('b','Birch','EUR','100'),{...rule('c','Maple','EUR','50'),amount:'varies'}, {...rule('d','Ash','EUR','80'),bill:{amount:'4'}}];
 assert.deepEqual(sortRules(rules,'amount','asc',{}).map(r=>r.id),['d','c','b','a']);
 assert.deepEqual(sortRules(rules,'amount','desc',{}).map(r=>r.id),['a','b','c','d']);
 assert.deepEqual(sortRules(rules,'name','asc',{}).map(r=>r.id),['d','b','c','a']);
 const dated=[rule('e','Later','EUR','2','2026-12-01'),rule('f','Soon','EUR','2','2026-11-02')];
 assert.deepEqual(sortRules(dated,'due','asc',{}).map(r=>r.id),['f','e']);
 const payers=[rule('g','First','EUR','2',undefined,'Assets:Z'),rule('h','Second','EUR','2',undefined,'Assets:A')];
 assert.deepEqual(sortRules(payers,'account','asc',{'Assets:Z':{kind:'cash',label:'Birch'},'Assets:A':{kind:'bank',label:'Willow'}}).map(r=>r.id),['g','h']);
 assert.deepEqual(rules.map(r=>r.id),['a','b','c','d']);
});
