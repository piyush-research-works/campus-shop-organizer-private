from __future__ import annotations

import math
from typing import Any

from contracts import Decision, Transfer


class AdaptivePolicy:
    """Deterministic learning baseline and fallback for failed model calls."""

    name = "blue_adaptive_policy"

    def __init__(self, strategy: dict[str, Any]) -> None:
        self.strategy = strategy
        self.prior_estimates: dict[str, dict[str, float]] | None = None
        self.preferred_items: dict[str, set[str]] | None = None

    def decide(self, observation: dict[str, Any]) -> Decision:
        estimates = self._estimates(observation)
        if self.preferred_items is None:
            self.preferred_items = self._choose_assortment(
                observation, estimates
            )
        pending = {
            shop: {item: 0 for item in observation["catalog"]}
            for shop in observation["shops"]
        }
        for shipment in observation["pending_deliveries"]:
            pending[shipment["shop"]][shipment["product"]] += shipment[
                "quantity"
            ]

        prices: dict[str, dict[str, float]] = {}
        desired: list[dict[str, Any]] = []
        low_signal = observation["today_signals"]["footfall_hint"]
        for shop_id, shop in observation["shops"].items():
            prices[shop_id] = {}
            for item_id, catalog in observation["catalog"].items():
                midpoint = sum(catalog["normal_price_range"]) / 2
                adjustment = self.strategy["product_adjustments"].get(
                    item_id, {}
                )
                price_ratio = adjustment.get(
                    "price_ratio",
                    self.strategy["price_ratio_to_reference"],
                )
                price = midpoint * price_ratio
                lots = shop["inventory"][item_id]["lots"]
                if any(
                    lot["expires_day"] <= observation["day"] + 1
                    for lot in lots
                ):
                    price *= 1 - min(
                        0.25, self.strategy["price_test_step"] * 2
                    )
                recent = observation["recent_history"][-3:]
                if recent and all(
                    day["stockout"][shop_id][item_id] for day in recent
                ):
                    price *= 1.02
                elif recent and all(
                    day["sales"][shop_id][item_id] == 0
                    and day["ending_inventory"][shop_id][item_id] > 0
                    for day in recent
                ):
                    price *= 0.96
                prices[shop_id][item_id] = round(max(1.0, price), 2)

                if item_id not in self.preferred_items[shop_id]:
                    continue
                perishable = catalog["shelf_life_days"] <= 4
                cover_key = "perishable" if perishable else "nonperishable"
                cover = self.strategy["target_cover_days"][cover_key]
                cover += adjustment.get("cover_days_delta", 0.0)
                if observation["day"] == 1:
                    cover = self.strategy["opening_cover_days"][cover_key]
                if low_signal[shop_id] in {"low", "very_low"}:
                    cover *= self.strategy["low_footfall_order_reduction"]
                if any(
                    word in " ".join(
                        observation["today_signals"]["announcements"]
                    ).lower()
                    for word in ["travel", "break", "weather"]
                ) and perishable:
                    cover *= self.strategy[
                        "festival_fresh_order_reduction"
                    ]
                on_hand = shop["inventory"][item_id]["units"]
                target = math.ceil(estimates[shop_id][item_id] * cover)
                needed = max(
                    0, target - on_hand - pending[shop_id][item_id]
                )
                pack = catalog["pack_size"]
                quantity = math.ceil(needed / pack) * pack if needed else 0
                if quantity:
                    margin = (
                        prices[shop_id][item_id]
                        - catalog["current_unit_cost"]
                    )
                    inventory_position = (
                        on_hand + pending[shop_id][item_id]
                    )
                    days_of_supply = inventory_position / max(
                        0.5, estimates[shop_id][item_id]
                    )
                    shortage = max(0.0, cover - days_of_supply)
                    shortage_ratio = shortage / max(0.5, cover)
                    stockout_rate = (
                        sum(
                            day["stockout"][shop_id][item_id]
                            for day in recent
                        )
                        / max(1, len(recent))
                    )
                    return_on_cash = max(0.0, margin) / max(
                        0.01, catalog["current_unit_cost"]
                    )
                    pack_count = quantity // pack
                    for pack_index in range(pack_count):
                        # Allocate one pack at a time. Diminishing priority
                        # prevents one high-volume line from starving the
                        # remaining shops and assortment.
                        score = (
                            shortage_ratio * 40
                            + stockout_rate * 30
                            + return_on_cash * 25
                            - pack_index * 8
                        )
                        desired.append(
                            {
                                "shop": shop_id,
                                "item": item_id,
                                "quantity": pack,
                                "pack": pack,
                                "unit_cost": catalog[
                                    "current_unit_cost"
                                ],
                                "score": score,
                            }
                        )

        orders = {
            shop: {item: 0 for item in observation["catalog"]}
            for shop in observation["shops"]
        }
        rent_total = observation["rent"]["total_daily"]
        reserve_days: float = (
            1
            if observation["day"] == 1
            else self.strategy["cash_reserve_rent_days"]
        )
        announcements = " ".join(
            observation["today_signals"]["announcements"]
        ).lower()
        footfall = observation["today_signals"]["footfall_hint"].values()
        if any(level == "very_low" for level in footfall):
            reserve_days = max(
                reserve_days, self.strategy["downturn_reserve_days"]
            )
        elif any(
            word in announcements
            for word in ["travel", "break", "closure", "weather"]
        ):
            reserve_days = max(
                reserve_days, self.strategy["warning_reserve_days"]
            )
        budget = max(
            0.0,
            observation["shared_cash"] - rent_total * reserve_days,
        )
        for candidate in sorted(
            desired, key=lambda item: item["score"], reverse=True
        ):
            pack_cost = candidate["pack"] * candidate["unit_cost"]
            if pack_cost > budget:
                continue
            orders[candidate["shop"]][candidate["item"]] += candidate[
                "pack"
            ]
            budget -= pack_cost

        transfers = self._transfers(observation, estimates)
        return Decision(
            orders=orders,
            prices=prices,
            transfers=transfers,
            reasoning=(
                "Adaptive fallback used rolling sales, stockouts, expiry, "
                "pending deliveries, signals, and the learned strategy."
            ),
            source="adaptive",
        )

    def _choose_assortment(
        self,
        observation: dict[str, Any],
        estimates: dict[str, dict[str, float]],
    ) -> dict[str, set[str]]:
        size = int(self.strategy["assortment_size"])
        result: dict[str, set[str]] = {}
        for shop_id in observation["shops"]:
            ranked: list[tuple[float, str]] = []
            for item_id, catalog in observation["catalog"].items():
                midpoint = sum(catalog["normal_price_range"]) / 2
                margin = max(
                    0.0, midpoint - catalog["current_unit_cost"]
                )
                contribution = estimates[shop_id][item_id] * margin
                cash_return = margin / max(
                    0.01, catalog["current_unit_cost"]
                )
                ranked.append(
                    (contribution + cash_return * 8, item_id)
                )
            ranked.sort(reverse=True)
            result[shop_id] = {
                item_id for _, item_id in ranked[:size]
            }
        return result

    def _estimates(
        self, observation: dict[str, Any]
    ) -> dict[str, dict[str, float]]:
        if observation["day"] == 1:
            hints = observation["opening_market_hint"]
            estimates = {
                shop: {
                    item: sum(value_range) / 2
                    for item, value_range in items.items()
                }
                for shop, items in hints.items()
            }
            self.prior_estimates = estimates
            return estimates
        result: dict[str, dict[str, float]] = {}
        history = observation["recent_history"]
        for shop in observation["shops"]:
            result[shop] = {}
            for item in observation["catalog"]:
                recent = history[-7:]
                sold = [day["sales"][shop][item] for day in recent]
                stockouts = [
                    day["stockout"][shop][item] for day in recent
                ]
                censored_sales = [
                    units_sold * (1.35 if was_stockout else 1.0)
                    for units_sold, was_stockout in zip(
                        sold, stockouts, strict=True
                    )
                ]
                average = sum(censored_sales) / max(
                    1, len(censored_sales)
                )
                stockout_days = sum(
                    stockouts
                )
                prior = (
                    self.prior_estimates[shop][item]
                    if self.prior_estimates is not None
                    else 0.5
                )
                result[shop][item] = max(
                    0.5,
                    prior * 0.85,
                    average * (1 + 0.08 * stockout_days),
                    max(sold, default=0) * 1.05,
                )
        return result

    def _transfers(
        self,
        observation: dict[str, Any],
        estimates: dict[str, dict[str, float]],
    ) -> list[Transfer]:
        # Transfers cost ₹2 per unit and can easily destroy the small margin
        # on campus staples. The deterministic fallback therefore leaves this
        # optional action to the model rather than moving stock speculatively.
        return []
