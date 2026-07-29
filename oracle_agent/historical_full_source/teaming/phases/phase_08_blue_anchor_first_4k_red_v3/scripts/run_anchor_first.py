from __future__ import annotations

import argparse
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
PHASE5_ROOT = PHASES_ROOT / "phase_05_blue_resilience_4k_red_v3"
PHASE6_ROOT = (
    PHASES_ROOT / "phase_06_blue_sustainable_profit_4k_red_v3"
)
PHASE7_ROOT = (
    PHASES_ROOT / "phase_07_blue_cashflow_guard_4k_red_v3"
)
TEAMING_ROOT = PHASE_ROOT.parents[1]
PROJECT_ROOT = TEAMING_ROOT.parent
for path in (
    TEAMING_ROOT / "src",
    PHASE1_ROOT / "blue_team_v2",
    PHASE2_ROOT / "blue_team_v2_1",
    PHASE3_ROOT / "blue_team_v2_2",
    PHASE4_ROOT / "blue_team_6k",
    PHASE4_ROOT / "red_team_v3",
    PHASE5_ROOT / "blue_team_4k",
    PHASE5_ROOT / "scripts",
    PHASE6_ROOT / "blue_team_sustainable_4k",
    PHASE7_ROOT / "blue_team_cashflow_4k",
    PHASE_ROOT / "blue_team_anchor_first_4k",
):
    sys.path.insert(0, str(path))

from analyst_cashflow_4k import CashflowDemandAnalyst4K  # noqa: E402
from context_manager_4k import ResilienceContextManager4K  # noqa: E402
from coordinator_anchor_first_4k import BlueAnchorFirst4K  # noqa: E402
from extension_v3 import CampusCrucibleExtension  # noqa: E402
from financial_controller_anchor_first import (  # noqa: E402
    AnchorFirstFinancialController,
)
from io_utils import read_json, sha256_json, write_json  # noqa: E402
from manager_cashflow_4k import CashflowCodexManager4K  # noqa: E402
from memory import CompactMemory  # noqa: E402
from operations_anchor_first_4k import (  # noqa: E402
    AnchorFirstOperations4K,
)
from run_resilience import (  # noqa: E402
    CONTROLLED_SEED,
    compare,
    context_summary,
    token_summary,
    write_quick_csv,
)
from scenario_v3 import generate_crucible_scenario  # noqa: E402
from simulation.engine import CycleSimulator  # noqa: E402


BASELINE_RUN = (
    PHASE7_ROOT
    / "runs"
    / "blue_v2_6_portfolio_vs_red_v3_controlled_60d_model_20260728"
)
BLUE_VERSION = "blue_v2_7_anchor_first_4k"
RED_VERSION = "campus_crucible_v3_controlled"
CONTEXT_LIMIT = 3800


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Blue V2.7 against unchanged Red V3."
    )
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--seed", type=int, default=CONTROLLED_SEED)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--scenario-days",
        type=int,
        default=None,
        help="Generate a longer scenario while running fewer QA days.",
    )
    parser.add_argument(
        "--codex-command",
        default=str(PROJECT_ROOT / "agent_runtime" / "codex.exe"),
    )
    return parser.parse_args()


def merged_blue_config() -> dict[str, Any]:
    config = read_json(
        PHASE7_ROOT / "configs" / "blue_cashflow_guard_4k.json"
    )
    config.update(
        read_json(
            PHASE_ROOT
            / "configs"
            / "blue_anchor_first_overrides.json"
        )
    )
    return config


def main() -> None:
    args = parse_args()
    if not 1 <= args.days <= 60:
        raise ValueError("--days must be between 1 and 60.")

    red_config = read_json(
        PHASE5_ROOT / "configs" / "red_v3_controlled_snapshot.json"
    )
    blue_config = merged_blue_config()
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
        "blue_anchor_first_4k_"
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
        "phase": "phase_08_blue_anchor_first_4k_red_v3",
        "red_version": RED_VERSION,
        "red_config_sha256": sha256_json(red_config),
        "red_seed": args.seed,
        "blue_version": BLUE_VERSION,
        "daily_context_token_limit": CONTEXT_LIMIT,
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
            "purpose": "Controlled Blue V2.7 anchor-first test",
            "seed": args.seed,
            "baseline_run": str(BASELINE_RUN),
            "red_changed": False,
            "blue_changed": True,
        },
    )
    write_json(red_dir / "cycle_seed.json", {"seed": args.seed})

    try:
        scenario_days = args.scenario_days or args.days
        if scenario_days < args.days:
            raise ValueError("--scenario-days cannot be shorter than --days.")
        scenario = generate_crucible_scenario(
            cycle=1,
            seed=args.seed,
            days=scenario_days,
            red_config=red_config,
            products=products,
            shops=shops,
        )
        write_json(red_dir / "hidden_scenario.json", scenario)
        context_manager = ResilienceContextManager4K(
            token_limit=CONTEXT_LIMIT,
            recent_days=blue_config["context_recent_days"],
            maximum_events=blue_config["context_long_term_events"],
            chars_per_token=blue_config[
                "context_conservative_chars_per_token"
            ],
        )
        manager = CashflowCodexManager4K(
            context_manager=context_manager,
            context_manifests_dir=(
                cycle_dir
                / "blue_anchor_first_4k"
                / "context_manifests"
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
        policy = BlueAnchorFirst4K(
            analyst=CashflowDemandAnalyst4K(
                blue_config["normal_delivery_days"],
                blue_config,
            ),
            manager=manager,
            finance=AnchorFirstFinancialController(blue_config),
            operations=AnchorFirstOperations4K(blue_config),
            memory=CompactMemory(blue_config["memory_days"]),
            artifacts_dir=cycle_dir / "blue_anchor_first_4k",
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
                "version": BLUE_VERSION,
                "context_limit": CONTEXT_LIMIT,
                "lessons": [
                    "Anchor rent and stock outrank extra reopening fees.",
                    "Only one reopening fee is funded while consolidating.",
                    "Close a non-anchor before the anchor misses rent.",
                    "Reopen only from strongly funded cash.",
                ],
            },
            products=products,
            complements=catalog_payload["complements"],
            shops=shops,
            weekday_multipliers=shop_payload["weekday_multipliers"],
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
            write_json(
                run_dir / "comparison_to_phase7.json",
                compare(
                    read_json(BASELINE_RUN / "quick_summary.json"),
                    quick,
                ),
            )
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
        "red_version": RED_VERSION,
        "blue_version": BLUE_VERSION,
        **{field: summary[field] for field in fields},
    }


if __name__ == "__main__":
    main()
