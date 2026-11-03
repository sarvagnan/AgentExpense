import { moduleSettings } from '../scripts/ledger-modules.mjs';
import { setCurrentAccounts } from '../scripts/account-metadata.mjs';
import { setMoneySettings } from './formatting';
import { setDateLocale } from './date-time';
// Ledger data is fetched at runtime from public/data/*.json.gz, outside the JS bundle.
import type { DashboardData, ReviewData, FuelData } from './data-types';
export type { DashboardData, ReviewData, FuelData, Provenance } from './data-types';

export let dashboard: DashboardData;
export let review: ReviewData;
export let fuel: FuelData;
const listeners = new Set<() => void>();
export const subscribeData = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
let version = 0;
export const dataVersion = () => version;

export function setData(next: { dashboard: DashboardData; review: ReviewData; fuel: FuelData }) {
  ({ dashboard, review, fuel } = next);
  setCurrentAccounts(dashboard.meta?.accounts);
  setDateLocale(dashboard.meta?.ledger?.locale);
  setMoneySettings(dashboard.meta?.ledger, dashboard.meta?.currencyMinorUnits);
  version++;
  listeners.forEach(listener => listener());
}

export async function fetchGzipJson<T>(name: string): Promise<T> {
  const response = await fetch(`${import.meta.env.BASE_URL}data/${name}.json.gz`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  // Static servers send .gz as application/gzip; if one decoded it already, the gzip magic bytes are gone.
  const gzipped = bytes[0] === 0x1f && bytes[1] === 0x8b;
  // WebKit may back Blob.stream() with a document-bound blob URL. A refresh
  // finishing during navigation must decode bytes without that retired origin.
  const body = new ReadableStream<BufferSource>({start(controller) { controller.enqueue(bytes); controller.close(); }});
  const text = await new Response(gzipped ? body.pipeThrough(new DecompressionStream('gzip')) : body).text();
  return JSON.parse(text) as T;
}

export async function loadData() {
  const [dashboard, review] = await Promise.all([
    fetchGzipJson<DashboardData>('dashboard'),
    fetchGzipJson<ReviewData>('pending-transactions'),
  ]);
  const fuel = moduleSettings(dashboard.meta.ledger).fuel ? await fetchGzipJson<FuelData>('fuel-log') : {meta: {}, entries: []};
  if (moduleSettings(dashboard.meta.ledger).fuel) await import('./styles/fuel.css');
  setData({ dashboard, review, fuel });
}

// Publish only a complete snapshot. Keep the current dashboard usable if any fetch fails.
let reloading: Promise<void> | null = null;
export function reloadData() {
  if (!reloading) reloading = loadData().finally(() => { reloading = null; });
  return reloading;
}
