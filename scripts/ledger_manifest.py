"""Ledger format gate and validated settings shared by Python entry points."""
from copy import deepcopy
import json
from functools import lru_cache
from pathlib import Path
import re
from datetime import date
from project_paths import APPLICATION_ROOT, ledger_root

CURRENT_FORMAT = 1
APP_VERSION = '0.1.0'


def ordered_manifest(value):
    """Preserve optional fields while fixing manifest and module key order."""
    def arrange(document, keys):
        return {key: document[key] for key in keys if key in document} | {
            key: document[key] for key in sorted(document.keys() - set(keys))}
    result = arrange(value, ('format', 'id', 'name', 'defaultCurrency', 'locale',
                             'startDate', 'modules', 'review'))
    if 'modules' in result:
        result['modules'] = arrange(result['modules'], ('fuel', 'investments', 'history', 'weeklyReview'))
    return result


def validate_manifest(value, schema, field='ledger.json'):
    """Validate this closed schema using stdlib, including lookup's python -S mode."""
    types = schema.get('type', [])
    types = types if isinstance(types, list) else [types]
    kind = ('null' if value is None else 'boolean' if type(value) is bool else
            'integer' if type(value) is int else 'object' if isinstance(value, dict) else
            'string' if isinstance(value, str) else 'array' if isinstance(value, list) else 'number')
    if types and kind not in types:
        raise ValueError(f'Invalid {field}: expected {" or ".join(types)}.')
    if 'const' in schema and value != schema['const']:
        raise ValueError(f'Invalid {field}.')
    if kind == 'string':
        if len(value) < schema.get('minLength', 0) or ('pattern' in schema and not re.search(schema['pattern'], value)):
            raise ValueError(f'Invalid {field}.')
        if schema.get('format') == 'uuid' and not re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', value):
            raise ValueError(f'Invalid {field}.')
        if schema.get('format') == 'date':
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError:
                raise ValueError(f'Invalid {field}.') from None
    if kind == 'object':
        for key in schema.get('required', []):
            if key not in value:
                raise ValueError(f'Invalid {field}: missing {key}.')
        properties = schema.get('properties', {})
        for key, item in value.items():
            if key not in properties:
                if schema.get('additionalProperties') is False:
                    raise ValueError(f'Invalid {field}: unexpected {key}.')
            else:
                validate_manifest(item, properties[key], field + '.' + key)


@lru_cache(maxsize=1)
def manifest_schema():
    return json.loads((APPLICATION_ROOT / 'schema/ledger.schema.json').read_text())


def read_manifest(root=None, *, check_format=True):
    root = Path(root) if root is not None else ledger_root()
    path = root / 'ledger.json'
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate manifest key: ' + key)
            result[key] = value
        return result
    manifest = json.loads(path.read_text(), object_pairs_hook=unique) if path.exists() else {'format': 0}
    version = manifest.get('format') if isinstance(manifest, dict) else None
    if type(version) is not int or version < 0:
        raise ValueError('Invalid ledger format.')
    if check_format:
        if version < CURRENT_FORMAT:
            raise ValueError(f'This ledger uses format {version}; run make upgrade-ledger.')
        if version > CURRENT_FORMAT:
            raise ValueError(f'This app reads format {CURRENT_FORMAT}; this ledger uses format {version}; a newer AgentExpense is needed.')
    if version != CURRENT_FORMAT:
        return manifest
    validate_manifest(manifest, manifest_schema())
    result = deepcopy(manifest)
    modules = result.setdefault('modules', {})
    modules.setdefault('fuel', False)
    modules.setdefault('investments', {}).setdefault('prices', None)
    modules.setdefault('history', {}).setdefault('oldLedger', None)
    modules.setdefault('weeklyReview', False)
    result.setdefault('review', {}).setdefault('storageKey', None)
    return result


if __name__ == '__main__':
    import sys
    try:
        read_manifest()
    except (ValueError, OSError) as exc:
        sys.exit(str(exc))
