from __future__ import annotations

from typing import Any

from simulation.inventory import NetworkInventory, snapshot, zone_usage


def build_observation(
    *,
    cycle: int,
    day: int,
    cash: float,
    inventories: NetworkInventory,
    prices: dict[str, dict[str, float]],
    pending: list[dict[str, Any]],
    products: dict[str, dict[str, Any]],
    shops: dict[str, dict[str, Any]],
    config: dict[str, Any],
    strategy: dict[str, Any],
    public_signal: dict[str, Any],
    wholesale_multiplier: dict[str, float],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "cycle": cycle,
        "day": day,
        "shared_cash": round(cash, 2),
        "rent": {
            "per_shop": config["daily_rent_per_shop"],
            "total_daily": config["daily_rent_per_shop"] * len(shops),
        },
        "delivery_rules": {
            "normal_days": config["normal_delivery_days"],
            "possible_extra_delay": 1,
            "opening_order_arrives_before_day_1_sales": True,
        },
        "today_signals": public_signal,
        "current_strategy": strategy,
        "opening_market_hint": (
            {
                shop_id: {
                    item_id: [
                        max(
                            0,
                            round(
                                shop["base_sales"][item_id]
                                * config["demand"]["base_scale"]
                                * 0.75
                            ),
                        ),
                        max(
                            1,
                            round(
                                shop["base_sales"][item_id]
                                * config["demand"]["base_scale"]
                                * 1.25
                            ),
                        ),
                    ]
                    for item_id in products
                }
                for shop_id, shop in shops.items()
            }
            if day == 1
            else None
        ),
        "shops": {
            shop_id: {
                "name": shop["name"],
                "customer_hint": shop["customer_hint"],
                "capacity": shop["capacity"],
                "used_capacity": zone_usage(
                    inventories[shop_id], products
                ),
                "inventory": snapshot(inventories[shop_id]),
                "current_prices": prices[shop_id],
            }
            for shop_id, shop in shops.items()
        },
        "catalog": {
            item_id: {
                "name": product["name"],
                "zone": product["zone"],
                "current_unit_cost": round(
                    product["unit_cost"]
                    * wholesale_multiplier[item_id],
                    2,
                ),
                "normal_price_range": [
                    round(product["reference_price"] * 0.95, 2),
                    round(product["reference_price"] * 1.05, 2),
                ],
                "shelf_life_days": product["shelf_life_days"],
                "pack_size": product["pack_size"],
                "supplier_daily_limit_all_shops": product[
                    "supplier_daily_limit"
                ],
            }
            for item_id, product in products.items()
        },
        "pending_deliveries": [dict(item) for item in pending],
        "recent_history": history[
            -config["history_days_visible_to_agent"] :
        ],
        "hidden_information_notice": (
            "Exact demand, price elasticity, affordability cutoffs, event "
            "multipliers, future events, and scenario seed are not visible."
        ),
    }
