import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Exercise the real hook through the autosave identity transition that can
// otherwise disable Continue between pointer-down and pointer-up.
test('Autosave identity keeps a ready words-only interpretation ready; files still require recognition',async()=>{
 const source=ts.transpileModule(readFileSync(new URL('../app/live/add-intake.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const slots=[],effects=[],timers=new Map(),requests=[];let index=0,timerId=0,dirty=false,current;
 const changed=(a,b)=>!a||a.length!==b.length||a.some((x,i)=>!Object.is(x,b[i]));
 const hooks={
  useState(initial){const i=index++;slots[i]??={value:typeof initial==='function'?initial():initial};return [slots[i].value,value=>{slots[i].value=typeof value==='function'?value(slots[i].value):value;dirty=true;}];},
  useRef(initial){const i=index++;return slots[i]??={current:initial};},
  useEffect(fn,deps){const i=index++;if(!slots[i]||changed(slots[i].deps,deps)){const old=slots[i];slots[i]={deps,cleanup:null};effects.push(()=>{old?.cleanup?.();slots[i].cleanup=fn();});}},
 };
 const exports={};
 vm.runInNewContext(source,{exports,AbortController,setTimeout:fn=>{const id=++timerId;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),require:name=>{
  if(name==='react')return hooks;
  if(name==='./api')return {request:async(path,options)=>{requests.push({path,body:JSON.parse(options.body)});return path==='add/interpret'?{kind:'expense'}:{words:{kind:'expense'},files:[{id:'receipt',kind:'receipt'}]};}};
  return {};
 }});
 const args=[null,'Lunch 14 cash','',{}, {},true];
 const render=()=>{index=0;dirty=false;current=exports.useAddInterpretation(...args);while(effects.length)effects.shift()();};
 const settle=async()=>{for(let n=0;n<8;n++){await new Promise(resolve=>setImmediate(resolve));if(dirty)render();}};
 const fire=async()=>{assert.equal(timers.size,1);const [id,fn]=[...timers][0];timers.delete(id);fn();await settle();};
 try{
  render();await settle();await fire();assert.equal(current.busy,false);assert.equal(current.value.words.kind,'expense');
  args[0]='a'.repeat(32);render();await settle();
  assert.equal(current.busy,false,'Autosave must not disable Continue after pointer-down for unchanged words');
  assert.equal(current.value.words.kind,'expense','Autosave keeps the interpretation that enabled Continue');
  assert.equal(timers.size,0,'A draft ID alone must not queue another words-only reading');assert.equal(requests.length,1);
  args[2]='receipt';render();await settle();assert.equal(current.busy,true);await fire();assert.equal(requests[1].path,`drafts/${args[0]}/interpret`);assert.equal(current.value.files[0].kind,'receipt');
  args[0]='b'.repeat(32);render();await settle();assert.equal(current.busy,true);await fire();assert.equal(requests[2].path,`drafts/${args[0]}/interpret`,'File interpretation stays bound to its actual draft');
  args[4]={receipt:'temporary password'};render();await settle();await fire();assert.equal(requests[3].body.passwords.receipt,'temporary password');
 }finally{for(const slot of slots)slot?.cleanup?.();assert.equal(timers.size,0);}
});

test('Usual payment waits for file interpretation before enabling its moving action',()=>{
 const source=ts.transpileModule(readFileSync(new URL('../app/live/rule-guess.tsx',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 const rule={id:'fictional-rule',description:'Fictional usual payment'},result={matches:[{id:rule.id}],rule_id:rule.id,pick_list:false};
 const composer={form:{text:'Fictional usual payment',declined_rules:[]},built:{fields:{}},pending:[],draft:{files:[{id:'receipt'}]},saving:false,recovering:false,interpretation:{busy:false,value:{words:{kind:'expense'},files:[{id:'receipt',kind:'receipt'}]}},update(){},save(){}};
 const element=(type,props)=>({type,props});
 const exports={};
 vm.runInNewContext(source,{exports,navigator:{language:'en'},require:name=>{
  if(name==='react')return {useMemo:fn=>fn(),useEffect(){},useState:initial=>[initial===null?{text:composer.form.text,result}:initial,()=>{}]};
  if(name==='react/jsx-runtime')return {jsx:element,jsxs:element,Fragment:'fragment'};
  if(name==='../data')return {dashboard:{meta:{accounts:{},ledger:{}}}};
  if(name==='../date-time')return {localDate:()=>''};
  if(name==='../../scripts/recurring-ui.mjs')return {matchRules:()=>result};
  if(name==='./rules-api')return {useRules:()=>({data:{rules:[rule]},ready:true})};
  return {};
 }});
 function action(node){if(!node||typeof node!=='object')return;if(node.type?.name==='RuleGuessChoice')return node;for(const child of [node.props?.children].flat()){const found=action(child);if(found)return found;}}
 const disabled=()=>{const choice=action(exports.RuleGuess({composer,online:true}));assert.ok(choice,'The actual rule component renders its action');return choice.props.disabled;};
 assert.equal(disabled(),false,'A settled receipt remains available for the usual payment');
 composer.interpretation.busy=true;assert.equal(disabled(),true,'File recognition must not leave a moving action enabled');
 composer.interpretation.busy=false;composer.interpretation.value=null;assert.equal(disabled(),true,'The upload-to-recognition handoff has no ready interpretation yet');
 for(const kind of ['ambiguous','statement']){composer.interpretation.value={words:{kind:'expense'},files:[{id:'receipt',kind}]};assert.equal(disabled(),true,kind+' is not a confirmed receipt');}
 composer.interpretation.value={words:{kind:'expense'},files:[{id:'receipt',kind:'receipt'}]};assert.equal(disabled(),false);
 composer.draft.files=[];composer.interpretation.value=null;assert.equal(disabled(),false,'A words-only usual payment still works');
});
