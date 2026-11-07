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

/**
 * JSON.parse for store documents. A number written with a decimal point or an exponent
 * (`5.0`, `5e0`) stays a Number object, so it is never taken for an integer: pydantic's
 * strict models refuse it where an integer belongs, and so does `validate`.
 */
export function parseStoreJson(text, label = 'JSON') {
  // Fast path: without a number token holding "." or an exponent after ":", "[" or ",", no literal
  // can be one (an amount is text, so its digits follow a quote). Otherwise check every literal.
  const value = !/[:[,]\s*-?\d+(?:\.|[eE])/.test(text) ? JSON.parse(text) : JSON.parse(text, (key, item, context) =>
    typeof item === 'number' && context?.source && /[.eE]/.test(context.source) ? new Number(item) : item);
  // JSON.parse keeps the last of repeated keys; Python refuses them. Count the keys written
  // against the keys kept, and only when they differ find which key repeats.
  if ((text.match(KEY_TOKENS) || []).length !== keyCount(value)) throw new Error(`Invalid ${label}: Duplicate JSON key: ${JSON.stringify(duplicateKey(text))}.`);
  return value;
}

// A string token followed by ":" is an object key: string tokens are matched whole, so none starts inside another.
const KEY_TOKENS = /"(?:[^"\\]|\\.)*"\s*:/g;
const TOKENS = /"(?:[^"\\]|\\.)*"(\s*:)?|[{}[\]]/g;
const keyCount = value => value === null || typeof value !== 'object' ? 0 : Array.isArray(value)
  ? value.reduce((sum, item) => sum + keyCount(item), 0)
  : Object.values(value).reduce((sum, item) => sum + keyCount(item), Object.keys(value).length);
function duplicateKey(text) {
  const open = [];
  for (const [token, colon] of text.matchAll(TOKENS)) {
    if (token === '{' || token === '[') open.push(token === '{' ? new Set() : null);
    else if (token === '}' || token === ']') open.pop();
    else if (colon !== undefined) {
      const key = JSON.parse(token.slice(0, token.length - colon.length)), keys = open.at(-1);
      if (keys.has(key)) return key;
      keys.add(key);
    }
  }
  return null;
}

// Exact decimal text as an integer count of 10^-scale units.
const decimal = text => {
  const [whole, fraction = ''] = String(text).split('.');
  return {units: BigInt(whole + fraction), scale: fraction.length};
};
const scaled = ({units, scale}, to) => units * 10n ** BigInt(to - scale);

/**
 * Each commodity's exact sum, as store_models.balance counts it (as hledger does): a unit
 * cost counts price × quantity; a total cost counts its amount, negated for a negative
 * quantity. Returns the sorted commodities that don't sum to zero.
 */
export function unbalancedCommodities(postings) {
  const totals = new Map();
  const add = (commodity, value) => {
    const current = totals.get(commodity);
    if (!current) return totals.set(commodity, value);
    const scale = Math.max(current.scale, value.scale);
    totals.set(commodity, {units: scaled(current, scale) + scaled(value, scale), scale});
  };
  for (const posting of postings) {
    const amount = decimal(posting.amount);
    if (!posting.cost) { add(posting.commodity, amount); continue; }
    const price = decimal(posting.cost.amount);
    if (posting.cost.per === 'total') add(posting.cost.commodity, amount.units < 0n ? {...price, units: -price.units} : price);
    else add(posting.cost.commodity, {units: price.units * amount.units, scale: price.scale + amount.scale});
  }
  return [...totals].filter(([, value]) => value.units !== 0n).map(([commodity]) => commodity).sort();
}

/** Why postings don't balance exactly (store_models.unbalanced), or null. */
export function unbalanced(postings) {
  const residue = unbalancedCommodities(postings);
  if (!residue.length) return null;
  const commodities = new Set(postings.flatMap(p => [p.commodity, ...(p.cost ? [p.cost.commodity] : [])]));
  return `the postings do not balance exactly in ${residue.join(', ')}`
    + (commodities.size > 1 ? '; amounts in different commodities need a cost (no implicit conversion)' : '');
}

const empty = value => value === null || Array.isArray(value) && !value.length;
const positive = text => decimal(text).units > 0n;
/**
 * The pydantic models' own validators (store_models.py), by $defs name, with their
 * messages: what one entry, posting, rule or statement must satisfy beyond its shape.
 */
const MODEL_CHECKS = {
  Posting: p => p.cost !== null && p.cost.commodity === p.commodity ? 'a cost must be in a different commodity from its posting'
    : p.cost !== null && p.cost.per === 'total' && decimal(p.amount).units === 0n ? 'a total cost needs a non-zero amount' : null,
  Entry: e => {
    const problem = unbalanced(e.postings);
    if (problem) return problem;
    const row = e.statement;
    if (row && row.end !== null && (row.serial === null || row.page === null)) return 'a card statement row needs its serial and page';
    const targets = e['possible-duplicate-of'].map(link => link.id);
    if (targets.join() !== [...new Set(targets)].sort().join() || targets.some(target => target <= e.id))
      return 'possible-duplicate-of needs unique, sorted IDs greater than the entry ID';
    return null;
  },
  Rule: r => {
    const problem = unbalanced(r.postings);
    if (problem) return problem;
    if (r.date !== r.schedule.from) return 'a rule date must equal schedule.from';
    if (r.lifecycle === 'removed' && r.removal === null) return 'a removed rule needs removal';
    if (r.postings.some(p => p.assert !== null)) return 'rule postings have no assert';
    if (r.amount === 'varies' && (r.postings.length !== 2 || new Set(r.postings.map(p => p.commodity)).size !== 1 || r.postings.some(p => p.cost !== null)))
      return 'only simple two-posting payments in one currency can vary';
    if (r.bill !== null && (r.amount !== 'varies' || !positive(r.bill.amount) || r.bill.occurrence !== r.date))
      return 'a bill needs a varying rule, a positive amount and the current occurrence';
    return null;
  },
  BankStatement: s => s['source-balance-style'] === null && s['assertion-id'] === null ? 'a balance-style statement needs assertion-id' : null,
};
const modelCheck = (name, definition, value) =>
  definition['x-group'] && Object.values(value).every(empty) ? 'a group with no values must be null' : MODEL_CHECKS[name]?.(value) ?? null;

// ISO date and time with an offset or Z, real on the calendar and the clock (pydantic: datetime.fromisoformat).
// Like Python, 24:00:00 (with a zero fraction) is the end of the day.
const realDateTime = value => {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (!match || !realDate(match[1]) || +match[3] > 59 || +match[4] > 59 || match[6] !== undefined && (+match[6] > 23 || +match[7] > 59)) return false;
  return +match[2] < 24 || +match[2] === 24 && +match[3] === 0 && +match[4] === 0 && !/[1-9]/.test((match[5] || '').slice(0, 6));
};
const days = new Map();
// Python's dates start at year 1; JavaScript also accepts year 0000.
const realDate = value => days.get(value) ?? days.set(value, /^\d{4}-\d{2}-\d{2}$/.test(value) && !value.startsWith('0000')
  && Number.isFinite(Date.parse(`${value}T00:00:00Z`)) && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value).get(value);

// Per schema node: its non-null anyOf options and its patternProperties as compiled pairs, computed once.
const nonNull = new WeakMap(), patterns = new WeakMap();
const optionsOf = schema => nonNull.get(schema) ?? nonNull.set(schema, schema.anyOf.filter(option => option.type !== 'null')).get(schema);
const patternsOf = schema => patterns.get(schema)
  ?? patterns.set(schema, Object.entries(schema.patternProperties || {}).map(([pattern, rule]) => [expression(pattern), rule])).get(schema);

export function validate(value, schema, field = 'ledger.json', rootSchema = schema) {
  if (schema.oneOf && schema.discriminator) {
    // A tagged union (pydantic's discriminator): the tag picks the one shape, and its error is reported.
    const tag = value?.[schema.discriminator.propertyName];
    const target = typeof tag === 'string' && Object.hasOwn(schema.discriminator.mapping, tag) ? schema.discriminator.mapping[tag] : null;
    if (!target || value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error(`Invalid ${field}.`);
    return validate(value, {$ref: target}, `${field}.${tag}`, rootSchema);
  }
  if (schema.anyOf) {
    // A nullable field with a value reports what is wrong inside it, not only that it is invalid.
    const options = value === null ? schema.anyOf : optionsOf(schema);
    if (options.length === 1) return validate(value, options[0], field, rootSchema);
    if (!schema.anyOf.some(option => { try { validate(value, option, field, rootSchema); return true; } catch { return false; } })) throw new Error(`Invalid ${field}.`);
    return;
  }
  if (schema.$ref) {
    const name = schema.$ref.slice('#/$defs/'.length), definition = rootSchema.$defs[name];
    validate(value, definition, field, rootSchema);
    const problem = value !== null && typeof value === 'object' && !Array.isArray(value) ? modelCheck(name, definition, value) : null;
    if (problem) throw new Error(`Invalid ${field}: ${problem}.`);
    return;
  }
  const types = Array.isArray(schema.type) ? schema.type : [schema.type];
  // A boxed Number is a number written with a decimal point or exponent (parseStoreJson): never an integer.
  const type = value === null ? 'null' : Array.isArray(value) ? 'array' : value instanceof Number ? 'number'
    : typeof value === 'number' && Number.isInteger(value) ? 'integer' : typeof value;
  if (schema.type && !types.includes(type) && !(type === 'integer' && types.includes('number'))) throw new Error(`Invalid ${field}: expected ${types.join(' or ')}.`);
  if ('const' in schema && value !== schema.const) throw new Error(`Invalid ${field}.`);
  if (schema.enum && !schema.enum.includes(value)) throw new Error(`Invalid ${field}.`);
  if (typeof value === 'number' && (schema.minimum !== undefined && value < schema.minimum || schema.maximum !== undefined && value > schema.maximum)) throw new Error(`Invalid ${field}.`);
  if (schema.not?.pattern && typeof value === 'string' && expression(schema.not.pattern).test(value)) throw new Error(`Invalid ${field}.`);
  if (typeof value === 'string') {
    if (schema.minLength && value.length < schema.minLength || schema.pattern && !expression(schema.pattern).test(value)) throw new Error(`Invalid ${field}.`);
    if (schema.format === 'uuid' && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) throw new Error(`Invalid ${field}.`);
    if (schema.format === 'date' && !realDate(value)) throw new Error(`Invalid ${field}.`);
    if (schema.format === 'date-time' && !realDateTime(value)) throw new Error(`Invalid ${field}.`);
  }
  if (type === 'array') {
    if (schema.minItems && value.length < schema.minItems) throw new Error(`Invalid ${field}: too few items.`);
    if (schema.uniqueItems && new Set(value.map(item => JSON.stringify(item))).size !== value.length) throw new Error(`Invalid ${field}: duplicate items.`);
    if (schema.items) value.forEach((item, index) => validate(item, schema.items, `${field}.${index}`, rootSchema));
  }
  if (type === 'object') {
    for (const key of schema.required || []) if (!(key in value)) throw new Error(`Invalid ${field}: missing ${key}.`);
    if (schema.minProperties && Object.keys(value).length < schema.minProperties) throw new Error(`Invalid ${field}: too few properties.`);
    const compiled = patternsOf(schema);
    for (const [key, item] of Object.entries(value)) {
      if (schema.propertyNames) validate(key, schema.propertyNames, field, rootSchema);
      const matches = compiled.length ? compiled.filter(([pattern]) => pattern.test(key)) : compiled;
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
    const clash = tags.filter(tag => schema['x-reserved-journal-tags'].includes(tag)).sort();
    if (clash.length) throw new Error(`extensions/${name}.json is not a valid extension definition: journal tags clash with core labels: ${clash.join(', ')}`);
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
    schema.$defs[group] = {additionalProperties: false, description: definition.description, properties: fields, required: Object.keys(fields), type: 'object', 'x-group': true};
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
