from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from context_manager_4k import ResilienceContextManager4K
from io_utils import append_jsonl, write_json
from manager_agent import CodexManagerAgent


class ResilientCodexManager4K(CodexManagerAgent):
    """Strategic Codex call with a measured 4K application payload."""

    def __init__(
        self,
        *,
        context_manager: ResilienceContextManager4K,
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
            dashboard, self._prompt
        )
        self.last_manifest = manifest
        write_json(
            self.context_manifests_dir
            / f"day_{int(dashboard['day']):03d}.json",
            manifest,
        )
        append_jsonl(self.context_usage_log, manifest)
        return super().decide(compact)

    @staticmethod
    def _prompt(dashboard: dict[str, Any]) -> str:
        return (
            "You manage three campus shops. The analyst supplied compact "
            "forecasts, event memory and financial outlook. A deterministic "
            "finance controller supplied binding operational guardrails: "
            "never recommend spending or reopening beyond them. Choose the "
            "cash posture and shop modes. Use exceptional stock priorities "
            "only for repeated location-specific stockouts with positive "
            "margin. Treat break/travel warnings as persistent, not one-day "
            "noise. Discount only expiring stock. Do not calculate exact "
            "quantities. Return schema-valid JSON with reasoning under 80 "
            "words.\n"
            + json.dumps(
                dashboard,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
