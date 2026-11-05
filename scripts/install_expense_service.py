"""Install the tracked user unit for this installation and ledger, and restart it."""
import os
from pathlib import Path
import shutil
import subprocess

from node_toolchain import node_environment
from project_paths import ledger_root

APPLICATION = Path(__file__).resolve().parents[1]


def unit_text(source: Path, ledger: Path, uv: str, path: str) -> bytes:
    """The tracked template with this installation's paths; machine settings stay in drop-ins."""
    content = (source.read_text().replace('@APPLICATION@', str(APPLICATION)).replace('@LEDGER_DIR@', str(ledger))
               .replace('@UV@', uv))
    return content.replace('[Service]', '[Service]\nEnvironment="PATH=' + path + '"\nEnvironment=npm_config_update_notifier=false').encode()


def install():
    systemctl = shutil.which('systemctl')
    if not systemctl or subprocess.run([systemctl, '--user', 'show-environment'],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise ValueError('systemctl --user is unavailable; run make service on the server host')
    ledger = ledger_root()
    if not (ledger / 'ledger.json').is_file():
        raise ValueError(f'No ledger at {ledger}; set LEDGER_DIR to your ledger before make service')
    uv = shutil.which('uv')
    if not uv:
        raise ValueError('uv is not on PATH; run make doctor')
    source = APPLICATION / 'review-dashboard/server/expense-dashboard.service'
    config = Path(os.getenv('XDG_CONFIG_HOME', '~/.config')).expanduser()
    target = config / 'systemd/user/expense-dashboard.service'
    content = unit_text(source, ledger, str(Path(uv).resolve()), node_environment(APPLICATION)['PATH'])
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
