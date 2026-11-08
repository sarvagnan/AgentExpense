import { hasExtension } from '../scripts/ledger-modules.mjs';
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
export type DataStatus = {version:number;epoch?:string;generation:string|null;data_stale:boolean;updating:boolean;refresh_error?:string|null};
export let dataStatus:DataStatus|null = null;
let backendStatus:DataStatus|null=null, publishedGeneration:string|null|undefined;
const retiredEpochs=new Set<DataStatus['epoch']>();
export async function retryDataRefresh() {
  try {
    const response=await fetch(`${import.meta.env.BASE_URL}api/data-refresh`,{method:'POST'});
    if(!response.ok)throw new Error('The saved views could not refresh.');
    const status:unknown=await response.json();
    if(!validStatus(status))throw new Error('The saved views could not refresh.');
    observeDataStatus(status);
  } catch(error) {
    if(dataStatus){dataStatus={...dataStatus,data_stale:true,refresh_error:error instanceof Error?error.message:String(error)};notifyStatus();}
  }
}
let readyTimer:ReturnType<typeof setTimeout>|undefined, statusReading=false, leaving=false;
const validStatus=(value:unknown):value is DataStatus=>!!value&&typeof value==='object'&&Number.isSafeInteger((value as DataStatus).version)&&typeof (value as DataStatus).data_stale==='boolean'&&typeof (value as DataStatus).updating==='boolean'&&((value as DataStatus).generation===null||typeof (value as DataStatus).generation==='string');
const notifyStatus=()=>{version++;listeners.forEach(listener=>listener());};
function pollReadiness(delay=250) {
  clearTimeout(readyTimer);readyTimer=undefined;
  if(!leaving&&backendStatus?.data_stale&&!backendStatus.refresh_error&&!document.hidden)readyTimer=setTimeout(()=>{readyTimer=undefined;void checkDataReady();},delay);
}
/** Saves answer at commit. Keep the last complete views, visibly stale, until ready. */
export function observeDataStatus(status?:DataStatus, refresh=true) {
  if(!status||leaving)return;
  if(retiredEpochs.has(status.epoch))return;
  if(backendStatus&&backendStatus.epoch===status.epoch&&status.version<backendStatus.version)return;
  if(backendStatus&&backendStatus.epoch!==status.epoch)retiredEpochs.add(backendStatus.epoch);
  backendStatus=status;
  const previous=dataStatus;
  dataStatus={...status,data_stale:status.data_stale||!!dashboard&&publishedGeneration!==status.generation};
  if(previous?.data_stale!==dataStatus.data_stale||previous?.refresh_error!==status.refresh_error||previous?.updating!==status.updating)notifyStatus();
  clearTimeout(readyTimer);readyTimer=undefined;
  if(status.data_stale && !status.refresh_error && !document.hidden&&!statusReading)pollReadiness();
  else if(refresh&&!status.data_stale&&dataStatus.data_stale)void reloadData().catch(()=>{
    if(dataStatus){dataStatus={...dataStatus,data_stale:true,refresh_error:'The saved views could not load.'};notifyStatus();}
  });
}
async function checkDataReady() {
  if(statusReading||leaving)return;statusReading=true;
  let failed=false;
  try {const response=await fetch(`${import.meta.env.BASE_URL}api/data-status`,{cache:'no-store'});if(!response.ok)throw new Error('Data status unavailable');const next:unknown=await response.json();if(!validStatus(next))throw new Error('Invalid data status');observeDataStatus(next);}
  catch {failed=true;}
  finally {statusReading=false;pollReadiness(failed?2000:250);}
}
if(typeof window!=='undefined') {
  window.addEventListener('pagehide',()=>{leaving=true;clearTimeout(readyTimer);readyTimer=undefined;});
  window.addEventListener('pageshow',()=>{leaving=false;if(dataStatus?.data_stale)void checkDataReady();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(readyTimer);readyTimer=undefined;}else if(dataStatus?.data_stale)void checkDataReady();});
}

export function setData(next: { dashboard: DashboardData; review: ReviewData; fuel: FuelData }) {
  ({ dashboard, review, fuel } = next);
  setCurrentAccounts(dashboard.meta?.accounts);
  setDateLocale(dashboard.meta?.ledger?.locale);
  setMoneySettings(dashboard.meta?.ledger, dashboard.meta?.currencyMinorUnits);
  version++;
  listeners.forEach(listener => listener());
}

export async function fetchGzipJson<T>(name: string, generation:string|null|undefined=publishedGeneration): Promise<T> {
  const response = await fetch(`${import.meta.env.BASE_URL}${generation?`api/data/${generation}/`:'data/'}${name}.json.gz`, { cache: 'no-store' });
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

function waitForInitialReady() {
  return new Promise<void>(resolve=>{
    let unsubscribe=()=>{};
    const check=()=>{
      if(leaving||backendStatus?.data_stale&&backendStatus.updating&&!backendStatus.refresh_error)return;
      unsubscribe();window.removeEventListener('pageshow',check);resolve();
    };
    unsubscribe=subscribeData(check);window.addEventListener('pageshow',check);check();
  });
}

export async function loadData() {
 for(;;) {
  if(leaving){if(dashboard)return;await waitForInitialReady();continue;}
  let status:DataStatus|null=null;
  const response=await fetch(`${import.meta.env.BASE_URL}api/data-status`,{cache:'no-store'});
  if(!response.ok&&response.status!==404)throw new Error(`data-status: HTTP ${response.status}`);
  const value:unknown=response.ok?await response.json().catch(()=>null):null;
  if(leaving){if(dashboard)return;await waitForInitialReady();continue;}
  if(validStatus(value))status=value;
  else if(backendStatus)throw new Error('The saved views are temporarily unavailable.');
  if(status)observeDataStatus(status,false);
  if(status?.data_stale&&dashboard)return;
  if(status?.data_stale&&status.updating&&!status.refresh_error){await waitForInitialReady();continue;}
  const generation=status?.generation;
  const [loadedDashboard, review] = await Promise.all([
    fetchGzipJson<DashboardData>('dashboard',generation),
    fetchGzipJson<ReviewData>('pending-transactions',generation),
  ]);
  const fuel = hasExtension(loadedDashboard.meta.ledger, 'fuel') ? await fetchGzipJson<FuelData>('fuel-log',generation) : {meta: {}, entries: []};
  if (hasExtension(loadedDashboard.meta.ledger, 'fuel')) await import('./styles/fuel.css');
  if(leaving){if(dashboard)return;await waitForInitialReady();continue;}
  const pending=backendStatus?.data_stale&&backendStatus.updating&&!backendStatus.refresh_error;
  if(status&&backendStatus&&(backendStatus.generation!==generation||backendStatus.data_stale&&(!!dashboard||pending))){
    if(dashboard)return;
    await waitForInitialReady();continue;
  }
  publishedGeneration=generation;
  if(backendStatus)dataStatus={...backendStatus};
  setData({ dashboard:loadedDashboard, review, fuel });
  return;
 }
}

// Publish only a complete snapshot. Keep the current dashboard usable if any fetch fails.
// One read runs at a time, so snapshots publish in the order they were read. A read
// begun before a write can hold the previous ledger, so every caller gets a snapshot
// whose read began after its call: callers during a read share one follow-up read.
let reading: Promise<void> | null = null, following: Promise<void> | null = null;
export function reloadData(): Promise<void> {
  if (!reading) return reading = loadData().finally(() => { reading = null; });
  following ??= reading.catch(() => undefined).then(() => { following = null; return reading ?? reloadData(); });
  return following;
}
