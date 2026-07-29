from __future__ import annotations

from typing import Any


class CompactMemory:
    """Small outcome memory derived only from public observations."""

    def __init__(self, maximum_days: int = 7) -> None:
        self.maximum_days = maximum_days
        self.last_processed_day = 0
        self.entries: list[dict[str, Any]] = []

    def update(self, observation: dict[str, Any]) -> dict[str, Any]:
        history = observation["recent_history"]
        if history:
            latest = history[-1]
            day = int(latest["day"])
            if day > self.last_processed_day:
                stockouts = sum(
                    bool(flag)
                    for shop in latest["stockout"].values()
                    for flag in shop.values()
                )
                spoiled = sum(
                    int(value)
                    for shop in latest["spoiled_units"].values()
                    for value in shop.values()
                )
                revenue = round(
                    sum(latest["revenue_by_shop"].values()), 2
                )
                self.entries.append(
                    {
                        "day": day,
                        "cash": latest["cash"],
                        "revenue": revenue,
                        "stockout_lines": stockouts,
                        "spoiled_units": spoiled,
                    }
                )
                self.entries = self.entries[-self.maximum_days :]
                self.last_processed_day = day
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        if not self.entries:
            return {
                "days_recorded": 0,
                "cash_trend": 0,
                "average_revenue": 0,
                "recent_stockout_lines": 0,
                "recent_spoiled_units": 0,
            }
        return {
            "days_recorded": len(self.entries),
            "cash_trend": round(
                self.entries[-1]["cash"] - self.entries[0]["cash"], 2
            ),
            "average_revenue": round(
                sum(item["revenue"] for item in self.entries)
                / len(self.entries),
                2,
            ),
            "recent_stockout_lines": sum(
                item["stockout_lines"] for item in self.entries[-3:]
            ),
            "recent_spoiled_units": sum(
                item["spoiled_units"] for item in self.entries[-3:]
            ),
            "last_days": self.entries[-3:],
        }
