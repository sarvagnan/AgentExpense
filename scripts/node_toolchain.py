"""Use the project's installed fnm Node; never install or fetch a runtime."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
from project_paths import APPLICATION_ROOT

_warned = False


def node_environment(application=APPLICATION_ROOT, environment=None):
    global _warned
    env = dict(os.environ if environment is None else environment)
    env['npm_config_update_notifier'] = 'false'
    env.setdefault('FNM_DIR', str(Path.home() / '.local/share/fnm'))
    version_file = application / '.node-version'
    version = version_file.read_text().strip()
    if shutil.which('fnm', path=env.get('PATH')):
        result = subprocess.run(['fnm', 'exec', '--using=' + str(version_file), '--', 'node', '-p', 'require("path").dirname(process.execPath)'], env=env, text=True, capture_output=True)
        if result.returncode:
            raise ValueError(f'Node selected by {version_file} is unavailable; run fnm install {version}.\n{result.stderr.strip()}')
        binary_dir = result.stdout.strip()
    else:
        node = shutil.which('node', path=env.get('PATH'))
        if not node:
            raise ValueError(f'No Node on PATH; install fnm and run fnm install {version} for {version_file}.')
        binary_dir = str(Path(node).parent)
        if not _warned:
            print(f'fnm unavailable; using Node on PATH instead of {version_file}', file=sys.stderr)
            _warned = True
    env['PATH'] = binary_dir + os.pathsep + env.get('PATH', '')
    return env


if __name__ == '__main__':
    print(node_environment()['PATH'].split(os.pathsep)[0])
