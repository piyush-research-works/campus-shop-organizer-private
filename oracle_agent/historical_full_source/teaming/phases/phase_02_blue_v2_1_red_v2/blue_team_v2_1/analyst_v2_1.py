from __future__ import annotations

from typing import Any

from analyst import DemandAnalyst


class DemandAnalystV21(DemandAnalyst):
    """Adds shop-level service pressure to the V2 demand dashboard."""

    def analyze(
        self,
        observation: dict[str, Any],
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        dashboard = super().analyze(observation, memory)
        history = observation["recent_history"][-5:]
        product_count = max(1, len(observation["catalog"]))
        shop_health: dict[str, dict[str, float | int]] = {}
        for shop in observation["shops"]:
            stockout_lines = sum(
                bool(day["stockout"][shop][product])
                for day in history
                for product in observation["catalog"]
            )
            spoiled_units = sum(
                int(day["spoiled_units"][shop][product])
                for day in history
                for product in observation["catalog"]
            )
            sales_units = sum(
                int(day["sales"][shop][product])
                for day in history
                for product in observation["catalog"]
            )
            possible_lines = len(history) * product_count
            shop_health[shop] = {
                "history_days": len(history),
                "stockout_lines_last_5": stockout_lines,
                "spoiled_units_last_5": spoiled_units,
                "sales_units_last_5": sales_units,
                "service_pressure": round(
                    stockout_lines / possible_lines
                    if possible_lines
                    else 0.0,
                    4,
                ),
            }
        dashboard["shop_health"] = shop_health
        return dashboard
