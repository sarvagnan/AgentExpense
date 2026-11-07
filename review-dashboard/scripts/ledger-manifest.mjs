import { moduleSettings } from './ledger-modules.mjs';
export { moduleSettings, hasExtension, priceSource } from './ledger-modules.mjs';
// Shared format gate; validate the application-owned manifest schema before defaults.
import { readFile } from 'node:fs/promises';
import { ledgerRoot, applicationRoot } from '../../scripts/project-paths.mjs';
import path from 'node:path';

export const CURRENT_FORMAT = 2;
// Built-in extensions live in the application's extensions/; a ledger can't define its own with these names.
export const BUILT_IN_EXTENSIONS = new Set(['fuel']);
const expressions = new Map();
const expression = pattern => expressions.get(pattern) ?? expressions.set(pattern, new RegExp(pattern)).get(pattern);
export function validate(value, schema, field = 'ledger.json', rootSchema = schema) {
  if (schema.anyOf) {
    // A nullable field with a value reports what is wrong inside it, not only that it is invalid.
    const options = value === null ? schema.anyOf : schema.anyOf.filter(option => option.type !== 'null');
    if (options.length === 1) return validate(value, options[0], field, rootSchema);
    if (!schema.anyOf.some(option => { try { validate(value, option, field, rootSchema); return true; } catch { return false; } })) throw new Error(`Invalid ${field}.`);
    return;
  }
  if (schema.$ref) return validate(value, rootSchema.$defs[schema.$ref.slice('#/$defs/'.length)], field, rootSchema);
  const types = Array.isArray(schema.type) ? schema.type : [schema.type];
  const type = value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value === 'number' && Number.isInteger(value) ? 'integer' : typeof value;
  if (schema.type && !types.includes(type)) throw new Error(`Invalid ${field}: expected ${types.join(' or ')}.`);
  if ('const' in schema && value !== schema.const) throw new Error(`Invalid ${field}.`);
  if (schema.enum && !schema.enum.includes(value)) throw new Error(`Invalid ${field}.`);
  if (typeof value === 'number' && (schema.minimum !== undefined && value < schema.minimum || schema.maximum !== undefined && value > schema.maximum)) throw new Error(`Invalid ${field}.`);
  if (schema.not?.pattern && typeof value === 'string' && expression(schema.not.pattern).test(value)) throw new Error(`Invalid ${field}.`);
  if (typeof value === 'string') {
    if (schema.minLength && value.length < schema.minLength || schema.pattern && !expression(schema.pattern).test(value)) throw new Error(`Invalid ${field}.`);
    if (schema.format === 'uuid' && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) throw new Error(`Invalid ${field}.`);
    if (schema.format === 'date' && (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(Date.parse(`${value}T00:00:00Z`)) || new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) !== value)) throw new Error(`Invalid ${field}.`);
  }
  if (type === 'array') {
    if (schema.minItems && value.length < schema.minItems) throw new Error(`Invalid ${field}: too few items.`);
    if (schema.uniqueItems && new Set(value.map(item => JSON.stringify(item))).size !== value.length) throw new Error(`Invalid ${field}: duplicate items.`);
    if (schema.items) value.forEach((item, index) => validate(item, schema.items, `${field}.${index}`, rootSchema));
  }
  if (type === 'object') {
    for (const key of schema.required || []) if (!(key in value)) throw new Error(`Invalid ${field}: missing ${key}.`);
    if (schema.minProperties && Object.keys(value).length < schema.minProperties) throw new Error(`Invalid ${field}: too few properties.`);
    for (const [key, item] of Object.entries(value)) {
      if (schema.propertyNames) validate(key, schema.propertyNames, field, rootSchema);
      const matches = Object.entries(schema.patternProperties || {}).filter(([pattern]) => expression(pattern).test(key));
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
  return { ...value, modules: moduleSettings(value), extensions: value.extensions,
    review: { storageKey: null, ...value.review } };
}

const readJson = async file => JSON.parse(await readFile(file, 'utf8'));

/** The extensions ledger.json lists, in order: {name, ask, definition}, checked like the Python store does. */
export async function readExtensions(root = ledgerRoot(), manifest) {
  manifest ??= await readManifest(root);
  const schema = await readJson(path.join(applicationRoot, 'schema/extension-definition.schema.json'));
  const result = [], owners = new Map();
  for (const [name, settings] of Object.entries(manifest.extensions || {})) {
    const own = path.join(root, 'extensions', `${name}.json`);
    let file = own;
    if (BUILT_IN_EXTENSIONS.has(name)) {
      if (await readFile(own).then(() => true, () => false)) throw new Error(`extensions/${name}.json: "${name}" is a built-in extension; choose another name`);
      file = path.join(applicationRoot, 'extensions', `${name}.json`);
    }
    let definition;
    try { definition = await readJson(file); }
    catch (error) { throw new Error(error.code === 'ENOENT' ? `ledger.json lists the extension "${name}", but extensions/${name}.json is missing` : `extensions/${name}.json is not valid JSON`); }
    validate(definition, schema, `extensions/${name}.json`);
    if (definition.name !== name) throw new Error(`extensions/${name}.json: the name must match the file name`);
    const tags = Object.values(definition.fields).map(field => field['journal-tag']);
    if (new Set(tags).size !== tags.length) throw new Error(`extensions/${name}.json: journal tags must differ`);
    for (const tag of tags) {
      if (owners.has(tag)) throw new Error(`The journal tag "${tag}" of extension "${name}" clashes with extension "${owners.get(tag)}"`);
      owners.set(tag, name);
    }
    result.push({name, ask: settings.ask, definition});
  }
  return result;
}

const pascal = name => name.split('-').map(part => part[0].toUpperCase() + part.slice(1)).join('');

/**
 * A ledger's store schema: the core schema with each extension's group composed into
 * $defs.Extensions, using the type map in schema/extension-types.json. The Python
 * models compose the same schema (store_models.compose_store_schema); tests compare them.
 */
export function composeStoreSchema(core, extensions, types) {
  if (!extensions.length) return core;
  const schema = structuredClone(core), properties = {};
  for (const {name, definition} of extensions) {
    const group = `${pascal(name)}Extension`, fields = {};
    for (const [key, item] of Object.entries(definition.fields)) {
      const value = {...types[item.type]};
      if (item.type === 'choice') value.enum = [...item.values];
      fields[key] = {anyOf: [value, {type: 'null'}], description: item.description, 'x-journal-tag': item['journal-tag'],
        'x-set-by': item['filled-by'] === 'agent' ? ['agent', 'person'] : ['person']};
    }
    schema.$defs[group] = {additionalProperties: false, description: definition.description, properties: fields, required: Object.keys(fields), type: 'object'};
    properties[name] = {anyOf: [{$ref: `#/$defs/${group}`}, {type: 'null'}], description: definition.description,
      'x-set-by': [...new Set(Object.values(fields).flatMap(field => field['x-set-by']))]};
  }
  schema.$defs.Extensions = {...schema.$defs.Extensions, properties, required: Object.keys(properties)};
  return schema;
}

/** The selected ledger's composed store schema and its listed extensions. */
export async function storeSchema(root = ledgerRoot(), manifest) {
  manifest ??= await readManifest(root);
  const extensions = await readExtensions(root, manifest);
  const [core, types] = await Promise.all(['store.schema.json', 'extension-types.json'].map(name => readJson(path.join(applicationRoot, 'schema', name))));
  return {schema: composeStoreSchema(core, extensions, types), extensions};
}

/** Validate one store document against the definition x-documents names for its path. */
export function validateDocument(value, schema, documentPath, label = documentPath) {
  const reference = schema['x-documents'][documentPath];
  validate(value, schema.$defs[reference.slice('#/$defs/'.length)], label, schema);
}
