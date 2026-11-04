import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// The override runs this same regression against an archived application file.
const source=readFileSync(process.env.VARYING_CHOICE_SOURCE||new URL('../app/live/rule-guess.tsx',import.meta.url),'utf8');
const js=ts.transpileModule(source.replace('function VaryingRuleChoice','export function VaryingRuleChoice'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
test('Varying bill selection survives an amount edit and a late passive effect',()=>{
 const slots=[],effects=[];let index=0,dirty=false,tree;
 const react={
  useState(initial){const i=index++;slots[i]??={value:typeof initial==='function'?initial():initial};return [slots[i].value,value=>{const next=typeof value==='function'?value(slots[i].value):value;if(!Object.is(next,slots[i].value)){slots[i].value=next;dirty=true;}}];},
  useEffect(fn,deps){const i=index++;if(!slots[i]||deps.some((v,j)=>v!==slots[i].deps[j])){slots[i]={deps};effects.push(fn);}},
 };
 const exports={},jsx=(type,props)=>({type,props});
 const dashboard={meta:{ledger:{defaultCurrency:'USD',locale:'en'},accounts:{'Assets:Bank:Willow':{label:'Willow'}}}};
 vm.runInNewContext(js,{exports,Intl,JSON,navigator:{language:'en'},require:name=>name==='react'?react:name==='react/jsx-runtime'?{jsx,jsxs:jsx}:name==='../data'?{dashboard}:name==='../date-time'?{localDate:()=> '2026-11-02'}:name==='../ui'?{money:n=>String(n),dayLabel:x=>x}:name.includes('recurring-ui')?{ruleAmounts:()=>({USD:'110.00'}),payer:()=>({account:'Assets:Bank:Willow'}),textAmounts:text=>[Number(text.match(/\d+(?:\.\d+)?/)[0])],paymentInPeriod:()=>null,varyingAmountCheck:()=>null}:{} });
 let text='Willow Energy 2000 USD';
 const render=()=>{do{dirty=false;index=0;tree=exports.VaryingRuleChoice({rule:{id:'willow',description:'Willow Energy'},match:{matched_words:['energy']},text,disabled:false,onAdd(){},onDecline(){}});}while(dirty);};
 const walk=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(walk):[node,...walk(node.props?.children)];
 const bill=()=>walk(tree).filter(node=>node.type==='input'&&node.props.type==='radio')[1];
 const flush=()=>{while(effects.length){effects.shift()();if(dirty)render();}};
 render();flush();bill().props.onChange();render();flush();assert.equal(bill().props.checked,true);
 text='Willow Energy 125.25 USD';render();
 // Native check does not emit a change for an already checked radio. Hold
 // passive work across that gesture, then release it as Pay by receives input.
 const beforeGesture=bill().props.checked;
 if(!beforeGesture){bill().props.onChange();render();}
 const afterGesture=JSON.stringify(tree);flush();
 assert.equal(bill().props.checked,true,'A late passive effect must not erase the owner’s Bill only selection');
 assert.equal(beforeGesture,false,'The changed amount starts without a preselected payment decision');
 assert.equal(JSON.stringify(tree),afterGesture,'Late work cannot move the received/pay-by fields or replace/disable Save bill');
 const save=walk(tree).find(node=>node.type==='button'&&node.props.children==='Save bill');assert.equal(save.props.disabled,false);
});
