import test from 'node:test';
import assert from 'node:assert/strict';
import * as jsx from 'react/jsx-runtime';
import {deferred, hookHarness} from './hook-harness.mjs';

function find(element, predicate) {
  if (Array.isArray(element)) return element.map(child => find(child, predicate)).find(Boolean);
  if (!element?.props) return undefined;
  return predicate(element) ? element : find(element.props.children, predicate);
}

test('the rule editor flags a schedule note beside Notes and submits other dates', async () => {
  const saved = [], request = deferred();
  let closed = false, revision = 0;
  const postings = [
    {account: 'Expenses:Club', amount: '20.00', commodity: 'EUR'},
    {account: 'Assets:Purse', amount: '-20.00', commodity: 'EUR'},
  ];
  const rule = {id: 'f000000000000001', description: 'Willow club', date: '2026-11-15', postings,
    notes: [], schedule: {every: 'month', interval: 1, from: '2026-11-15'}};
  const harness = hookHarness('../app/rule-editor.tsx', {
    'react/jsx-runtime': jsx,
    './live/interaction': {useActionGuard: () => ({
      touch: () => { revision++; }, begin: () => revision, unchanged: ticket => ticket === revision,
    })},
    './data': {dashboard: {meta: {accounts: {'Assets:Purse': {kind: 'cash', currency: 'EUR'}}, ledger: {defaultCurrency: 'EUR'}}, transactions: []}},
    './date-time': {localDate: () => '2026-11-15'},
    './ui': {Dialog: () => null, useDocumentScrollLock() {}},
    '../scripts/recurring-ui.mjs': {complexRule: () => false, mainPostings: () => [postings[0]], payer: () => postings[1],
      varyingDetails: () => undefined, ruleDestinations: () => ['Expenses:Club']},
    './live/rules-api': {useRuleOperation: () => ({operate: body => { saved.push(body); return request.promise; }})},
    './account-labels': {accountFieldLabel: account => account},
  });
  const render = () => harness.render(({RuleEditor}) => RuleEditor({rule, onClose() { closed = true; }, onSaved() {}}));
  const notes = tree => find(tree, node => node.type === 'textarea' && node.props.id === 'rule-notes');
  const submit = tree => find(tree, node => node.type === 'form').props.onSubmit({preventDefault() {}});
  const first = notes(render());
  assert.ok(first, 'The rule editor exposes a Notes field');
  first.props.onChange({target: {value: 'Next forecast renewal 17 Dec'}});
  const invalid = render();
  assert.equal(notes(invalid).props['aria-invalid'], true);
  const message = find(invalid, node => node.props.id === notes(invalid).props['aria-describedby']);
  assert.equal(message.props.children, 'Use Next date and Schedule for the next occurrence.');
  submit(invalid);
  assert.deepEqual(saved, []);
  notes(invalid).props.onChange({target: {value: 'price rises in March\npaid 12 Nov by cash'}});
  const valid = render();
  assert.equal(notes(valid).props['aria-invalid'], false);
  submit(valid);
  assert.deepEqual(Array.from(saved[0].rule.notes), ['price rises in March', 'paid 12 Nov by cash']);
  notes(valid).props.onChange({target: {value: 'price rises in April'}});
  request.resolve(true);
  await harness.settle();
  assert.equal(closed, false);
  assert.equal(notes(harness.current).props.value, 'price rises in April');
});
