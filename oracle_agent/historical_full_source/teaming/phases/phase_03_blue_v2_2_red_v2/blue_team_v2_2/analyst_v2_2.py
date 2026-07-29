from __future__ import annotations

from typing import Any

from analyst_v2_1 import DemandAnalystV21


class DemandAnalystV22(DemandAnalystV21):
    """Adds delivery-aware service flags to the V2.1 dashboard."""

    def __init__(self, normal_delivery_days: int) -> None:
        super().__init__()
        self.normal_delivery_days = normal_delivery_days

    def analyze(
        self,
        observation: dict[str, Any],
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        dashboard = super().analyze(observation, memory)
        urgent_lines = 0
        for shop, products in dashboard["products"].items():
            for product, metrics in products.items():
                service_urgent = (
                    metrics["stockout_days_last_5"] >= 2
                    and metrics["days_cover"]
                    < self.normal_delivery_days
                )
                metrics["service_urgent"] = service_urgent
                urgent_lines += int(service_urgent)
        dashboard["delivery_context"] = {
            "normal_delivery_days": self.normal_delivery_days,
            "service_urgent_product_lines": urgent_lines,
        }
        return dashboard
