import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';

// Tailscale Serve sends static files uncompressed, so publish gzip copies for the app to fetch and decompress.
const source = new URL('../app/data/', import.meta.url);
const target = new URL('../public/data/', import.meta.url);
await mkdir(target, { recursive: true });

let total = 0;
for (const name of ['dashboard', 'pending-transactions', 'fuel-log', 'add-patterns']) {
  const json = JSON.stringify(JSON.parse(await readFile(new URL(`${name}.json`, source), 'utf8')));
  const gzipped = gzipSync(json, { level: 9 });
  await writeFile(new URL(`${name}.json.gz`, target), gzipped);
  total += gzipped.length;
  console.log(`Wrote public/data/${name}.json.gz (${(gzipped.length / 1024).toFixed(1)} KB)`);
}
console.log(`Compressed dashboard data: ${(total / 1024).toFixed(1)} KB total`);
