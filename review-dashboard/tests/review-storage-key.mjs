// Browser fixtures must exercise the namespace used by the current Review UI.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../app/review/page.tsx', import.meta.url), 'utf8');
export const reviewStorageKey = source.match(/const STORAGE_KEY = '([^']+)'/)?.[1];
assert.ok(reviewStorageKey, 'Review must declare its decision storage key');
