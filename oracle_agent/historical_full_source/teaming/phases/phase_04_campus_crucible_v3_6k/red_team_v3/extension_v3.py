from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from simulation.demand import calculate_demand
from simulation.inventory import units


class CampusCrucibleExtension:
    """Dynamic reputation, shop lifecycle, substitution, and liquidation."""

    def __init__(
        self,
        *,
        config: dict[str, Any],
        shops: dict[str, dict[str, Any]],
        products: dict[str, dict[str, Any]],
    ) -> None:
        self.config = config
        self.rules = config["crucible"]
        self.shops = shops
        self.products = products
        self.state = {
            shop: {
                "open": True,
                "closed_until_day": 0,
                "reputation": 1.0,
                "forced_closures": 0,
                "voluntary_closures": 0,
                "missed_rent_days": 0,
            }
            for shop in shops
        }
        self.lifecycle_events: list[dict[str, Any]] = []

    def shop_is_open(self, shop: str) -> bool:
        return bool(self.state[shop]["open"])

    def current_daily_rent(self) -> float:
        return round(
            self.config["daily_rent_per_shop"]
            * sum(self.shop_is_open(shop) for shop in self.shops),
            2,
        )

    def observation_fields(
        self,
        *,
        day: int,
        cash: float,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "shop_status": {
                shop: {
                    **details,
                    "reputation_band": self._reputation_band(
                        details["reputation"]
                    ),
                    "eligible_to_reopen": (
                        not details["open"]
                        and day >= details["closed_until_day"]
                    ),
                }
                for shop, details in self.state.items()
            },
            "closure_rules": {
                "missed_rent_days_before_closure": 1,
                "minimum_closed_days": self.rules[
                    "minimum_closed_days"
                ],
                "reopening_cost": self.rules["reopening_cost"],
                "reopening_reserve_days": self.rules[
                    "reopening_reserve_days"
                ],
            },
        }

    def apply_shop_actions(
        self,
        *,
        day: int,
        decision: Any,
        cash: float,
        observation: dict[str, Any],
    ) -> tuple[Any, dict[str, Any]]:
        requested = list(getattr(decision, "shop_actions", []))
        issues: list[str] = []
        actions: list[dict[str, Any]] = []
        cash_cost = 0.0
        for item in requested:
            shop = item.get("shop")
            action = item.get("action")
            if shop not in self.shops:
                issues.append(f"shop action ignored: unknown shop {shop}")
                continue
            if action == "close" and self.state[shop]["open"]:
                self._close(
                    shop=shop,
                    day=day,
                    reason="agent_liquidity_control",
                    forced=False,
                )
                actions.append(
                    {"shop": shop, "action": "close", "accepted": True}
                )
            elif action == "reopen" and not self.state[shop]["open"]:
                reserve = (
                    self.config["daily_rent_per_shop"]
                    * (
                        sum(
                            self.shop_is_open(value)
                            for value in self.shops
                        )
                        + 1
                    )
                    * self.rules["reopening_reserve_days"]
                )
                cost = self.rules["reopening_cost"]
                eligible = day >= self.state[shop]["closed_until_day"]
                affordable = cash - cash_cost >= reserve + cost
                if eligible and affordable:
                    self.state[shop]["open"] = True
                    self.state[shop]["missed_rent_days"] = 0
                    cash_cost += cost
                    self.lifecycle_events.append(
                        {
                            "day": day,
                            "shop": shop,
                            "event": "reopened",
                            "cost": cost,
                        }
                    )
                    actions.append(
                        {
                            "shop": shop,
                            "action": "reopen",
                            "accepted": True,
                            "cost": cost,
                        }
                    )
                else:
                    issues.append(
                        f"{shop}: reopen denied by duration or reserve"
                    )
                    actions.append(
                        {
                            "shop": shop,
                            "action": "reopen",
                            "accepted": False,
                        }
                    )

        orders = {
            shop: (
                dict(decision.orders.get(shop, {}))
                if self.shop_is_open(shop)
                else {
                    product: 0 for product in self.products
                }
            )
            for shop in self.shops
        }
        transfers = [
            item
            for item in decision.transfers
            if self.shop_is_open(item.to_shop)
        ]
        return (
            replace(
                decision,
                orders=orders,
                transfers=transfers,
            ),
            {
                "cash_cost": round(cash_cost, 2),
                "issues": issues,
                "actions": actions,
            },
        )

    def settle_rent(
        self,
        *,
        day: int,
        cash: float,
        revenue_by_shop: dict[str, float],
    ) -> dict[str, Any]:
        rent = self.config["daily_rent_per_shop"]
        open_shops = [
            shop for shop in self.shops if self.shop_is_open(shop)
        ]
        ranked = sorted(
            open_shops,
            key=lambda shop: revenue_by_shop.get(shop, 0.0),
            reverse=True,
        )
        paid = []
        forced = []
        remaining = cash
        for shop in ranked:
            if remaining >= rent:
                remaining -= rent
                paid.append(shop)
                self.state[shop]["missed_rent_days"] = 0
                continue
            self.state[shop]["missed_rent_days"] += 1
            if (
                self.state[shop]["missed_rent_days"]
                >= self.rules["missed_rent_days_before_closure"]
            ):
                self._close(
                    shop=shop,
                    day=day,
                    reason="first_missed_rent",
                    forced=True,
                )
                forced.append(shop)
        return {
            "cash_after": round(remaining, 2),
            "rent_paid": round(len(paid) * rent, 2),
            "paid_shops": paid,
            "forced_closures": forced,
        }

    def calculate_demand(self, **arguments: Any) -> dict[str, Any]:
        result = calculate_demand(**arguments)
        inventories = arguments["inventories"]
        prices = arguments["prices"]
        hidden = arguments["scenario_day"]["hidden"]

        for shop in self.shops:
            reputation = self.state[shop]["reputation"]
            for product in self.products:
                self._scale_row(result[shop][product], reputation)
                result[shop][product]["reputation_multiplier"] = round(
                    reputation, 4
                )
                result[shop][product]["shop_open"] = self.shop_is_open(
                    shop
                )

        for relation in self.rules["substitutions"]:
            source = relation["source"]
            target = relation["target"]
            rate = relation["rate"]
            for shop in self.shops:
                if not self.shop_is_open(shop):
                    continue
                target_affordable = (
                    prices[shop][target]
                    / self.products[target]["reference_price"]
                    <= hidden["soft_price_ratio"][shop][target]
                )
                if (
                    units(inventories[shop], source) == 0
                    and units(inventories[shop], target) > 0
                    and target_affordable
                ):
                    moved = math.floor(
                        result[shop][source][
                            "price_eligible_demand"
                        ]
                        * rate
                    )
                    self._move_demand(
                        result[shop][source],
                        result[shop][target],
                        moved,
                    )

        closed = [
            shop for shop in self.shops if not self.shop_is_open(shop)
        ]
        open_shops = [
            shop for shop in self.shops if self.shop_is_open(shop)
        ]
        if closed and open_shops:
            spillover = self.rules["closed_shop_spillover_rate"]
            for closed_shop in closed:
                for product in self.products:
                    available = result[closed_shop][product][
                        "price_eligible_demand"
                    ]
                    shifted = math.floor(available * spillover)
                    if shifted <= 0:
                        continue
                    each = shifted // len(open_shops)
                    if each <= 0:
                        continue
                    total = each * len(open_shops)
                    result[closed_shop][product][
                        "price_eligible_demand"
                    ] -= total
                    result[closed_shop][product][
                        "market_demand"
                    ] = max(
                        result[closed_shop][product][
                            "price_eligible_demand"
                        ],
                        result[closed_shop][product]["market_demand"]
                        - total,
                    )
                    result[closed_shop][product][
                        "closure_spillover_out"
                    ] = total
                    for destination in open_shops:
                        result[destination][product][
                            "price_eligible_demand"
                        ] += each
                        result[destination][product][
                            "market_demand"
                        ] += each
                        result[destination][product][
                            "closure_spillover_in"
                        ] = (
                            result[destination][product].get(
                                "closure_spillover_in", 0
                            )
                            + each
                        )
        return result

    def after_day(
        self,
        *,
        day: int,
        record: dict[str, Any],
        diagnostics: dict[str, Any],
    ) -> None:
        for shop in self.shops:
            eligible = sum(
                diagnostics[shop][product]["price_eligible_demand"]
                for product in self.products
            )
            sold = sum(record["sales"][shop].values())
            service = sold / eligible if eligible else 1.0
            reputation = self.state[shop]["reputation"]
            if service < 0.55:
                reputation -= self.rules["reputation_severe_penalty"]
            elif service < 0.78:
                reputation -= self.rules["reputation_mild_penalty"]
            elif service >= 0.9:
                reputation += self.rules["reputation_recovery"]
            self.state[shop]["reputation"] = round(
                min(
                    self.rules["reputation_maximum"],
                    max(self.rules["reputation_minimum"], reputation),
                ),
                4,
            )

    def public_state(self) -> dict[str, Any]:
        return {
            shop: {
                "open": details["open"],
                "closed_until_day": details["closed_until_day"],
                "reputation_band": self._reputation_band(
                    details["reputation"]
                ),
                "missed_rent_days": details["missed_rent_days"],
            }
            for shop, details in self.state.items()
        }

    def enrich_summary(
        self,
        *,
        summary: dict[str, Any],
        cash: float,
        inventories: dict[str, Any],
        pending: list[dict[str, Any]],
        products: dict[str, dict[str, Any]],
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        inventory_liquidation = 0.0
        for inventory in inventories.values():
            for product, lots in inventory.items():
                rate = (
                    self.rules["fresh_liquidation_rate"]
                    if products[product]["shelf_life_days"] <= 4
                    else self.rules["durable_liquidation_rate"]
                )
                inventory_liquidation += sum(
                    lot["quantity"] * lot["unit_cost"] * rate
                    for lot in lots
                )
        pending_liquidation = sum(
            shipment["cost"]
            * (
                self.rules["fresh_pending_liquidation_rate"]
                if products[shipment["product"]]["shelf_life_days"]
                <= 4
                else self.rules["pending_liquidation_rate"]
            )
            for shipment in pending
        )
        liquidation_net_worth = round(
            cash + inventory_liquidation + pending_liquidation, 2
        )
        liquidation_profit = round(
            liquidation_net_worth
            - self.config["starting_shared_cash"],
            2,
        )
        closed_shop_days = sum(
            not state["open"]
            for record in records
            for state in record.get("shop_lifecycle", {}).values()
        )
        forced_closures = sum(
            len(record.get("forced_closures", []))
            for record in records
        )
        summary.update(
            {
                "liquidation_inventory_value": round(
                    inventory_liquidation, 2
                ),
                "liquidation_pending_value": round(
                    pending_liquidation, 2
                ),
                "liquidation_net_worth": liquidation_net_worth,
                "liquidation_profit": liquidation_profit,
                "closed_shop_days": closed_shop_days,
                "forced_closure_count": forced_closures,
                "lifecycle_events": self.lifecycle_events,
                "final_shop_status": self.public_state(),
            }
        )
        summary["challenge_checks"]["minimum_profit"] = (
            liquidation_profit
            >= self.config["success_criteria"][
                "minimum_liquidation_profit"
            ]
        )
        summary["challenge_checks"]["shop_continuity"] = (
            forced_closures
            <= self.config["success_criteria"][
                "maximum_forced_closures"
            ]
        )
        summary["challenge_checks"]["all_shops_open_at_end"] = all(
            state["open"] for state in self.state.values()
        )
        summary["challenge_pass"] = all(
            summary["challenge_checks"].values()
        )
        summary["balanced_success"] = summary["challenge_pass"]
        return summary

    def _close(
        self,
        *,
        shop: str,
        day: int,
        reason: str,
        forced: bool,
    ) -> None:
        self.state[shop]["open"] = False
        self.state[shop]["closed_until_day"] = (
            day
            + self.rules["minimum_closed_days"]
            + int(forced)
        )
        self.state[shop]["missed_rent_days"] = 0
        key = "forced_closures" if forced else "voluntary_closures"
        self.state[shop][key] += 1
        self.lifecycle_events.append(
            {
                "day": day,
                "shop": shop,
                "event": "closed",
                "reason": reason,
                "forced": forced,
                "eligible_reopen_day": self.state[shop][
                    "closed_until_day"
                ],
            }
        )

    @staticmethod
    def _scale_row(row: dict[str, Any], factor: float) -> None:
        row["latent_float"] = round(row["latent_float"] * factor, 6)
        for field in [
            "market_demand",
            "price_eligible_demand",
            "elasticity_refused_units",
            "affordability_refused_units",
            "hard_cutoff_refused_units",
            "discount_uplift_units",
        ]:
            row[field] = round(row[field] * factor)
        row["price_refused_units"] = max(
            0,
            row["market_demand"] - row["price_eligible_demand"],
        )

    @staticmethod
    def _move_demand(
        source: dict[str, Any],
        target: dict[str, Any],
        quantity: int,
    ) -> None:
        if quantity <= 0:
            return
        source["price_eligible_demand"] = max(
            0, source["price_eligible_demand"] - quantity
        )
        source["market_demand"] = max(
            source["price_eligible_demand"],
            source["market_demand"] - quantity,
        )
        source["substitution_out"] = (
            source.get("substitution_out", 0) + quantity
        )
        target["price_eligible_demand"] += quantity
        target["market_demand"] += quantity
        target["substitution_in"] = (
            target.get("substitution_in", 0) + quantity
        )

    @staticmethod
    def _reputation_band(value: float) -> str:
        if value < 0.82:
            return "weak"
        if value < 0.95:
            return "strained"
        if value > 1.01:
            return "strong"
        return "normal"
