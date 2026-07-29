from __future__ import annotations

from typing import Any

from operations_v2_2 import OperationsRiskAgentV22
from shop_decision import ShopDecision


class OperationsRiskAgent6K(OperationsRiskAgentV22):
    """Adaptive fresh cover plus deterministic shop lifecycle actions."""

    def _orders(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
        plan: dict[str, Any],
        *,
        transfers: list[Any] | None = None,
    ) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
        self._active_dashboard = dashboard
        return super()._orders(
            observation,
            dashboard,
            plan,
            transfers=transfers,
        )

    def _delivery_aware_cover(
        self,
        *,
        shop: str,
        product: str,
        catalog: dict[str, Any],
        perishable: bool,
        pending_pressure: float,
    ) -> float:
        if perishable:
            metrics = self._active_dashboard["products"][shop][product]
            if (
                metrics["stockout_days_last_5"]
                < self.config["adaptive_fresh_stockout_days"]
            ):
                return min(
                    catalog["shelf_life_days"]
                    - self.config["expiry_safety_days"],
                    float(
                        self.config["cover_day_overrides"].get(
                            shop, {}
                        ).get(
                            product,
                            self.config["perishable_cover_days"],
                        )
                    ),
                )
        return super()._delivery_aware_cover(
            shop=shop,
            product=product,
            catalog=catalog,
            perishable=perishable,
            pending_pressure=pending_pressure,
        )

    def execute(
        self,
        *,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
        manager_plan: dict[str, Any],
        source: str,
    ) -> tuple[ShopDecision, dict[str, Any]]:
        decision, diagnostics = super().execute(
            observation=observation,
            dashboard=dashboard,
            manager_plan=manager_plan,
            source=source,
        )
        actions = self._shop_actions(observation, dashboard)
        diagnostics["shop_actions_requested"] = actions
        return (
            ShopDecision(
                orders=decision.orders,
                prices=decision.prices,
                transfers=decision.transfers,
                reasoning=decision.reasoning,
                source=decision.source,
                shop_actions=actions,
            ),
            diagnostics,
        )

    def _shop_actions(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
    ) -> list[dict[str, str]]:
        status = observation.get("shop_status", {})
        if not status:
            return []
        open_shops = [
            shop for shop, state in status.items() if state["open"]
        ]
        actions: list[dict[str, str]] = []
        daily_rent = observation["rent"]["per_shop"] * len(open_shops)
        if (
            len(open_shops) > 1
            and observation["shared_cash"]
            < daily_rent * self.config["voluntary_close_cash_days"]
        ):
            weakest = min(
                open_shops,
                key=lambda shop: dashboard["shop_health"][shop][
                    "sales_units_last_5"
                ],
            )
            actions.append({"shop": weakest, "action": "close"})
            return actions

        closed = [
            shop
            for shop, state in status.items()
            if not state["open"] and state["eligible_to_reopen"]
        ]
        reopening_cost = observation["closure_rules"][
            "reopening_cost"
        ]
        required = (
            observation["rent"]["per_shop"]
            * (len(open_shops) + 1)
            * self.config["reopen_cash_reserve_days"]
            + reopening_cost
        )
        if closed and observation["shared_cash"] >= required:
            actions.append({"shop": closed[0], "action": "reopen"})
        return actions
