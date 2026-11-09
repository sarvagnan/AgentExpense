import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {hookHarness,deferred} from './hook-harness.mjs';
const text=node=>node==null||typeof node==='boolean'?'':typeof node!=='object'?String(node):Array.isArray(node)?node.map(text).join(''):text(node.props?.children);
const nodes=node=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(nodes):[node,...nodes(node.props?.children)];
for(const outcome of ['failed','saved'])test(`A’s late ${outcome} Review notice never appears in B’s decision form`,async()=>{
 const held=deferred(),stored=new Map(),entry=id=>({id,version:'version-'+id,description:'Entry '+id,date:'2026-11-01',accountLabel:'Willow',currency:'USD',amount:'12.00',sources:[],notes:[],postings:[]}),a=entry('a'),b=entry('b');
 const data={count:2,important:0,entries:{a,b},export_entries:{a,b},accounts:['Expenses:Food'],history:[],decisions:{},items:[a,b].map(entry=>({kind:'pending',important:false,reason:'Choose category',entries:[entry]}))};
 const jsx=(type,props)=>({type,props});
 const h=hookHarness('../app/review/page.tsx',{
  '../live/interaction':{useActionGuard:()=>({begin:()=>0,unchanged:()=>true,interaction:{}})},'../data':{dashboard:{meta:{ledger:{id:'fictional'}}},reloadData:async()=>{}},'../../scripts/account-metadata.mjs':{reviewKeys:()=>({drafts:'drafts',decisions:'decisions'})},
  '../ui':{Dialog:'dialog',Icon:'icon',PageHeader:'header',dayLabel:x=>x,money:x=>x,evidenceHref:()=>null,EvidenceName:'source'},'../date-time':{formatTimestamp:x=>x},'./api':{postReview:()=>held.promise,requestId:()=> 'request-a'},'../styles/server-review.css':{},'../../scripts/ledger-modules.mjs':{hasExtension:()=>false},'./fuel-log':{},'../../scripts/review-export.mjs':{reviewExportRows:()=>[]},'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'},
 },{localStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value),removeItem:key=>stored.delete(key)}});
 try{h.render(m=>m.default({live:{data,error:'',refresh:async()=>data}}));const open=id=>{const row=nodes(h.current).find(node=>node.props?.className==='review-rest-row'&&text(node).includes('Entry '+id));nodes(row).find(node=>node.type==='button').props.onClick();};open('a');await h.settle();nodes(h.current).find(node=>node.type==='button'&&node.props.children==='Mark for Follow-up').props.onClick();await h.settle();open('b');await h.settle();if(outcome==='failed')held.reject(Error('A’s private failure reason'));else held.resolve({audit_id:'saved-a'});await h.settle();const dialog=nodes(h.current).find(node=>node.type==='dialog');assert.equal(dialog.props.title,'Review Entry b');assert.ok(!text(dialog).includes(outcome==='failed'?'A’s private failure reason':'Marked for follow-up.'));}finally{h.close();}
});
