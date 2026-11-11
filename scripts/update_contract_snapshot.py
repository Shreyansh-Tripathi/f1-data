"""Rewrite tests/contract/snapshot.json from the current output models.

Run after an additive contract change, or after bumping a file's schemaVersion for a breaking one:
    uv run python scripts/update_contract_snapshot.py
"""

import json
from pathlib import Path

from f1data.models.contract import contract

SNAPSHOT = Path(__file__).resolve().parent.parent / "tests" / "contract" / "snapshot.json"

if __name__ == "__main__":
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(contract(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {SNAPSHOT}")
