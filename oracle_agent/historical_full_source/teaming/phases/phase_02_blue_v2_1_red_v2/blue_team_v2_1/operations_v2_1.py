from __future__ import annotations

import math
from typing import Any

from operations_agent import OperationsRiskAgent


class OperationsRiskAgentV21(OperationsRiskAgent):
    """Allocates cash and capacity using service and contribution value."""

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
            service_pressure = dashboard["shop_health"][shop][
                "service_pressure"
            ]
            for product, catalog in observation["catalog"].items():
                metrics = dashboard["products"][shop][product]
                perishable = catalog["shelf_life_days"] <= 4
                cover = self._cover_days(
                    shop=shop,
                    product=product,
                    perishable=perishable,
                )
                mode_factor = self._mode_factor(
                    shop_mode=shop_mode,
                    perishable=perishable,
                )
                factor = mode_factor * {
                    "low": 0.65,
                    "normal": 1.0,
                    "high": 1.3,
                }[priorities.get((shop, product), "normal")]
                if risk == "warning" and perishable:
                    factor *= self.config["warning_perishable_factor"]
                if risk == "downturn":
                    factor *= (
                        self.config["downturn_perishable_factor"]
                        if perishable
                        else self.config["downturn_durable_factor"]
                    )
                target = math.ceil(
                    metrics["forecast_daily"] * cover * factor
                )
                needed = max(
                    0, target - metrics["inventory_position"]
                )
                pack = catalog["pack_size"]
                packs = math.ceil(needed / pack) if needed else 0
                midpoint = sum(catalog["normal_price_range"]) / 2
                unit_margin = max(
                    0.0, midpoint - catalog["current_unit_cost"]
                )
                margin_return = unit_margin / max(
                    0.01, catalog["current_unit_cost"]
                )
                shortfall = max(
                    0.0, cover - metrics["days_cover"]
                ) / max(0.5, cover)
                expected_contribution = (
                    metrics["forecast_daily"] * unit_margin
                )
                base_score = (
                    shortfall * self.config["shortfall_score_weight"]
                    + metrics["stockout_days_last_5"]
                    * self.config["product_stockout_score_weight"]
                    + margin_return
                    * self.config["margin_return_score_weight"]
                    + min(
                        self.config["expected_contribution_score_cap"],
                        expected_contribution
                        * self.config[
                            "expected_contribution_score_weight"
                        ],
                    )
                    + service_pressure
                    * self.config["shop_service_pressure_score_weight"]
                )
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
                                base_score
                                - pack_index
                                * self.config["additional_pack_penalty"]
                            ),
                        }
                    )

        spent = 0.0
        accepted = 0
        accepted_by_shop = {shop: 0 for shop in observation["shops"]}
        rejected = {"budget": 0, "capacity": 0, "supplier": 0}
        for item in sorted(
            candidates, key=lambda value: value["score"], reverse=True
        ):
            if spent + item["cost"] > budget:
                rejected["budget"] += 1
                continue
            if zone_remaining[item["shop"]][item["zone"]] < item["pack"]:
                rejected["capacity"] += 1
                continue
            if supplier[item["product"]] < item["pack"]:
                rejected["supplier"] += 1
                continue
            orders[item["shop"]][item["product"]] += item["pack"]
            spent += item["cost"]
            accepted += 1
            accepted_by_shop[item["shop"]] += 1
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
            "accepted_packs": accepted,
            "accepted_packs_by_shop": accepted_by_shop,
            "rejected_packs": rejected,
            "capacity_remaining_after_plan": zone_remaining,
        }

    def _cover_days(
        self,
        *,
        shop: str,
        product: str,
        perishable: bool,
    ) -> float:
        override = self.config["cover_day_overrides"].get(
            shop, {}
        ).get(product)
        if override is not None:
            return float(override)
        return float(
            self.config["perishable_cover_days"]
            if perishable
            else self.config["durable_cover_days"]
        )

    def _mode_factor(
        self, *, shop_mode: str, perishable: bool
    ) -> float:
        if shop_mode == "clear_fresh":
            return float(
                self.config["clear_fresh_perishable_factor"]
                if perishable
                else self.config["clear_fresh_durable_factor"]
            )
        return {
            "protect": 0.85,
            "balanced": 1.0,
            "growth": 1.15,
        }[shop_mode]
