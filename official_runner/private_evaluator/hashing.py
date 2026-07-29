"""Hash frozen participant submissions and private input files."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    "__pycache__",
    "validation_reports",
}
EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hash_agent_code(
    manifest_path: Path,
    manifest: dict[str, Any],
) -> str:
    """Use the same canonical source hash as Module 5."""
    submission_root = manifest_path.resolve().parent
    working = Path(str(manifest.get("working_directory", ".")))
    if not working.is_absolute():
        working = submission_root / working
    working = working.resolve()
    memory = Path(str(manifest.get("memory_directory", "memory")))
    if not memory.is_absolute():
        memory = submission_root / memory
    memory = memory.resolve()

    try:
        working.relative_to(submission_root)
    except ValueError as exc:
        raise ValueError(
            "working_directory must remain inside the submission"
        ) from exc

    digest = hashlib.sha256()
    files: list[Path] = []
    for path in submission_root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(submission_root)
        if any(
            part in EXCLUDED_DIRECTORY_NAMES for part in relative.parts
        ):
            continue
        try:
            path.resolve().relative_to(memory)
            continue
        except ValueError:
            pass
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        if path.name.startswith("."):
            continue
        files.append(path)

    for path in sorted(
        files,
        key=lambda value: value.relative_to(submission_root).as_posix(),
    ):
        relative = path.relative_to(submission_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    digest.update(
        f"working={working.relative_to(submission_root).as_posix()}".encode(
            "utf-8"
        )
    )
    return digest.hexdigest()
