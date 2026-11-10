"""Consecutive deadlines belong to one task, never the provider outage pause."""
LIMIT = 3


def reset(item):
    item['consecutive_timeouts'] = 0


def failed(item, kind):
    if kind != 'timeout':
        reset(item)
        return False
    item['consecutive_timeouts'] = item.get('consecutive_timeouts', 0) + 1
    return item['consecutive_timeouts'] >= LIMIT


def reason(seconds, *, receipt=False, fix=False, attachments=True, statement=False):
    value, unit = (seconds / 60, 'minute') if seconds >= 60 and seconds % 60 == 0 else (seconds, 'second')
    duration = f'{value:g} {unit}{"" if value == 1 else "s"}'
    action = 'propose a fix' if fix else 'read'
    saved = 'Your payment is saved; no receipt details were changed.' if receipt else 'Nothing was saved.'
    advice = ('Retry or edit the entry yourself.' if fix else 'Retry, or Dismiss and use Edit entry to add the details.' if receipt else
              'Try a smaller statement file.' if statement else
              'Try fewer pages or files.' if attachments else 'Retry or finish the entry yourself.')
    return f'This took too long to {action} (three tries of {duration}). {saved} {advice}'
