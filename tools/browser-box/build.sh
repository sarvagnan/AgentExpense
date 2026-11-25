#!/bin/bash
# Runs only in the networked image builder, never on the operator's machine.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1
export npm_config_update_notifier=false
export FNM_DIR=/root/.local/share/fnm
export PATH=/usr/local/bin:$PATH
verify_download() {
  python3 - "$1" "$2" <<'PY_HASH'
import hashlib, sys
with open(sys.argv[1], 'rb') as source:
    actual = hashlib.file_digest(source, 'sha256').hexdigest()
if actual != sys.argv[2]:
    raise SystemExit('SHA-256 mismatch: ' + sys.argv[1])
PY_HASH
}
test "$(dpkg --print-architecture)" = amd64
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl unzip xz-utils git make \
  build-essential python3 locales tzdata tzdata-legacy libgmp10 libtinfo6 fontconfig fonts-dejavu-core fonts-liberation fonts-noto-core fonts-noto-color-emoji
sed -i 's/^# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
locale-gen
ln -snf /usr/share/zoneinfo/Asia/Kolkata /etc/localtime
printf 'Asia/Kolkata\n' > /etc/timezone
export TZ=Asia/Kolkata LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
mkdir -p /opt/browser-box/downloads
cd /opt/browser-box/downloads
curl --fail --location --retry 3 -o fnm.zip \
  "https://github.com/Schniz/fnm/releases/download/v${BOX_FNM}/fnm-linux.zip"
verify_download fnm.zip "$BOX_FNM_SHA256"
unzip fnm.zip -d /usr/local/bin
chmod +x /usr/local/bin/fnm
fnm install "$(cat /opt/agentexpense/.node-version)"
eval "$(fnm env --shell bash)"
fnm use "$(cat /opt/agentexpense/.node-version)"
curl --fail --location --retry 3 -o uv.tar.gz \
  "https://github.com/astral-sh/uv/releases/download/${BOX_UV}/uv-x86_64-unknown-linux-gnu.tar.gz"
verify_download uv.tar.gz "$BOX_UV_SHA256"
tar -xzf uv.tar.gz
install uv-x86_64-unknown-linux-gnu/uv /usr/local/bin/uv
curl --fail --location --retry 3 -o hledger.tar.gz \
  "https://github.com/simonmichael/hledger/releases/download/${BOX_HLEDGER}/hledger-linux-x64.tar.gz"
printf '%s  hledger.tar.gz\n' f76a2e4088e162b7658e43077502569d5a6fdacd219b19bfb9aab6588b69643a | sha256sum --check --strict
mkdir hledger
tar -xzf hledger.tar.gz -C hledger
find hledger -name '*.tar' -exec tar -xf '{}' -C hledger \;
hledger_binary=$(find hledger -type f -name hledger | head -1)
test -n "$hledger_binary"
install "$hledger_binary" /usr/local/bin/hledger
curl --fail --location --retry 3 -o jj.tar.gz \
  "https://github.com/jj-vcs/jj/releases/download/v${BOX_JJ}/jj-v${BOX_JJ}-x86_64-unknown-linux-musl.tar.gz"
verify_download jj.tar.gz "$BOX_JJ_SHA256"
mkdir jj
tar -xzf jj.tar.gz -C jj
install jj/jj /usr/local/bin/jj
cd /opt/agentexpense
test "${BOX_PYTHON%.*}" = "$(cat review-dashboard/server/.python-version)"
uv python install "$BOX_PYTHON"
uv sync --locked --python "$BOX_PYTHON" --directory review-dashboard/server
# Locked application packages use the npm registry; uv uses the registries in uv.lock.
# These are image-build inputs only. No package registry is contacted by a run.
npm --prefix review-dashboard ci --ignore-scripts --no-audit --no-fund
mkdir -p /root/.cache/expense-playwright
npm --prefix /root/.cache/expense-playwright install --save-exact --no-audit --no-fund "playwright@${BOX_PLAYWRIGHT}"
/root/.cache/expense-playwright/node_modules/.bin/playwright install --with-deps chromium-headless-shell firefox webkit
cp .node-version /opt/browser-box/node-version
export BOX_NODE="$(cat .node-version)"
review-dashboard/server/.venv/bin/python - <<'PY'
import json, os, subprocess
from pathlib import Path

def required_commands():
    import shutil
    required = ('python3', 'make', 'cc', 'git')
    commands = {name: shutil.which(name) for name in required}
    missing = [name for name, path in commands.items() if path is None]
    if missing:
        raise RuntimeError('Image missing required commands: ' + ', '.join(missing))
    return commands

def check_project_node(application, expected):
    import subprocess
    from pathlib import Path
    # Exercise the exact system-python entry point that Make's NODE_BIN uses.
    directory = subprocess.check_output(['python3', str(application/'scripts/node_toolchain.py')], text=True).strip()
    version = subprocess.check_output([str(Path(directory)/'node'), '--version'], text=True).strip()
    if version != 'v' + expected:
        raise RuntimeError('Node resolver selected ' + version + ', expected v' + expected)
    return {'directory': directory, 'version': version}

def check_timezones():
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, TZPATH
    names = ('UTC', 'Asia/Kolkata', 'Asia/Calcutta', 'America/Chicago', 'Europe/Berlin')
    missing = []
    for name in names:
        try:
            ZoneInfo(name)
        except ZoneInfoNotFoundError:
            missing.append(name)
    if missing:
        raise RuntimeError('Image missing IANA timezone names: ' + ', '.join(missing))
    return {'paths': list(TZPATH), 'zones': list(names)}

capabilities = required_commands()
node_toolchain = check_project_node(Path('/opt/agentexpense'), os.environ['BOX_NODE'])
timezones = check_timezones()
commands = {
    'node': ['node', '--version'], 'fnm': ['fnm', '--version'], 'uv': ['uv', '--version'],
    'python': ['/opt/agentexpense/review-dashboard/server/.venv/bin/python', '--version'],
    'hledger': ['hledger', '--version'], 'jj': ['jj', '--version'],
    'playwright': ['/root/.cache/expense-playwright/node_modules/.bin/playwright', '--version'],
}
versions = {name: subprocess.check_output(cmd, text=True).strip() for name, cmd in commands.items()}
for name, version in versions.items():
    assert os.environ['BOX_' + name.upper()] in version, (name, version)
versions.update(timezone='Asia/Kolkata', locale='en_US.UTF-8', debian=Path('/etc/debian_version').read_text().strip())
versions.update(requiredCommands=capabilities, nodeToolchain=node_toolchain, timezones=timezones)
versions['playwrightBrowsers'] = json.loads(Path('/root/.cache/expense-playwright/node_modules/playwright-core/browsers.json').read_text())['browsers']
versions['fonts'] = subprocess.check_output(['fc-list', ':', 'family'], text=True).splitlines()
Path('/opt/browser-box/versions.json').write_text(json.dumps(versions, indent=2) + '\n')
print(json.dumps(versions, indent=2))
PY
# No source snapshot or application identity belongs in the reusable image.
rm /opt/agentexpense/.node-version /opt/agentexpense/review-dashboard/package.json \
  /opt/agentexpense/review-dashboard/package-lock.json /opt/agentexpense/review-dashboard/server/pyproject.toml \
  /opt/agentexpense/review-dashboard/server/.python-version /opt/agentexpense/review-dashboard/server/uv.lock
rm /opt/agentexpense/scripts/node_toolchain.py /opt/agentexpense/scripts/project_paths.py
apt-get clean
uv cache clean
npm cache clean --force
rm -rf /opt/browser-box/downloads /var/lib/apt/lists/*
