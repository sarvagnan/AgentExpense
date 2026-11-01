import {disposableDashboard, sourceApplication, createTestServer} from './test-isolation.mjs';
import {cp, mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {join} from 'node:path';
import assert from 'node:assert/strict';
import {after, test} from 'node:test';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {moduleSettings} from '../scripts/ledger-manifest.mjs';
import {buildSentence, draftPieces, availablePatterns} from '../app/live/builder.ts';
import {fuelLedgerEntry} from '../app/fuel-links.ts';
import {personaData} from './persona-data.mjs';

const asha = await personaData('asha');
const server = await createTestServer({server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
after(() => server.close());

test('all off personas build without opening any module input and remove stale fuel outputs', async t => {
  const copy = await disposableDashboard();
  t.after(() => rm(copy.application,{recursive:true,force:true}));
  const guard = join(copy.application,'guard.mjs');
  await writeFile(guard, `import fs from 'node:fs/promises'; import {syncBuiltinESMExports} from 'node:module';
const read = fs.readFile; fs.readFile = function(file,...args) {
if (/data\\/(?:fuel-log\\.json|prices\\/(?:mutual-funds\\.prices|fund-sources\\.json))$/.test(String(file))) throw new Error('Disabled module input opened');
return read.call(this,file,...args); }; syncBuiltinESMExports();`);
  for (const name of ['jordan','sam','empty']) {
    const root = join(copy.application,name);
    await cp(join(sourceApplication,'fixtures/ledgers',name),root,{recursive:true});
    const manifest = JSON.parse(await readFile(join(root,'ledger.json'),'utf8'));
    assert.deepEqual(moduleSettings(manifest),{fuel:false,investments:false,history:{oldLedger:null},weeklyReview:false});
    // Present but invalid inputs must also be ignored, never used to infer a flag.
    await mkdir(join(root,'data/prices'),{recursive:true});
    for (const file of ['fuel-log.json','prices/mutual-funds.prices','prices/fund-sources.json']) await writeFile(join(root,'data',file),'invalid disabled input');
    for (const file of ['app/data/fuel-log.json','public/data/fuel-log.json.gz']) await writeFile(join(copy.dash,file),'stale');
    const env = {...copy.env,LEDGER_DIR:root};
    for (const script of ['copy-fuel-log','build-dashboard-data','build-review-data','build-add-patterns','check-dashboard-data','compress-dashboard-data']) {
      execFileSync(process.execPath,['--import',guard,join(copy.dash,'scripts',script+'.mjs')],{env,stdio:'pipe'});
    }
    for (const file of ['app/data/fuel-log.json','public/data/fuel-log.json.gz']) await assert.rejects(readFile(join(copy.dash,file)),{code:'ENOENT'});
    const data = JSON.parse(await readFile(join(copy.dash,'app/data/dashboard.json'),'utf8'));
    assert.equal(data.prices.length,0); assert.ok(data.transactions.every(row => row.fuelLog === null));
    assert.equal(data.meta.ledger.modules.investments,false);
    assert.ok(!JSON.parse(await readFile(join(copy.dash,'app/data/add-patterns.json'),'utf8')).patterns.fuel);
    manifest.modules.investments={prices:null};
    await writeFile(join(root,'ledger.json'),JSON.stringify(manifest));
    await rm(join(root,'data/prices/mutual-funds.prices'));
    execFileSync(process.execPath,[join(copy.dash,'scripts/build-dashboard-data.mjs')],{env,stdio:'pipe'});
    assert.deepEqual(JSON.parse(await readFile(join(copy.dash,'app/data/dashboard.json'),'utf8')).prices,[]);
  }
});

test('manual prices produce the same dashboard data with or without an automatic source', async t => {
  const copy = await disposableDashboard();
  t.after(() => rm(copy.application,{recursive:true,force:true}));
  const root = join(copy.application,'ledger');
  await cp(join(sourceApplication,'fixtures/ledgers/asha'),root,{recursive:true});
  await rm(join(root,'data/prices/fund-sources.json'));
  const manifest = JSON.parse(await readFile(join(root,'ledger.json'),'utf8'));
  const build = async source => {
    manifest.modules.investments = {prices:source};
    await writeFile(join(root,'ledger.json'),JSON.stringify(manifest));
    execFileSync(process.execPath,[join(copy.dash,'scripts/build-dashboard-data.mjs')],{env:{...copy.env,LEDGER_DIR:root},stdio:'pipe'});
    const data = JSON.parse(await readFile(join(copy.dash,'app/data/dashboard.json'),'utf8'));
    delete data.meta.generatedAt;
    delete data.meta.ledger.modules.investments.prices;
    return data;
  };
  const configured = await build('mutual-fund-nav');
  assert.ok(configured.prices.length);
  assert.deepEqual(await build(null),configured);
  await rm(join(root,'data/prices/mutual-funds.prices'));
  assert.deepEqual((await build(null)).prices,[]);
});

test('fuel on builds existing readings and refuses missing or invalid fuel data', async t => {
  const copy = await disposableDashboard();
  t.after(() => rm(copy.application,{recursive:true,force:true}));
  const root=join(copy.application,'ledger');
  await cp(join(sourceApplication,'fixtures/ledgers/asha'),root,{recursive:true});
  const env={...copy.env,LEDGER_DIR:root},script=join(copy.dash,'scripts/copy-fuel-log.mjs');
  execFileSync(process.execPath,[script],{env,stdio:'pipe'});
  assert.deepEqual(JSON.parse(await readFile(join(copy.dash,'app/data/fuel-log.json'),'utf8')),asha.fuel);
  await rm(join(root,'data/fuel-log.json'));
  assert.throws(()=>execFileSync(process.execPath,[script],{env,stdio:'pipe'}),/Command failed/);
  await writeFile(join(root,'data/fuel-log.json'),'{}');
  assert.throws(()=>execFileSync(process.execPath,[script],{env,stdio:'pipe'}),/Command failed/);
});

test('fuel switches gate composer pieces, recovered edits and confirmed links', () => {
  const on = {modules:{fuel:true},patterns:{},people:[],accounts:[]};
  const off = {...on,modules:{fuel:false}};
  assert.equal(buildSentence('fuel 100 8l 200km',on).pattern,'fuel');
  const built = buildSentence('fuel 100 8l 200km',off,{vehicle:'old car',outlet:'old outlet'});
  assert.equal(built.pattern,'new');
  for (const key of ['litres','km','vehicle','outlet']) assert.ok(!(key in built.fields));
  assert.deepEqual(draftPieces(built,{vehicle:'old car',outlet:'old outlet'}).edits,{});
  assert.ok(!availablePatterns(off).some(p => p.id==='fuel'));
  const reading = asha.fuel.entries.find(row => row.ledgerStatus==='Linked');
  assert.ok(fuelLedgerEntry(reading,asha.dashboard.transactions,on));
  assert.equal(fuelLedgerEntry(reading,asha.dashboard.transactions,off),undefined);
});

test('Review and investment sections render only when enabled; unpriced units retain cost', async () => {
  const dataModule = await server.ssrLoadModule('/app/data.ts');
  const {default:Review} = await server.ssrLoadModule('/app/review/page.tsx');
  const {default:Accounts} = await server.ssrLoadModule('/app/account-summary.tsx');
  const show = (modules, view) => {
    const data = structuredClone(asha.dashboard); data.meta.ledger.modules = modules;
    if (modules.investments === false) data.prices = [];
    dataModule.setData({dashboard:data,review:asha.review,fuel:asha.fuel});
    return renderToStaticMarkup(createElement(view));
  };
  assert.match(show({fuel:true,investments:{prices:'mutual-fund-nav'}},Review),/Fuel Log/);
  assert.doesNotMatch(show({fuel:false,investments:false,weeklyReview:false},Review),/Fuel Log|fuel-log|weekly.review/i);
  assert.match(show({investments:{prices:'mutual-fund-nav'}},Accounts),/Investments/);
  assert.equal(show({investments:{prices:null}},Accounts),show({investments:{prices:'mutual-fund-nav'}},Accounts));
  const disabled = show({investments:false},Accounts);
  assert.doesNotMatch(disabled,/Investments|Investment holdings|Demo Index|Market value over time/);
  // Select the fund through the default account, without a browser or dev server.
  const data = structuredClone(asha.dashboard);
  const fund = Object.entries(data.meta.accounts).find(([,meta])=>meta.kind==='investment');
  const accounts = {...data.meta.accounts}; delete accounts[fund[0]];
  data.meta.accounts = {[fund[0]]:fund[1],...Object.fromEntries(Object.entries(accounts).filter(([,meta])=>meta.kind!=='bank'))};
  data.meta.ledger.modules = {investments:{prices:null},fuel:false}; data.prices=[];
  dataModule.setData({dashboard:data,review:asha.review,fuel:{meta:{},entries:[]}});
  const html = renderToStaticMarkup(createElement(Accounts));
  assert.match(html,/Showing recorded cost. No recorded prices available./);
  assert.match(html,/summary-total fund-headline">₹500.00/); assert.doesNotMatch(html,/Unrealised gain|Market value over time/);
  data.prices = structuredClone(asha.dashboard.prices);
  dataModule.setData({dashboard:data,review:asha.review,fuel:{meta:{},entries:[]}});
  const manualHtml = renderToStaticMarkup(createElement(Accounts));
  assert.match(manualHtml,/Unrealised gain|Market value over time/);
  assert.doesNotMatch(manualHtml,/No recorded prices available/);
  data.meta.ledger.modules.investments.prices='mutual-fund-nav';
  dataModule.setData({dashboard:data,review:asha.review,fuel:{meta:{},entries:[]}});
  assert.equal(renderToStaticMarkup(createElement(Accounts)),manualHtml);
  const cash=Object.entries(data.meta.accounts).find(([,meta])=>meta.kind==='cash');
  data.meta.accounts={[fund[0]]:fund[1],[cash[0]]:cash[1]};
  data.meta.ledger.modules.investments=false;
  dataModule.setData({dashboard:data,review:asha.review,fuel:{meta:{},entries:[]}});
  const offHtml=renderToStaticMarkup(createElement(Accounts));
  assert.match(offHtml,/<h2 tabindex="-1">Cash<\/h2>/);
  assert.doesNotMatch(offHtml,/Account not found|Demo Index/);
});

test('runtime loader skips fuel requests when off and requests fuel when on', async () => {
  const dataModule = await server.ssrLoadModule('/app/data.ts');
  const original = globalThis.fetch;
  try {
    for (const enabled of [false,true]) {
      const data = structuredClone(asha.dashboard); data.meta.ledger.modules.fuel=enabled;
      const requested=[];
      globalThis.fetch=async url => {
        requested.push(url);
        return new Response(JSON.stringify(url.includes('pending-transactions') ? asha.review : url.includes('fuel-log') ? asha.fuel : data));
      };
      await dataModule.loadData();
      assert.equal(requested.some(url=>url.includes('fuel-log')),enabled);
      assert.equal(dataModule.fuel.entries.length,enabled ? asha.fuel.entries.length : 0);
    }
  } finally {globalThis.fetch=original;}
});
