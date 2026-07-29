"""Run one frozen agent on a three-seed private batch."""

from __future__ import annotations

import json
import re
import sys
import traceback
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .hashing import hash_agent_code, sha256_file


MODULE_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = MODULE_DIR / "private_runtime"
RED_V3_DIR = RUNTIME_DIR / "red_team_v3"
for value in (RUNTIME_DIR, RED_V3_DIR):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from agent_protocol import CommandAgentPolicy  # noqa: E402
from extension_v3 import CampusCrucibleExtension  # noqa: E402
from io_utils import (  # noqa: E402
    append_jsonl,
    read_json,
    sha256_json,
    write_json,
)
from scenario_v3 import generate_crucible_scenario  # noqa: E402
from simulation.engine import CycleSimulator  # noqa: E402


CONTEXT_LIMIT = 3800
TOTAL_DAYS = 60
EXPECTED_SEEDS = 3
PRIVATE_RED_VERSION = "origin_one_campus_crucible_v3"


def load_seed_set(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("seed set must be a JSON object")
    seeds = payload.get("seeds")
    if not isinstance(seeds, list) or len(seeds) != EXPECTED_SEEDS:
        raise ValueError("seed set must contain exactly three seeds")

    identifiers: set[str] = set()
    values: set[int] = set()
    for index, seed in enumerate(seeds, start=1):
        if not isinstance(seed, dict):
            raise ValueError(f"seed {index} must be an object")
        seed_id = seed.get("seed_id")
        value = seed.get("value")
        if not isinstance(seed_id, str) or not seed_id.strip():
            raise ValueError(f"seed {index} requires seed_id")
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value >= 2**63
        ):
            raise ValueError(
                f"seed {index} value must be a 63-bit non-negative integer"
            )
        if seed_id in identifiers or value in values:
            raise ValueError("seed identifiers and values must be unique")
        identifiers.add(seed_id)
        values.add(value)
    return payload


def create_batch_dir(
    *,
    output_root: Path,
    participant_id: str,
) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", participant_id):
        raise ValueError(
            "participant_id must use 1-40 letters, numbers, '_' or '-'"
        )
    output_root.mkdir(parents=True, exist_ok=True)
    base = (
        f"private_eval_{participant_id}_"
        + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    )
    candidate = output_root / base
    suffix = 1
    while candidate.exists():
        candidate = output_root / f"{base}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=False, exist_ok=False)
    return candidate


def run_private_batch(
    *,
    participant_id: str,
    manifest_path: Path,
    seeds_path: Path,
    output_root: Path,
    days: int,
    mode: str,
) -> tuple[Path, dict[str, Any]]:
    if mode not in {"qa", "final"}:
        raise ValueError("mode must be qa or final")
    if mode == "final" and days != TOTAL_DAYS:
        raise ValueError("final evaluation must run exactly 60 days")
    if mode == "qa" and not 1 <= days <= 7:
        raise ValueError("QA evaluation must run from 1 to 7 days")

    manifest_path = manifest_path.resolve()
    seeds_path = seeds_path.resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"agent manifest not found: {manifest_path}"
        )
    if not seeds_path.is_file():
        raise FileNotFoundError(
            f"private seed set not found: {seeds_path}"
        )

    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("agent manifest must be a JSON object")
    seed_set = load_seed_set(seeds_path)
    seed_set_hash = sha256_file(seeds_path)
    source_hash = hash_agent_code(manifest_path, manifest)

    red_config = read_json(
        MODULE_DIR
        / "private_config"
        / "red_origin_one_v1.json"
    )
    red_config = deepcopy(red_config)
    red_config["days_per_cycle"] = int(days)
    red_config["success_criteria"][
        "minimum_days_completed"
    ] = int(days)
    catalog_payload = read_json(
        MODULE_DIR / "private_config" / "internal_catalog.json"
    )
    shop_payload = read_json(
        MODULE_DIR / "private_config" / "internal_shops.json"
    )
    products = {
        product["id"]: product
        for product in catalog_payload["products"]
    }
    shops = {
        shop["id"]: shop for shop in shop_payload["shops"]
    }

    batch_dir = create_batch_dir(
        output_root=output_root,
        participant_id=participant_id,
    )
    progress_log = batch_dir / "batch_progress.jsonl"
    progress_log.touch(exist_ok=False)
    print(f"Private batch: {batch_dir}")
    print(f"Live batch progress: {progress_log}")

    write_json(
        batch_dir / "participant_manifest_snapshot.json",
        manifest,
    )
    write_json(
        batch_dir / "private_seed_set_snapshot.json",
        seed_set,
    )
    write_json(
        batch_dir / "private_red_config_snapshot.json",
        red_config,
    )
    state = {
        "status": "running",
        "mode": mode,
        "participant_id": participant_id,
        "private_red_version": PRIVATE_RED_VERSION,
        "days_per_seed": days,
        "seed_count": EXPECTED_SEEDS,
        "seed_set_sha256": seed_set_hash,
        "participant_source_sha256": source_hash,
        "created_at": datetime.now().astimezone().isoformat(),
        "seeds_completed": 0,
    }
    write_json(batch_dir / "run_state.json", state)
    seed_summaries: list[dict[str, Any]] = []

    try:
        for index, seed_record in enumerate(
            seed_set["seeds"],
            start=1,
        ):
            current_hash = hash_agent_code(
                manifest_path,
                manifest,
            )
            if current_hash != source_hash:
                raise RuntimeError(
                    "participant source changed before a seed started"
                )

            seed_dir = batch_dir / f"seed_{index:02d}"
            seed_dir.mkdir(exist_ok=False)
            append_jsonl(
                progress_log,
                {
                    "seed_index": index,
                    "seed_id": seed_record["seed_id"],
                    "status": "running",
                    "started_at": (
                        datetime.now().astimezone().isoformat()
                    ),
                },
            )
            scenario = generate_crucible_scenario(
                cycle=1,
                seed=int(seed_record["value"]),
                days=TOTAL_DAYS,
                red_config=red_config,
                products=products,
                shops=shops,
            )
            policy = CommandAgentPolicy(
                manifest_path=manifest_path,
                stage_dir=seed_dir,
                context_limit=CONTEXT_LIMIT,
                memory_dir_override=seed_dir / "agent_memory",
            )
            print(
                f"Seed {index}/{EXPECTED_SEEDS} agent log: "
                f"{seed_dir / 'agent_execution.jsonl'}"
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
                    "version": manifest.get(
                        "agent_version", "unspecified"
                    ),
                    "context_limit": CONTEXT_LIMIT,
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
            full_summary = simulator.run(policy, seed_dir)

            after_hash = hash_agent_code(
                manifest_path,
                manifest,
            )
            if after_hash != source_hash:
                raise RuntimeError(
                    "participant source changed during a frozen seed"
                )

            compact = compact_seed_summary(
                seed_id=seed_record["seed_id"],
                requested_days=days,
                summary=full_summary,
            )
            write_json(
                seed_dir / "generated_scenario.private.json",
                scenario,
            )
            write_json(
                seed_dir / "private_summary.json",
                full_summary,
            )
            write_json(
                seed_dir / "summary.json",
                compact,
                overwrite=True,
            )
            write_json(
                seed_dir / "seed_record.json",
                {
                    "seed_index": index,
                    "seed_id": seed_record["seed_id"],
                    "seed_value": int(seed_record["value"]),
                    "source_sha256_before": current_hash,
                    "source_sha256_after": after_hash,
                    "source_frozen": True,
                    "summary": compact,
                },
            )
            seed_summaries.append(compact)
            state["seeds_completed"] = index
            state["updated_at"] = (
                datetime.now().astimezone().isoformat()
            )
            write_json(
                batch_dir / "run_state.json",
                state,
                overwrite=True,
            )
            append_jsonl(
                progress_log,
                {
                    "seed_index": index,
                    "seed_id": seed_record["seed_id"],
                    "status": "completed",
                    "days_completed": compact["days_completed"],
                    "bankrupt": compact["bankrupt"],
                    "liquidation_profit": compact[
                        "liquidation_profit"
                    ],
                    "completed_at": (
                        datetime.now().astimezone().isoformat()
                    ),
                },
            )

        batch_summary = {
            "status": "completed",
            "mode": mode,
            "participant_id": participant_id,
            "private_red_version": PRIVATE_RED_VERSION,
            "days_per_seed": days,
            "seed_count": EXPECTED_SEEDS,
            "seed_set_sha256": seed_set_hash,
            "private_red_config_sha256": sha256_json(red_config),
            "participant_source_sha256": source_hash,
            "source_frozen_across_batch": True,
            "seed_results": seed_summaries,
            "metric_summary": aggregate_metrics(seed_summaries),
            "official_score": None,
            "score_notice": (
                "Official point calculation is performed by Module 7."
            ),
        }
        write_json(
            batch_dir / "batch_summary.json",
            batch_summary,
        )
        state["status"] = "completed"
        state["completed_at"] = (
            datetime.now().astimezone().isoformat()
        )
        write_json(
            batch_dir / "run_state.json",
            state,
            overwrite=True,
        )
        return batch_dir, batch_summary
    except Exception as exc:
        state["status"] = "failed"
        state["error"] = str(exc)
        state["traceback"] = traceback.format_exc()
        state["updated_at"] = (
            datetime.now().astimezone().isoformat()
        )
        write_json(
            batch_dir / "run_state.json",
            state,
            overwrite=True,
        )
        append_jsonl(
            progress_log,
            {
                "status": "failed",
                "error": str(exc),
                "failed_at": datetime.now().astimezone().isoformat(),
            },
        )
        raise


def compact_seed_summary(
    *,
    seed_id: str,
    requested_days: int,
    summary: dict[str, Any],
) -> dict[str, Any]:
    closed_shop_days = int(summary.get("closed_shop_days", 0))
    return {
        "seed_id": seed_id,
        "days_requested": requested_days,
        "days_completed": int(summary["days_completed"]),
        "completed": int(summary["days_completed"]) == requested_days,
        "bankrupt": bool(summary["bankrupt"]),
        "ending_cash": float(summary["ending_cash"]),
        "accounting_profit": float(summary["accounting_profit"]),
        "liquidation_profit": float(
            summary.get(
                "liquidation_profit",
                summary["accounting_profit"],
            )
        ),
        "service_rate": float(summary["service_rate"]),
        "service_rate_by_shop": summary[
            "service_rate_by_shop"
        ],
        "minimum_shop_service_rate": float(
            summary["minimum_shop_service_rate"]
        ),
        "spoilage_rate": float(summary["spoilage_rate"]),
        "forced_closure_count": int(
            summary.get("forced_closure_count", 0)
        ),
        "closed_shop_days": closed_shop_days,
        "open_shop_days": (
            requested_days * len(summary["service_rate_by_shop"])
            - closed_shop_days
        ),
        "fallback_days": int(summary.get("fallback_days", 0)),
        "failed_seed": (
            bool(summary["bankrupt"])
            or int(summary["days_completed"]) != requested_days
        ),
    }


def aggregate_metrics(
    seeds: list[dict[str, Any]],
) -> dict[str, Any]:
    def average(field: str) -> float:
        return round(
            sum(float(seed[field]) for seed in seeds) / len(seeds),
            4,
        )

    return {
        "average_liquidation_profit": average(
            "liquidation_profit"
        ),
        "worst_liquidation_profit": min(
            seed["liquidation_profit"] for seed in seeds
        ),
        "average_service_rate": average("service_rate"),
        "worst_service_rate": min(
            seed["service_rate"] for seed in seeds
        ),
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
        "any_failed_seed": any(
            seed["failed_seed"] for seed in seeds
        ),
    }
