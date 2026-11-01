import { moduleSettings } from './ledger-modules.mjs';
export { moduleSettings, priceSource } from './ledger-modules.mjs';
// Shared format gate; validate the application-owned manifest schema before defaults.
import { readFile } from 'node:fs/promises';
import { ledgerRoot, applicationRoot } from '../../scripts/project-paths.mjs';
import path from 'node:path';

export const CURRENT_FORMAT = 1;
export function validate(value, schema, field = 'ledger.json', rootSchema = schema) {
  if (schema.anyOf) {
    if (!schema.anyOf.some(option => { try { validate(value, option, field, rootSchema); return true; } catch { return false; } })) throw new Error(`Invalid ${field}.`);
    return;
  }
  if (schema.$ref) return validate(value, rootSchema.$defs[schema.$ref.slice('#/$defs/'.length)], field, rootSchema);
  const types = Array.isArray(schema.type) ? schema.type : [schema.type];
  const type = value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value === 'number' && Number.isInteger(value) ? 'integer' : typeof value;
  if (schema.type && !types.includes(type)) throw new Error(`Invalid ${field}: expected ${types.join(' or ')}.`);
  if ('const' in schema && value !== schema.const) throw new Error(`Invalid ${field}.`);
  if (schema.enum && !schema.enum.includes(value)) throw new Error(`Invalid ${field}.`);
  if (schema.not?.pattern && typeof value === 'string' && new RegExp(schema.not.pattern).test(value)) throw new Error(`Invalid ${field}.`);
  if (typeof value === 'string') {
    if (schema.minLength && value.length < schema.minLength || schema.pattern && !new RegExp(schema.pattern).test(value)) throw new Error(`Invalid ${field}.`);
    if (schema.format === 'uuid' && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) throw new Error(`Invalid ${field}.`);
    if (schema.format === 'date' && (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(Date.parse(`${value}T00:00:00Z`)) || new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) !== value)) throw new Error(`Invalid ${field}.`);
  }
  if (type === 'array') {
    if (schema.uniqueItems && new Set(value.map(item => JSON.stringify(item))).size !== value.length) throw new Error(`Invalid ${field}: duplicate items.`);
    if (schema.items) value.forEach((item, index) => validate(item, schema.items, `${field}.${index}`, rootSchema));
  }
  if (type === 'object') {
    for (const key of schema.required || []) if (!(key in value)) throw new Error(`Invalid ${field}: missing ${key}.`);
    for (const [key, item] of Object.entries(value)) {
      if (schema.propertyNames) validate(key, schema.propertyNames, field, rootSchema);
      const matches = Object.entries(schema.patternProperties || {}).filter(([pattern]) => new RegExp(pattern).test(key));
      for (const [, rule] of matches) validate(item, rule, `${field}.${key}`, rootSchema);
      if (Object.hasOwn(schema.properties || {}, key)) {
        validate(item, schema.properties[key], `${field}.${key}`, rootSchema);
      } else if (!matches.length) {
        if (schema.additionalProperties === false) throw new Error(`Invalid ${field}: unexpected ${key}.`);
        if (typeof schema.additionalProperties === 'object') validate(item, schema.additionalProperties, `${field}.${key}`, rootSchema);
      }
    }
  }
}
export async function readManifest(root = ledgerRoot()) {
  let value;
  try { value = JSON.parse(await readFile(path.join(root, 'ledger.json'), 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw error; value = { format: 0 }; }
  const version = value?.format;
  if (!Number.isInteger(version) || version < 0) throw new Error('Invalid ledger format.');
  if (version < CURRENT_FORMAT) throw new Error(`This ledger uses format ${version}; run make upgrade-ledger.`);
  if (version > CURRENT_FORMAT) throw new Error(`This app reads format ${CURRENT_FORMAT}; this ledger uses format ${version}; a newer AgentExpense is needed.`);
  const schema = JSON.parse(await readFile(path.join(applicationRoot, 'schema/ledger.schema.json'), 'utf8'));
  validate(value, schema);
  return { ...value, modules: moduleSettings(value),
    review: { storageKey: null, ...value.review } };
}
