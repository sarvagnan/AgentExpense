import assert from 'node:assert/strict';
import {guard} from './held.mjs';
/** Hold each real direct-write request before forwarding. The ordinary workflow
 * assertions then check its committed outcome. Pending captures use real routes. */
export async function observeDirectWrites({page,base,snap,record}){
 const seen=new Set(),failures=[];
 const direct=/^(entries\/[^/]+\/corrections\/|drafts\/[^/]+\/(hand-add|usual|bill|approve-accounts|receipt-choice)$|retention\/operations\/|rules\/operations\/|statements\/[^/]+\/apply$|review\/actions$|setup\/(about|opening|accounts|import)$|settings\/(name|default-currency)$|accounts(?:\/currency)?$)/;
 const handler=async route=>{
  const request=route.request(),path=request.url().slice((base+'api/').length);if(!(request.method()==='POST'&&(direct.test(path)||/^(drafts|statements)\/[^/]+\/restore$/.test(path))||request.method()==='DELETE'&&/^(drafts|statements)\/[^/]+$/.test(path)))return route.fallback();
  const error=await guard(route,async()=>{
   let body={};try{body=request.postDataJSON()||{};}catch{}
   const family=path.startsWith('retention/')?'retention-'+body.action:path.startsWith('rules/')?'rule-'+body.action:path.startsWith('entries/')?'correction':path.startsWith('drafts/')?path.split('/').at(-1):path.startsWith('statements/')?'statement-apply':path.replaceAll('/','-');
   // Two native paint opportunities while the response is held. The request is
   // still entirely uncommitted on the fixture server.
   await page.waitForFunction(path=>Array.from(document.querySelectorAll('[data-action-path]')).some(node=>node.dataset.actionPath===path&&['queued','saving','waiting'].includes(node.dataset.actionState)&&node.getClientRects().length&&getComputedStyle(node).visibility!=='hidden'),path);
   const ready=await page.evaluate(()=>({text:document.querySelector('.pending-actions summary')?.textContent,controls:[...document.querySelectorAll('a,button,input,textarea,select')].filter(node=>!node.disabled&&node.getBoundingClientRect().width>0).length}));
   assert.ok(ready.controls>0,'Other controls remain available during a direct save');
   if(!seen.has(family)){seen.add(family);await snap('immediate-'+family+'-pending');record('held direct request: '+family+' shows pending before a commit and leaves other controls usable');}
   await route.continue();
  });
  if(error&&!page.isClosed())failures.push(error);
 };
 await page.route(base+'api/**',handler);
 return {seen,failures,async close(){await page.unroute(base+'api/**',handler);assert.deepEqual(failures,[],'Every direct-write observation completed');}};
}
