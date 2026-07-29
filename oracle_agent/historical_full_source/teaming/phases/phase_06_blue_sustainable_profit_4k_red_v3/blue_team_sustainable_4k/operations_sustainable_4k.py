from __future__ import annotations

import copy
from typing import Any

from operations_4k import ResilientOperations4K
from operations_6k import OperationsRiskAgent6K
from shop_decision import ShopDecision


class SustainableOperations4K(ResilientOperations4K):
    """Protects liquidity without switching off the revenue engine."""

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
        forced = guardrails["force_cash_posture"]
        if forced:
            plan["cash_posture"] = forced
        elif (
            plan["cash_posture"] == "protect"
            and not guardrails["allow_discretionary_protect"]
        ):
            plan["cash_posture"] = "balanced"

        inactive = set(guardrails["inactive_shops_after"])
        for shop in inactive:
            plan["shop_modes"][shop] = "protect"
        self._add_core_priorities(
            plan,
            active_shops=set(guardrails["active_shops_after"]),
        )

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
            f"{plan['reasoning']} Sustainable guardrail: "
            f"{guardrails['action_reason']}; anchor "
            f"{guardrails['anchor_shop']}; runway "
            f"{guardrails['cash_runway_days']} days. Operations used "
            f"Rs {diagnostics['order_cost']:.2f} of "
            f"Rs {diagnostics['order_budget']:.2f} order budget."
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

        anchor = self._guardrails["anchor_shop"]
        if anchor in adjusted_dashboard["shop_health"]:
            weight = max(
                1.0,
                float(self.config["shop_service_pressure_score_weight"]),
            )
            adjusted_dashboard["shop_health"][anchor][
                "service_pressure"
            ] += float(self.config["anchor_priority_score_bonus"]) / weight

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
            float(observation["shared_cash"])
            - float(self._guardrails["action_cash_cost"]),
        )
        rent_after = float(
            self._guardrails["daily_rent_after_actions"]
        )
        escrow = float(self._guardrails["reopening_escrow"])
        long_reserve = rent_after * reserve_days + escrow
        standard_headroom = max(
            0.0, available_cash - long_reserve - transfer_cost
        )
        fraction = (
            self.config["opening_order_cash_fraction"]
            if observation["day"] == 1
            else self.config["order_cash_fraction_by_risk"][risk]
        )
        standard_budget = min(
            standard_headroom, available_cash * fraction
        )

        active_count = len(self._guardrails["active_shops_after"])
        survival_target = (
            float(self.config["survival_budget_per_open_shop"])
            * active_count
        )
        if anchor in self._guardrails["active_shops_after"]:
            survival_target = max(
                survival_target,
                float(self.config["anchor_minimum_order_budget"]),
            )
        survival_headroom = max(
            0.0,
            available_cash
            - escrow
            - transfer_cost
            - rent_after
            * float(self.config["survival_cash_buffer_days"]),
        )
        survival_budget = min(survival_target, survival_headroom)
        safe_budget = max(standard_budget, survival_budget)

        adjusted_observation = copy.deepcopy(observation)
        adjusted_observation["rent"]["total_daily"] = rent_after
        base_reserve = rent_after * reserve_days
        adjusted_observation["shared_cash"] = (
            base_reserve + transfer_cost + safe_budget
        )
        orders, diagnostics = OperationsRiskAgent6K._orders(
            self,
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
                "reopening_escrow": round(escrow, 2),
                "long_reserve_value": round(long_reserve, 2),
                "standard_order_budget": round(standard_budget, 2),
                "survival_order_floor": round(survival_budget, 2),
                "cash_fraction_cap": fraction,
                "daily_rent_after_actions": round(rent_after, 2),
                "anchor_shop": anchor,
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
        base = OperationsRiskAgent6K._delivery_aware_cover(
            self,
            shop=shop,
            product=product,
            catalog=catalog,
            perishable=perishable,
            pending_pressure=pending_pressure,
        )
        severity = self._active_dashboard["event_risk"][
            "line_severity"
        ][shop][product]
        multiplier = float(
            self.config["local_cover_multiplier"][severity]
        )
        return base * multiplier

    def _prices(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, dict[str, float]]:
        prices = super()._prices(observation, dashboard, plan)
        history = observation["recent_history"][-2:]
        for shop, products in prices.items():
            for product, price in products.items():
                midpoint = (
                    sum(
                        observation["catalog"][product][
                            "normal_price_range"
                        ]
                    )
                    / 2
                )
                stalled_at_premium = (
                    price > midpoint
                    and len(history) == 2
                    and all(
                        day["sales"][shop][product] == 0
                        and day["ending_inventory"][shop][product] > 0
                        for day in history
                    )
                )
                if stalled_at_premium:
                    prices[shop][product] = round(midpoint, 2)
        return prices

    def _add_core_priorities(
        self,
        plan: dict[str, Any],
        *,
        active_shops: set[str],
    ) -> None:
        core_items: list[dict[str, str]] = []
        core_keys: set[tuple[str, str]] = set()
        for shop in sorted(active_shops):
            for product in self.config["core_products_by_shop"].get(
                shop, []
            ):
                core_keys.add((shop, product))
                core_items.append(
                    {
                        "shop": shop,
                        "product": product,
                        "priority": "high",
                    }
                )
        other_items = [
            dict(item)
            for item in plan["stock_actions"]
            if (item["shop"], item["product"]) not in core_keys
        ]
        plan["stock_actions"] = (core_items + other_items)[
            : self.config["manager_action_limit"]
        ]
