"""Install the tracked user unit and restart it on the server host."""
import os
from pathlib import Path
import shutil
import subprocess

from node_toolchain import node_environment

APPLICATION = Path(__file__).resolve().parents[1]


def install():
    systemctl = shutil.which('systemctl')
    if not systemctl or subprocess.run([systemctl, '--user', 'show-environment'],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise ValueError('systemctl --user is unavailable; run make service on the server host')
    source = APPLICATION / 'review-dashboard/server/expense-dashboard.service'
    config = Path(os.getenv('XDG_CONFIG_HOME', '~/.config')).expanduser()
    target = config / 'systemd/user/expense-dashboard.service'
    env = node_environment(APPLICATION)
    content = source.read_text().replace('[Service]', '[Service]\nEnvironment="PATH=' + env['PATH'] + '"\nEnvironment=npm_config_update_notifier=false').encode()
    if not target.exists() or target.read_bytes() != content:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        print('Installed expense-dashboard.service')
    subprocess.run([systemctl, '--user', 'daemon-reload'], check=True)
    subprocess.run([systemctl, '--user', 'restart', 'expense-dashboard.service'], check=True)


if __name__ == '__main__':
    try:
        install()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc))
