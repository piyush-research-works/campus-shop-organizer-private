from __future__ import annotations

import json
from typing import Any

from manager_4k import ResilientCodexManager4K


class SustainableCodexManager4K(ResilientCodexManager4K):
    """Strategic call focused on profitable continuity, not cash hoarding."""

    @staticmethod
    def _prompt(dashboard: dict[str, Any]) -> str:
        return (
            "You manage three campus shops. Keep profitable operations "
            "alive through uncertain demand. The finance controller's "
            "anchor shop, escrow and order budget are binding. Warnings "
            "now decay and are scoped by shop and product; never suppress "
            "the whole network for one local warning. Prefer balanced "
            "operation and contribution-positive core products. Use "
            "protect only when the guardrail reports real cash stress. "
            "Do not calculate exact quantities. Return schema-valid JSON "
            "with reasoning under 80 words.\n"
            + json.dumps(
                dashboard,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
