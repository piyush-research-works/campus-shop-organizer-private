from __future__ import annotations

from typing import Any

from contracts import Decision, Policy


class BlueManager:
    name = "blue_team_manager"

    def __init__(
        self,
        *,
        primary: Policy,
        fallback: Policy,
        strict: bool,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.strict = strict
        self.primary_calls = 0
        self.fallback_calls = 0

    def decide(self, observation: dict[str, Any]) -> Decision:
        self.primary_calls += 1
        try:
            return self.primary.decide(observation)
        except Exception as exc:
            if self.strict:
                raise
            self.fallback_calls += 1
            fallback = self.fallback.decide(observation)
            return Decision(
                orders=fallback.orders,
                prices=fallback.prices,
                transfers=fallback.transfers,
                reasoning=(
                    "Fallback after Codex failure: "
                    f"{str(exc).rstrip('.')}. {fallback.reasoning}"
                ),
                source=f"fallback:{fallback.source}",
            )
