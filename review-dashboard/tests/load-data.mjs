import { readFile } from 'node:fs/promises';
import { gunzipSync } from 'node:zlib';

// The app fetches public/data/*.json.gz at runtime; SSR tests inject the same published files.
export async function loadPublishedData(server) {
  const read = async name => JSON.parse(gunzipSync(await readFile(new URL(`../public/data/${name}.json.gz`, import.meta.url))).toString('utf8'));
  const [dashboard, review] = await Promise.all(['dashboard', 'pending-transactions'].map(read));
  const fuel = dashboard.meta.ledger.modules.fuel ? await read('fuel-log') : {meta:{},entries:[]};
  (await server.ssrLoadModule('/app/data.ts')).setData({ dashboard, review, fuel });
  return { dashboard, review, fuel };
}
