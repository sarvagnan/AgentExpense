import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {hookHarness} from './hook-harness.mjs';
test('Receipt Retry clears the old refusal after committed done props arrive',async()=>{
 const jsx=(type,props)=>({type,props}),id='r'.repeat(32);let actions=0;
 let draft={id,receipt_reading:{state:'question',answer:{amount:'12.00',currency:'EUR'},entry_totals:{EUR:'10.00'},entry:{date:'2026-11-02'}}};
 const h=hookHarness('../app/live/receipt-reading.tsx',{
  '../ui':{dayLabel:x=>x,money:v=>String(v)},'../data':{dashboard:{meta:{accounts:{}}}},'./balance-refusal':{BalanceNotice:'balance-notice'},
  './api':{api:{receiptChoice:async()=>{throw Error('Fictional original refusal');}},ApiError:class extends Error{}},'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'},
 });
 const nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 try{
  h.render(m=>m.default({draft,onAction:async()=>{actions++;},compact:true}));nodes(h.current).find(node=>node.type==='button'&&node.props.children==='Use the receipt').props.onClick();await h.settle();
  assert.ok(nodes(h.current).some(node=>node.props?.role==='alert'));
  draft={...draft,receipt_reading:{...draft.receipt_reading,state:'done',message:'Receipt details saved.',result:{commit_id:'a'.repeat(40)}}};
  h.events.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{id:'other-job',path:'drafts/other/receipt-choice',body:'{}',result:draft}}));await h.settle();assert.equal(actions,0);assert.ok(nodes(h.current).some(node=>node.props?.role==='alert'));
  h.events.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{path:'drafts/'+id+'/receipt-choice',result:draft}}));h.render();await h.settle();
  assert.equal(nodes(h.current).some(node=>node.props?.role==='alert'),false,'Committed receipt Retry must clear its previous refusal even after the background refresh publishes done');
  assert.equal(actions,1,'Retry must run the usual completion refresh once');
 }finally{h.close();}
});


test('Ordinary receipt choice and repeated committed notifications refresh once',async()=>{
 let actions=0;const jsx=(type,props)=>({type,props}),id='a'.repeat(32),draft={id,receipt_reading:{state:'question',answer:{amount:'12.00',currency:'EUR'},entry_totals:{EUR:'10.00'},entry:{date:'2026-11-02'}}};let h;
 const done={...draft,receipt_reading:{...draft.receipt_reading,state:'done'}};
 h=hookHarness('../app/live/receipt-reading.tsx',{'../ui':{dayLabel:x=>x,money:v=>String(v)},'../data':{dashboard:{meta:{accounts:{}}}},'./balance-refusal':{BalanceNotice:'balance-notice'},'./api':{api:{receiptChoice:async()=>{h.events.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{id:'normal-job',path:'drafts/'+id+'/receipt-choice',body:'{}',result:done}}));return done;}},ApiError:class extends Error{}},'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'}});
 const nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 try{h.render(m=>m.default({draft,onAction:async()=>{actions++;},compact:true}));nodes(h.current).find(node=>node.type==='button'&&node.props.children==='Keep the entry').props.onClick();await h.settle();assert.equal(actions,1);h.events.dispatchEvent(new CustomEvent('direct-write-saved',{detail:{id:'normal-job',path:'drafts/'+id+'/receipt-choice',body:'{}',result:done}}));await h.settle();assert.equal(actions,1);}finally{h.close();}
});

for(const recovered of [false,true])test(`Receipt completion follows the real ${recovered?'recovered':'shelf'} queue Retry without another choice`,async()=>{
 const stored=new Map(),storage={getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)},id='b'.repeat(32),path='drafts/'+id+'/receipt-choice';let actions=0,fail=true,receipt,requests=[];
 const draft={id,receipt_reading:{state:'question',answer:{amount:'12.00',currency:'EUR'},entry_totals:{EUR:'10.00'},entry:{date:'2026-11-02'}}},done={...draft,receipt_reading:{...draft.receipt_reading,state:'done',message:'Receipt details saved.'}};
 const queue=()=>hookHarness('../app/live/direct-writes.ts',{}, {Error,location:{pathname:'/expenses/add/',search:''},sessionStorage:storage});let direct=queue();direct.exports.recoverWrites('fictional',async()=>done);
 const jsx=(type,props)=>({type,props}),nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
 const mount=()=>hookHarness('../app/live/receipt-reading.tsx',{'../ui':{dayLabel:x=>x,money:v=>String(v)},'../data':{dashboard:{meta:{accounts:{}}}},'./balance-refusal':{BalanceNotice:'balance-notice'},'./direct-writes':{writeSnapshot:()=>direct.exports.writeSnapshot()},'./api':{api:{receiptChoice:async(_id,choice,options)=>direct.exports.enqueueWrite(path,{...options,method:'POST',body:JSON.stringify({choice})},async()=>{requests.push(JSON.stringify({choice}));if(fail)throw Error('Fictional receipt refusal');return done;})},ApiError:class extends Error{}},'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'}});
 const bridge=()=>direct.events.addEventListener('direct-write-saved',event=>receipt.events.dispatchEvent(new CustomEvent('direct-write-saved',{detail:event.detail})));
 try{
  receipt=mount();receipt.render(m=>m.default({draft,onAction:async()=>{actions++;},compact:true}));bridge();nodes(receipt.current).find(node=>node.type==='button'&&node.props.children==='Use the receipt').props.onClick();await receipt.settle();assert.ok(nodes(receipt.current).some(node=>node.props?.role==='alert'));const original=direct.exports.writeSnapshot()[0];assert.equal(original.state,'failed');fail=false;
  if(recovered){receipt.close();direct.close();direct=queue();receipt=mount();receipt.render(m=>m.default({draft,onAction:async()=>{actions++;},compact:true}));bridge();direct.exports.recoverWrites('fictional',async(_path,options)=>{requests.push(options.body);return done;});assert.equal(direct.exports.writeSnapshot()[0].recovered,true);}
  await direct.exports.retryWrite(original.id);await receipt.settle();assert.equal(actions,1);assert.equal(nodes(receipt.current).some(node=>node.props?.role==='alert'),false);assert.deepEqual(requests,[original.body,original.body]);assert.equal(direct.exports.writeSnapshot()[0].state,'saved');
 }finally{receipt?.close();direct.close();}
});

test('Dismiss ends a failed receipt reading while the saved payment remains',async()=>{
 const jsx=(type,props)=>({type,props}),id='d'.repeat(32);let dismissed=0,actions=0;
 const draft={id,state:'added',receipt_reading:{state:'failed',message:'Your payment is saved.'}};
 const h=hookHarness('../app/live/receipt-reading.tsx',{'../ui':{},'../data':{dashboard:{meta:{accounts:{}}}},'./balance-refusal':{},'./api':{api:{receiptDismiss:async identity=>{assert.equal(identity,id);dismissed++;return {...draft,receipt_reading:{state:'dismissed'}};}},ApiError:class extends Error{}},'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'}});
 const nodes=n=>!n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(nodes):[n,...nodes(n.props?.children)];
 try{h.render(m=>m.default({draft,onAction:async()=>{actions++;}}));const dismiss=nodes(h.current).find(n=>n.type==='button'&&n.props.children==='Dismiss');assert.ok(dismiss);dismiss.props.onClick();await h.settle();assert.equal(dismissed,1);assert.equal(actions,1);assert.equal(draft.state,'added');}finally{h.close();}
});
