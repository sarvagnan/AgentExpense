import assert from 'node:assert/strict';
/** The report's user actions. Aliases share one HTTP operation and outcome. */
export const actionCoverage=[
 ['Hand addition',['hand-add']],['Edit entry',['correction']],['Delete entry',['delete']],
 ['Restore entry / Queue item / statement',['restore','queue-restore','statement-restore']],
 ['Remove for good / Empty Trash',['trash-removal','empty-trash']],['Discard Queue item or statement',['queue-discard','statement-discard']],
 ['Rule lifecycle',['rule-pause','rule-resume','rule-end','rule-restore','rule-remove','rule-keep','rule-catch-up']],
 ['Suggested rules',['suggestion-confirm','suggestion-dismiss']],['New/Edit rule and saved bill',['new-rule','rule-edit','remove-bill']],
 ['Usual payment',['usual-payment']],['Unpaid bill',['bill']],['Account approval / receipt choice',['account-approval','receipt-use','receipt-keep']],
 ['Statement Apply',['statement-apply']],['Review decisions',['review-decision','review-followup','review-separate','review-merge','review-import']],
 ['First-run About',['setup-about']],['First-run opening balances',['setup-opening']],['First-run account/import',['setup-account','setup-import']],
 ['Name / default currency',['settings-name','settings-currency']],['Account currency / creation',['account-currency','account-create']],['Time limits',['time-limits']],
];
const pages=new WeakMap();
export function coveredAction(page,name,evidence){let actions=pages.get(page);if(!actions)pages.set(page,actions=new Map());actions.set(name,evidence);}
export function assertActionCoverage(page){const actions=pages.get(page)||new Map(),missing=actionCoverage.flatMap(([,names])=>names).filter(name=>!actions.has(name));assert.deepEqual(missing,[],'Every action listed in the report needs a held success and failure');for(const [name,evidence] of actions){assert.ok(evidence.failed&&evidence.saved&&evidence.usable&&evidence.before&&evidence.after,name+' needs complete evidence');assert.ok(evidence.visibleSaved&&evidence.outcomeChecked,name+' needs a visible saved result and checked outcome');assert.equal(evidence.ownAlertsCleared,true,name+' must clear its own visible alert');}return Object.fromEntries(actions);}

export function committedOutcome(name,path,body,result,truth,status){
 if(name==='queue-discard'||name==='statement-discard'){
  assert.equal(status,204);assert.equal(result,undefined);assert.ok(truth.items.some(item=>item.id===path.split('/')[1]),'Committed DELETE is in Trash');return {status:204,item_id:path.split('/')[1]};
 }
 assert.ok(result&&typeof result==='object',name+' has a real result');assert.ok(!result.refused,name+' was not refused');
 const slim=value=>JSON.parse(JSON.stringify({state:value.state,action:value.action,commit_id:value.receipt_reading?.result?.commit_id||value.result?.commit_id||value.commit_id,entry_id:value.result?.entry_id||value.entry_id,account:value.account,item_id:value.item_id,rule_id:value.rule_id||value.result?.rule_id,name:value.name,currency:value.currency,label:value.label,start:value.start,accounts:value.accounts,limits:value.limits,sessions:value.sessions?.map(item=>({id:item.id,state:item.state,commit_id:item.result?.commit_id})).sort((a,b)=>a.id.localeCompare(b.id))}));
 const actual=slim(result);assert.deepEqual(actual,slim(truth),name+' exact committed result agrees with the server');
 if(name==='setup-import'){assert.ok(actual.sessions.length);for(const item of actual.sessions){assert.equal(item.state,'applied');assert.match(item.commit_id,/^[a-f0-9]{40}$/);}}
 else assert.match(actual.commit_id,/^[a-f0-9]{40}$/,name+' requires its commit');
 if(/^drafts\/[^/]+\/(hand-add|usual|bill|approve-accounts|receipt-choice)$/.test(path))assert.equal(actual.state,'added');
 if(path.endsWith('/receipt-choice')){assert.equal(result.receipt_reading.state,'done');assert.ok(result.receipt_reading.result);}
 if(name==='bill'){assert.equal(result.result.outcome,'bill');assert.equal(result.result.entry_id,undefined);}
 if(name==='statement-apply')assert.equal(actual.state,'applied');
 if(['hand-add','usual-payment','account-approval','correction'].includes(name))assert.match(actual.entry_id,/^[a-f0-9]{16}$/);
 if(['settings/name','setup/about'].includes(path))assert.equal(result.name,body.name);
 if(['settings/default-currency','accounts/currency','setup/about','setup/accounts','accounts'].includes(path))assert.equal(result.currency,body.currency);
 if(path==='setup/opening'){assert.equal(result.start,body.start);assert.ok(result.accounts.length>=body.accounts.length);}
 if(path==='review/actions'||path.startsWith('rules/operations/'))assert.equal(actual.action,body.action);
 if(body.entry_id&&actual.entry_id)assert.equal(actual.entry_id,body.entry_id);
 return actual;
}
