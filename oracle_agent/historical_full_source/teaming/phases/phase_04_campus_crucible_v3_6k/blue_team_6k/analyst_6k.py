from __future__ import annotations

from typing import Any

from analyst_v2_2 import DemandAnalystV22


class DemandAnalyst6K(DemandAnalystV22):
    """Carries public lifecycle facts into the budgeted dashboard."""

    def analyze(
        self,
        observation: dict[str, Any],
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        dashboard = super().analyze(observation, memory)
        dashboard["shop_status"] = observation.get("shop_status", {})
        dashboard["closure_rules"] = observation.get(
            "closure_rules", {}
        )
        if any(
            not state["open"]
            for state in dashboard["shop_status"].values()
        ):
            dashboard["risk_state"] = "warning"
        return dashboard
