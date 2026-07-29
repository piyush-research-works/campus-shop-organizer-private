from __future__ import annotations

from typing import Any


def build_context(observation: dict[str, Any]) -> dict[str, Any]:
    """Keep only decision-relevant public information for the manager."""
    history = observation.get("recent_history", [])[-3:]
    compact_history = []
    for day in history:
        compact_history.append(
            {
                "day": day.get("day"),
                "cash": day.get("cash"),
                "sales": day.get("sales", {}),
                "stockout": day.get("stockout", {}),
                "spoiled_units": day.get("spoiled_units", {}),
                "revenue_by_shop": day.get("revenue_by_shop", {}),
            }
        )
    shops = {}
    for shop_id, shop in observation["shops"].items():
        shops[shop_id] = {
            "open": observation["shop_status"][shop_id]["open"],
            "inventory_units": {
                product: int(details["units"])
                for product, details in shop["inventory"].items()
            },
        }
    return {
        "day": observation["day"],
        "cash": observation["shared_cash"],
        "daily_rent": observation["rent"]["total_daily"],
        "signals": observation.get("today_signals", {}),
        "shops": shops,
        "recent_history": compact_history,
        "instruction": (
            "Choose lean, balanced, or service posture and one focus per shop. "
            "Do not calculate order quantities."
        ),
    }

