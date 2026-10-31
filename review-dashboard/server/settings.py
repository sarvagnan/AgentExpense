"""Environment settings. Ledger selection is shared with the application CLIs."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import sys

APPLICATION = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APPLICATION / 'scripts'))
from project_paths import ledger_root


@dataclass(frozen=True)
class Settings:
    application: Path = APPLICATION
    ledger: Path = field(default_factory=ledger_root)
    port: int = 8701
    base_path: str = '/'
    allowed_hosts: tuple[str, ...] = ()
    allowed_logins: tuple[str, ...] = ()
    dev: bool = False
    codex: str | None = None
    state_dir: Path = field(default_factory=lambda: Path.home() / '.local/state/expense-dashboard')
    codex_home: Path | None = None
    model: str = 'gpt-6.1-terra'
    effort: str = 'xhigh'
    timeout: float = 600
    old_ledger: Path | None = None

    def __post_init__(self):
        object.__setattr__(self, 'state_dir', self.state_dir.expanduser().resolve())
        object.__setattr__(self, 'codex_home', (self.codex_home or self.state_dir / 'codex-home').expanduser().resolve())
        object.__setattr__(self, 'codex', self.codex or str(self.application / 'review-dashboard/node_modules/.bin/codex'))

    @property
    def hosts(self):
        return ('127.0.0.1', 'localhost', 'testserver', *self.allowed_hosts)

    @classmethod
    def from_env(cls):
        def items(name):
            return tuple(x.strip() for x in os.getenv(name, '').split(',') if x.strip())
        base = '/' + os.getenv('EXPENSES_BASE_PATH', '/').strip('/')
        if base != '/':
            base += '/'
        port = int(os.getenv('EXPENSES_PORT', '8701'))
        timeout = float(os.getenv('EXPENSES_AGENT_TIMEOUT', '600'))
        if not 1 <= port <= 65535 or timeout <= 0:
            raise ValueError('Invalid server port or agent timeout')
        return cls(port=port, base_path=base, allowed_hosts=items('EXPENSES_ALLOWED_HOSTS'),
                   allowed_logins=items('EXPENSES_ALLOWED_LOGINS'),
                   dev=os.getenv('EXPENSES_DEV', '').lower() in ('1', 'true', 'yes', 'on'),
                   codex=os.getenv('EXPENSES_CODEX'),
                   state_dir=(Path(os.environ['EXPENSES_STATE_DIR']) if os.getenv('EXPENSES_STATE_DIR')
                              else Path.home() / '.local/state/expense-dashboard'),
                   codex_home=(Path(os.environ['EXPENSES_CODEX_HOME']).expanduser().resolve()
                               if os.getenv('EXPENSES_CODEX_HOME') else None),
                   model=os.getenv('EXPENSES_AGENT_MODEL', 'gpt-6.1-terra'),
                   effort=os.getenv('EXPENSES_AGENT_EFFORT', 'xhigh'), timeout=timeout,
                   old_ledger=(Path(os.environ['EXPENSES_OLD_LEDGER']).expanduser() if os.getenv('EXPENSES_OLD_LEDGER') else None))
