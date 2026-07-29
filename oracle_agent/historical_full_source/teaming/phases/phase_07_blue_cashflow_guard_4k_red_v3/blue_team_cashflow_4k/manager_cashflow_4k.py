from __future__ import annotations

import json
from typing import Any

from manager_sustainable_4k import SustainableCodexManager4K


class CashflowCodexManager4K(SustainableCodexManager4K):
    """Manager prompt centered on continuity and cash conversion."""

    @staticmethod
    def _prompt(dashboard: dict[str, Any]) -> str:
        return (
            "Manage three campus shops for long-run profit. Finance and "
            "operations guardrails are binding. Protect at least one "
            "revenue-producing anchor: zero open shops is a recovery "
            "failure even if cash is positive. Accept early reversible "
            "consolidation of a persistently loss-making non-anchor. "
            "During severe or very-low local demand, sell down fresh stock "
            "and prioritize durable contribution-positive core items. "
            "Warnings decay and stay locally scoped. Do not calculate exact "
            "quantities. Return schema-valid JSON with reasoning under 80 "
            "words.\n"
            + json.dumps(
                dashboard,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

