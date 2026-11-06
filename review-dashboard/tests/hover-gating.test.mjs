import './test-isolation.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync,readdirSync} from 'node:fs';

// A tap on a touch screen leaves the pointer "hovering" where it landed, so a hover
// fill there reads as a stuck state (grade A-2 N3). Every hover rule that fills or
// shades a control sits inside @media (hover:hover); only selected-state rules, which
// keep the selection's own fill, may stay outside.
const folder=new URL('../app/styles/',import.meta.url);
function hoverRules(css){
  const rules=[];let depth=0,media=[],selector='',start=0;
  css=css.replace(/\/\*[\s\S]*?\*\//g,'');
  for(let i=0;i<css.length;i++){
    const ch=css[i];
    if(ch==='{'){const head=css.slice(start,i).trim();if(head.startsWith('@'))media.push(head);else selector=head;depth++;start=i+1;}
    else if(ch==='}'){if(selector){rules.push({selector,body:css.slice(start,i),media:[...media]});selector='';}else media.pop();depth--;start=i+1;}
    else if(ch===';'&&!selector)start=i+1;
  }
  return rules.filter(rule=>rule.selector.includes(':hover'));
}
test('hover fills apply only where a pointer can hover', () => {
  const outside=[];
  for(const file of readdirSync(folder).filter(name=>name.endsWith('.css'))){
    for(const rule of hoverRules(readFileSync(new URL(file,folder),'utf8'))){
      if(!/background|filter|border-color/.test(rule.body))continue;
      if(rule.media.some(m=>/hover\s*:\s*hover/.test(m)))continue;
      // Selected rows and pushed buttons keep their own selection fill under the pointer.
      if(rule.selector.split(',').every(part=>!part.includes(':hover')||/\[(aria-current|aria-selected|data-selected)/.test(part)))continue;
      outside.push(`${file}: ${rule.selector}`);
    }
  }
  assert.deepEqual(outside,[]);
  assert.ok(hoverRules(readFileSync(new URL('base.css',folder),'utf8')).some(rule=>rule.selector.startsWith('.icon-button:hover')&&rule.media.some(m=>m.includes('hover:hover'))),'The icon button hover fill is gated');
});
