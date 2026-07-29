from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


PHASE_ROOT = Path(__file__).resolve().parents[1]
PHASES_ROOT = PHASE_ROOT.parent
PHASE1_ROOT = PHASES_ROOT / "phase_01_blue_team_v2"
PHASE2_ROOT = PHASES_ROOT / "phase_02_blue_v2_1_red_v2"
PHASE3_ROOT = PHASES_ROOT / "phase_03_blue_v2_2_red_v2"
PHASE4_ROOT = PHASES_ROOT / "phase_04_campus_crucible_v3_6k"
TEAMING_ROOT = PHASE_ROOT.parents[1]
PROJECT_ROOT = TEAMING_ROOT.parent
for path in (
    TEAMING_ROOT / "src",
    PHASE1_ROOT / "blue_team_v2",
    PHASE2_ROOT / "blue_team_v2_1",
    PHASE3_ROOT / "blue_team_v2_2",
    PHASE4_ROOT / "blue_team_6k",
    PHASE4_ROOT / "red_team_v3",
    PHASE_ROOT / "blue_team_4k",
):
    sys.path.insert(0, str(path))

from analyst_4k import ResilienceAnalyst4K  # noqa: E402
from context_manager_4k import (  # noqa: E402
    ResilienceContextManager4K,
)
from coordinator_4k import BlueResilience4K  # noqa: E402
from extension_v3 import CampusCrucibleExtension  # noqa: E402
from financial_controller import FinancialRiskController  # noqa: E402
from io_utils import read_json, sha256_json, write_json  # noqa: E402
from manager_4k import ResilientCodexManager4K  # noqa: E402
from memory import CompactMemory  # noqa: E402
from operations_4k import ResilientOperations4K  # noqa: E402
from scenario_v3 import generate_crucible_scenario  # noqa: E402
from simulation.engine import CycleSimulator  # noqa: E402


CONTROLLED_SEED = 6584760441880220463
BASELINE_RUN = (
    PHASE4_ROOT
    / "runs"
    / "campus_crucible_v3_6k_60d_20260727"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Blue Resilience 4K against Campus Crucible Red V3."
        )
    )
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--seed", type=int, default=CONTROLLED_SEED)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--codex-command",
        default=str(PROJECT_ROOT / "agent_runtime" / "codex.exe"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    red_config = read_json(
        PHASE_ROOT / "configs" / "red_v3_controlled_snapshot.json"
    )
    blue_config = read_json(
        PHASE_ROOT / "configs" / "blue_resilience_4k.json"
    )
    if not 1 <= args.days <= 60:
        raise ValueError("--days must be between 1 and 60.")
    red_config["days_per_cycle"] = args.days
    red_config["success_criteria"]["minimum_days_completed"] = args.days
    blue_config["days"] = args.days

    catalog_payload = read_json(TEAMING_ROOT / "config" / "catalog.json")
    shop_payload = read_json(TEAMING_ROOT / "config" / "shops.json")
    products = {
        product["id"]: product
        for product in catalog_payload["products"]
    }
    shops = {shop["id"]: shop for shop in shop_payload["shops"]}
    run_id = args.run_name or (
        "blue_resilience_4k_controlled_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + "_"
        + uuid.uuid4().hex[:6]
    )
    if any(value in run_id for value in ("/", "\\", "..")):
        raise ValueError("Run name must be a simple folder name.")
    run_dir = PHASE_ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    cycle_dir = run_dir / "cycle_01"
    cycle_dir.mkdir()
    red_dir = cycle_dir / "red_team_v3"
    red_dir.mkdir()

    manifest = {
        "run_id": run_id,
        "status": "running",
        "started_at": datetime.now().astimezone().isoformat(),
        "completed_at": None,
        "phase": "phase_05_blue_resilience_4k_red_v3",
        "red_version": "campus_crucible_v3_controlled",
        "red_config_sha256": sha256_json(red_config),
        "red_seed": args.seed,
        "blue_version": "blue_v2_4_resilience_4k",
        "daily_context_token_limit": 4000,
        "days_requested": args.days,
        "days_completed": 0,
        "baseline_run": str(BASELINE_RUN),
        "error": None,
    }
    write_json(run_dir / "manifest.json", manifest)
    write_json(run_dir / "effective_red_config.json", red_config)
    write_json(run_dir / "blue_4k_config.json", blue_config)
    write_json(run_dir / "catalog.json", catalog_payload)
    write_json(run_dir / "shops.json", shop_payload)
    write_json(
        run_dir / "control_metadata.json",
        {
            "purpose": "Same-seed controlled Blue improvement test",
            "seed": args.seed,
            "baseline_run": str(BASELINE_RUN),
            "red_changed": False,
            "blue_changed": True,
        },
    )
    write_json(red_dir / "cycle_seed.json", {"seed": args.seed})

    try:
        scenario = generate_crucible_scenario(
            cycle=1,
            seed=args.seed,
            days=args.days,
            red_config=red_config,
            products=products,
            shops=shops,
        )
        write_json(red_dir / "hidden_scenario.json", scenario)
        context_manager = ResilienceContextManager4K(
            token_limit=4000,
            recent_days=blue_config["context_recent_days"],
            maximum_events=blue_config["context_long_term_events"],
            chars_per_token=blue_config[
                "context_conservative_chars_per_token"
            ],
        )
        manager = ResilientCodexManager4K(
            context_manager=context_manager,
            context_manifests_dir=(
                cycle_dir / "blue_4k" / "context_manifests"
            ),
            context_usage_log=cycle_dir / "context_usage.jsonl",
            schema_path=(
                PHASE1_ROOT / "schemas" / "manager_plan.schema.json"
            ),
            sessions_dir=cycle_dir / "sessions",
            token_log_path=cycle_dir / "token_usage.jsonl",
            codex_command=args.codex_command,
            model=blue_config["model"],
            reasoning_effort=blue_config["reasoning_effort"],
            timeout_seconds=blue_config["timeout_seconds"],
        )
        finance = FinancialRiskController(blue_config)
        policy = BlueResilience4K(
            analyst=ResilienceAnalyst4K(
                blue_config["normal_delivery_days"],
                blue_config["event_warning_memory_days"],
            ),
            manager=manager,
            finance=finance,
            operations=ResilientOperations4K(blue_config),
            memory=CompactMemory(blue_config["memory_days"]),
            artifacts_dir=cycle_dir / "blue_4k",
        )
        extension = CampusCrucibleExtension(
            config=red_config,
            shops=shops,
            products=products,
        )
        simulator = CycleSimulator(
            cycle=1,
            config=red_config,
            strategy={
                "version": "blue_v2_4_resilience_4k",
                "context_limit": 4000,
                "lessons": [
                    "Financial guardrails are binding.",
                    "Demand-drop warnings persist across days.",
                    "Close negative shops before cash becomes critical.",
                    "Reopen only with a funded reserve.",
                ],
            },
            products=products,
            complements=catalog_payload["complements"],
            shops=shops,
            weekday_multipliers=shop_payload[
                "weekday_multipliers"
            ],
            scenario=scenario,
            extension=extension,
        )
        summary = simulator.run(policy, cycle_dir)
        summary["token_usage"] = token_summary(
            cycle_dir / "token_usage.jsonl"
        )
        summary["context_usage"] = context_summary(
            cycle_dir / "context_usage.jsonl"
        )
        write_json(
            cycle_dir / "summary.json", summary, overwrite=True
        )
        quick = quick_summary(run_id, summary)
        write_json(run_dir / "quick_summary.json", quick)
        write_quick_csv(run_dir / "quick_summary.csv", quick)
        if BASELINE_RUN.exists():
            baseline = read_json(BASELINE_RUN / "quick_summary.json")
            comparison = compare(baseline, quick)
            write_json(run_dir / "comparison_to_phase4.json", comparison)
        summaries_dir = PHASE_ROOT / "summaries"
        summaries_dir.mkdir(parents=True, exist_ok=True)
        write_json(summaries_dir / f"{run_id}.json", quick)
        manifest.update(
            {
                "status": "completed",
                "completed_at": datetime.now().astimezone().isoformat(),
                "days_completed": summary["days_completed"],
            }
        )
        write_json(
            run_dir / "manifest.json", manifest, overwrite=True
        )
        print(json.dumps(quick, indent=2), flush=True)
    except Exception as exc:
        manifest.update(
            {
                "status": "failed",
                "completed_at": datetime.now().astimezone().isoformat(),
                "error": str(exc),
            }
        )
        write_json(
            run_dir / "manifest.json", manifest, overwrite=True
        )
        raise


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def token_summary(path: Path) -> dict[str, Any]:
    records = read_jsonl(path)
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "noncached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    totals = {
        field: sum(int(row.get(field, 0)) for row in records)
        for field in fields
    }
    return {
        "calls_recorded": len(records),
        **totals,
        "average_total_tokens_per_call": (
            round(totals["total_tokens"] / len(records))
            if records
            else 0
        ),
    }


def context_summary(path: Path) -> dict[str, Any]:
    records = read_jsonl(path)
    estimates = [
        int(row["estimated_prompt_tokens"]) for row in records
    ]
    return {
        "days_recorded": len(records),
        "daily_limit": 4000,
        "maximum_estimated_prompt_tokens": max(
            estimates, default=0
        ),
        "average_estimated_prompt_tokens": (
            round(sum(estimates) / len(estimates))
            if estimates
            else 0
        ),
        "limit_violations": sum(value > 4000 for value in estimates),
        "counter_method": (
            records[0]["counter_method"] if records else None
        ),
    }


def quick_summary(
    run_id: str, summary: dict[str, Any]
) -> dict[str, Any]:
    fields = (
        "days_completed",
        "bankrupt",
        "accounting_profit",
        "liquidation_profit",
        "ending_cash",
        "service_rate",
        "minimum_shop_service_rate",
        "service_rate_by_shop",
        "spoilage_rate",
        "forced_closure_count",
        "closed_shop_days",
        "final_shop_status",
        "challenge_pass",
        "challenge_checks",
        "codex_decision_days",
        "fallback_days",
        "context_usage",
        "token_usage",
    )
    return {
        "run_id": run_id,
        "red_version": "campus_crucible_v3_controlled",
        "blue_version": "blue_v2_4_resilience_4k",
        **{field: summary[field] for field in fields},
    }


def compare(
    baseline: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    metrics = (
        "accounting_profit",
        "liquidation_profit",
        "ending_cash",
        "service_rate",
        "minimum_shop_service_rate",
        "spoilage_rate",
        "forced_closure_count",
        "closed_shop_days",
    )
    return {
        "controlled_seed": CONTROLLED_SEED,
        "baseline_run": baseline["run_id"],
        "current_run": current["run_id"],
        "red_seed_and_config_held_constant": True,
        "metrics": {
            metric: {
                "baseline": baseline[metric],
                "current": current[metric],
                "difference": round(
                    current[metric] - baseline[metric], 4
                ),
            }
            for metric in metrics
        },
    }


def write_quick_csv(path: Path, quick: dict[str, Any]) -> None:
    fields = (
        "run_id",
        "red_version",
        "blue_version",
        "days_completed",
        "bankrupt",
        "accounting_profit",
        "liquidation_profit",
        "ending_cash",
        "service_rate",
        "minimum_shop_service_rate",
        "spoilage_rate",
        "forced_closure_count",
        "closed_shop_days",
        "challenge_pass",
        "codex_decision_days",
        "fallback_days",
    )
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({field: quick[field] for field in fields})


if __name__ == "__main__":
    main()
