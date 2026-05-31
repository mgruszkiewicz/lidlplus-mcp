from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def load_seen(state_file: Path) -> set[str]:
    if not state_file.exists():
        return set()
    data = json.loads(state_file.read_text())
    return set(data.get("seen_ids", []))


def save_seen(state_file: Path, seen: set[str]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "seen_ids": sorted(seen),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )
