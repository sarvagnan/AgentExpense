import './test-isolation.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import {reviewExportRows} from '../scripts/review-export.mjs';

test('version 2 exports retain native amounts, signs, store IDs and decisions per currency',()=>{
 const decision={status:'resolved',account:'Expenses:Learning',label:'Workshop',notes:'Checked',updatedAt:'2026-11-01T12:00:00Z'};
 const entry={id:'0000000000000001',description:'Workshop',currency:'GBP',amount:'12.50',signedAmount:'12.50',amounts:{GBP:'12.50',EUR:'7.00'},signedAmounts:{GBP:'12.50',EUR:'-7.00'}};
 const rows=reviewExportRows({[entry.id]:entry},{[entry.id]:decision});
 assert.deepEqual(rows.map(({id,storeId,currency,amount,signedAmount,decision:d})=>({id,storeId,currency,amount,signedAmount,decision:d})),[
  {id:entry.id+'-GBP',storeId:entry.id,currency:'GBP',amount:12.5,signedAmount:12.5,decision},
  {id:entry.id+'-EUR',storeId:entry.id,currency:'EUR',amount:7,signedAmount:-7,decision},
 ]);
 const single=reviewExportRows({[entry.id]:{...entry,amounts:{GBP:'12.50'}}});
 assert.equal(single[0].id,entry.id);assert.equal(single[0].storeId,undefined);assert.equal(single[0].decision.status,'unreviewed');
 const legacy={...decision,notes:'Legacy per-currency work'};
 assert.equal(reviewExportRows({[entry.id]:entry},{},{[entry.id+'-EUR']:legacy})[1].decision.notes,legacy.notes);
});
