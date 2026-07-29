from __future__ import annotations

from typing import Any


class FinancialRiskController:
    """Binding lifecycle and liquidity rules outside the LLM."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.last_day = 0
        self.last_open_contribution: dict[str, float] = {}

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
        if day > self.last_day:
            for shop in open_shops:
                contribution = dashboard["shop_finance"][shop][
                    "average_daily_contribution"
                ]
                self.last_open_contribution[shop] = contribution
            self.last_day = day

        cash = float(observation["shared_cash"])
        rent = float(observation["rent"]["per_shop"])
        current_daily_rent = rent * len(open_shops)
        runway = cash / max(1.0, current_daily_rent)
        severity = dashboard["event_risk"]["severity"]
        actions: list[dict[str, str]] = []
        action_reason = "hold"

        eligible_closed = [
            shop
            for shop in closed_shops
            if status[shop]["eligible_to_reopen"]
        ]
        remaining_days = self.config["days"] - day + 1
        if eligible_closed:
            reserve_days = (
                self.config["final_reopen_reserve_days"]
                if remaining_days
                <= self.config["final_reopen_window_days"]
                else self.config["reopen_cash_reserve_days"]
            )
            required = (
                observation["closure_rules"]["reopening_cost"]
                + rent * (len(open_shops) + 1) * reserve_days
            )
            viable = [
                shop
                for shop in eligible_closed
                if observation["today_signals"]["footfall_hint"][shop]
                != "very_low"
            ]
            if (
                viable
                and severity != "severe"
                and cash >= required
            ):
                target = max(
                    viable,
                    key=lambda shop: self.last_open_contribution.get(
                        shop, -10_000.0
                    ),
                )
                actions.append({"shop": target, "action": "reopen"})
                action_reason = (
                    f"reopen_{target}_with_{reserve_days}_day_reserve"
                )

        if (
            not actions
            and len(open_shops) > self.config["minimum_open_shops"]
            and day <= self.config["latest_voluntary_close_day"]
            and day >= self.config["minimum_history_before_closure"]
        ):
            finance = dashboard["shop_finance"]
            very_low = set(
                dashboard["event_risk"]["very_low_shops"]
            )
            severe_candidates = [
                shop
                for shop in open_shops
                if shop in very_low
                and finance[shop]["average_daily_contribution"]
                < self.config["close_max_daily_contribution"]
            ]
            emergency = (
                runway < self.config["emergency_close_runway_days"]
            )
            warning_stress = (
                severity == "severe"
                and runway
                < self.config["warning_close_runway_days"]
            )
            if severe_candidates and warning_stress:
                target = min(
                    severe_candidates,
                    key=lambda shop: finance[shop][
                        "average_daily_contribution"
                    ],
                )
                actions.append({"shop": target, "action": "close"})
                action_reason = (
                    f"close_{target}_severe_demand_and_negative_margin"
                )
            elif emergency:
                target = min(
                    open_shops,
                    key=lambda shop: finance[shop][
                        "average_daily_contribution"
                    ],
                )
                if (
                    finance[target]["average_daily_contribution"]
                    < self.config["close_max_daily_contribution"]
                ):
                    actions.append(
                        {"shop": target, "action": "close"}
                    )
                    action_reason = (
                        f"close_{target}_emergency_cash_runway"
                    )

        reopening_count = sum(
            action["action"] == "reopen" for action in actions
        )
        action_cash_cost = (
            reopening_count
            * observation.get("closure_rules", {}).get(
                "reopening_cost", 0
            )
        )
        inactive_after_actions = {
            shop for shop in closed_shops
        }
        for action in actions:
            if action["action"] == "close":
                inactive_after_actions.add(action["shop"])
            else:
                inactive_after_actions.discard(action["shop"])

        return {
            "actions": actions,
            "action_reason": action_reason,
            "action_cash_cost": action_cash_cost,
            "cash_runway_days": round(runway, 2),
            "open_shops_before": open_shops,
            "inactive_shops_after": sorted(inactive_after_actions),
            "event_severity": severity,
            "force_cash_posture": (
                "protect"
                if severity != "normal"
                or runway
                < self.config["protect_cash_runway_days"]
                else None
            ),
        }
