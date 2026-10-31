"""Local save context, independent of entry dates and retry/run timestamps."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, TZPATH, available_timezones


def server_zone():
    """Find the system's IANA name, including hosts with a copied localtime file."""
    candidates = [os.environ.get('TZ', '').removeprefix(':')]
    localtime = Path('/etc/localtime')
    resolved = str(localtime.resolve())
    if '/zoneinfo/' in resolved:
        candidates.append(resolved.split('/zoneinfo/', 1)[1])
    timezone_file = Path('/etc/timezone')
    if timezone_file.is_file():
        candidates.append(timezone_file.read_text().strip())
    for name in candidates:
        try:
            return ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError):
            pass
    if localtime.is_file():
        contents = localtime.read_bytes()
        for name in sorted(available_timezones()):
            for directory in TZPATH:
                path = Path(directory) / name
                if path.is_file() and path.read_bytes() == contents:
                    return ZoneInfo(name)
    # Systems without a timezone database configuration normally use UTC.
    if datetime.now().astimezone().utcoffset() == timedelta(0):
        return ZoneInfo('UTC')
    raise ValueError('Cannot identify the server local zone; set TZ to an IANA zone name.')


def client_context(client_time=None, client_zone=None, *, at=None):
    """Validate browser input or record the server-local fallback, without a lower age limit."""
    at = at or datetime.now(timezone.utc)
    if client_time is None and client_zone is None:
        zone = server_zone()
        return dict(client_time=at.astimezone(zone).isoformat(), client_zone=zone.key)
    if not isinstance(client_time, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?[+-](?:(?:0\d|1[0-3]):[0-5]\d|14:00)', client_time):
        raise ValueError('client_time must be an ISO timestamp with an offset within ±14:00.')
    try:
        saved = datetime.fromisoformat(client_time)
    except ValueError:
        raise ValueError('client_time must contain a valid date and time.') from None
    if saved > at + timedelta(hours=48):
        raise ValueError('client_time is more than 48 hours in the future.')
    try:
        if not isinstance(client_zone, str) or not client_zone:
            raise ValueError()
        ZoneInfo(client_zone)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError('client_zone must be a known IANA zone name.') from None
    return dict(client_time=client_time, client_zone=client_zone)


def ensure_context(record):
    """Old durable records use their original save instant in the server's local zone."""
    if not record.get('client_time') or not record.get('client_zone'):
        at = datetime.fromisoformat(record.get('saved_at') or record['created_at'])
        record.update(client_context(at=at.astimezone()))
    return record


def saved_local(draft):
    context = ensure_context(dict(draft))
    saved = datetime.fromisoformat(context['client_time'])
    offset = saved.strftime('%z')
    return f"{saved:%Y-%m-%d %H:%M} {offset[:3]}:{offset[3:]} {context['client_zone']}"
