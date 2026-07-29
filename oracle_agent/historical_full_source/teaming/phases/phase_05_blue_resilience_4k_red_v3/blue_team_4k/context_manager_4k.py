from __future__ import annotations

from typing import Any

from context_manager import ContextWindowManager


class ResilienceContextManager4K(ContextWindowManager):
    """Compact 4K view: current facts, two days, selected events."""

    def _compact(self, dashboard: dict[str, Any]) -> dict[str, Any]:
        memory = dashboard["memory"]
        return {
            "cycle": dashboard["cycle"],
            "day": dashboard["day"],
            "cash": dashboard["cash"],
            "daily_rent": dashboard["daily_rent"],
            "risk": dashboard["risk_state"],
            "signals": dashboard["signals"],
            "event_risk": dashboard["event_risk"],
            "financial_outlook": dashboard["financial_outlook"],
            "operational_guardrails": dashboard.get(
                "operational_guardrails", {}
            ),
            "shop_status": dashboard.get("shop_status", {}),
            "shop_finance": dashboard.get("shop_finance", {}),
            "delivery": dashboard.get("delivery_context", {}),
            "memory": {
                "cash_trend": memory.get("cash_trend", 0),
                "average_revenue": memory.get("average_revenue", 0),
                "recent_stockout_lines": memory.get(
                    "recent_stockout_lines", 0
                ),
                "recent_spoiled_units": memory.get(
                    "recent_spoiled_units", 0
                ),
                "last_days": memory.get("last_days", [])[
                    -self.recent_days :
                ],
            },
            "products": {
                shop: {
                    product: {
                        "forecast": metrics["forecast_daily"],
                        "avg": metrics["recent_sales_average"],
                        "stockout5": metrics[
                            "stockout_days_last_5"
                        ],
                        "on_hand": metrics["on_hand"],
                        "pending": metrics["pending"],
                        "cover": metrics["days_cover"],
                        "expiry2": metrics[
                            "expiring_within_2_days"
                        ],
                        "margin": metrics[
                            "unit_margin_at_reference"
                        ],
                        "urgent": metrics.get(
                            "service_urgent", False
                        ),
                    }
                    for product, metrics in products.items()
                }
                for shop, products in dashboard["products"].items()
            },
            "long_term_memory": self._retrieve(dashboard),
            "rules": (
                "Guardrails are binding. Raw logs remain external. "
                "All 24 current product lines are present."
            ),
        }
