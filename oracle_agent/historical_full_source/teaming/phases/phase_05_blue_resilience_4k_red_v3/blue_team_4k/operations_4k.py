from __future__ import annotations

import copy
from typing import Any

from operations_6k import OperationsRiskAgent6K
from shop_decision import ShopDecision


class ResilientOperations4K(OperationsRiskAgent6K):
    """Safe execution: guardrails override unsafe strategic suggestions."""

    def execute(
        self,
        *,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
        manager_plan: dict[str, Any],
        guardrails: dict[str, Any],
        source: str,
    ) -> tuple[ShopDecision, dict[str, Any]]:
        plan = copy.deepcopy(manager_plan)
        if guardrails["force_cash_posture"]:
            plan["cash_posture"] = guardrails["force_cash_posture"]
        inactive = set(guardrails["inactive_shops_after"])
        for shop in inactive:
            plan["shop_modes"][shop] = "protect"

        self._guardrails = guardrails
        self._inactive_shops = inactive
        prices = self._prices(observation, dashboard, plan)
        transfers, transfer_diagnostics = self._transfers(
            observation, dashboard
        )
        orders, diagnostics = self._orders(
            observation,
            dashboard,
            plan,
            transfers=transfers,
        )
        diagnostics["transfers"] = transfer_diagnostics
        diagnostics["guardrails"] = guardrails
        reasoning = (
            f"{plan['reasoning']} Finance guardrail: "
            f"{guardrails['action_reason']}; runway "
            f"{guardrails['cash_runway_days']} days. Operations used "
            f"Rs {diagnostics['order_cost']:.2f} of "
            f"Rs {diagnostics['order_budget']:.2f} safe order budget."
        )
        return (
            ShopDecision(
                orders=orders,
                prices=prices,
                transfers=transfers,
                reasoning=reasoning[-800:],
                source=source,
                shop_actions=guardrails["actions"],
            ),
            diagnostics,
        )

    def _orders(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
        plan: dict[str, Any],
        *,
        transfers: list[Any] | None = None,
    ) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
        adjusted_dashboard = copy.deepcopy(dashboard)
        for shop in self._inactive_shops:
            for metrics in adjusted_dashboard["products"][shop].values():
                metrics["forecast_daily"] = 0.0
                metrics["inventory_position"] = max(
                    metrics["on_hand"], metrics["pending"]
                )
                metrics["service_urgent"] = False

        risk = adjusted_dashboard["risk_state"]
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
        transfer_cost = sum(
            transfer.quantity for transfer in transfers or []
        ) * self.config["transfer_cost_per_unit"]
        available_cash = max(
            0.0,
            observation["shared_cash"]
            - self._guardrails["action_cash_cost"],
        )
        reserve_value = (
            observation["rent"]["total_daily"] * reserve_days
        )
        raw_budget = max(
            0.0, available_cash - reserve_value - transfer_cost
        )
        fraction = (
            self.config["opening_order_cash_fraction"]
            if observation["day"] == 1
            else self.config["order_cash_fraction_by_risk"][risk]
        )
        safe_budget = min(raw_budget, available_cash * fraction)
        adjusted_observation = copy.deepcopy(observation)
        adjusted_observation["shared_cash"] = (
            reserve_value + transfer_cost + safe_budget
        )
        event_multiplier = self.config[
            "cover_multiplier_by_event_severity"
        ][adjusted_dashboard["event_risk"]["severity"]]
        self._resilience_cover_multiplier = event_multiplier
        orders, diagnostics = super()._orders(
            adjusted_observation,
            adjusted_dashboard,
            plan,
            transfers=transfers,
        )
        for shop in self._inactive_shops:
            orders[shop] = {
                product: 0 for product in observation["catalog"]
            }
        diagnostics.update(
            {
                "cash_before_action": observation["shared_cash"],
                "action_cash_reserved": self._guardrails[
                    "action_cash_cost"
                ],
                "unconstrained_order_budget": round(raw_budget, 2),
                "cash_fraction_cap": fraction,
                "event_cover_multiplier": event_multiplier,
            }
        )
        return orders, diagnostics

    def _delivery_aware_cover(
        self,
        *,
        shop: str,
        product: str,
        catalog: dict[str, Any],
        perishable: bool,
        pending_pressure: float,
    ) -> float:
        base = super()._delivery_aware_cover(
            shop=shop,
            product=product,
            catalog=catalog,
            perishable=perishable,
            pending_pressure=pending_pressure,
        )
        return base * self._resilience_cover_multiplier

    def _transfers(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
    ) -> tuple[list[Any], dict[str, Any]]:
        transfers, diagnostics = super()._transfers(
            observation, dashboard
        )
        kept = [
            transfer
            for transfer in transfers
            if transfer.from_shop not in self._inactive_shops
            and transfer.to_shop not in self._inactive_shops
        ]
        removed_units = sum(
            transfer.quantity
            for transfer in transfers
            if transfer not in kept
        )
        diagnostics["removed_inactive_shop_units"] = removed_units
        diagnostics["routes"] = len(kept)
        diagnostics["units"] = sum(
            transfer.quantity for transfer in kept
        )
        diagnostics["estimated_cost"] = round(
            diagnostics["units"]
            * self.config["transfer_cost_per_unit"],
            2,
        )
        return kept, diagnostics

    def _prices(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, dict[str, float]]:
        prices = super()._prices(observation, dashboard, plan)
        event = dashboard["event_risk"]
        announcement_text = " ".join(
            observation["today_signals"].get("announcements", [])
        ).lower()
        for shop, products in dashboard["products"].items():
            for product, metrics in products.items():
                catalog = observation["catalog"][product]
                midpoint = sum(catalog["normal_price_range"]) / 2
                if metrics["expiring_within_2_days"] > 0:
                    floor = max(
                        catalog["current_unit_cost"] * 1.08,
                        midpoint
                        * self.config[
                            "automatic_expiry_discount_ratio"
                        ],
                    )
                    prices[shop][product] = round(floor, 2)
                    continue
                can_premium = (
                    product in self.config["automatic_premium_products"]
                    and metrics["on_hand"] > 0
                    and metrics["days_cover"]
                    >= self.config["premium_minimum_cover_days"]
                    and not event["price_pressure"]
                    and product.replace("_", " ") not in announcement_text
                )
                if can_premium:
                    prices[shop][product] = round(
                        midpoint
                        * self.config["automatic_premium_ratio"],
                        2,
                    )
        return prices
