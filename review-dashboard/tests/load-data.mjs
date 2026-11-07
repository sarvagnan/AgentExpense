import {dashboardDataPath} from '../../scripts/dashboard-data.mjs';
import { readFile } from 'node:fs/promises';
import { gunzipSync } from 'node:zlib';

// The app fetches public/data/*.json.gz at runtime; SSR tests inject the same published files.
export async function loadPublishedData(server) {
  const read = async name => JSON.parse(gunzipSync(await readFile(dashboardDataPath('public/data',`${name}.json.gz`))).toString('utf8'));
  const [dashboard, review] = await Promise.all(['dashboard', 'pending-transactions'].map(read));
  const fuel = dashboard.meta.ledger.extensions?.fuel ? await read('fuel-log') : {meta:{},entries:[]};
  (await server.ssrLoadModule('/app/data.ts')).setData({ dashboard, review, fuel });
  return { dashboard, review, fuel };
}
