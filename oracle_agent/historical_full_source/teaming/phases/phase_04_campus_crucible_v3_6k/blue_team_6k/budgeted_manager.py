from __future__ import annotations

from pathlib import Path
from typing import Any

from context_manager import ContextWindowManager
from io_utils import append_jsonl, write_json
from manager_agent import CodexManagerAgent


class BudgetedCodexManagerAgent(CodexManagerAgent):
    """Codex manager whose supplied daily prompt cannot exceed 6K."""

    def __init__(
        self,
        *,
        context_manager: ContextWindowManager,
        context_manifests_dir: Path,
        context_usage_log: Path,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.context_manager = context_manager
        self.context_manifests_dir = context_manifests_dir
        self.context_usage_log = context_usage_log
        self.last_manifest: dict[str, Any] = {}

    def decide(
        self, dashboard: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        compact, manifest = self.context_manager.build(
            dashboard, CodexManagerAgent._prompt
        )
        self.last_manifest = manifest
        write_json(
            self.context_manifests_dir
            / f"day_{int(dashboard['day']):03d}.json",
            manifest,
        )
        append_jsonl(self.context_usage_log, manifest)
        return super().decide(compact)
