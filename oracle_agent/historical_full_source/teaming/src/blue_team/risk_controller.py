from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from contracts import Decision, Policy, Transfer
from io_utils import append_jsonl


class RiskController:
    """Auditable deterministic guard around an agent's proposed decision."""

    name = "blue_risk_controlled_agent"

    def __init__(
        self,
        *,
        base: Policy,
        strategy: dict[str, Any],
        log_path: Path,
    ) -> None:
        self.base = base
        self.strategy = strategy
        self.log_path = log_path

    def decide(self, observation: dict[str, Any]) -> Decision:
        proposed = self.base.decide(observation)
        warning = self._warning_state(observation)
        prices, price_changes = self._guard_prices(
            observation, proposed.prices
        )
        transfers, transfer_cost = self._guard_transfers(
            observation, proposed.transfers, warning
        )
        reserve_days = self._reserve_days(observation, warning)
        order_budget = max(
            0.0,
            observation["shared_cash"]
            - observation["rent"]["total_daily"] * reserve_days
            - transfer_cost,
        )
        orders, before_cost, after_cost, removed_fresh = (
            self._guard_orders(
                observation=observation,
                proposed=proposed.orders,
                budget=order_budget,
                warning=warning,
            )
        )
        intervention = {
            "cycle": observation["cycle"],
            "day": observation["day"],
            "source": proposed.source,
            "warning_state": warning,
            "rent_reserve_days": reserve_days,
            "rent_reserve_value": round(
                observation["rent"]["total_daily"] * reserve_days, 2
            ),
            "proposed_order_cost": round(before_cost, 2),
            "guarded_order_cost": round(after_cost, 2),
            "price_changes": price_changes,
            "fresh_units_removed": removed_fresh,
            "transfers_removed": (
                len(proposed.transfers) - len(transfers)
            ),
            "intervened": (
                round(before_cost, 2) != round(after_cost, 2)
                or price_changes > 0
                or removed_fresh > 0
                or len(transfers) != len(proposed.transfers)
            ),
        }
        append_jsonl(self.log_path, intervention)
        suffix = (
            " Risk controller preserved "
            f"{reserve_days:g} rent days, guarded order cost "
            f"₹{before_cost:.2f}→₹{after_cost:.2f}, changed "
            f"{price_changes} prices, and removed "
            f"{removed_fresh} risky fresh units."
        )
        return Decision(
            orders=orders,
            prices=prices,
            transfers=transfers,
            reasoning=(proposed.reasoning + suffix)[-800:],
            source=proposed.source,
        )

    def _warning_state(self, observation: dict[str, Any]) -> str:
        footfall = observation["today_signals"][
            "footfall_hint"
        ].values()
        if any(level == "very_low" for level in footfall):
            return "downturn"
        announcements = " ".join(
            observation["today_signals"]["announcements"]
        ).lower()
        if any(
            word in announcements
            for word in ["travel", "break", "closure", "weather"]
        ):
            return "warning"
        return "normal"

    def _reserve_days(
        self, observation: dict[str, Any], warning: str
    ) -> float:
        if observation["day"] == 1:
            return 1.0
        if warning == "downturn":
            return float(self.strategy["downturn_reserve_days"])
        if warning == "warning":
            return float(self.strategy["warning_reserve_days"])
        return float(self.strategy["cash_reserve_rent_days"])

    def _guard_prices(
        self,
        observation: dict[str, Any],
        proposed: dict[str, dict[str, float]],
    ) -> tuple[dict[str, dict[str, float]], int]:
        guarded: dict[str, dict[str, float]] = {}
        changes = 0
        day = observation["day"]
        for shop_id, shop in observation["shops"].items():
            guarded[shop_id] = {}
            for item_id, catalog in observation["catalog"].items():
                low, high = catalog["normal_price_range"]
                raw = float(proposed[shop_id][item_id])
                expiring = any(
                    lot["expires_day"] <= day + 1
                    for lot in shop["inventory"][item_id]["lots"]
                )
                floor = (
                    max(
                        catalog["current_unit_cost"] * 1.08,
                        low * 0.85,
                    )
                    if expiring
                    else low
                )
                value = round(min(high, max(floor, raw)), 2)
                changes += int(abs(value - raw) > 0.001)
                guarded[shop_id][item_id] = value
        return guarded, changes

    def _guard_transfers(
        self,
        observation: dict[str, Any],
        transfers: list[Transfer],
        warning: str,
    ) -> tuple[list[Transfer], float]:
        if warning != "normal":
            return [], 0.0
        limit = 2
        accepted = transfers[:limit]
        cost = sum(item.quantity for item in accepted) * 2.0
        maximum = max(
            0.0,
            observation["shared_cash"]
            - observation["rent"]["total_daily"],
        )
        if cost > maximum:
            return [], 0.0
        return accepted, cost

    def _guard_orders(
        self,
        *,
        observation: dict[str, Any],
        proposed: dict[str, dict[str, int]],
        budget: float,
        warning: str,
    ) -> tuple[
        dict[str, dict[str, int]], float, float, int
    ]:
        orders = {
            shop: {item: 0 for item in observation["catalog"]}
            for shop in observation["shops"]
        }
        candidates: list[dict[str, Any]] = []
        before_cost = 0.0
        removed_fresh = 0
        recent = observation["recent_history"][-3:]
        for shop in observation["shops"]:
            for item, catalog in observation["catalog"].items():
                requested = max(0, int(proposed[shop][item]))
                pack = int(catalog["pack_size"])
                requested -= requested % pack
                before_cost += requested * catalog["current_unit_cost"]
                perishable = catalog["shelf_life_days"] <= 4
                guarded_request = requested
                if perishable and warning == "downturn":
                    guarded_request = 0
                elif perishable and warning == "warning":
                    factor = self.strategy[
                        "festival_fresh_order_reduction"
                    ]
                    guarded_request = (
                        math.floor(requested * factor / pack) * pack
                    )
                removed_fresh += requested - guarded_request
                stockouts = sum(
                    bool(day["stockout"][shop][item])
                    for day in recent
                )
                low, high = catalog["normal_price_range"]
                midpoint = (low + high) / 2
                margin_return = max(
                    0.0,
                    midpoint - catalog["current_unit_cost"],
                ) / max(0.01, catalog["current_unit_cost"])
                for pack_index in range(guarded_request // pack):
                    candidates.append(
                        {
                            "shop": shop,
                            "item": item,
                            "pack": pack,
                            "cost": pack
                            * catalog["current_unit_cost"],
                            "score": (
                                stockouts * 30
                                + margin_return * 25
                                - pack_index * 5
                                + (
                                    8
                                    if warning != "normal"
                                    and not perishable
                                    else 0
                                )
                            ),
                        }
                    )
        spent = 0.0
        for candidate in sorted(
            candidates, key=lambda value: value["score"], reverse=True
        ):
            if spent + candidate["cost"] > budget:
                continue
            orders[candidate["shop"]][candidate["item"]] += candidate[
                "pack"
            ]
            spent += candidate["cost"]
        return (
            orders,
            round(before_cost, 2),
            round(spent, 2),
            removed_fresh,
        )
