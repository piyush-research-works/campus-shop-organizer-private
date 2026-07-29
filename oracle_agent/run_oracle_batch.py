from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ORACLE_ROOT = Path(__file__).resolve().parent
PHASE_ROOT = (
    ORACLE_ROOT
    / "historical_full_source"
    / "teaming"
    / "phases"
    / "phase_08_blue_anchor_first_4k_red_v3"
)
RUNNER = PHASE_ROOT / "scripts" / "run_anchor_first.py"
FINAL_SEEDS = (
    ROOT
    / "official_runner"
    / "private_inputs"
    / "final_seeds.json"
)
QA_SEEDS = ROOT / "official_runner" / "private_seed_set.example.json"


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def source_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the exact Blue V2.7 source across three seeds."
    )
    parser.add_argument(
        "mode", choices=("qa", "final"), help="QA or official 60-day batch."
    )
    parser.add_argument(
        "--codex-command",
        default=os.environ.get("CODEX_COMMAND", "codex"),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="QA only: 1-7 days.",
    )
    parser.add_argument(
        "--allow-fallback-only",
        action="store_true",
        help="Allow QA without a Codex executable. Never use for a benchmark.",
    )
    return parser.parse_args()


def compact(
    seed_id: str, days: int, summary: dict[str, Any]
) -> dict[str, Any]:
    service_by_shop = summary["service_rate_by_shop"]
    closed_days = int(summary.get("closed_shop_days", 0))
    return {
        "seed_id": seed_id,
        "days_requested": days,
        "days_completed": int(summary["days_completed"]),
        "completed": int(summary["days_completed"]) == days,
        "bankrupt": bool(summary["bankrupt"]),
        "ending_cash": float(summary["ending_cash"]),
        "accounting_profit": float(summary["accounting_profit"]),
        "liquidation_profit": float(summary["liquidation_profit"]),
        "service_rate": float(summary["service_rate"]),
        "service_rate_by_shop": service_by_shop,
        "minimum_shop_service_rate": float(
            summary["minimum_shop_service_rate"]
        ),
        "spoilage_rate": float(summary["spoilage_rate"]),
        "forced_closure_count": int(
            summary.get("forced_closure_count", 0)
        ),
        "closed_shop_days": closed_days,
        "open_shop_days": days * len(service_by_shop) - closed_days,
        "fallback_days": int(summary.get("fallback_days", 0)),
        "failed_seed": (
            bool(summary["bankrupt"])
            or int(summary["days_completed"]) != days
        ),
    }


def aggregate(seeds: list[dict[str, Any]]) -> dict[str, Any]:
    def average(key: str) -> float:
        return round(
            sum(float(seed[key]) for seed in seeds) / len(seeds), 4
        )

    return {
        "average_liquidation_profit": average("liquidation_profit"),
        "worst_liquidation_profit": min(
            seed["liquidation_profit"] for seed in seeds
        ),
        "average_service_rate": average("service_rate"),
        "worst_service_rate": min(seed["service_rate"] for seed in seeds),
        "average_minimum_shop_service_rate": average(
            "minimum_shop_service_rate"
        ),
        "worst_minimum_shop_service_rate": min(
            seed["minimum_shop_service_rate"] for seed in seeds
        ),
        "average_spoilage_rate": average("spoilage_rate"),
        "worst_spoilage_rate": max(
            seed["spoilage_rate"] for seed in seeds
        ),
        "total_forced_closures": sum(
            seed["forced_closure_count"] for seed in seeds
        ),
        "total_fallback_days": sum(
            seed["fallback_days"] for seed in seeds
        ),
        "any_failed_seed": any(seed["failed_seed"] for seed in seeds),
    }


def main() -> None:
    args = parse_args()
    if args.mode == "final":
        days = 60
        seeds_path = FINAL_SEEDS
    else:
        days = int(args.days or 2)
        if not 1 <= days <= 7:
            raise ValueError("QA days must be between 1 and 7.")
        seeds_path = QA_SEEDS

    executable = shutil.which(args.codex_command)
    if executable is None and not args.allow_fallback_only:
        raise RuntimeError(
            "Codex executable not found. Configure --codex-command. "
            "Fallback-only runs are pipeline QA, not Oracle benchmarks."
        )
    codex_command = executable or args.codex_command
    seed_set = read_object(seeds_path)
    if len(seed_set.get("seeds", [])) != 3:
        raise ValueError("Oracle batch requires exactly three seeds.")

    batch_id = (
        "oracle_exact_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + "_"
        + uuid.uuid4().hex[:6]
    )
    batch_dir = ORACLE_ROOT / "batch_runs" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    for index, seed in enumerate(seed_set["seeds"], start=1):
        run_name = f"{batch_id}_seed_{index:02d}"
        command = [
            sys.executable,
            str(RUNNER),
            "--run-name",
            run_name,
            "--seed",
            str(int(seed["value"])),
            "--days",
            str(days),
            "--codex-command",
            str(codex_command),
        ]
        if args.mode == "qa":
            command.extend(["--scenario-days", "60"])
        completed = subprocess.run(
            command,
            cwd=RUNNER.parent,
            text=True,
            capture_output=True,
            check=False,
        )
        (batch_dir / f"seed_{index:02d}.stdout.log").write_text(
            completed.stdout, encoding="utf-8"
        )
        (batch_dir / f"seed_{index:02d}.stderr.log").write_text(
            completed.stderr, encoding="utf-8"
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Oracle seed {index} failed. See {batch_dir}."
            )
        summary = read_object(PHASE_ROOT / "runs" / run_name / "quick_summary.json")
        results.append(compact(str(seed["seed_id"]), days, summary))

    batch = {
        "status": "completed",
        "mode": args.mode,
        "participant_id": "oracle_blue_v2_7_anchor_first",
        "private_red_version": "origin_one_campus_crucible_v3",
        "days_per_seed": days,
        "seed_count": 3,
        "seed_set_sha256": hashlib.sha256(
            seeds_path.read_bytes()
        ).hexdigest(),
        "participant_source_sha256": source_hash(
            ORACLE_ROOT / "historical_full_source"
        ),
        "source_frozen_across_batch": True,
        "model_enabled": executable is not None,
        "daily_context_token_limit": 3800,
        "seed_results": results,
        "metric_summary": aggregate(results),
    }
    output = batch_dir / "batch_summary.json"
    output.write_text(
        json.dumps(batch, indent=2) + "\n", encoding="utf-8"
    )
    print("ORACLE BATCH COMPLETE")
    print(f"Summary: {output}")


if __name__ == "__main__":
    main()
