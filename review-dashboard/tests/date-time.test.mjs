import './test-isolation.mjs';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {test} from 'node:test';
import {createTestServer, sourceDashboard} from './test-isolation.mjs';

test('local dates, browser context and rendered save times follow each viewer zone', () => {
  const code = `
    import assert from 'node:assert/strict';
    import {createElement} from 'react';
    import {renderToStaticMarkup} from 'react-dom/server';
    import {createTestServer} from './tests/test-isolation.mjs';
    import {buildSentence} from './app/live/builder.ts';
    import {localDate, clientTime, formatTimestamp, formatCalendarDate, setDateLocale} from './scripts/date-time.mjs';
    const east = process.env.TZ === 'Asia/Kolkata';
    const now = new Date('2026-10-29T19:00:00Z');
    const today = east ? '2026-10-30' : '2026-10-29';
    assert.equal(localDate(now), today);
    assert.equal(localDate(new Date('2026-10-30T02:00:00Z')), east ? '2026-10-30' : '2026-10-29');
    const context = clientTime(now);
    assert.equal(context.client_time, east ? '2026-10-30T00:30:00+05:30' : '2026-10-29T14:00:00-05:00');
    assert.equal(context.client_zone, Intl.DateTimeFormat().resolvedOptions().timeZone);
    assert.equal(Date.parse(context.client_time), now.getTime());
    const data = {patterns:{}, people:[], accounts:[]};
    assert.equal(buildSentence('lunch today', data, {}, false, today).fields.date.value, today);
    assert.equal(buildSentence('lunch yesterday', data, {}, false, today).fields.date.value, east ? '2026-10-29' : '2026-10-28');
    assert.equal(buildSentence('lunch yesterday', data, {}, false, '2026-03-09').fields.date.value, '2026-03-08');
    const RealDate = Date;
    globalThis.Date = class extends RealDate { constructor(...args) { if (args.length) super(...args); else super(now.getTime()); } };
    try {
      assert.equal(buildSentence('lunch today', data).fields.date.value, today);
      assert.equal(buildSentence('lunch yesterday', data).fields.date.value, east ? '2026-10-29' : '2026-10-28');
    } finally { globalThis.Date = RealDate; }
    setDateLocale('en-US');
    const expected = east ? 'Oct 30, 2026, 12:30 AM' : 'Oct 29, 2026, 2:00 PM';
    assert.equal(formatTimestamp('2026-10-30T00:30:00+05:30'), expected);
    assert.equal(formatCalendarDate('2026-10-30'), 'Oct 30, 2026');
    setDateLocale('de-DE');
    assert.equal(formatCalendarDate('2026-10-30'), '30. Okt. 2026');
    setDateLocale();
    assert.equal(formatTimestamp(now.toISOString()), now.toLocaleString(undefined, {day:'numeric',month:'short',year:'numeric',hour:'numeric',minute:'2-digit'}));
    const server = await createTestServer({optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
    try {
      const runtime = await server.ssrLoadModule('/app/data.ts');
      runtime.setData({dashboard:{meta:{ledger:{locale:'en-US'}},transactions:[]},review:{},fuel:{}});
      const {DraftDetail} = await server.ssrLoadModule('/app/live/drafts.tsx');
      const draft = {id:'a',state:'editing',text:'lunch',created_at:'2026-10-28T00:00:00Z',saved_at:'2026-10-30T03:00:00Z',client_time:'2026-10-30T00:30:00+05:30',client_zone:'Asia/Kolkata',files:[],runs:[],questions:[],result:null};
      const html = renderToStaticMarkup(createElement(DraftDetail,{draft,onAction:async()=>{}}));
      assert.ok(html.includes(expected), html);
      assert.ok(!html.includes('Asia/Kolkata'), html);
    } finally { await server.close(); }
  `;
  for (const zone of ['America/Chicago', 'Asia/Kolkata']) {
    execFileSync(process.execPath, ['--input-type=module', '-e', code], {
      cwd: sourceDashboard, env: {...process.env, TZ:zone}, encoding:'utf8', stdio:'pipe',
    });
  }
});

test('Add save JSON and statement multipart submissions carry current browser context', async () => {
  const server = await createTestServer({optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,ws:false},appType:'custom'});
  const original = globalThis.fetch;
  try {
    const {api} = await server.ssrLoadModule('/app/live/api.ts');
    const {statementsApi} = await server.ssrLoadModule('/app/live/statements-api.ts');
    const requests=[];
    globalThis.fetch=async(url,init)=>{requests.push({url,init});return new Response('{}');};
    await api.create('lunch', null);
    await api.edit('a', 'lunch yesterday', null);
    await api.save('a');
    await statementsApi.upload(new File(['pdf'], 'Statement.pdf', {type:'application/pdf'}), 'b'.repeat(32));
    for (const {init} of requests) {
      const body = init.body instanceof FormData ? Object.fromEntries(init.body) : JSON.parse(init.body);
      assert.match(body.client_time, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/);
      assert.equal(body.client_zone, Intl.DateTimeFormat().resolvedOptions().timeZone);
      assert.ok(Math.abs(Date.now()-Date.parse(body.client_time))<5000);
    }
  } finally { globalThis.fetch=original;await server.close(); }
});
