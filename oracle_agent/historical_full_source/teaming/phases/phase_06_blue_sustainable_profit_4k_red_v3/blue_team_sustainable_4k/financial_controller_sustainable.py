from __future__ import annotations

from typing import Any


class SustainableFinancialController:
    """Keeps a revenue-producing anchor and makes closures reversible."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.anchor_shop: str | None = None
        self.negative_streak: dict[str, int] = {}
        self.last_open_contribution: dict[str, float] = {}
        self.escrowed_closed_shops: set[str] = set()

    def evaluate(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
    ) -> dict[str, Any]:
        day = int(observation["day"])
        status = observation.get("shop_status", {})
        open_shops = [
            shop for shop, state in status.items() if state["open"]
        ]
        closed_shops = [
            shop for shop, state in status.items() if not state["open"]
        ]
        finance = dashboard["shop_finance"]

        for shop in open_shops:
            contribution = float(
                finance[shop]["average_daily_contribution"]
            )
            self.last_open_contribution[shop] = contribution
            enough_history = (
                day >= self.config["minimum_history_before_closure"]
                and finance[shop]["open_history_days"] >= 3
            )
            if (
                enough_history
                and contribution
                < self.config["close_max_daily_contribution"]
            ):
                self.negative_streak[shop] = (
                    self.negative_streak.get(shop, 0) + 1
                )
            else:
                self.negative_streak[shop] = 0

        for shop in list(self.escrowed_closed_shops):
            if shop in open_shops:
                self.escrowed_closed_shops.discard(shop)

        self._choose_anchor(open_shops, finance)
        cash = float(observation["shared_cash"])
        rent = float(observation["rent"]["per_shop"])
        current_daily_rent = rent * len(open_shops)
        runway = cash / max(1.0, current_daily_rent)
        event = dashboard["event_risk"]
        actions: list[dict[str, str]] = []
        action_reason = "hold"

        reopening_cost = float(
            observation.get("closure_rules", {}).get(
                "reopening_cost", 0
            )
        )
        eligible = [
            shop
            for shop in closed_shops
            if status[shop]["eligible_to_reopen"]
            and event["shop_severity"].get(shop) != "severe"
        ]
        if eligible:
            target = max(
                eligible,
                key=lambda shop: self.last_open_contribution.get(
                    shop, -10_000.0
                ),
            )
            red_required_days = float(
                observation.get("closure_rules", {}).get(
                    "reopening_reserve_days", 0
                )
            )
            blue_required_days = float(
                self.config["reopen_cash_reserve_days"]
            )
            reserve_days = max(red_required_days, blue_required_days)
            other_escrows = max(
                0,
                len(self.escrowed_closed_shops - {target}),
            ) * reopening_cost
            required = (
                reopening_cost
                + other_escrows
                + rent * (len(open_shops) + 1) * reserve_days
                + float(self.config["reopen_seed_inventory_budget"])
            )
            if cash >= required:
                actions.append({"shop": target, "action": "reopen"})
                action_reason = (
                    f"reopen_{target}_with_funded_inventory_and_rent"
                )

        if (
            not actions
            and len(open_shops) > self.config["minimum_open_shops"]
            and day <= self.config["latest_voluntary_close_day"]
        ):
            candidates = [
                shop
                for shop in open_shops
                if shop != self.anchor_shop
                and self.negative_streak.get(shop, 0)
                >= self.config[
                    "negative_contribution_days_before_closure"
                ]
            ]
            emergency = (
                runway < self.config["emergency_close_runway_days"]
            )
            stressed = [
                shop
                for shop in candidates
                if event["shop_severity"].get(shop) == "severe"
                and runway < self.config["warning_close_runway_days"]
            ]
            selectable = candidates if emergency else stressed
            if selectable:
                target = min(
                    selectable,
                    key=lambda shop: finance[shop][
                        "average_daily_contribution"
                    ],
                )
                future_escrow = (
                    len(self.escrowed_closed_shops | {target})
                    * reopening_cost
                )
                future_rent = rent * (len(open_shops) - 1)
                required_after_close = (
                    future_escrow
                    + future_rent
                    * self.config["close_post_action_reserve_days"]
                )
                if cash >= required_after_close:
                    actions.append({"shop": target, "action": "close"})
                    self.escrowed_closed_shops.add(target)
                    action_reason = (
                        f"close_{target}_but_escrow_reopening_fee"
                    )

        active_after = set(open_shops)
        for action in actions:
            if action["action"] == "close":
                active_after.discard(action["shop"])
            else:
                active_after.add(action["shop"])
                self.escrowed_closed_shops.discard(action["shop"])
        if self.anchor_shop not in active_after:
            self._choose_anchor(sorted(active_after), finance)

        reopening_count = sum(
            action["action"] == "reopen" for action in actions
        )
        action_cash_cost = reopening_count * reopening_cost
        reopening_escrow = (
            len(self.escrowed_closed_shops) * reopening_cost
        )
        severity = event["severity"]
        if (
            runway < self.config["protect_cash_runway_days"]
            or severity == "severe"
        ):
            posture = "protect"
        elif severity == "caution":
            posture = "balanced"
        else:
            posture = None

        inactive_after = sorted(set(status) - active_after)
        daily_rent_after = rent * len(active_after)
        return {
            "actions": actions,
            "action_reason": action_reason,
            "action_cash_cost": round(action_cash_cost, 2),
            "cash_runway_days": round(runway, 2),
            "open_shops_before": open_shops,
            "active_shops_after": sorted(active_after),
            "inactive_shops_after": inactive_after,
            "daily_rent_after_actions": round(daily_rent_after, 2),
            "anchor_shop": self.anchor_shop,
            "event_severity": severity,
            "force_cash_posture": posture,
            "allow_discretionary_protect": (
                posture == "protect"
                or dashboard["financial_outlook"][
                    "estimated_daily_network_contribution"
                ]
                < 0
            ),
            "reopening_escrow": round(reopening_escrow, 2),
            "escrowed_closed_shops": sorted(
                self.escrowed_closed_shops
            ),
        }

    def _choose_anchor(
        self,
        open_shops: list[str],
        finance: dict[str, dict[str, Any]],
    ) -> None:
        if not open_shops:
            self.anchor_shop = None
            return
        if self.anchor_shop not in open_shops:
            self.anchor_shop = max(
                open_shops,
                key=lambda shop: self.last_open_contribution.get(
                    shop,
                    float(
                        finance.get(shop, {}).get(
                            "average_daily_contribution", 0
                        )
                    ),
                ),
            )
            return
        anchor_value = float(
            finance[self.anchor_shop]["average_daily_contribution"]
        )
        anchor_streak = self.negative_streak.get(self.anchor_shop, 0)
        if (
            anchor_streak
            < self.config["anchor_switch_negative_days"]
        ):
            return
        candidate = max(
            open_shops,
            key=lambda shop: float(
                finance[shop]["average_daily_contribution"]
            ),
        )
        candidate_value = float(
            finance[candidate]["average_daily_contribution"]
        )
        if (
            candidate != self.anchor_shop
            and candidate_value
            >= anchor_value + self.config["anchor_switch_margin"]
        ):
            self.anchor_shop = candidate
