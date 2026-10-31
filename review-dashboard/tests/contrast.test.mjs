import './test-isolation.mjs';
// WCAG contrast of chrome roles, including tinted selections and Increase Contrast.
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
const css = readFileSync(new URL('../app/styles/tokens.css', import.meta.url), 'utf8');
const rgb = (hex) => hex.match(/[a-f\d]{2}/gi).map((part) => parseInt(part, 16) / 255);
const mix = (a, b, amount) => a.map((value, index) => value * amount + b[index] * (1 - amount));
const luminance = (color) => color.map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
const ratio = (a, b) => { const [low, high] = [luminance(a), luminance(b)].sort((x, y) => x - y); return (high + .05) / (low + .05); };
let checks = 0;
for (const dark of [false, true]) for (const increased of [false, true]) {
  const source = increased ? css : css.split('/* Increase Contrast')[0];
  const tokens = Object.fromEntries([...source.matchAll(/--([\w-]+): light-dark\((#[\da-f]{6}), (#[\da-f]{6})\)/g)].map(([, key, light, night]) => [key, rgb(dark ? night : light)]));
  const surfaces = ['bg', 'sidebar', 'surface', 'surface-raised', 'surface-sunken', 'selection-fill-inactive', 'sidebar-selection-inactive', 'control-fill'].map((key) => tokens[key]);
  const selection = mix(tokens.accent, tokens.surface, .17);
  surfaces.push(selection);
  const measure = (name, foreground, background, minimum) => { const value = ratio(foreground, background); assert.ok(value >= minimum, `${dark ? 'Dark' : 'Light'} ${increased ? 'increased' : 'normal'} ${name}: ${value.toFixed(2)} < ${minimum}`); checks++; return value; };
  for (const role of ['text', 'text-secondary', 'text-tertiary', 'accent', 'positive', 'negative', 'warning']) for (const surface of surfaces) measure(role, tokens[role], surface, 4.5);
  for (const role of ['accent', 'control-border']) for (const surface of surfaces) measure(role, tokens[role], surface, 3);
  for (const [name, background] of [['focused list', selection], ['form or activity focused', tokens['selection-fill-inactive']]]) {
    measure(`Review selected-row marker, ${name}`, tokens.accent, background, 3);
    measure(`Accounts selected-row marker, ${name}`, tokens.accent, background, 3);
    measure(`Entry selected-row marker, ${name}`, tokens.accent, background, 3);
  }
  measure('destructive text', [1, 1, 1], tokens['destructive-fill'], 4.5);
  const fill = dark ? tokens.accent : mix(tokens.accent, [0, 0, 0], .86);
  for (const brightness of [1, 1.08, .92]) measure('filled accent text', tokens['on-accent'].map((v) => Math.min(1, v * brightness)), fill.map((v) => Math.min(1, v * brightness)), 4.5);
  console.log(`${dark ? 'Dark' : 'Light'}, ${increased ? 'Increase Contrast' : 'normal'}: accent on surface ${ratio(tokens.accent, tokens.surface).toFixed(2)}:1; selection ${ratio(tokens.accent, selection).toFixed(2)}:1; filled text ${ratio(tokens['on-accent'], fill).toFixed(2)}:1`);
}
console.log(`${checks} contrast checks passed`);
