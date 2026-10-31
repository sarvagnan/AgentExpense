import { useEffect, useRef, useState, type SetStateAction } from 'react';

type State = 'loaded' | 'absent' | 'unreadable' | 'damaged' | 'unstored';
type Snapshot<T> = { data: Record<string, T>; state: State; raw?: string };
const memory = new Map<string, { data: Record<string, unknown>; baseline: Record<string, unknown> }>();
type Recovery<T> = Record<string, T | null>;
// Separate from the currently displayed snapshot: remote saves must never erase this copy.
const recoveryCopies = new Map<string, Recovery<unknown>>();
function retainRecovery<T>(key: string, data: Record<string, T>, baseline: Record<string, T>) {
  const changes: Recovery<T> = { ...recoveryCopies.get(key) } as Recovery<T>;
  for (const id of new Set([...Object.keys(data), ...Object.keys(baseline)])) {
    if (!equal(data[id], baseline[id])) changes[id] = data[id] ?? null;
  }
  if (Object.keys(changes).length) recoveryCopies.set(key, changes);
}
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const listeners = new Set<() => void>();
let started = false;
let editable = typeof navigator === 'undefined' || !navigator.locks;
let request = 0;
const notify = () => listeners.forEach((listener) => listener());
/** The lock belongs to the tab, including while Review is unmounted. */
function acquire(steal = false) {
  if (typeof navigator === 'undefined' || !navigator.locks) return;
  started = true;
  const generation = ++request;
  navigator.locks.request('expense-review-editor', { steal }, async () => {
    if (generation !== request) return;
    editable = true; window.dispatchEvent(new Event('review-editor-acquired')); notify();
    await new Promise<void>(() => {});
  }).catch(() => {
    if (generation !== request) return;
    editable = false; notify();
    // WebKit rejects held requests while tearing down a document, then rejects new
    // requests in that document too. Retrying in this promise's microtask would
    // starve navigation forever. A task lets teardown finish; a live tab still
    // queues for editing after its lock is stolen. A newer takeover wins the race.
    window.setTimeout(() => { if (generation === request) acquire(); }, 0);
  });
}
export function useReviewEditor() {
  const [allowed, setAllowed] = useState(editable);
  useEffect(() => {
    const listener = () => setAllowed(editable);
    listeners.add(listener);
    if (!started) acquire();
    listener();
    return () => { listeners.delete(listener); };
  }, []);
  return { editable: allowed, canEdit: () => editable, takeOver: () => acquire(true) };
}
function read<T>(key: string, valid: (value: unknown) => value is T): Snapshot<T> {
  let raw: string | null;
  try { raw = localStorage.getItem(key); } catch { return { data: {}, state: 'unreadable' }; }
  if (raw === null) return { data: {}, state: 'absent' };
  try {
    const data = JSON.parse(raw);
    if (!data || typeof data !== 'object' || Array.isArray(data) || !Object.entries(data).every(([id, value]) => id && valid(value))) throw Error();
    return { data, state: 'loaded' };
  } catch { return { data: {}, state: 'damaged', raw }; }
}
if (typeof window !== 'undefined') window.addEventListener('beforeunload', (event) => {
  if (!memory.size && !recoveryCopies.size) return;
  event.preventDefault(); event.returnValue = '';
});

/** A protected working copy; only the editing tab persists changes. */
export function useWorkStore<T>(key: string, valid: (value: unknown) => value is T, onAdopt: () => void, allowed: boolean) {
  const [initial] = useState(() => {
    const fresh = read(key, valid);
    const retained = memory.get(key);
    const keep = retained && (fresh.state === 'unreadable' || fresh.state === 'damaged' || equal(retained.baseline, fresh.data)) ? retained : undefined;
    if (retained && !keep) {
      retainRecovery(key, retained.data, retained.baseline); memory.delete(key);
    }
    return { ...fresh, state: keep && fresh.state !== 'unreadable' && fresh.state !== 'damaged' ? 'unstored' as const : fresh.state, data: keep?.data as Record<string, T> || fresh.data, baseline: keep?.baseline as Record<string, T> || fresh.data };
  });
  const [data, renderData] = useState(initial.data);
  const [state, setState] = useState<State>(initial.state);
  const [raw, setRaw] = useState(initial.raw);
  const [recovery, setRecovery] = useState(() => recoveryCopies.get(key) as Recovery<T> | undefined);
  const baseline = useRef(initial.baseline);
  const current = useRef(data);
  const setData = (update: SetStateAction<Record<string, T>>) => {
    if (!editable) return;
    current.current = typeof update === 'function' ? update(current.current) : update;
    memory.set(key, { data: current.current, baseline: baseline.current });
    renderData(current.current);
  };
  const adopt = useRef(onAdopt); adopt.current = onAdopt;
  const blocked = state === 'unreadable' || state === 'damaged';
  useEffect(() => {
    if (equal(data, baseline.current) && state !== 'unstored') { memory.delete(key); return; }
    memory.set(key, { data, baseline: baseline.current });
    if (!allowed || !editable || blocked) return;
    try {
      localStorage.setItem(key, JSON.stringify(data)); baseline.current = data;
      memory.delete(key); setState('loaded'); window.dispatchEvent(new Event('review-progress'));
    } catch { setState('unstored'); }
  }, [key, data, blocked, state, allowed]);
  const accept = (next: Snapshot<T>) => {
    setState(next.state); setRaw(next.raw);
    if (next.state === 'unreadable' || next.state === 'damaged') return;
    if (!equal(current.current, baseline.current)) {
      retainRecovery(key, current.current, baseline.current);
      setRecovery(recoveryCopies.get(key) as Recovery<T> | undefined);
    }
    baseline.current = next.data; current.current = next.data;
    renderData(next.data); memory.delete(key); adopt.current();
  };
  const acceptRef = useRef(accept); acceptRef.current = accept;
  useEffect(() => {
    const listener = (event: StorageEvent) => { if (event.key === key || event.key === null) acceptRef.current(read(key, valid)); };
    const refresh = () => { const next = read(key, valid); if (!equal(next.data, baseline.current) || next.state === 'unreadable' || next.state === 'damaged') acceptRef.current(next); };
    window.addEventListener('storage', listener); window.addEventListener('review-editor-acquired', refresh);
    return () => { window.removeEventListener('storage', listener); window.removeEventListener('review-editor-acquired', refresh); };
  }, [key, valid]);
  const recoveredData = () => {
    const next = { ...current.current };
    for (const [id, value] of Object.entries(recovery || {})) { if (value === null) delete next[id]; else next[id] = value; }
    return next;
  };
  return { data, setData, current, state, raw, recovery, recoveredData,
    discardRecovery: () => { recoveryCopies.delete(key); setRecovery(undefined); },
    saved: !blocked && state !== 'unstored',
    retry: () => accept(read(key, valid)),
    replace: () => { if (!editable) return; if (raw !== undefined) try { localStorage.setItem(`${key}-damaged-${Date.now()}`, raw); } catch { /* Download preserves original bytes. */ } baseline.current = {}; setRaw(undefined); setState('unstored'); }
  };
}
