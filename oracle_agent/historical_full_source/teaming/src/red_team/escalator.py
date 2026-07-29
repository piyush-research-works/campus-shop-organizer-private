from __future__ import annotations

from copy import deepcopy
from typing import Any


def escalate(
    *,
    cycle: int,
    current_config: dict[str, Any],
    cycle_summary: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Increase difficulty with a few bounded, explainable changes."""
    updated = deepcopy(current_config)
    demand = updated["demand"]
    bounds = updated["red_team_bounds"]
    changes: list[dict[str, Any]] = []
    gate = updated.get("escalation_gate")
    gate_passed = (
        not gate
        or (
            cycle_summary["days_completed"]
            >= gate["minimum_days_completed"]
            and cycle_summary["accounting_profit"]
            >= gate["minimum_accounting_profit"]
            and cycle_summary["service_rate"]
            >= gate["minimum_service_rate"]
            and cycle_summary["spoilage_rate"]
            <= gate["maximum_spoilage_rate"]
        )
    )

    profit = float(cycle_summary["accounting_profit"])
    revenue = max(1.0, float(cycle_summary["revenue"]))
    profit_margin = profit / revenue
    spoilage_rate = float(cycle_summary["spoilage_rate"])
    price_refusal_rate = float(cycle_summary["price_refusal_rate"])

    if gate_passed and profit > 0:
        _change(
            changes,
            demand,
            "base_scale",
            max(bounds["minimum_base_scale"], demand["base_scale"] - 0.04),
            "Positive profit: slightly reduce ordinary footfall.",
        )
        _change(
            changes,
            demand,
            "random_low_day_probability",
            min(
                bounds["maximum_low_day_probability"],
                demand["random_low_day_probability"] + 0.025,
            ),
            "Positive profit: add a few more location-specific quiet days.",
        )

    if gate_passed and (
        profit_margin > 0.08 or price_refusal_rate < 0.08
    ):
        _change(
            changes,
            demand,
            "elasticity_scale",
            min(
                bounds["maximum_elasticity_scale"],
                demand["elasticity_scale"] + 0.08,
            ),
            "Pricing was comfortable: make demand more price-sensitive.",
        )
        _change(
            changes,
            demand,
            "budget_pressure_choke_ratio",
            max(
                bounds["minimum_budget_pressure_choke_ratio"],
                demand["budget_pressure_choke_ratio"] - 0.02,
            ),
            "Pricing was comfortable: tighten willingness-to-pay on budget days.",
        )

    if gate_passed and spoilage_rate < 0.03:
        _change(
            changes,
            demand,
            "reversal_drop_multiplier",
            max(
                bounds["minimum_reversal_drop_multiplier"],
                demand["reversal_drop_multiplier"] - 0.035,
            ),
            "Little spoilage occurred: deepen demand collapses after buildups.",
        )
        _change(
            changes,
            demand,
            "event_warning_lead_days",
            max(
                bounds["minimum_event_warning_lead_days"],
                demand["event_warning_lead_days"] - 1,
            ),
            "Little spoilage occurred: shorten event warning time.",
        )

    next_delay = min(
        bounds["maximum_extra_delay_probability"],
        updated["extra_delay_probability"] + 0.02,
    )
    if gate_passed and cycle < updated["cycles"]:
        changes.append(
            {
                "parameter": "extra_delay_probability",
                "before": updated["extra_delay_probability"],
                "after": round(next_delay, 4),
                "reason": "Later cycles add a small amount of supply uncertainty.",
            }
        )
        updated["extra_delay_probability"] = round(next_delay, 4)

    review = {
        "completed_cycle": cycle,
        "profit_margin": round(profit_margin, 4),
        "spoilage_rate": round(spoilage_rate, 4),
        "price_refusal_rate": round(price_refusal_rate, 4),
        "escalation_gate_applied": bool(gate),
        "escalation_gate_passed": gate_passed,
        "changes_for_next_cycle": changes,
        "principle": (
            "Only bounded configuration values changed; source code and exact "
            "day patterns were not tailored to the Blue Team."
        ),
    }
    return updated, review


def _change(
    changes: list[dict[str, Any]],
    target: dict[str, Any],
    key: str,
    new_value: float | int,
    reason: str,
) -> None:
    old_value = target[key]
    if new_value == old_value:
        return
    target[key] = round(new_value, 4) if isinstance(new_value, float) else new_value
    changes.append(
        {
            "parameter": f"demand.{key}",
            "before": old_value,
            "after": target[key],
            "reason": reason,
        }
    )
