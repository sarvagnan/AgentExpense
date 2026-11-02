"""Bounded provider diagnostics and protocol-level failure classification."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import re

KINDS = ('usage_limit', 'auth', 'transient', 'bad_answer')


def diagnostic(value):
    if isinstance(value,bytes): value=value.decode('utf-8',errors='replace')
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    # Provider diagnostics can echo credentials. Keep the message, never a credential.
    text = re.sub(r'(?i)(bearer\s+|(?:api[_ -]?key|token|authorization)["\s:=]+)[^\s",}]+', r'\1[redacted]', text)
    return text[-4000:]


def classify(value):
    text = json.dumps(value).casefold()
    if re.search(r'\b429\b',text) or any(word in text for word in ('usage_limit', 'rate_limit', 'ratelimit', 'rate limit', 'too many requests', 'quota', 'usage limit', 'insufficient_quota')):
        return 'usage_limit'
    if re.search(r'\b(?:401|403)\b',text) or any(word in text for word in ('unauthorized', 'authentication', 'not authenticated', 'sign in', 'auth_error', 'autherror', 'token_expired', 'invalid_api_key')):
        return 'auth'
    if re.search(r'\b5[0-9]{2}\b',text) or any(word in text for word in ('timeout', 'timed out', 'network', 'connection', 'overloaded', 'server_error', 'server error', 'serviceunavailable', 'temporarily unavailable')):
        return 'transient'
    return 'bad_answer'


def reset_time(value, at=None):
    at = at or datetime.now(timezone.utc)
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def event_failure(events, *, usage=None, lookups=None):
    from .base import AdapterError
    errors = [diagnostic(e) for e in events][-20:]
    kinds = [classify(e) for e in events]
    kind = next((k for k in KINDS if k in kinds), 'bad_answer')
    def reset(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('reset_at', 'resets_at', 'resetAt'):
                    found = reset_time(item)
                    if found: return found
                found = reset(item)
                if found: return found
        return None
    return AdapterError('Agent reported a failure', kind=kind, reset_at=next((found for e in events if (found:=reset(e))),None),
                        errors=errors, usage=usage, lookups=lookups)


def http_reset(headers):
    for key in ('x-ratelimit-reset', 'x-ratelimit-reset-requests', 'anthropic-ratelimit-requests-reset', 'anthropic-ratelimit-tokens-reset'):
        if headers.get(key):
            found = reset_time(headers[key])
            if found: return found
            duration=headers[key]
            parts=re.findall(r'(\d+(?:\.\d+)?)(ms|s|m|h|d)',duration)
            if parts and ''.join(a+b for a,b in parts)==duration:
                seconds=sum(float(a)*{'ms':.001,'s':1,'m':60,'h':3600,'d':86400}[b] for a,b in parts)
                return (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()
    value = headers.get('retry-after')
    if value:
        try: return (datetime.now(timezone.utc) + timedelta(seconds=float(value))).isoformat()
        except ValueError:
            try: return parsedate_to_datetime(value).isoformat()
            except (ValueError, TypeError): pass
    return None
