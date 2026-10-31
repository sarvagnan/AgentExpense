// Ledger data is fetched at runtime from public/data/*.json.gz (written by `npm run data`),
// so it stays out of the JS bundle. Type-only imports keep the JSON shapes without bundling it.
type RawDashboard = typeof import('./data/dashboard.json');
export type Provenance = {auditId:string;text:string;time:string;savedAt:string|null;questions:{question:string;answer:string|null}[];doubts:string[];models:string[]};
export type DashboardData = Omit<RawDashboard,'transactions'> & {transactions:(Omit<RawDashboard['transactions'][number],'provenance'> & {provenance:Provenance|null})[]};
export type ReviewData = typeof import('./data/pending-transactions.json');
export type FuelData = typeof import('./data/fuel-log.json');

export let dashboard: DashboardData;
export let review: ReviewData;
export let fuel: FuelData;
const listeners = new Set<() => void>();
export const subscribeData = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
let version = 0;
export const dataVersion = () => version;

export function setData(next: { dashboard: DashboardData; review: ReviewData; fuel: FuelData }) {
  ({ dashboard, review, fuel } = next);
  version++;
  listeners.forEach(listener => listener());
}

export async function fetchGzipJson<T>(name: string): Promise<T> {
  const response = await fetch(`${import.meta.env.BASE_URL}data/${name}.json.gz`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  // Static servers send .gz as application/gzip; if one decoded it already, the gzip magic bytes are gone.
  const gzipped = bytes[0] === 0x1f && bytes[1] === 0x8b;
  const body = new Blob([bytes]).stream();
  const text = await new Response(gzipped ? body.pipeThrough(new DecompressionStream('gzip')) : body).text();
  return JSON.parse(text) as T;
}

export async function loadData() {
  const [dashboard, review, fuel] = await Promise.all([
    fetchGzipJson<DashboardData>('dashboard'),
    fetchGzipJson<ReviewData>('pending-transactions'),
    fetchGzipJson<FuelData>('fuel-log'),
  ]);
  setData({ dashboard, review, fuel });
}

// Publish only a complete snapshot. Keep the current dashboard usable if any fetch fails.
let reloading: Promise<void> | null = null;
export function reloadData() {
  if (!reloading) reloading = loadData().finally(() => { reloading = null; });
  return reloading;
}
