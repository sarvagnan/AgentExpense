import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {gunzipSync} from 'node:zlib';
import {hold} from './held.mjs';

// A published ledger name may arrive between native pointerdown and click.
export async function runIdentityPublication({page,base,persona,width,stage,expect,control,go,quiesce,snap,record}){
 await quiesce();await control('gate',{state:'ready'});await control('drafts',{persona,empty:true});
 const original=JSON.parse(await readFile(join(stage.ledger,'ledger.json'),'utf8')).name;
 const write=async name=>{const response=await fetch(base+'api/settings/name',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});assert.ok(response.ok,await response.clone().text());};
 const long='Fictional long ledger name for a held publication',short='Ari';let held;
 try{
  await write(long);await go('add/');const identity=page.locator('.identity:visible'),words=page.locator('#expense-words');
  await expect(identity.locator('.identity-name')).toHaveText(long);await words.fill('Fictional words kept during publication');
  held=await hold(page,'**/api/data/*/dashboard.json.gz',{accept:body=>JSON.parse((body[0]===0x1f?gunzipSync(body):body).toString()).meta.ledger.name===short});
  await write(short);await page.evaluate(()=>window.dispatchEvent(new Event('drafts-changed')));await held.ready;
  await identity.hover();await identity.focus();const before=await identity.boundingBox();
  await page.mouse.move(before.x+before.width/2,before.y+before.height/2);await page.mouse.down();
  // Native WebKit mouse-down clears button focus on macOS. Establish the focus
  // condition after that native event, before releasing the held publication.
  await identity.focus();await expect(identity).toBeFocused();
  await held.release();
  // The other, hidden layout proves that the new generation has reached React.
  const other=page.locator(width<760?'.sidebar .identity-name':'.phone-top .identity-name');await expect(other).toHaveText(short);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const after=await identity.boundingBox();for(const key of ['x','y','width','height'])assert.ok(Math.abs(before[key]-after[key])<=2,'A ledger-name publication must not move or shrink Settings under the pointer: '+key);
  await expect(identity.locator('.identity-name')).toHaveText(long);await expect(identity).toBeFocused();await expect(words).toHaveValue('Fictional words kept during publication');
  await page.mouse.up();const dialog=page.getByRole('dialog',{name:'Settings',exact:true});await expect(dialog).toBeVisible();await expect(dialog.getByLabel('Name',{exact:true})).toHaveValue(short);await snap('identity-held-publication');
  // Hold a second publication until Cancel has returned focus to its opener.
  // Focus must stay there: moving to another field would conceal a stale label.
  const updated='Ari Updated';
  held=await hold(page,'**/api/data/*/dashboard.json.gz',{accept:body=>JSON.parse((body[0]===0x1f?gunzipSync(body):body).toString()).meta.ledger.name===updated});
  await write(updated);await page.evaluate(()=>window.dispatchEvent(new Event('drafts-changed')));await held.ready;
  await dialog.getByRole('button',{name:'Cancel',exact:true}).click();await expect(dialog).toHaveCount(0);await expect(identity).toBeFocused();
  await held.release();await expect(other).toHaveText(updated);
  await expect(identity.locator('.identity-name')).toHaveText(updated);await expect(identity).toHaveAccessibleName(updated+', Settings');await expect(identity).toBeFocused();
  await expect(words).toHaveValue('Fictional words kept during publication');await snap('identity-focus-return-publication');
  record('held name publication: Settings keeps its pointer target and focus across pointerdown/publication/click, opens on click, preserves newer words, and adopts a later publication with focus returned to its opener');
 }finally{await held?.release();await write(original);await quiesce();}
}
