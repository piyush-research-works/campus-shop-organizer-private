from __future__ import annotations

import math
import re
from typing import Any, Callable


class ConservativeTokenCounter:
    """Dependency-free conservative estimate for English/JSON prompts."""

    def __init__(self, chars_per_token: float = 3.0) -> None:
        self.chars_per_token = chars_per_token
        self.method = (
            f"max(utf8_bytes/{chars_per_token}, lexical_pieces)"
        )

    def count(self, text: str) -> int:
        byte_estimate = math.ceil(
            len(text.encode("utf-8")) / self.chars_per_token
        )
        lexical = len(re.findall(r"[A-Za-z]+|\d+|[^\w\s]", text))
        return max(byte_estimate, lexical)


class ContextWindowManager:
    """Builds a focused prompt while raw history remains external."""

    def __init__(
        self,
        *,
        token_limit: int,
        recent_days: int,
        maximum_events: int,
        chars_per_token: float,
    ) -> None:
        self.token_limit = token_limit
        self.recent_days = recent_days
        self.maximum_events = maximum_events
        self.counter = ConservativeTokenCounter(chars_per_token)
        self.events: list[dict[str, Any]] = []
        self.last_event_key: tuple[Any, ...] | None = None

    def build(
        self,
        dashboard: dict[str, Any],
        prompt_builder: Callable[[dict[str, Any]], str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self._remember(dashboard)
        compact = self._compact(dashboard)
        stages: list[str] = []
        prompt = prompt_builder(compact)
        estimated = self.counter.count(prompt)

        if estimated > self.token_limit and compact["long_term_memory"]:
            compact["long_term_memory"] = compact[
                "long_term_memory"
            ][-3:]
            stages.append("trimmed_long_term_memory_to_3")
            prompt = prompt_builder(compact)
            estimated = self.counter.count(prompt)
        if estimated > self.token_limit:
            compact["memory"]["last_days"] = compact["memory"][
                "last_days"
            ][-1:]
            stages.append("trimmed_working_memory_to_1_day")
            prompt = prompt_builder(compact)
            estimated = self.counter.count(prompt)
        if estimated > self.token_limit:
            for products in compact["products"].values():
                for metrics in products.values():
                    metrics.pop("avg", None)
                    metrics.pop("margin", None)
            stages.append("removed_optional_product_fields")
            prompt = prompt_builder(compact)
            estimated = self.counter.count(prompt)
        if estimated > self.token_limit:
            raise RuntimeError(
                f"Daily context estimate {estimated} exceeds "
                f"{self.token_limit} token limit."
            )

        manifest = {
            "day": dashboard["day"],
            "token_limit": self.token_limit,
            "estimated_prompt_tokens": estimated,
            "remaining_tokens": self.token_limit - estimated,
            "utilization": round(estimated / self.token_limit, 4),
            "counter_method": self.counter.method,
            "current_product_lines": sum(
                len(products)
                for products in compact["products"].values()
            ),
            "working_memory_days": len(
                compact["memory"]["last_days"]
            ),
            "long_term_events": len(compact["long_term_memory"]),
            "compression_stages": stages,
            "hard_limit_passed": True,
        }
        return compact, manifest

    def _compact(self, dashboard: dict[str, Any]) -> dict[str, Any]:
        memory = dashboard["memory"]
        return {
            "cycle": dashboard["cycle"],
            "day": dashboard["day"],
            "cash": dashboard["cash"],
            "daily_rent": dashboard["daily_rent"],
            "risk_state": dashboard["risk_state"],
            "signals": dashboard["signals"],
            "delivery_context": dashboard.get("delivery_context", {}),
            "shop_status": dashboard.get("shop_status", {}),
            "closure_rules": dashboard.get("closure_rules", {}),
            "shop_health": dashboard.get("shop_health", {}),
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
            "context_rule": (
                "Raw logs are external. Base decisions only on this "
                "budgeted daily context."
            ),
        }

    def _remember(self, dashboard: dict[str, Any]) -> None:
        announcements = dashboard["signals"].get("announcements", [])
        memory = dashboard["memory"]
        event_key = (
            dashboard["day"],
            dashboard["risk_state"],
            tuple(announcements),
            memory.get("recent_stockout_lines", 0),
            memory.get("recent_spoiled_units", 0),
        )
        important = (
            dashboard["risk_state"] != "normal"
            or bool(announcements)
            or memory.get("recent_stockout_lines", 0) >= 5
            or memory.get("recent_spoiled_units", 0) >= 5
        )
        if important and event_key != self.last_event_key:
            self.events.append(
                {
                    "day": dashboard["day"],
                    "risk": dashboard["risk_state"],
                    "cash": dashboard["cash"],
                    "announcements": announcements[:2],
                    "stockout_lines": memory.get(
                        "recent_stockout_lines", 0
                    ),
                    "spoiled_units": memory.get(
                        "recent_spoiled_units", 0
                    ),
                }
            )
            self.events = self.events[-30:]
            self.last_event_key = event_key

    def _retrieve(
        self, dashboard: dict[str, Any]
    ) -> list[dict[str, Any]]:
        current_text = " ".join(
            dashboard["signals"].get("announcements", [])
        ).lower()
        scored = []
        for event in self.events:
            age = dashboard["day"] - event["day"]
            recency = max(0, 30 - age)
            event_text = " ".join(event["announcements"]).lower()
            overlap = sum(
                word in event_text
                for word in set(current_text.split())
                if len(word) > 4
            )
            impact = (
                event["stockout_lines"] + event["spoiled_units"]
            )
            scored.append(
                (recency + overlap * 10 + impact, event)
            )
        selected = sorted(
            scored, key=lambda item: item[0], reverse=True
        )[: self.maximum_events]
        return [
            event
            for _, event in sorted(
                selected, key=lambda item: item[1]["day"]
            )
        ]
