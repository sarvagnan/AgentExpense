import {useProjectNode} from '../../scripts/node-toolchain.mjs';
useProjectNode();
import {ensureTestRoot} from "../../scripts/test-run.mjs";
ensureTestRoot();
import {mkdtempSync,rmSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {applicationRoot, ledgerRoot} from '../../scripts/project-paths.mjs';
const env = {...process.env, LEDGER_DIR:ledgerRoot(), DASHBOARD_DATA_DIR:mkdtempSync(process.env.EXPENSES_TEST_TMP+'/expense-node-data-'), npm_config_update_notifier:'false'};
env.EXPENSES_TEST_TMP=env.DASHBOARD_DATA_DIR+'/tests';
process.once('exit',()=>rmSync(env.DASHBOARD_DATA_DIR,{recursive:true,force:true}));
for (const [command, args] of [['npm', ['--prefix','review-dashboard','run','data']], [process.execPath, ['--test','--test-concurrency=2','review-dashboard/tests/*.test.mjs']]]) {
  const result = spawnSync(command, args, {cwd:applicationRoot, env, stdio:'inherit'});
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
