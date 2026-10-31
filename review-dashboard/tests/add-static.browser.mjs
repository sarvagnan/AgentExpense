import { temporaryDirectory } from './test-isolation.mjs';
// Built /expenses/ directory indexes and static-host recovery, without an API or SPA fallback.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {readFile,stat,mkdir} from 'node:fs/promises';
import {join,resolve,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
process.env.TMPDIR=process.env.EXPENSES_TEST_TMP;
const {chromium}=createRequire(process.env.HOME+'/.cache/expense-playwright/')('playwright');
const {expect}=createRequire(process.env.HOME+'/.cache/expense-playwright/')('playwright/test');
const work=await temporaryDirectory('expense-add-static-'),dist=join(work,'dist');
await (await import('node:fs/promises')).cp(fileURLToPath(new URL('../dist/',import.meta.url)),dist,{recursive:true});
const index=await readFile(join(dist,'index.html'));
for(const view of ['explorer','atlas','journal','forecast','review','account-summary','add','drafts','statements'])assert.deepEqual(await readFile(join(dist,view,'index.html')),index,view);
const calls=[];
const types={'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.gz':'application/gzip'};
const server=createServer(async(req,res)=>{
 calls.push({url:req.url,headers:req.headers});
 try{
  const path=new URL(req.url,'http://127.0.0.1').pathname;
  if(!path.startsWith('/expenses/'))throw Error('Outside base');
  let file=resolve(dist,decodeURIComponent(path.slice('/expenses/'.length)));
  if(!file.startsWith(dist))throw Error('Outside dist');
  if((await stat(file)).isDirectory())file=join(file,'index.html');
  res.setHeader('Content-Type',types[extname(file)]||'application/octet-stream');res.end(await readFile(file));
 }catch{res.statusCode=404;res.end('Not found');}
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base=`http://127.0.0.1:${server.address().port}/expenses/`;
let browser;
try{
 browser=await chromium.launch({headless:true,downloadsPath:join(work,'downloads')});
 for(const colorScheme of ['light','dark'])for(const width of [390,1440]){
  const context=await browser.newContext({viewport:{width,height:900},colorScheme});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
  await page.goto(base+'add/');await expect(page.getByText('Adding needs the live server',{exact:true})).toBeVisible();
  await page.reload();await expect(page.getByText('Adding needs the live server',{exact:true})).toBeVisible();
  await page.goto(base+'drafts/');await expect(page.getByText('Drafts needs the live server',{exact:true})).toBeVisible();
  await page.goto(base+'statements/');await expect(page.getByText('The Statements screen needs the live server. Your other ledger views remain available.')).toBeVisible();
  await page.goto(base+'explorer/');await expect(page.locator('.content h1')).toHaveText('Explorer');
  assert.deepEqual(errors,[]);await context.close();console.log(`PASS /expenses/ static indexes and no-server recovery: ${width}px ${colorScheme}`);
 }
 assert.ok(calls.some(c=>c.url==='/expenses/api/health'));
 assert.ok(calls.every(c=>!Object.keys(c.headers).some(k=>/tailscale|identity/i.test(k))));
 console.log('STATIC ADD TOTAL: 4 passed; all 9 directory indexes are byte-identical; BASE_URL API requests verified.');
}finally{await browser?.close();await new Promise(r=>server.close(r));}
