from __future__ import annotations

from copy import deepcopy
from typing import Any


def learn(
    *,
    cycle: int,
    strategy: dict[str, Any],
    summary: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Update bounded strategy parameters from aggregate completed-run logs."""
    updated = deepcopy(strategy)
    changes: list[dict[str, Any]] = []
    lessons = list(updated.get("lessons", []))

    if summary["price_refusal_rate"] > 0.2:
        _change(
            changes,
            updated,
            "price_ratio_to_reference",
            max(0.85, updated["price_ratio_to_reference"] - 0.04),
            "High price refusal: start nearer to the normal market price.",
        )
        lessons.append(
            "High prices caused substantial refused demand; use smaller price tests."
        )
    elif (
        summary["price_refusal_rate"] < 0.05
        and summary["accounting_profit"] > 0
    ):
        _change(
            changes,
            updated,
            "price_ratio_to_reference",
            min(1.08, updated["price_ratio_to_reference"] + 0.015),
            "Low price refusal and positive profit allow a small cautious increase.",
        )

    if (
        not summary["bankrupt"]
        and summary["stockout_lost_units"]
        > summary["units_sold"] * 0.25
    ):
        key = "nonperishable"
        old = updated["target_cover_days"][key]
        new = min(4.0, old + (0.25 if cycle == 1 else 0.15))
        updated["target_cover_days"][key] = round(new, 3)
        changes.append(
            {
                "parameter": f"target_cover_days.{key}",
                "before": old,
                "after": updated["target_cover_days"][key],
                "reason": "Stockout loss was high; carry slightly more durable stock.",
            }
        )
        lessons.append(
            "Durable products lost too many sales; increase cover without overstocking fresh goods."
        )
        if summary["spoilage_rate"] < 0.05:
            old_fresh = updated["target_cover_days"]["perishable"]
            new_fresh = min(
                2.8, old_fresh + (0.15 if cycle == 1 else 0.1)
            )
            updated["target_cover_days"]["perishable"] = round(
                new_fresh, 3
            )
            changes.append(
                {
                    "parameter": "target_cover_days.perishable",
                    "before": old_fresh,
                    "after": updated["target_cover_days"]["perishable"],
                    "reason": (
                        "Fresh stockouts were costly while spoilage remained low."
                    ),
                }
            )

    if summary["spoilage_rate"] > 0.08:
        old = updated["target_cover_days"]["perishable"]
        new = max(0.75, old - (0.35 if cycle == 1 else 0.2))
        updated["target_cover_days"]["perishable"] = round(new, 3)
        changes.append(
            {
                "parameter": "target_cover_days.perishable",
                "before": old,
                "after": updated["target_cover_days"]["perishable"],
                "reason": "Fresh spoilage was high; reduce fresh days of cover.",
            }
        )
        old_reduction = updated["festival_fresh_order_reduction"]
        updated["festival_fresh_order_reduction"] = round(
            max(0.2, old_reduction - 0.08), 3
        )
        changes.append(
            {
                "parameter": "festival_fresh_order_reduction",
                "before": old_reduction,
                "after": updated["festival_fresh_order_reduction"],
                "reason": "React more strongly to travel and break warnings.",
            }
        )
        lessons.append(
            "Fresh inventory remained too high around demand collapses."
        )

    daily_rent = 270
    if summary["bankrupt"]:
        _change(
            changes,
            updated,
            "cash_reserve_rent_days",
            min(4.0, updated["cash_reserve_rent_days"] + 1.0),
            "Bankruptcy: retain more liquid cash during ordinary days.",
        )
        _change(
            changes,
            updated,
            "warning_reserve_days",
            min(7.0, updated["warning_reserve_days"] + 1.0),
            "Bankruptcy near a warning: protect more rent before a reversal.",
        )
        _change(
            changes,
            updated,
            "downturn_reserve_days",
            min(9.0, updated["downturn_reserve_days"] + 1.0),
            "Bankruptcy in weak demand: extend the downturn cash runway.",
        )
        lessons.append(
            "Bankruptcy occurred with value trapped in stock; liquidity takes priority over extra cover."
        )
    elif (
        summary["service_rate"] < 0.75
        and summary["stockout_lost_units"] > summary["units_sold"] * 0.25
    ):
        _change(
            changes,
            updated,
            "cash_reserve_rent_days",
            max(1.0, updated["cash_reserve_rent_days"] - 0.5),
            "Stockouts dominated: release reserve into productive inventory.",
        )
        lessons.append(
            "Cash constraints caused lost sales; use more cash as working inventory."
        )
    elif (
        summary["minimum_cash"] < daily_rent
        and summary["service_rate"] >= 0.75
    ):
        _change(
            changes,
            updated,
            "cash_reserve_rent_days",
            min(4.0, updated["cash_reserve_rent_days"] + 0.25),
            "Service was healthy but cash was thin: rebuild a small buffer.",
        )

    if (
        summary["accounting_profit"] < 0
        and summary["median_price_reference_ratio"] < 0.95
    ):
        old_ratio = updated["price_ratio_to_reference"]
        new_ratio = max(0.99, old_ratio)
        updated["price_ratio_to_reference"] = round(new_ratio, 3)
        if new_ratio != old_ratio:
            changes.append(
                {
                    "parameter": "price_ratio_to_reference",
                    "before": old_ratio,
                    "after": updated["price_ratio_to_reference"],
                    "reason": "Broad discounting reduced margin without preventing failure.",
                }
            )
        lessons.append(
            "Do not discount the full catalog; discount only stock close to expiry."
        )

    updated["version"] = int(updated.get("version", 1)) + 1
    updated["lessons"] = lessons[-10:]
    review = {
        "completed_cycle": cycle,
        "observed": {
            "accounting_profit": summary["accounting_profit"],
            "service_rate": summary["service_rate"],
            "minimum_shop_service_rate": summary[
                "minimum_shop_service_rate"
            ],
            "price_refusal_rate": summary["price_refusal_rate"],
            "spoilage_rate": summary["spoilage_rate"],
            "order_clip_rate": summary["order_clip_rate"],
            "minimum_cash": summary["minimum_cash"],
            "bankrupt": summary["bankrupt"],
            "ending_inventory_value": summary[
                "ending_inventory_value"
            ],
            "ending_pending_value": summary["ending_pending_value"],
        },
        "lessons_retained": updated["lessons"],
        "source_change_policy": (
            "No Python source was rewritten. Only bounded strategy JSON changed."
        ),
    }
    return updated, review, changes


def _change(
    changes: list[dict[str, Any]],
    target: dict[str, Any],
    key: str,
    new_value: float,
    reason: str,
) -> None:
    old = target[key]
    new_value = round(new_value, 3)
    if old == new_value:
        return
    target[key] = new_value
    changes.append(
        {
            "parameter": key,
            "before": old,
            "after": new_value,
            "reason": reason,
        }
    )
