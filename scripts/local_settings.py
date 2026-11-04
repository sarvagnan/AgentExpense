"""Machine settings in the application's ignored local.json: updates and the server service.

```json
{"updates": {"remote": "origin", "notice": true},
 "service": {"systemd": "expense-dashboard.service"}}
```

`service` names either a `systemd --user` unit (`{"systemd": "<unit>"}`) or a launchd
label (`{"launchd": "<label>"}`). Without it, make update never touches a service.
`updates.notice` turns on the dashboard's update notice (off by default); `remote`
names the git remote that make update and the notice read tags from.
Standard library only; the agent settings in the same file are read by the server.
"""
from __future__ import annotations
import json
from pathlib import Path
import re

APPLICATION = Path(__file__).resolve().parents[1]
REMOTE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
UNIT = re.compile(r'[A-Za-z0-9][A-Za-z0-9:_.@-]*')
LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')


def read_local(application=APPLICATION):
    path = Path(application) / 'local.json'
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('local.json must contain an object')
    return value


def update_settings(local):
    """`{'remote': str, 'notice': bool}` from local.json's optional updates object."""
    value = local.get('updates', {})
    if not isinstance(value, dict) or set(value) - {'remote', 'notice'}:
        raise ValueError('local.json updates accepts only remote and notice')
    remote = value.get('remote', 'origin')
    if not isinstance(remote, str) or not REMOTE.fullmatch(remote):
        raise ValueError('local.json updates.remote must name a git remote')
    notice = value.get('notice', False)
    if type(notice) is not bool:
        raise ValueError('local.json updates.notice must be true or false')
    return {'remote': remote, 'notice': notice}


def service_settings(local):
    """`None`, `('systemd', unit)` or `('launchd', label)` from local.json's service object."""
    if 'service' not in local:
        return None
    value = local['service']
    if not isinstance(value, dict) or len(value) != 1 or set(value) - {'systemd', 'launchd'}:
        raise ValueError('local.json service must be {"systemd": "<unit>"} or {"launchd": "<label>"}')
    (kind, name), = value.items()
    if not isinstance(name, str) or not (UNIT if kind == 'systemd' else LABEL).fullmatch(name):
        raise ValueError(f'local.json service.{kind} is not a valid {"unit name" if kind == "systemd" else "launchd label"}')
    return kind, name
