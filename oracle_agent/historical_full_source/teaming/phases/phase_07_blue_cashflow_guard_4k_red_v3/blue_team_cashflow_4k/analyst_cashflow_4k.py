from __future__ import annotations

from typing import Any

from analyst_sustainable_4k import SustainableDemandAnalyst4K


class CashflowDemandAnalyst4K(SustainableDemandAnalyst4K):
    """Adds business-continuity state to the scoped demand analysis."""

    def analyze(
        self,
        observation: dict[str, Any],
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        dashboard = super().analyze(observation, memory)
        status = observation.get("shop_status", {})
        open_shops = sorted(
            shop for shop, state in status.items() if state["open"]
        )
        cash = float(observation["shared_cash"])
        per_shop_rent = float(observation["rent"]["per_shop"])
        rules = observation.get("closure_rules", {})
        reopening_cost = float(rules.get("reopening_cost", 0))
        reserve_days = max(
            float(rules.get("reopening_reserve_days", 0)),
            float(self.config["reopen_cash_reserve_days"]),
        )
        recovery_required = (
            reopening_cost
            + per_shop_rent * reserve_days
            + float(self.config["reopen_seed_inventory_budget"])
        )
        recovery_gap = (
            max(0.0, recovery_required - cash)
            if not open_shops
            else 0.0
        )
        dashboard["business_continuity"] = {
            "open_shop_count": len(open_shops),
            "open_shops": open_shops,
            "network_closed": not open_shops,
            "next_day_rent": round(
                per_shop_rent * len(open_shops), 2
            ),
            "cash_after_next_rent": round(
                cash - per_shop_rent * len(open_shops), 2
            ),
            "minimum_single_shop_recovery_cash": round(
                recovery_required, 2
            ),
            "recovery_capital_gap": round(recovery_gap, 2),
        }
        if not open_shops:
            dashboard["risk_state"] = "downturn"
            dashboard["risk_reason"] = "closed_network_recovery_deadlock"
        return dashboard

