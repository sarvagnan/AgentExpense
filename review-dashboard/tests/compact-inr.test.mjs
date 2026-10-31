import './test-isolation.mjs';
import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { createTestServer } from './test-isolation.mjs';

const server = await createTestServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' });
after(() => server.close());
const { compactMoney: compactInr, setMoneySettings } = await server.ssrLoadModule('/app/formatting.ts');
setMoneySettings({locale:'en-IN', defaultCurrency:'INR'});

test('negative compact amounts use a true minus before the rupee sign', () => {
  for (const [value, expected] of [[-740, '−₹740'], [-7914.77, '−₹8K'], [-150000, '−₹1.5L']]) {
    assert.equal(compactInr(value), expected);
    assert.equal(compactInr(value).replace('₹', ''), expected.replace('₹', ''), 'calendar keeps the true minus');
  }
});

test('negative compact amounts round symmetrically into and within thousands', () => {
  for (const [value, expected] of [[-999.49, '−₹999'], [-999.5, '−₹1K'], [-1000, '−₹1K'], [-1499.99, '−₹1K'], [-1500, '−₹2K']]) {
    assert.equal(compactInr(value), expected);
    assert.equal(compactInr(value), `−${compactInr(-value)}`);
  }
});

test('negative compact amounts round symmetrically into and within lakhs', () => {
  for (const [value, expected] of [[-99949.99, '−₹100K'], [-99950, '−₹1.0L'], [-100000, '−₹1.0L'], [-145000, '−₹1.5L'], [-165294, '−₹1.7L']]) {
    assert.equal(compactInr(value), expected);
    assert.equal(compactInr(value), `−${compactInr(-value)}`);
  }
});

test('compact amounts that round to zero have no negative sign', () => {
  for (const value of [-0.4999, -0.01, -Number.EPSILON, -0, 0, 0.4999]) assert.equal(compactInr(value), '₹0');
  assert.equal(compactInr(-0.5), '−₹1');
  assert.equal(compactInr(0.5), '₹1');
});

test('positive compact rounding and units stay consistent at the K and L boundaries', () => {
  for (const [value, expected] of [[740, '₹740'], [999.49, '₹999'], [999.5, '₹1K'], [45000, '₹45K'], [99949.99, '₹100K'], [99950, '₹1.0L'], [100000, '₹1.0L'], [165294, '₹1.7L']]) {
    assert.equal(compactInr(value), expected);
  }
});
