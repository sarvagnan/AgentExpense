import test from 'node:test';
import assert from 'node:assert/strict';
import {editOverviewOnLoad} from '../scripts/edit-navigation.mjs';
test('an editor history entry recovers Queue even without navigation timing',()=>{
 assert.equal(editOverviewOnLoad('?correct=entry',{agentExpenseEditing:true},undefined),true);
 assert.equal(editOverviewOnLoad('?draft=fix&edit=1',{agentExpenseEditing:true},undefined),true);
 assert.equal(editOverviewOnLoad('?correct=entry',null,'reload'),true);
 assert.equal(editOverviewOnLoad('?correct=entry',null,'navigate'),false,'A fresh explicit link still opens the editor');
 assert.equal(editOverviewOnLoad('?draft=item',{agentExpenseEditing:true},'reload'),false,'An explicit item reload opens its summary');
 assert.equal(editOverviewOnLoad('?session=statement',{agentExpenseEditing:true},undefined),false);
 assert.equal(editOverviewOnLoad('?check=ledger',{agentExpenseEditing:true},'reload'),false);
 assert.equal(editOverviewOnLoad('?edit=0',null,'reload'),false);
});
