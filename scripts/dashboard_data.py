"""Shared dashboard output location and private source-ledger provenance."""
import json
import os
import secrets
import stat
from pathlib import Path
from project_paths import APPLICATION_ROOT


def dashboard_data_root(application=APPLICATION_ROOT):
    configured = os.environ.get('DASHBOARD_DATA_DIR')
    return Path(configured).expanduser().resolve() if configured else application / 'review-dashboard'


def public_data(output):
    return output / 'public/data'


def check_source(output, ledger):
    """Refuse an unapproved source; return False when explicit replacement is needed."""
    stamp = output / 'app/data/.source-ledger.json'
    if not stamp.exists():
        return True
    try:
        value = json.loads(stamp.read_text())
    except json.JSONDecodeError:
        value = None
    matches = isinstance(value, dict) and value.get('format') == 1 and value.get('ledger') == str(ledger.resolve())
    if not matches and os.environ.get('DASHBOARD_DATA_REPLACE') != '1':
        raise ValueError('DASHBOARD_DATA_DIR belongs to a different ledger; choose another output folder or set DASHBOARD_DATA_REPLACE=1 to replace it.')
    return matches


def write_source(output, ledger):
    """Publish the complete provenance document without truncating a reader's file."""
    stamp = output / 'app/data/.source-ledger.json'
    stamp.parent.mkdir(parents=True, exist_ok=True)
    temporary = stamp.with_name(f'.source-ledger.{secrets.token_hex(8)}.tmp')
    try:
        temporary.write_text(json.dumps(dict(format=1, ledger=str(ledger.resolve()))) + '\n')
        if stamp.exists(): temporary.chmod(stat.S_IMODE(stamp.stat().st_mode))
        temporary.replace(stamp)
    finally:
        temporary.unlink(missing_ok=True)
