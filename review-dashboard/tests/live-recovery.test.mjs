import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validatePiece,writeStored,readStored,answerKey,findRecoveryDraft,restorePieceEdits} from '../app/live/persistence.ts';
test('edited numeric pieces, currency and dates validate without blocking blank agent values',()=>{
 for(const key of ['amount','litres','km','inr']){
  for(const bad of ['banana','NaN','Infinity','-1','0','1e309','2.3.4'])assert.ok(validatePiece(key,bad),`${key} ${bad}`);
  for(const good of ['12','0.50','.2',' 1.25 ',''])assert.equal(validatePiece(key,good),'');
 }
 for(const bad of ['rupees','inr','12','INRR','ZZZ','ABC'])assert.ok(validatePiece('currency',bad));
 for(const good of ['INR','USD','EUR',''])assert.equal(validatePiece('currency',good),'');
 for(const bad of ['2026-02-30','2025-02-29','2026-13-01','2026-1-2','0000-01-01','banana'])assert.ok(validatePiece('date',bad));
 for(const good of ['2024-02-29','2026-10-30',''])assert.equal(validatePiece('date',good),'');
 assert.equal(validatePiece('outlet','Fable Fuels'),'');
});
test('storage denial is explicit and answer keys isolate each draft and question',()=>{
 const original=globalThis.localStorage;
 try{globalThis.localStorage={setItem(){throw Error('quota');},getItem(){throw Error('denied');}};assert.equal(writeStored('words','petrol'),false);assert.equal(readStored('words'),null);
 const values=new Map();globalThis.localStorage={setItem:(k,v)=>values.set(k,v),getItem:k=>values.get(k)};
 assert.equal(writeStored('words',{text:'petrol'}),true);assert.deepEqual(readStored('words'),{text:'petrol'});
 assert.notEqual(answerKey('a',0,'Which bank?'),answerKey('a',1,'Which bank?'));assert.notEqual(answerKey('a',0,'Which bank?'),answerKey('b',0,'Which bank?'));
 }finally{globalThis.localStorage=original;}
});

test('server recovery beats stale words or IDs but never replaces a newer in-progress edit',()=>{
 const old={id:'old',state:'added',text:'submitted',client_updated_at:10};
 const editing={id:'new',state:'editing',text:'newer words',client_updated_at:80};
 assert.equal(findRecoveryDraft({text:'old words',updatedAt:10},[old,editing]),editing);
 assert.equal(findRecoveryDraft({text:'newer words',updatedAt:80},[editing]),editing);
 assert.equal(findRecoveryDraft({text:'typing now',updatedAt:90},[editing]),undefined);
 assert.equal(findRecoveryDraft({text:'different',updatedAt:80},[editing]),undefined);
 assert.equal(findRecoveryDraft({text:'',updatedAt:90},[editing]),undefined);
});

test('server piece snapshots retain explicit blank edits without pinning parsed words',async()=>{
 const {buildSentence,draftPieces}=await import('../app/live/builder.ts');
 const patterns={patterns:{},people:[],accounts:[],modules:{fuel:true}};
 const edits={amount:'',outlet:'Manual outlet'};
 const snapshot=draftPieces(buildSentence('petrol 300 120km',patterns,edits,true,'2026-10-30'),edits);
 assert.deepEqual(restorePieceEdits(snapshot),edits);
 const rebuilt=buildSentence('petrol 999 222km',patterns,restorePieceEdits(snapshot),true,'2026-10-30');
 assert.equal(rebuilt.fields.amount.value,'');assert.equal(rebuilt.fields.amount.source,'agent');assert.equal(rebuilt.fields.outlet.value,'Manual outlet');assert.equal(rebuilt.fields.km.value,'222');
 assert.deepEqual(restorePieceEdits({fields:{outlet:{source:'typed',value:'Legacy outlet'}}}),{outlet:'Legacy outlet'});
});
