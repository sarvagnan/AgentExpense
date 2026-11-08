"""Environment settings. Ledger selection is shared with the application CLIs."""
from dataclasses import dataclass, field
import os
import json
import math
from pathlib import Path
import sys

APPLICATION = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APPLICATION / 'scripts'))
from project_paths import ledger_root
from dashboard_data import dashboard_data_root


def state_directory():
    if value := os.getenv('EXPENSES_STATE_DIR'):
        return Path(value)
    return Path(os.getenv('XDG_STATE_HOME') or Path.home() / '.local/state') / 'agentexpense'


@dataclass(frozen=True)
class Settings:
    application: Path = APPLICATION
    ledger: Path = field(default_factory=ledger_root)
    dashboard_data: Path | None = None
    port: int = 8701
    # The dashboard build loads its files from /expenses/ (`base` in vite.config.ts).
    # Requests whose prefix a proxy removed are accepted too.
    base_path: str = '/expenses/'
    allowed_hosts: tuple[str, ...] = ()
    allowed_logins: tuple[str, ...] = ()
    dev: bool = False
    codex: str | None = None
    state_dir: Path = field(default_factory=state_directory)
    codex_home: Path | None = None
    model: str = 'gpt-6.1-terra'
    effort: str = 'xhigh'
    timeout: float = 600
    old_ledger: Path | None = None
    adapter: str = 'codex'
    agent_binary: str | None = None  # Explicit offline doubles / maintainer candidates only.
    allow_unisolated: bool = False
    custom_adapter: str | None = None
    api_options: str = '{}'  # Canonical JSON keeps Settings hashable; never contains a key.
    update_notice: bool = False  # local.json updates.notice; off unless turned on.
    update_remote: str = 'origin'
    # hledger's background second check of each commit (ledger_check.py); it runs only when
    # hledger is installed. The offline tests turn it on where they test it.
    ledger_check: bool = True

    @property
    def agent_home(self):
        return self.codex_home if self.adapter == 'codex' else self.state_dir / (self.adapter + '-home')

    @property
    def api(self):
        return json.loads(self.api_options)


    def __post_init__(self):
        object.__setattr__(self, 'dashboard_data', (self.dashboard_data or dashboard_data_root(self.application)).expanduser().resolve())
        object.__setattr__(self, 'state_dir', self.state_dir.expanduser().resolve())
        object.__setattr__(self, 'codex_home', (self.codex_home or self.state_dir / 'codex-home').expanduser().resolve())
        object.__setattr__(self, 'codex', self.codex or str(self.application / 'review-dashboard/node_modules/.bin/codex'))
        # Anchor operator modules before the gate replaces the data ledger with
        # its fictional probe ledger. Never discover code inside the probe.
        if self.custom_adapter:
            raw = self.custom_adapter
            path = self.ledger / raw.removeprefix('ledger:') if raw.startswith('ledger:') else Path(raw).expanduser()
            if not path.is_absolute():
                path = self.application / path
            object.__setattr__(self, 'custom_adapter', str(path.resolve()))

    @property
    def hosts(self):
        return ('127.0.0.1', 'localhost', 'testserver', *self.allowed_hosts)

    @classmethod
    def from_env(cls):
        def items(name):
            return tuple(x.strip() for x in os.getenv(name, '').split(',') if x.strip())
        base = '/' + os.getenv('EXPENSES_BASE_PATH', cls.base_path).strip('/')
        if base != '/':
            base += '/'
        local_path = APPLICATION / 'local.json'
        local = json.loads(local_path.read_text()) if local_path.exists() else {}
        if not isinstance(local, dict) or not isinstance(local.get('agent', {}), dict):
            raise ValueError('local.json must contain an agent object')
        agent = local.get('agent', {})
        from local_settings import update_settings
        updates = update_settings(local)
        adapter = os.getenv('EXPENSES_AGENT') or os.getenv('ADAPTER') or agent.get('adapter', 'codex')
        if not isinstance(adapter, str) or not __import__('re').fullmatch(r'[a-z][a-z0-9-]*', adapter):
            raise ValueError('Invalid agent adapter id')
        defaults = {'codex': 'gpt-6.1-terra', 'claude': 'claude-opus-5-5',
                    'pi': 'anthropic/claude-opus-5-5', 'opencode': 'anthropic/claude-opus-5-5',
                    'anthropic': 'claude-opus-5-5'}
        if set(agent) - {'adapter','model','effort','allowUnisolated','module','api'}:
            raise ValueError('Unknown agent machine setting')
        if any(key in agent and (not isinstance(agent[key], str) or not agent[key].strip()) for key in ('model','effort','module')):
            raise ValueError('Agent model, effort and module must be nonblank strings')
        api = agent.get('api', {})
        if not isinstance(api, dict) or set(api) - {'baseUrl','keyEnv','keyFile','structuredOutput','sendEffort','maxTurns','maxTools','maxTokens','maxOutputTokens','retries'}:
            raise ValueError('Invalid agent API settings (keys belong in an environment variable or key file)')
        if 'allowUnisolated' in agent and type(agent['allowUnisolated']) is not bool:
            raise ValueError('agent.allowUnisolated must be boolean')
        port = int(os.getenv('EXPENSES_PORT', '8701'))
        timeout = float(os.getenv('EXPENSES_AGENT_TIMEOUT', '600'))
        if not 1 <= port <= 65535 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Invalid server port or agent timeout')
        return cls(port=port, base_path=base, allowed_hosts=items('EXPENSES_ALLOWED_HOSTS'),
                   allowed_logins=items('EXPENSES_ALLOWED_LOGINS'),
                   dev=os.getenv('EXPENSES_DEV', '').lower() in ('1', 'true', 'yes', 'on'),
                   codex=os.getenv('EXPENSES_CODEX'),
                   state_dir=state_directory(),
                   codex_home=(Path(os.environ['EXPENSES_CODEX_HOME']).expanduser().resolve()
                               if os.getenv('EXPENSES_CODEX_HOME') else None),
                   model=os.getenv('EXPENSES_AGENT_MODEL') or agent.get('model') or defaults.get(adapter, ''),
                   effort=os.getenv('EXPENSES_AGENT_EFFORT') or agent.get('effort') or ('xhigh' if adapter == 'codex' else 'high'),
                   timeout=timeout, adapter=adapter, agent_binary=os.getenv('EXPENSES_AGENT_BINARY'),
                   allow_unisolated=agent.get('allowUnisolated', False), custom_adapter=agent.get('module'),
                   api_options=json.dumps(api, sort_keys=True),
                   update_notice=updates['notice'], update_remote=updates['remote'])
