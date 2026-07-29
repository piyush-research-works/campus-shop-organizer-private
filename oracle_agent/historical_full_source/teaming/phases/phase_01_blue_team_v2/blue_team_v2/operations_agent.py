from __future__ import annotations

import math
from typing import Any

from contracts import Decision


class OperationsRiskAgent:
    """Turns a strategic plan into affordable, capacity-safe daily actions."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def execute(
        self,
        *,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
        manager_plan: dict[str, Any],
        source: str,
    ) -> tuple[Decision, dict[str, Any]]:
        prices = self._prices(observation, dashboard, manager_plan)
        orders, diagnostics = self._orders(
            observation, dashboard, manager_plan
        )
        reasoning = (
            f"{manager_plan['reasoning']} Operations used "
            f"₹{diagnostics['order_cost']:.2f} of "
            f"₹{diagnostics['order_budget']:.2f} order budget and kept "
            f"{diagnostics['reserve_days']} rent days."
        )
        return (
            Decision(
                orders=orders,
                prices=prices,
                transfers=[],
                reasoning=reasoning[-800:],
                source=source,
            ),
            diagnostics,
        )

    def _prices(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, dict[str, float]]:
        actions = {
            (item["shop"], item["product"]): item["action"]
            for item in plan["price_actions"]
        }
        result: dict[str, dict[str, float]] = {}
        day = observation["day"]
        for shop, state in observation["shops"].items():
            result[shop] = {}
            for product, catalog in observation["catalog"].items():
                midpoint = sum(catalog["normal_price_range"]) / 2
                action = actions.get((shop, product), "reference")
                expiring = (
                    dashboard["products"][shop][product][
                        "expiring_within_2_days"
                    ]
                    > 0
                )
                ratio = 1.0
                if action == "premium":
                    ratio = self.config["premium_price_ratio"]
                elif action == "discount" and expiring:
                    ratio = self.config["discount_price_ratio"]
                price = midpoint * ratio
                low = midpoint * self.config[
                    "minimum_normal_price_ratio"
                ]
                if expiring:
                    low = max(
                        catalog["current_unit_cost"] * 1.08,
                        midpoint * 0.88,
                    )
                high = midpoint * self.config["maximum_price_ratio"]
                result[shop][product] = round(
                    min(high, max(low, price)), 2
                )
        return result

    def _orders(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
        plan: dict[str, Any],
    ) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
        risk = dashboard["risk_state"]
        reserve_days = {
            "normal": self.config["minimum_rent_reserve_days"],
            "warning": self.config["warning_rent_reserve_days"],
            "downturn": self.config["downturn_rent_reserve_days"],
        }[risk]
        reserve_days += {
            "protect": 1,
            "balanced": 0,
            "growth": -0.5,
        }[plan["cash_posture"]]
        reserve_days = max(1.0, reserve_days)
        budget = max(
            0.0,
            observation["shared_cash"]
            - observation["rent"]["total_daily"] * reserve_days,
        )
        priorities = {
            (item["shop"], item["product"]): item["priority"]
            for item in plan["stock_actions"]
        }
        orders = {
            shop: {product: 0 for product in observation["catalog"]}
            for shop in observation["shops"]
        }
        zone_remaining = {
            shop: {
                zone: capacity
                - observation["shops"][shop]["used_capacity"].get(
                    zone, 0
                )
                - sum(
                    shipment["quantity"]
                    for shipment in observation["pending_deliveries"]
                    if shipment["shop"] == shop
                    and observation["catalog"][
                        shipment["product"]
                    ]["zone"]
                    == zone
                )
                for zone, capacity in observation["shops"][shop][
                    "capacity"
                ].items()
            }
            for shop in observation["shops"]
        }
        supplier = {
            product: catalog["supplier_daily_limit_all_shops"]
            for product, catalog in observation["catalog"].items()
        }
        candidates: list[dict[str, Any]] = []
        for shop in observation["shops"]:
            shop_mode = plan["shop_modes"][shop]
            mode_factor = {
                "protect": 0.8,
                "balanced": 1.0,
                "growth": 1.15,
                "clear_fresh": 0.55,
            }[shop_mode]
            for product, catalog in observation["catalog"].items():
                metrics = dashboard["products"][shop][product]
                perishable = catalog["shelf_life_days"] <= 4
                cover = (
                    self.config["perishable_cover_days"]
                    if perishable
                    else self.config["durable_cover_days"]
                )
                factor = mode_factor * {
                    "low": 0.65,
                    "normal": 1.0,
                    "high": 1.3,
                }[priorities.get((shop, product), "normal")]
                if risk == "warning" and perishable:
                    factor *= 0.4
                if risk == "downturn":
                    factor *= 0.0 if perishable else 0.45
                target = math.ceil(
                    metrics["forecast_daily"] * cover * factor
                )
                needed = max(
                    0, target - metrics["inventory_position"]
                )
                pack = catalog["pack_size"]
                packs = math.ceil(needed / pack) if needed else 0
                midpoint = sum(catalog["normal_price_range"]) / 2
                margin_return = max(
                    0.0, midpoint - catalog["current_unit_cost"]
                ) / max(0.01, catalog["current_unit_cost"])
                shortfall = max(
                    0.0, cover - metrics["days_cover"]
                ) / max(0.5, cover)
                for pack_index in range(packs):
                    candidates.append(
                        {
                            "shop": shop,
                            "product": product,
                            "zone": catalog["zone"],
                            "pack": pack,
                            "cost": pack
                            * catalog["current_unit_cost"],
                            "score": (
                                shortfall * 40
                                + metrics["stockout_days_last_5"] * 18
                                + margin_return * 20
                                - pack_index * 7
                            ),
                        }
                    )
        spent = 0.0
        accepted_packs = 0
        for item in sorted(
            candidates, key=lambda value: value["score"], reverse=True
        ):
            if spent + item["cost"] > budget:
                continue
            if zone_remaining[item["shop"]][item["zone"]] < item["pack"]:
                continue
            if supplier[item["product"]] < item["pack"]:
                continue
            orders[item["shop"]][item["product"]] += item["pack"]
            spent += item["cost"]
            accepted_packs += 1
            zone_remaining[item["shop"]][item["zone"]] -= item["pack"]
            supplier[item["product"]] -= item["pack"]
        return orders, {
            "risk_state": risk,
            "cash_posture": plan["cash_posture"],
            "reserve_days": reserve_days,
            "reserve_value": round(
                reserve_days * observation["rent"]["total_daily"], 2
            ),
            "order_budget": round(budget, 2),
            "order_cost": round(spent, 2),
            "candidate_packs": len(candidates),
            "accepted_packs": accepted_packs,
        }
