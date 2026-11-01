import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
export const shots = '/tmp/agentexpense-4a-shots';
const escape = value => String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export async function capture(page, manifest, info) {
  await mkdir(shots,{recursive:true});
  const name=[info.engine,info.persona,info.state,info.theme,info.width].join('-');
  const file=name+'.png';
  await page.screenshot({path:join(shots,file),animations:'disabled'});
  const tall=await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight+2);
  let full;
  if(tall) {
    full=name+'-full.png';
    // Fixed phone controls belong at the document foot in a full-page companion.
    const style=await page.addStyleTag({content:'@media(max-width:760px){body{position:relative}.phone-live-nav{position:absolute!important;top:auto!important;bottom:0!important}.phone-more-views{position:absolute!important;top:auto!important;bottom:calc(var(--live-nav-height,64px) + 12px)!important}.add-savebar{position:absolute!important;top:auto!important;bottom:var(--live-nav-height,64px)!important}}'});
    try {await page.screenshot({path:join(shots,full),fullPage:true,animations:'disabled'});}
    finally {await style.evaluate(node=>node.remove());}
  }
  manifest.push({...info,file,full,route:new URL(page.url()).pathname+new URL(page.url()).search});
}
export async function contactSheet(manifest) {
  await mkdir(shots,{recursive:true});
  await writeFile(join(shots,'manifest.json'),JSON.stringify(manifest,null,2)+'\n');
  const groups=Map.groupBy(manifest,item=>`${item.persona} · ${item.state}`);
  await writeFile(join(shots,'index.html'),`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentExpense 4A · real app captures</title><style>body{font:14px system-ui;margin:24px;background:#eee;color:#222}section{margin-block:32px}h1{font-size:24px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}figure{margin:0;padding:12px;background:white;border-radius:8px}img{display:block;width:100%;height:230px;object-fit:contain;object-position:top;background:#ddd}figcaption{margin-block:8px;font-size:12px}a{color:#853400}code{overflow-wrap:anywhere}</style><h1>AgentExpense · step 4A</h1><p>${manifest.length} exact viewport captures. Headless Playwright 1.64.1; fictional disposable ledgers. Each card links its original PNG and, when taller, a full-page companion.</p>${[...groups].map(([group,items])=>`<section><h2>${escape(group)}</h2><div class="grid">${items.map(item=>`<figure><a href="${escape(item.file)}"><img loading="lazy" src="${escape(item.file)}" alt="${escape(group+' '+item.engine+' '+item.theme+' '+item.width)}"></a><figcaption>${escape(item.engine)} · ${escape(item.theme)} · ${item.width}×${item.height}<br><code>${escape(item.route)}</code></figcaption>${item.full?`<a href="${escape(item.full)}">Full page</a>`:''}</figure>`).join('')}</div></section>`).join('')}</html>`);
}
