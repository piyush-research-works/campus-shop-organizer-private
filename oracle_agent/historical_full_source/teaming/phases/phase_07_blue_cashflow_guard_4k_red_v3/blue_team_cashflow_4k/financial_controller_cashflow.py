from __future__ import annotations

from typing import Any

from financial_controller_sustainable import (
    SustainableFinancialController,
)


class CashflowFinancialController(SustainableFinancialController):
    """Consolidates earlier and reports a truthful closed-network state."""

    def evaluate(
        self,
        observation: dict[str, Any],
        dashboard: dict[str, Any],
    ) -> dict[str, Any]:
        guardrails = super().evaluate(observation, dashboard)
        continuity = dashboard["business_continuity"]
        cash = float(observation["shared_cash"])
        network_closed_after_actions = not guardrails[
            "active_shops_after"
        ]
        if network_closed_after_actions:
            guardrails.update(
                {
                    "cash_runway_days": 0.0,
                    "force_cash_posture": "protect",
                    "allow_discretionary_protect": True,
                    "business_state": "closed_recovery_deadlock",
                    "recovery_capital_gap": continuity[
                        "recovery_capital_gap"
                    ],
                }
            )
        else:
            protected_cash = (
                float(guardrails["reopening_escrow"])
                + float(guardrails["daily_rent_after_actions"]) * 2
            )
            guardrails.update(
                {
                    "business_state": (
                        "cash_critical"
                        if cash < protected_cash
                        else "operating"
                    ),
                    "recovery_capital_gap": 0.0,
                    "cash_after_two_rents_and_escrow": round(
                        cash - protected_cash, 2
                    ),
                }
            )
        return guardrails
