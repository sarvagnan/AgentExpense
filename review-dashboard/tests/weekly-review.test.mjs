import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,access} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
test('weekly review documents locked append-only verdicts and shell-valid commands',async()=>{
 const text=await readFile(new URL('../server/WEEKLY-REVIEW.md',import.meta.url),'utf8');
 assert.ok(text.includes('audit.py list --unreviewed'));
 assert.ok(text.includes('audit.py review --verdicts'));
 assert.ok(text.includes('LEDGER_DIR'));
 for(const block of text.matchAll(/```sh\n([\s\S]*?)```/g)) execFileSync('/bin/sh',['-n'],{input:block[1]});
 const json=[...text.matchAll(/```json\n([\s\S]*?)```/g)].map(m=>JSON.parse(m[1]));
 assert.deepEqual(json.flat().map(v=>Object.keys(v).sort().join()),['line,note,verdict','line,note,verdict','reason,rule,verdict','reason,rule,verdict']);
 for(const path of ['scripts/ledger/audit.py','scripts/ledger/ledger_lock.py','review-dashboard/server/README.md'])await access(fileURLToPath(new URL('../../'+path,import.meta.url)));
});
