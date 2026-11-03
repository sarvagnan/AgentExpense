import {useProjectNode} from '../../scripts/node-toolchain.mjs';
useProjectNode();
import { temporaryDirectory, disposableDashboard, stopChildren } from './test-isolation.mjs';
// Inline action errors and recovery through the real React/API-client boundary.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
process.env.TMPDIR=process.env.EXPENSES_TEST_TMP;
const fixture=await disposableDashboard(),dash=fixture.dash,work=await temporaryDirectory('expense-draft-actions-'),stage=process.env.STAGE||work;
const require=createRequire(process.env.HOME+'/.cache/expense-playwright/'),{chromium}=require('playwright'),{expect}=require('playwright/test');
const vite=spawn('npm',['run','dev','--','--port','15176','--strictPort'],{cwd:dash,env:{...fixture.env,EXPENSES_DEV:'0',npm_config_cache:join(stage,'.npm-cache')},stdio:['ignore','pipe','pipe']});let log='';vite.stdout.on('data',b=>log+=b);vite.stderr.on('data',b=>log+=b);
const base='http://127.0.0.1:15176/',id='a'.repeat(32);let browser;const output=[];
try{
 for(let n=0;n<100;n++){try{if((await fetch(base)).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
 browser=await chromium.launch({headless:true,downloadsPath:join(work,'downloads')});
 for(const width of [390,1440])for(const colorScheme of ['light','dark']){
  const context=await browser.newContext({viewport:{width,height:900},colorScheme,hasTouch:width===390}),page=await context.newPage();let state='question',exists=true,allow=false;
  const record=()=>({id,state,text:'Coffee with a receipt',created_at:'2026-10-30T01:00:00Z',queued_at:'2026-10-30T01:00:00Z',saved_at:'2026-10-30T01:00:00Z',pieces:null,files:[],runs:[],questions:[{question:'Which bank paid?',answer:null}],result:null,waiting:null,error:state==='failed'?'The agent could not finish.':null});
  await page.route('**/api/**',async r=>{const path=new URL(r.request().url()).pathname,method=r.request().method();
   const respond=(status,body)=>r.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
   if(path.endsWith('/health'))return respond(200,{ok:true});
   if(path==='/api/drafts')return respond(200,exists?[record()]:[]);
   if(path.endsWith('/unsent-answer'))return respond(200,record());
   if(method==='DELETE'){if(!allow)return respond(503,{detail:'Discard is unavailable. Try again.'});exists=false;return r.fulfill({status:204});}
   if(!allow)return respond(503,{detail:path.endsWith('/answer')?'Answer could not be sent. Try again.':'Retry is unavailable. Try again.'});
   state='queued';return respond(200,record());
  });
  const go=async()=>{await page.goto(base+`drafts/?draft=${id}`);await expect(page.locator('article[data-draft-id]')).toBeVisible();};
  await go();await page.getByLabel('Which bank paid?').fill('Savings Account');await page.getByRole('button',{name:'Send answer'}).click();await expect(page.locator('.draft-question [role="alert"]')).toHaveText('Answer could not be sent. Try again.');await expect(page.getByLabel('Which bank paid?')).toHaveValue('Savings Account');
  allow=true;await page.getByRole('button',{name:'Send answer'}).click();await expect(page.getByText('In the queue',{exact:true})).toBeVisible();await expect(page.locator('.agent-next p[aria-live="off"]')).toContainText('Elapsed');
  state='failed';allow=false;await go();await page.getByRole('button',{name:'Retry',exact:true}).click();await expect(page.locator('.failure [role="alert"]')).toHaveText('Retry is unavailable. Try again.');allow=true;await page.getByRole('button',{name:'Retry',exact:true}).click();await expect(page.getByText('In the queue',{exact:true})).toBeVisible();
  state='failed';allow=false;await go();await page.getByRole('button',{name:'Discard',exact:true}).click();await expect(page.locator('.draft-actions [role="alert"]')).toHaveText('Discard is unavailable. Try again.');allow=true;await page.getByRole('button',{name:'Discard',exact:true}).click();await expect(page.getByText('All clear',{exact:true})).toBeVisible();
  const line=`PASS ${width}px ${colorScheme}: answer/retry/discard errors stay beside their actions, values survive, retry succeeds, elapsed updates stay quiet.`;output.push(line);console.log(line);await context.close();
 }
}catch(e){console.error(e);output.push(String(e.stack));process.exitCode=1;}
finally{await browser?.close();await stopChildren([vite]);await mkdir(join(stage,'logs'),{recursive:true});await writeFile(join(stage,'logs/draft-actions.log'),output.join('\n')+'\n');}
