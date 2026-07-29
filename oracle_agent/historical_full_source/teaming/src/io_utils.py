from __future__ import annotations

import csv
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(
    path: Path, payload: dict[str, Any], *, overwrite: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w" if overwrite else "x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()


def make_experiment_dir(root: Path, requested_name: str | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if requested_name:
        if any(part in requested_name for part in ("/", "\\", "..")):
            raise ValueError("Experiment name must be a simple folder name.")
        name = requested_name
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        name = f"teaming_{stamp}_{uuid.uuid4().hex[:8]}"
    path = root / name
    path.mkdir(exist_ok=False)
    return path


def sha256_json(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_csv(
    path: Path, rows: list[dict[str, Any]], fieldnames: list[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
