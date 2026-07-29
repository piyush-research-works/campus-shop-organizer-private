from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_memory(memory_dir: Path) -> dict[str, Any]:
    path = memory_dir / "state.json"
    if not path.exists():
        return {"days_seen": 0, "last_plan": None}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {"days_seen": 0}


def save_memory(
    memory_dir: Path,
    state: dict[str, Any],
    *,
    day: int,
    plan: dict[str, Any],
) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    value = {
        **state,
        "days_seen": max(int(state.get("days_seen", 0)), day),
        "last_plan": plan,
    }
    path = memory_dir / "state.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)

