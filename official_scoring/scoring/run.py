from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .formula import ranking_key, score_participant


MODULE_ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_batch(batch: dict[str, Any], participant_id: str) -> None:
    problems: list[str] = []
    if batch.get("status") != "completed":
        problems.append("status must be completed")
    if batch.get("mode") != "final":
        problems.append("mode must be final")
    if batch.get("participant_id") != participant_id:
        problems.append("participant_id does not match the manifest")
    if batch.get("days_per_seed") != 60:
        problems.append("days_per_seed must be 60")
    if batch.get("seed_count") != 3:
        problems.append("seed_count must be 3")
    if batch.get("source_frozen_across_batch") is not True:
        problems.append("participant source was not frozen")
    seeds = batch.get("seed_results")
    if not isinstance(seeds, list) or len(seeds) != 3:
        problems.append("exactly three seed results are required")
    if problems:
        raise ValueError(f"{participant_id}: " + "; ".join(problems))


def _unique_result_dir(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    result_dir = root / f"results_{stamp}"
    result_dir.mkdir(parents=True, exist_ok=False)
    return result_dir


def _write_csv(path: Path, ranked: list[dict[str, Any]]) -> None:
    fields = [
        "rank",
        "participant_id",
        "final_score",
        "simulator_score",
        "human_review_score",
        "worst_seed_score",
        "average_seed_score",
        "total_liquidation_profit",
        "fallback_days",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for position, item in enumerate(ranked, start=1):
            writer.writerow(
                {
                    "rank": position,
                    "participant_id": item["participant_id"],
                    "final_score": item["final_score"],
                    "simulator_score": item["simulator_score"],
                    "human_review_score": item["human_review"]["total"],
                    "worst_seed_score": item["worst_seed_score"],
                    "average_seed_score": item["average_seed_score"],
                    "total_liquidation_profit": item["total_liquidation_profit"],
                    "fallback_days": item["fallback_days"],
                }
            )


def _public_rows(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "rank": rank,
            "participant_id": item["participant_id"],
            "final_score": item["final_score"],
            "simulator_score": item["simulator_score"],
            "human_review_score": item["human_review"]["total"],
            "worst_seed_score": item["worst_seed_score"],
            "total_liquidation_profit": item["total_liquidation_profit"],
            "fallback_days": item["fallback_days"],
        }
        for rank, item in enumerate(ranked, start=1)
    ]


def _write_summary(
    path: Path, event_id: str, rows: list[dict[str, Any]]
) -> None:
    lines = [
        f"# Results - {event_id}",
        "",
        "| Rank | Participant | Final /100 | Simulator /90 | Human /10 |",
        "|---:|---|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['rank']} | {row['participant_id']} | "
            f"{row['final_score']:.2f} | {row['simulator_score']:.2f} | "
            f"{row['human_review_score']:.2f} |"
        )
    lines.extend(
        [
            "",
            "Tie-breaks: worst-seed score, total liquidation profit, fewer "
            "fallback days, then participant ID.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def score_manifest(manifest_path: Path, output_root: Path) -> Path:
    manifest_path = manifest_path.resolve()
    manifest = _load_json(manifest_path)
    event_id = str(manifest.get("event_id", "")).strip()
    entries = manifest.get("participants")
    if not event_id:
        raise ValueError("event_id is required")
    if not isinstance(entries, list) or not entries:
        raise ValueError("participants must be a non-empty list")

    seen: set[str] = set()
    scored: list[dict[str, Any]] = []
    input_hashes: dict[str, Any] = {
        "scoring_manifest_sha256": _sha256(manifest_path),
        "batch_summaries": {},
    }
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Every participant entry must be an object")
        participant_id = str(entry.get("participant_id", "")).strip()
        if not participant_id or participant_id in seen:
            raise ValueError(f"Invalid or duplicate participant_id: {participant_id!r}")
        seen.add(participant_id)
        batch_path = Path(str(entry.get("batch_summary", ""))).expanduser().resolve()
        batch = _load_json(batch_path)
        _validate_batch(batch, participant_id)
        scored_item = score_participant(
            participant_id, batch, entry.get("human_review", {})
        )
        scored_item["batch_summary"] = str(batch_path)
        scored_item["batch_summary_sha256"] = _sha256(batch_path)
        scored.append(scored_item)
        input_hashes["batch_summaries"][participant_id] = {
            "path": str(batch_path),
            "sha256": scored_item["batch_summary_sha256"],
        }

    ranked = sorted(scored, key=ranking_key)
    result_dir = _unique_result_dir(output_root.resolve())
    snapshot = dict(manifest)
    snapshot["source_manifest"] = str(manifest_path)
    (result_dir / "scoring_manifest_snapshot.json").write_text(
        json.dumps(snapshot, indent=2) + "\n", encoding="utf-8"
    )
    (result_dir / "input_hashes.json").write_text(
        json.dumps(input_hashes, indent=2) + "\n", encoding="utf-8"
    )
    details = {
        "event_id": event_id,
        "generated_at": datetime.now().astimezone().isoformat(),
        "formula_version": "campus_shop_v1",
        "participant_count": len(ranked),
        "ranked_results": [
            {"rank": rank, **item} for rank, item in enumerate(ranked, start=1)
        ],
    }
    (result_dir / "detailed_scores.json").write_text(
        json.dumps(details, indent=2) + "\n", encoding="utf-8"
    )
    public_rows = _public_rows(ranked)
    (result_dir / "leaderboard.json").write_text(
        json.dumps(
            {
                "event_id": event_id,
                "generated_at": details["generated_at"],
                "leaderboard": public_rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_csv(result_dir / "leaderboard.csv", ranked)
    _write_summary(result_dir / "results_summary.md", event_id, public_rows)
    return result_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score private evaluation batches.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--output-root", type=Path, default=MODULE_ROOT / "results"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result_dir = score_manifest(args.manifest, args.output_root)
    except ValueError as exc:
        print(f"SCORING FAILED: {exc}")
        return 1
    print("SCORING COMPLETE")
    print(f"Results: {result_dir}")
    return 0

