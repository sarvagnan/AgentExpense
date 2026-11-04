import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createTestServer} from './test-isolation.mjs';

test('Confirmed balance notice starts its source sentence with a capital for every source',async()=>{
 const server=await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
 try{
  (await server.ssrLoadModule('/app/data.ts')).setData({dashboard:{meta:{ledger:{locale:'en-US'},accounts:{}}},review:{},fuel:{}});
  const {BalanceNotice,balanceSource}=await server.ssrLoadModule('/app/live/balance-refusal.tsx');
  for(const kind of ['statement','balance'])for(const label of ['cedar current','(cedar) reader','42 reader','Érable']){
   const source={kind,label,date:'2026-10-31'},refusal={source,date:source.date,account_label:'Cedar Current',asserted:'100',calculated:'110',difference:'10',commodity:'USD',possible_duplicates:[]};
   const html=renderToStaticMarkup(createElement(BalanceNotice,{refusal}));
   assert.match(html,/<p>The /,html);assert.equal(source.label,label,'Rendering must not mutate stored names');
   assert.match(balanceSource(source),/^the /,'Inline source wording stays grammatical');
  }
 }finally{await server.close();}
});

test('Sentence dates use the ledger locale for all typed, relative and edited dates without changing values',async()=>{
 const server=await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
 try{
  const {buildSentence,draftPieces}=await server.ssrLoadModule('/app/live/builder.ts');
  const {setDateLocale,formatCalendarDate}=await server.ssrLoadModule('/scripts/date-time.mjs');
  for(const locale of ['en-IN','en-US','en-GB']){
   setDateLocale(locale);
   for(const [text,edits,expected] of [['Lunch 2026-11-02',{},'2026-11-02'],['Lunch 2 November 2026',{},'2026-11-02'],['Lunch 02/11/2026',{},'2026-11-02'],['Lunch yesterday',{},'2026-11-01'],['Lunch today',{},'2026-11-02'],['Lunch',{date:'2026-11-03'},'2026-11-03']]){
    const built=buildSentence(text,{patterns:{},people:[],accounts:[]},edits,false,'2026-11-02');
    assert.equal(built.fields.date.value,expected);assert.equal(built.fields.date.shown,formatCalendarDate(expected));assert.equal(draftPieces(built).fields.date.value,expected);
   }
   assert.equal(buildSentence('Lunch',{patterns:{},people:[],accounts:[]},{},false,'2026-11-02').fields.date.shown,'today');
  }
 }finally{await server.close();}
});
