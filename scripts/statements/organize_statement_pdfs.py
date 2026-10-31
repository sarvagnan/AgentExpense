from __future__ import annotations

import json
from pathlib import Path


# Direct script execution puts this directory on sys.path; share the application helper.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_paths import ledger_root

ROOT = ledger_root()
DATA_PATH = ROOT / "data" / "statements" / "extracted.json"
BILLS = ROOT / "Bills"


def main() -> None:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    destinations: dict[str, str] = {}
    moved: list[dict[str, str]] = []

    for statement in data["statements"]:
        basename = Path(statement["sourceFile"]).name
        statement_day = str(statement["periodEnd"])[:10]
        relative_destination = Path("Bills") / statement_day / basename
        destination = ROOT / relative_destination
        current_reference = ROOT / statement["sourceFile"]
        source = current_reference if current_reference.exists() else ROOT / basename

        if basename in destinations and destinations[basename] != relative_destination.as_posix():
            raise ValueError(f"Duplicate statement filename maps to multiple dates: {basename}")
        destinations[basename] = relative_destination.as_posix()

        if source.resolve() != destination.resolve():
            if not source.exists():
                raise FileNotFoundError(f"Statement PDF not found: {source}")
            if destination.exists():
                raise FileExistsError(f"Refusing to overwrite existing statement PDF: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            moved.append({"from": str(source.relative_to(ROOT)), "to": str(relative_destination)})

        statement["sourceFile"] = relative_destination.as_posix()

    for row in data["transactions"]:
        basename = Path(row["sourceFile"]).name
        if basename not in destinations:
            raise ValueError(f"Transaction references an unknown statement PDF: {row['sourceFile']}")
        row["sourceFile"] = destinations[basename]

    DATA_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"moved": moved, "statement_count": len(destinations)}, indent=2))


if __name__ == "__main__":
    main()
