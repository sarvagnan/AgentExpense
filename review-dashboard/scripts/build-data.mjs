// One immutable input snapshot, one Node process and one validated store load.
// Terminal npm run data and the server's background builder share this entry point.
const builders = process.env.EXPENSES_DATA_BUILDERS ? JSON.parse(process.env.EXPENSES_DATA_BUILDERS) : ['copy-fuel-log', 'build-dashboard-data', 'build-review-data', 'build-add-patterns'];
for (const name of [...builders, 'check-dashboard-data', 'compress-dashboard-data']) {
  await import(`./${name}.mjs`);
}
