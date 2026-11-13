import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {hold} from './held.mjs';

// Fixture reset must follow browser-observed completion, not only the server commit.
export async function runFixtureIsolation({page,base,persona,stage,expect,control,go,quiesce,record}){
 await quiesce();await control('reset-workflows',{});await control('drafts',{persona,empty:true});await control('gate',{state:'ready'});await control('scenario',{mode:'new-root'});
 const post=async(path,body={})=>{const response=await fetch(base+'api/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});assert.ok(response.ok);return response.json();};
 const draft=await post('drafts',{text:'Fictional fixture-isolation purchase'});await post('drafts/'+draft.id+'/save');
 await expect.poll(async()=>(await(await fetch(base+'api/drafts/'+draft.id)).json()).state,{timeout:120000}).toBe('question');
 await go('queue/?draft='+draft.id);
 const held=await hold(page,base+'api/drafts/'+draft.id,{accept:body=>JSON.parse(body).state==='added'});
 const path='drafts/'+draft.id+'/approve-accounts';let leaving;
 try{
  await page.getByRole('button',{name:'Create and add',exact:true}).click();await held.ready;
  await expect(page.locator(`[data-action-path="${path}"][data-action-state="saving"],[data-action-path="${path}"][data-action-state="waiting"]`).first()).toBeVisible();
  const ledger=JSON.parse(await readFile(join(stage.ledger,'ledger.json'),'utf8')),key='ledger-add-visit-'+ledger.id,value=JSON.stringify([{id:draft.id,type:'draft'}]);
  await page.evaluate(({key,value})=>sessionStorage.setItem(key,value),{key,value});
  leaving=quiesce({writesSettled:true});leaving.catch(()=>{});
  const retained=await page.evaluate(key=>sessionStorage.getItem(key),key).catch(()=>'<document already left>');
  assert.equal(retained,value,'Fixture cleanup keeps recovery while the committed worker result is held');
  await held.release();await leaving;
 }finally{await held.release();await leaving?.catch(()=>{});}
 await control('reset-workflows',{});
 const requests=[];const observe=request=>{if(request.url()===base+'api/drafts/'+draft.id)requests.push(request.url());};page.on('request',observe);
 try{await go('add/');await expect(page.locator('#expense-words')).toHaveValue('');assert.deepEqual(requests,[],'Reset fixtures must not leave a deleted result ID for the next document');}
 finally{page.off('request',observe);}
 record('fixture isolation: a held committed worker result blocks recovery cleanup; the next Add document never fetches its deleted draft');
}
