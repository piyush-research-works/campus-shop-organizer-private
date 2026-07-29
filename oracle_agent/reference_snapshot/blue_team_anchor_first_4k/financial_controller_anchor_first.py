from __future__ import annotations

from typing import Any

from financial_controller_cashflow import CashflowFinancialController


class AnchorFirstFinancialController(CashflowFinancialController):
    """Puts the operating anchor ahead of additional reopening fees."""

    def evaluate(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
    ) -> dict[str, Any]:
        guardrails = super().evaluate(observation, dashboard)
        cash = float(observation["shared_cash"])
        rent = float(observation["rent"]["per_shop"])
        active = list(guardrails["active_shops_after"])

        if (
            not guardrails["actions"]
            and len(active) > 1
            and cash / max(1.0, rent * len(active))
            < float(self.config["hard_consolidation_runway_days"])
        ):
            anchor = guardrails["anchor_shop"]
            candidates = [shop for shop in active if shop != anchor]
            if candidates:
                finance = dashboard["shop_finance"]
                target = min(
                    candidates,
                    key=lambda shop: finance[shop][
                        "average_daily_contribution"
                    ],
                )
                guardrails["actions"] = [
                    {"shop": target, "action": "close"}
                ]
                guardrails["action_reason"] = (
                    f"emergency_close_{target}_to_protect_anchor"
                )
                self.escrowed_closed_shops.add(target)
                active.remove(target)
                guardrails["active_shops_after"] = sorted(active)
                guardrails["inactive_shops_after"] = sorted(
                    set(observation["shop_status"]) - set(active)
                )
                guardrails["daily_rent_after_actions"] = round(
                    rent * len(active), 2
                )

        reopening_cost = float(
            observation.get("closure_rules", {}).get(
                "reopening_cost", 0
            )
        )
        raw_escrow = (
            len(self.escrowed_closed_shops) * reopening_cost
        )
        funded_escrow = min(
            raw_escrow,
            float(self.config["recovery_escrow_cap"]),
        )
        guardrails["reopening_escrow"] = round(funded_escrow, 2)
        guardrails["unfunded_reopening_fees"] = round(
            max(0.0, raw_escrow - funded_escrow), 2
        )
        guardrails["reserve_priority"] = [
            "anchor_rent",
            "anchor_inventory",
            "one_recovery_fee",
            "additional_reopenings",
        ]

        active = guardrails["active_shops_after"]
        if active:
            protected = (
                funded_escrow
                + float(guardrails["daily_rent_after_actions"]) * 2
            )
            guardrails["business_state"] = (
                "cash_critical" if cash < protected else "operating"
            )
            guardrails["cash_after_two_rents_and_escrow"] = round(
                cash - protected, 2
            )
        return guardrails

