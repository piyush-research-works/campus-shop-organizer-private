from __future__ import annotations

from typing import Any

from operations_sustainable_4k import SustainableOperations4K


class CashflowOperations4K(SustainableOperations4K):
    """Uses local demand severity to prevent fresh-stock cash traps."""

    def _orders(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
        plan: dict[str, Any],
        *,
        transfers: list[Any] | None = None,
    ) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
        self._current_footfall = observation["today_signals"].get(
            "footfall_hint", {}
        )
        return super()._orders(
            observation,
            dashboard,
            plan,
            transfers=transfers,
        )

    def _delivery_aware_cover(
        self,
        *,
        shop: str,
        product: str,
        catalog: dict[str, Any],
        perishable: bool,
        pending_pressure: float,
    ) -> float:
        base = super()._delivery_aware_cover(
            shop=shop,
            product=product,
            catalog=catalog,
            perishable=perishable,
            pending_pressure=pending_pressure,
        )
        severity = self._active_dashboard["event_risk"][
            "line_severity"
        ][shop][product]
        footfall = self._current_footfall.get(shop, "normal")
        if perishable:
            if severity == "severe" or footfall == "very_low":
                return 0.0
            cap = (
                self.config["fresh_caution_cover_cap"]
                if severity == "caution" or footfall == "low"
                else self.config["fresh_normal_cover_cap"]
            )
            return min(base, float(cap))
        factor = {
            "normal": 1.0,
            "caution": self.config["durable_caution_cover_factor"],
            "severe": self.config["durable_severe_cover_factor"],
        }[severity]
        return base * float(factor)

