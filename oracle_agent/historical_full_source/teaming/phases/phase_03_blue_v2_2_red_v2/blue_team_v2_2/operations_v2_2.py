from __future__ import annotations

import math
from collections import Counter
from typing import Any

from contracts import Decision, Transfer
from operations_v2_1 import OperationsRiskAgentV21


class OperationsRiskAgentV22(OperationsRiskAgentV21):
    """Delivery-aware orders plus conservative durable-stock transfers."""

    def execute(
        self,
        *,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
        manager_plan: dict[str, Any],
        source: str,
    ) -> tuple[Decision, dict[str, Any]]:
        prices = self._prices(observation, dashboard, manager_plan)
        transfers, transfer_diagnostics = self._transfers(
            observation, dashboard
        )
        orders, diagnostics = self._orders(
            observation,
            dashboard,
            manager_plan,
            transfers=transfers,
        )
        diagnostics["transfers"] = transfer_diagnostics
        reasoning = (
            f"{manager_plan['reasoning']} Operations planned "
            f"{transfer_diagnostics['units']} transfer units and used "
            f"₹{diagnostics['order_cost']:.2f} of "
            f"₹{diagnostics['order_budget']:.2f} order budget while "
            f"keeping {diagnostics['reserve_days']} rent days."
        )
        return (
            Decision(
                orders=orders,
                prices=prices,
                transfers=transfers,
                reasoning=reasoning[-800:],
                source=source,
            ),
            diagnostics,
        )

    def _transfers(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
    ) -> tuple[list[Transfer], dict[str, Any]]:
        reasons = Counter()
        if dashboard["risk_state"] != "normal":
            return [], {
                "routes": 0,
                "units": 0,
                "estimated_cost": 0.0,
                "skipped": {"non_normal_risk": 1},
            }
        if observation["day"] < self.config["transfer_start_day"]:
            return [], {
                "routes": 0,
                "units": 0,
                "estimated_cost": 0.0,
                "skipped": {"insufficient_history": 1},
            }

        rent = observation["rent"]["total_daily"]
        transferable_cash = max(
            0.0,
            observation["shared_cash"]
            - rent * self.config["transfer_cash_reserve_days"],
        )
        affordable_units = math.floor(
            transferable_cash / self.config["transfer_cost_per_unit"]
        )
        remaining_limit = min(
            self.config["maximum_transfer_units_per_day"],
            affordable_units,
        )
        if remaining_limit <= 0:
            return [], {
                "routes": 0,
                "units": 0,
                "estimated_cost": 0.0,
                "skipped": {"cash_reserve": 1},
            }

        free_by_shop_zone = {
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
        donor_surplus: dict[tuple[str, str], int] = {}
        for shop in observation["shops"]:
            for product, catalog in observation["catalog"].items():
                if (
                    catalog["shelf_life_days"]
                    < self.config["transfer_minimum_shelf_life_days"]
                ):
                    continue
                metrics = dashboard["products"][shop][product]
                keep = math.ceil(
                    metrics["forecast_daily"]
                    * self.config["transfer_donor_keep_days"]
                )
                donor_surplus[(shop, product)] = max(
                    0, metrics["on_hand"] - keep
                )

        needs: list[dict[str, Any]] = []
        for shop in observation["shops"]:
            for product, catalog in observation["catalog"].items():
                metrics = dashboard["products"][shop][product]
                if (
                    catalog["shelf_life_days"]
                    < self.config["transfer_minimum_shelf_life_days"]
                ):
                    continue
                if (
                    metrics["stockout_days_last_5"]
                    < self.config["transfer_required_stockout_days"]
                ):
                    continue
                target = math.ceil(
                    metrics["forecast_daily"]
                    * self.config["transfer_recipient_target_days"]
                )
                need = max(
                    0, target - metrics["inventory_position"]
                )
                if need:
                    needs.append(
                        {
                            "shop": shop,
                            "product": product,
                            "zone": catalog["zone"],
                            "need": need,
                            "priority": (
                                metrics["stockout_days_last_5"] * 100
                                + metrics["forecast_daily"]
                                * metrics["unit_margin_at_reference"]
                            ),
                        }
                    )

        transfers: list[Transfer] = []
        for need in sorted(
            needs, key=lambda value: value["priority"], reverse=True
        ):
            if remaining_limit <= 0:
                reasons["daily_limit"] += 1
                break
            destination_space = free_by_shop_zone[need["shop"]][
                need["zone"]
            ]
            if destination_space <= 0:
                reasons["destination_capacity"] += 1
                continue
            donors = sorted(
                (
                    (surplus, shop)
                    for (shop, product), surplus in donor_surplus.items()
                    if product == need["product"]
                    and shop != need["shop"]
                    and surplus > 0
                ),
                reverse=True,
            )
            if not donors:
                reasons["no_safe_donor"] += 1
                continue
            for surplus, donor in donors:
                quantity = min(
                    surplus,
                    need["need"],
                    destination_space,
                    remaining_limit,
                    self.config["maximum_transfer_units_per_route"],
                )
                if quantity <= 0:
                    continue
                transfers.append(
                    Transfer(
                        from_shop=donor,
                        to_shop=need["shop"],
                        product=need["product"],
                        quantity=quantity,
                    )
                )
                donor_surplus[(donor, need["product"])] -= quantity
                free_by_shop_zone[need["shop"]][need["zone"]] -= quantity
                free_by_shop_zone[donor][need["zone"]] += quantity
                need["need"] -= quantity
                destination_space -= quantity
                remaining_limit -= quantity
                if (
                    need["need"] <= 0
                    or destination_space <= 0
                    or remaining_limit <= 0
                ):
                    break

        units = sum(transfer.quantity for transfer in transfers)
        return transfers, {
            "routes": len(transfers),
            "units": units,
            "estimated_cost": round(
                units * self.config["transfer_cost_per_unit"], 2
            ),
            "skipped": dict(reasons),
        }

    def _orders(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
        plan: dict[str, Any],
        *,
        transfers: list[Transfer] | None = None,
    ) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
        transfers = transfers or []
        transfer_units = sum(item.quantity for item in transfers)
        transfer_cost = (
            transfer_units * self.config["transfer_cost_per_unit"]
        )
        transfer_delta: dict[tuple[str, str], int] = Counter()
        for transfer in transfers:
            transfer_delta[(transfer.from_shop, transfer.product)] -= (
                transfer.quantity
            )
            transfer_delta[(transfer.to_shop, transfer.product)] += (
                transfer.quantity
            )

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
            - transfer_cost
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
        pending_by_zone = {
            shop: {
                zone: sum(
                    shipment["quantity"]
                    for shipment in observation["pending_deliveries"]
                    if shipment["shop"] == shop
                    and observation["catalog"][
                        shipment["product"]
                    ]["zone"]
                    == zone
                )
                for zone in observation["shops"][shop]["capacity"]
            }
            for shop in observation["shops"]
        }
        zone_remaining = {
            shop: {
                zone: capacity
                - observation["shops"][shop]["used_capacity"].get(
                    zone, 0
                )
                - pending_by_zone[shop][zone]
                - sum(
                    delta
                    for (changed_shop, product), delta
                    in transfer_delta.items()
                    if changed_shop == shop
                    and observation["catalog"][product]["zone"] == zone
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
        target_covers: dict[str, dict[str, float]] = {
            shop: {} for shop in observation["shops"]
        }
        for shop in observation["shops"]:
            shop_mode = plan["shop_modes"][shop]
            service_pressure = dashboard["shop_health"][shop][
                "service_pressure"
            ]
            for product, catalog in observation["catalog"].items():
                metrics = dashboard["products"][shop][product]
                perishable = catalog["shelf_life_days"] <= 4
                cover = self._delivery_aware_cover(
                    shop=shop,
                    product=product,
                    catalog=catalog,
                    perishable=perishable,
                    pending_pressure=self._pending_pressure(
                        observation,
                        pending_by_zone,
                        shop,
                        catalog["zone"],
                    ),
                )
                factor = self._mode_factor(
                    shop_mode=shop_mode,
                    perishable=perishable,
                ) * {
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
                effective_cover = cover * factor
                if (
                    risk == "normal"
                    and perishable
                    and metrics["stockout_days_last_5"] >= 1
                ):
                    effective_cover = max(
                        effective_cover,
                        min(
                            catalog["shelf_life_days"]
                            - self.config["expiry_safety_days"],
                            self.config["normal_delivery_days"]
                            + self.config["delivery_safety_days"],
                        ),
                    )
                target_covers[shop][product] = round(
                    effective_cover, 2
                )
                inventory_position = max(
                    0,
                    metrics["inventory_position"]
                    + transfer_delta.get((shop, product), 0),
                )
                target = math.ceil(
                    metrics["forecast_daily"] * effective_cover
                )
                needed = max(0, target - inventory_position)
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
                    0.0,
                    effective_cover - metrics["days_cover"],
                ) / max(0.5, effective_cover)
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
                critical_need = 0
                if (
                    risk == "normal"
                    and metrics["stockout_days_last_5"]
                    >= self.config["critical_stockout_days"]
                ):
                    critical_target = math.ceil(
                        metrics["forecast_daily"]
                        * min(
                            catalog["shelf_life_days"]
                            - self.config["expiry_safety_days"]
                            if perishable
                            else self.config[
                                "critical_durable_cover_days"
                            ],
                            self.config["normal_delivery_days"]
                            + self.config["delivery_safety_days"],
                        )
                    )
                    critical_need = max(
                        0, critical_target - inventory_position
                    )
                critical_packs = (
                    math.ceil(critical_need / pack)
                    if critical_need
                    else 0
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
                            "critical": pack_index < critical_packs,
                            "score": (
                                base_score
                                - pack_index
                                * self.config["additional_pack_penalty"]
                            ),
                        }
                    )

        spent = 0.0
        accepted = 0
        accepted_critical = 0
        accepted_by_shop = {shop: 0 for shop in observation["shops"]}
        rejected = {"budget": 0, "capacity": 0, "supplier": 0}
        rejected_capacity_zone = Counter()
        rejected_capacity_product = Counter()
        for item in sorted(
            candidates,
            key=lambda value: (
                value["critical"],
                value["score"],
            ),
            reverse=True,
        ):
            if spent + item["cost"] > budget:
                rejected["budget"] += 1
                continue
            if zone_remaining[item["shop"]][item["zone"]] < item["pack"]:
                rejected["capacity"] += 1
                rejected_capacity_zone[
                    f"{item['shop']}/{item['zone']}"
                ] += 1
                rejected_capacity_product[
                    f"{item['shop']}/{item['product']}"
                ] += 1
                continue
            if supplier[item["product"]] < item["pack"]:
                rejected["supplier"] += 1
                continue
            orders[item["shop"]][item["product"]] += item["pack"]
            spent += item["cost"]
            accepted += 1
            accepted_critical += int(item["critical"])
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
            "transfer_cost_reserved": round(transfer_cost, 2),
            "order_budget": round(budget, 2),
            "order_cost": round(spent, 2),
            "candidate_packs": len(candidates),
            "critical_candidate_packs": sum(
                item["critical"] for item in candidates
            ),
            "accepted_packs": accepted,
            "accepted_critical_packs": accepted_critical,
            "accepted_packs_by_shop": accepted_by_shop,
            "rejected_packs": rejected,
            "capacity_rejections_by_zone": dict(
                rejected_capacity_zone.most_common()
            ),
            "top_capacity_rejected_products": dict(
                rejected_capacity_product.most_common(8)
            ),
            "capacity_remaining_after_plan": zone_remaining,
            "target_cover_days": target_covers,
        }

    def _delivery_aware_cover(
        self,
        *,
        shop: str,
        product: str,
        catalog: dict[str, Any],
        perishable: bool,
        pending_pressure: float,
    ) -> float:
        base = self.config["cover_day_overrides"].get(
            shop, {}
        ).get(
            product,
            self.config["perishable_cover_days"]
            if perishable
            else self.config["durable_cover_days"],
        )
        if perishable:
            return min(
                catalog["shelf_life_days"]
                - self.config["expiry_safety_days"],
                max(
                    float(base),
                    self.config["normal_delivery_days"]
                    + self.config["delivery_safety_days"],
                ),
            )
        reduction = max(
            self.config["minimum_pending_cover_factor"],
            1
            - pending_pressure
            * self.config["pending_pressure_cover_reduction"],
        )
        return float(base) * reduction

    @staticmethod
    def _pending_pressure(
        observation: dict[str, Any],
        pending_by_zone: dict[str, dict[str, int]],
        shop: str,
        zone: str,
    ) -> float:
        capacity = observation["shops"][shop]["capacity"][zone]
        return (
            pending_by_zone[shop][zone] / capacity
            if capacity
            else 0.0
        )
