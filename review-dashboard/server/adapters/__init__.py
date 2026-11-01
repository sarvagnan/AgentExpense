"""Machine-selected adapter registry. Custom modules are trusted operator code."""
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
from .base import INTERFACE_VERSION, Description

PACKAGES = {'codex': ('@openai/codex', 'codex'), 'claude': ('@anthropic-ai/claude-code', 'claude'),
            'pi': ('@earendil-works/pi-coding-agent', 'pi'), 'opencode': ('@opencode/cli', 'opencode')}


def pinned_version(settings, package):
    return json.loads((settings.application / 'review-dashboard/package.json').read_text())['dependencies'][package]


def get_adapter(settings):
    if settings.adapter == 'codex':
        from .codex import Codex
        adapter = Codex(settings)
    elif settings.adapter in ('claude', 'pi', 'opencode'):
        from .cli import CLI
        adapter = CLI(settings)
    elif settings.adapter in ('openai', 'anthropic'):
        from .direct import DirectAPI
        try:
            adapter = DirectAPI(settings)
        except ValueError as exc:
            from agent_check import DiagnosticUnavailable
            raise DiagnosticUnavailable(str(exc)) from None
    elif settings.custom_adapter:
        module_path = settings.custom_adapter
        path = (settings.ledger / module_path.removeprefix('ledger:') if module_path.startswith('ledger:') else Path(module_path).expanduser())
        if not path.is_absolute():
            path = settings.application / path
        spec = importlib.util.spec_from_file_location('expense_custom_adapter_' + hashlib.sha256(str(path).encode()).hexdigest(), path)
        if spec is None or spec.loader is None:
            raise ValueError('Custom adapter module cannot be loaded')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        adapter = module.create_adapter(settings)
    else:
        raise ValueError('Unknown agent adapter; configure a custom module in local.json')
    description = adapter.describe()
    if description.interface_version != INTERFACE_VERSION or description.id != settings.adapter:
        raise ValueError('Adapter interface version or id mismatch')
    return adapter


def require_isolation(adapter, settings):
    from agent_check import PolicyFailure
    if not adapter.describe().isolation and not settings.allow_unisolated:
        raise PolicyFailure('This adapter cannot enforce full isolation. Set agent.allowUnisolated=true in local.json to opt in.')


def status_details(settings):
    try:
        description = asdict(get_adapter(settings).describe())
    except ValueError:
        name={'openai':'OpenAI-compatible API','anthropic':'Anthropic API'}.get(settings.adapter,settings.adapter)
        description = asdict(Description(settings.adapter,name,'unavailable',settings.adapter in ('openai','anthropic'),'Adapter configuration is unavailable; see readiness reason.'))
    return dict(adapter=description, model=settings.model, effort=settings.effort,
                login_command='make agent-login' + ('' if settings.adapter == 'codex' else ' ADAPTER=' + settings.adapter),
                isolation_warning=(None if description['isolation'] else 'Full agent isolation is not enforced; machine opt-in is enabled.' if settings.allow_unisolated else 'Full agent isolation is not enforced. This adapter is disabled until you explicitly opt in.'))


def adapter_version(adapter, work, *, stop=None):
    method = getattr(adapter, 'version', None)
    return method(work, stop=stop) if callable(method) else adapter.describe().pinned_version


def adapter_fingerprint(adapter, settings):
    method = getattr(adapter, 'fingerprint', None)
    values = method() if callable(method) else {}
    return {'declared': asdict(adapter.describe()), 'launch': values,
            'custom_source': hashlib.sha256(Path(settings.custom_adapter).read_bytes()).hexdigest()
            if settings.custom_adapter else None}


def login_environment(adapter, settings):
    method = getattr(adapter, 'environment', None)
    if callable(method):
        return method()
    from .cli import child_environment
    environment = child_environment(settings)
    for key in ('HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME','XDG_CACHE_HOME'):
        path = Path(environment[key])
        if path.is_symlink():
            raise ValueError('Dedicated adapter home symlink refused')
        path.mkdir(parents=True, exist_ok=True)
    return environment
