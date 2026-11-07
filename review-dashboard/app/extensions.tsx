import { Fragment } from 'react';
import { dashboard } from './data';
import { dayLabel } from './ui';
import type { DashboardTransaction, ExtensionDefinition, ExtensionField, ExtensionGroups, ExtensionValue } from './data-types';

/** Extensions are generic: each field is shown and edited by its type, never by extension-specific screens. */
const definitions = (): ExtensionDefinition[] => dashboard.meta.extensions || [];
/** "trip-km" → "Trip km"; an extension's or field's name read as words. */
export const nameLabel = (name: string) => name.charAt(0).toUpperCase() + name.slice(1).replaceAll('-', ' ');

export function valueText(field: ExtensionField, value: ExtensionValue): string {
  if (value === null || value === undefined) return '';
  if (field.type === 'yes-no') return value ? 'Yes' : 'No';
  if (field.type === 'date' && typeof value === 'string') return dayLabel(value);
  return String(value);
}

/** Entry details: each extension that holds values, with its field names. */
export function ExtensionFacts({ transaction }: { transaction: Pick<DashboardTransaction, 'extensions'> }) {
  const shown = definitions().filter(item => transaction.extensions?.[item.name]);
  return <>{shown.map(item => {
    const group = transaction.extensions[item.name]!;
    const fields = Object.entries(item.fields).filter(([key]) => group[key] !== null && group[key] !== undefined);
    return <section key={item.name} className="inspector-section" aria-label={nameLabel(item.name)}><h3>{nameLabel(item.name)}</h3>
      <dl className="facts">{fields.map(([key, field]) => <Fragment key={key}><dt title={field.description}>{nameLabel(key)}</dt><dd>{valueText(field, group[key])}</dd></Fragment>)}</dl>
    </section>;
  })}</>;
}

/** Turn a control's text into the field's stored value: null when empty. */
export function parseValue(field: ExtensionField, text: string): ExtensionValue {
  if (text === '') return null;
  if (field.type === 'whole-number') return /^\d+$/.test(text) ? Number(text) : text;
  if (field.type === 'yes-no') return text === 'yes';
  return text;
}

function Control({ id, field, value, onChange, disabled }: { id: string; field: ExtensionField; value: ExtensionValue; onChange: (value: ExtensionValue) => void; disabled?: boolean }) {
  const text = value === null || value === undefined ? '' : field.type === 'yes-no' ? (value ? 'yes' : 'no') : String(value);
  const set = (next: string) => onChange(parseValue(field, next));
  if (field.type === 'yes-no') return <select id={id} className="field" value={text} disabled={disabled} onChange={e => set(e.target.value)}><option value="">Not given</option><option value="yes">Yes</option><option value="no">No</option></select>;
  if (field.type === 'choice') return <select id={id} className="field" value={text} disabled={disabled} onChange={e => set(e.target.value)}><option value="">Not given</option>{(field.values || []).map(option => <option key={option} value={option}>{option}</option>)}</select>;
  if (field.type === 'date') return <input id={id} type="date" className="field" value={text} disabled={disabled} onChange={e => set(e.target.value)}/>;
  if (field.type === 'whole-number') return <input id={id} className="field" inputMode="numeric" pattern="[0-9]*" value={text} disabled={disabled} onChange={e => set(e.target.value.trim())}/>;
  return <input id={id} className="field" inputMode={field.type === 'decimal' ? 'decimal' : undefined} value={text} disabled={disabled} onChange={e => set(field.type === 'decimal' ? e.target.value.trim() : e.target.value)}/>;
}

/** Edit entry and the hand form: one control per field of every listed extension. */
export function ExtensionFields({ idPrefix, value, onChange, disabled }: { idPrefix: string; value: ExtensionGroups; onChange: (value: ExtensionGroups) => void; disabled?: boolean }) {
  const listed = definitions().filter(item => Object.hasOwn(value, item.name));
  return <>{listed.map(item => {
    const group = value[item.name];
    const update = (key: string, next: ExtensionValue) => {
      const values = Object.fromEntries(Object.keys(item.fields).map(name => [name, name === key ? next : group?.[name] ?? null]));
      onChange({ ...value, [item.name]: Object.values(values).some(v => v !== null) ? values : null });
    };
    return <fieldset key={item.name} className="hand-extension"><legend className="sr-only">{nameLabel(item.name)}</legend><p className="hand-extension-title" aria-hidden="true">{nameLabel(item.name)}</p>
      {Object.entries(item.fields).map(([key, field]) => {
        const id = `${idPrefix}-${item.name}-${key}`;
        return <label key={key} htmlFor={id}>{nameLabel(key)}<Control id={id} field={field} value={group?.[key] ?? null} disabled={disabled} onChange={next => update(key, next)}/><small className="live-hint">{field.description}</small></label>;
      })}
    </fieldset>;
  })}</>;
}
