// Direct Node launchers re-enter through the project's installed fnm version.
import {execFileSync, spawnSync} from 'node:child_process';
import path from 'node:path';
import {realpathSync} from 'node:fs';
import {applicationRoot} from './project-paths.mjs';
export function useProjectNode() {
  const bin = execFileSync('python3', [path.join(applicationRoot, 'scripts/node_toolchain.py')], {encoding:'utf8'}).trim();
  process.env.PATH = bin + path.delimiter + process.env.PATH;
  process.env.npm_config_update_notifier = 'false';
  if (realpathSync(process.execPath) !== realpathSync(path.join(bin, 'node'))) {
    const result = spawnSync(path.join(bin, 'node'), process.argv.slice(1), {env:process.env, stdio:'inherit'});
    if (result.error) throw result.error;
    process.exit(result.status ?? 1);
  }
}
