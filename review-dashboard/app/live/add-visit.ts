import {dashboard} from '../data';
export type VisitResult={id:string;type:'draft'|'statement'};
let memory:VisitResult[]=[];
const key=()=>`ledger-add-visit-${dashboard.meta.ledger.id}`;
export function readVisit():VisitResult[]{
 try{const raw=JSON.parse(sessionStorage.getItem(key())||'[]');if(Array.isArray(raw)&&raw.every(x=>x&&/^[a-f0-9]{32}$/.test(x.id)&&['draft','statement'].includes(x.type)))return raw.slice(0,3);}catch{}
 return memory;
}
export function rememberResults(results:VisitResult[]){memory=[...results,...readVisit().filter(old=>!results.some(x=>x.id===old.id))].slice(0,3);persist();}
export function forgetResult(id:string){memory=readVisit().filter(x=>x.id!==id);persist();}
function persist(){try{sessionStorage.setItem(key(),JSON.stringify(memory));}catch{}window.dispatchEvent(new Event('add-results-changed'));}
