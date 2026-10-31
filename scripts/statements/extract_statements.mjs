/** Batch export of example statement text through the shared Python reader. */
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const script = fileURLToPath(new URL('./extract_statements.py', import.meta.url));
const result = spawnSync('uv', ['run', '--with', 'pypdfium2', 'python', script, ...process.argv.slice(2)], {
  stdio: 'inherit', env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
});
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
