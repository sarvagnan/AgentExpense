"""Application paths and the single Python rule for locating the expense repository."""
from pathlib import Path
import os

APPLICATION_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = APPLICATION_ROOT / "schema/store.schema.json"


def ledger_root() -> Path:
    """Resolve LEDGER_DIR relative to the caller's directory, or use the nested ledger."""
    configured = os.environ.get("LEDGER_DIR")
    return Path(configured).expanduser().resolve() if configured else APPLICATION_ROOT / "ledger"


if __name__ == "__main__":
    print(ledger_root())
