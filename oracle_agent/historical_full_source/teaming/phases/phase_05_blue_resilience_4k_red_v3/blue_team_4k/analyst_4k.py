from __future__ import annotations

from typing import Any

from analyst_6k import DemandAnalyst6K


class ResilienceAnalyst4K(DemandAnalyst6K):
    """Adds persistent event risk and shop-level contribution estimates."""

    DROP_WORDS = (
        "break",
        "travel",
        "closure",
        "quieter",
        "very low",
        "depend on weather",
    )

    def __init__(
        self, normal_delivery_days: int, warning_memory_days: int
    ) -> None:
        super().__init__(normal_delivery_days)
        self.warning_memory_days = warning_memory_days
        self.caution_until_day = 0
        self.caution_reason = ""

    def analyze(
        self,
        observation: dict[str, Any],
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        dashboard = super().analyze(observation, memory)
        event_risk = self._event_risk(observation)
        shop_finance = self._shop_finance(observation)
        dashboard["event_risk"] = event_risk
        dashboard["shop_finance"] = shop_finance
        for shop, metrics in shop_finance.items():
            dashboard["shop_health"][shop].update(metrics)

        if event_risk["severity"] == "severe":
            dashboard["risk_state"] = "downturn"
        elif (
            event_risk["severity"] == "caution"
            and dashboard["risk_state"] == "normal"
        ):
            dashboard["risk_state"] = "warning"

        open_shops = [
            shop
            for shop, state in dashboard["shop_status"].items()
            if state["open"]
        ]
        daily_rent = observation["rent"]["per_shop"] * len(open_shops)
        expected_contribution = sum(
            shop_finance[shop]["average_daily_contribution"]
            for shop in open_shops
        )
        dashboard["financial_outlook"] = {
            "open_shop_count": len(open_shops),
            "cash_runway_at_zero_sales": round(
                observation["shared_cash"] / max(1.0, daily_rent), 2
            ),
            "estimated_daily_network_contribution": round(
                expected_contribution, 2
            ),
            "projected_cash_after_5_days": round(
                observation["shared_cash"]
                + expected_contribution * 5,
                2,
            ),
            "recent_network_revenue": round(
                sum(
                    metrics["revenue_last_5"]
                    for metrics in shop_finance.values()
                ),
                2,
            ),
        }
        return dashboard

    def _event_risk(
        self, observation: dict[str, Any]
    ) -> dict[str, Any]:
        signals = observation["today_signals"]
        bulletin = signals.get("weekly_bulletin") or ""
        announcements = signals.get("announcements", [])
        combined = " ".join([bulletin, *announcements]).lower()
        day = int(observation["day"])
        matched = [word for word in self.DROP_WORDS if word in combined]
        very_low_shops = [
            shop
            for shop, hint in signals["footfall_hint"].items()
            if hint == "very_low"
        ]
        price_pressure = "price-conscious" in combined
        supply_risk = any(
            phrase in combined
            for phrase in ("supply timing", "less reliable", "wholesale")
        )
        if matched:
            self.caution_until_day = max(
                self.caution_until_day,
                day + self.warning_memory_days,
            )
            self.caution_reason = matched[0]

        if very_low_shops:
            severity = "severe"
        elif matched or day <= self.caution_until_day:
            severity = "caution"
        else:
            severity = "normal"
        return {
            "severity": severity,
            "caution_until_day": self.caution_until_day,
            "days_remaining": max(0, self.caution_until_day - day),
            "reason": self.caution_reason if severity != "normal" else "",
            "very_low_shops": very_low_shops,
            "price_pressure": price_pressure,
            "supply_risk": supply_risk,
            "current_warning_terms": matched,
        }

    @staticmethod
    def _shop_finance(
        observation: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        history = observation["recent_history"][-5:]
        per_shop_rent = observation["rent"]["per_shop"]
        result: dict[str, dict[str, Any]] = {}
        for shop in observation["shops"]:
            revenue = 0.0
            estimated_cogs = 0.0
            open_days = 0
            negative_days = 0
            sales_units = 0
            for day in history:
                lifecycle = day.get("shop_lifecycle", {}).get(shop, {})
                was_open = lifecycle.get("open", True)
                if not was_open:
                    continue
                open_days += 1
                day_revenue = float(day["revenue_by_shop"][shop])
                day_cogs = 0.0
                for product, catalog in observation["catalog"].items():
                    sold = int(day["sales"][shop][product])
                    sales_units += sold
                    day_cogs += sold * float(
                        catalog["current_unit_cost"]
                    )
                revenue += day_revenue
                estimated_cogs += day_cogs
                if day_revenue - day_cogs - per_shop_rent < 0:
                    negative_days += 1
            contribution = (
                revenue - estimated_cogs - open_days * per_shop_rent
            )
            divisor = max(1, open_days)
            result[shop] = {
                "open_history_days": open_days,
                "revenue_last_5": round(revenue, 2),
                "estimated_gross_margin_last_5": round(
                    revenue - estimated_cogs, 2
                ),
                "contribution_last_5": round(contribution, 2),
                "average_daily_contribution": round(
                    contribution / divisor, 2
                ),
                "negative_contribution_days": negative_days,
                "sales_units_last_5_finance": sales_units,
            }
        return result
