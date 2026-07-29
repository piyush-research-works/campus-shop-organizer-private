from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _number(data: dict[str, Any], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    return float(value)


def score_seed(seed: dict[str, Any]) -> dict[str, Any]:
    """Return the transparent 90-point score for one private seed."""
    failed = (
        bool(seed.get("failed_seed"))
        or bool(seed.get("bankrupt"))
        or not bool(seed.get("completed"))
        or int(seed.get("days_completed", 0)) != int(seed.get("days_requested", 0))
    )
    if failed:
        return {
            "seed_id": str(seed.get("seed_id", "unknown")),
            "failed": True,
            "profit_points": 0.0,
            "service_points": 0.0,
            "weakest_shop_points": 0.0,
            "coverage_points": 0.0,
            "spoilage_points": 0.0,
            "forced_closure_penalty": 0.0,
            "simulator_score": 0.0,
        }

    profit = _number(seed, "liquidation_profit")
    service = _number(seed, "service_rate")
    weakest = _number(seed, "minimum_shop_service_rate")
    spoilage = _number(seed, "spoilage_rate")
    open_shop_days = _number(seed, "open_shop_days")
    closures = int(_number(seed, "forced_closure_count"))

    profit_points = 45.0 * clamp(profit / 3000.0)
    service_points = 20.0 * clamp(service / 0.72)
    weakest_points = 10.0 * clamp(weakest / 0.62)
    coverage_points = 10.0 * clamp(open_shop_days / 180.0)

    if spoilage <= 0.10:
        spoilage_points = 5.0
    elif spoilage >= 0.25:
        spoilage_points = 0.0
    else:
        spoilage_points = 5.0 * (0.25 - spoilage) / 0.15

    closure_penalty = -min(10.0, 2.0 * max(0, closures))
    total = clamp(
        profit_points
        + service_points
        + weakest_points
        + coverage_points
        + spoilage_points
        + closure_penalty,
        0.0,
        90.0,
    )
    return {
        "seed_id": str(seed.get("seed_id", "unknown")),
        "failed": False,
        "profit_points": round(profit_points, 4),
        "service_points": round(service_points, 4),
        "weakest_shop_points": round(weakest_points, 4),
        "coverage_points": round(coverage_points, 4),
        "spoilage_points": round(spoilage_points, 4),
        "forced_closure_penalty": round(closure_penalty, 4),
        "simulator_score": round(total, 4),
    }


@dataclass(frozen=True)
class HumanReview:
    design_and_code: float
    final_explanation: float
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HumanReview":
        design = _number(data, "design_and_code")
        explanation = _number(data, "final_explanation")
        if not 0.0 <= design <= 5.0:
            raise ValueError("design_and_code must be between 0 and 5")
        if not 0.0 <= explanation <= 5.0:
            raise ValueError("final_explanation must be between 0 and 5")
        return cls(design, explanation, str(data.get("notes", "")))

    @property
    def total(self) -> float:
        return self.design_and_code + self.final_explanation


def score_participant(
    participant_id: str,
    batch: dict[str, Any],
    human_review: dict[str, Any],
) -> dict[str, Any]:
    seeds = batch["seed_results"]
    seed_scores = [score_seed(seed) for seed in seeds]
    values = [item["simulator_score"] for item in seed_scores]
    average = sum(values) / len(values)
    worst = min(values)
    simulator = 0.70 * average + 0.30 * worst
    review = HumanReview.from_dict(human_review)

    total_profit = sum(float(seed.get("liquidation_profit", 0.0)) for seed in seeds)
    fallback_days = sum(int(seed.get("fallback_days", 0)) for seed in seeds)
    final_total = simulator + review.total

    return {
        "participant_id": participant_id,
        "seed_scores": seed_scores,
        "average_seed_score": round(average, 4),
        "worst_seed_score": round(worst, 4),
        "simulator_score": round(simulator, 4),
        "human_review": {
            "design_and_code": round(review.design_and_code, 4),
            "final_explanation": round(review.final_explanation, 4),
            "total": round(review.total, 4),
            "notes": review.notes,
        },
        "final_score": round(final_total, 4),
        "total_liquidation_profit": round(total_profit, 4),
        "fallback_days": fallback_days,
    }


def ranking_key(result: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -float(result["final_score"]),
        -float(result["worst_seed_score"]),
        -float(result["total_liquidation_profit"]),
        int(result["fallback_days"]),
        str(result["participant_id"]).casefold(),
    )

