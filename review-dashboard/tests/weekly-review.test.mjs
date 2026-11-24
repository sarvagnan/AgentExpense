import './test-isolation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

test('owner help uses the weekly review controls and Queue action labels',async()=>{
  const [help,settings,review,issue,queue]=await Promise.all([
    '../server/WEEKLY-REVIEW.md','../app/settings-dialog.tsx','../app/review/weekly.tsx',
    '../app/review/weekly-issue.tsx','../app/live/drafts.tsx',
  ].map(path=>readFile(new URL(path,import.meta.url),'utf8')));
  for(const [source,labels] of [
    [settings,['Run a review every week']],
    [review,['Review now','Retry summary']],
    [queue,['Needs you','In progress']],
    [issue,['Apply suggestion','Keep as is','Answer','Explain more','Who made this change?','You','An agent']],
  ]){
    for(const label of labels){
      assert.ok(source.includes(label),`Dashboard label changed: ${label}`);
      assert.ok(help.includes(`**${label}**`)||help.includes(` ${label}**`),`Missing owner help for ${label}`);
    }
  }
  assert.match(help,/^## Hand changes$/m);
});
