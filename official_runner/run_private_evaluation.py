"""Command-line entry point for organizer-only private evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path

from private_evaluator.run import run_private_batch


MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_FINAL_SEEDS = (
    MODULE_DIR / "private_inputs" / "final_seeds.json"
)
DEFAULT_QA_SEEDS = MODULE_DIR / "private_seed_set.example.json"
DEFAULT_OUTPUT_ROOT = MODULE_DIR / "runs"


def add_common(
    parser: argparse.ArgumentParser,
    *,
    default_seeds: Path,
) -> None:
    parser.add_argument("--participant-id", required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--seeds-file",
        type=Path,
        default=default_seeds,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a private Campus Shop Agent evaluation"
    )
    subparsers = parser.add_subparsers(
        dest="mode",
        required=True,
    )
    final = subparsers.add_parser(
        "final",
        help="Run the official three-seed, 60-day evaluation.",
    )
    add_common(final, default_seeds=DEFAULT_FINAL_SEEDS)

    qa = subparsers.add_parser(
        "qa",
        help="Run a 1-7 day organizer QA batch.",
    )
    add_common(qa, default_seeds=DEFAULT_QA_SEEDS)
    qa.add_argument(
        "--days",
        type=int,
        default=2,
        choices=range(1, 8),
        metavar="{1,2,3,4,5,6,7}",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    days = 60 if args.mode == "final" else int(args.days)
    batch_dir, summary = run_private_batch(
        participant_id=args.participant_id,
        manifest_path=args.manifest,
        seeds_path=args.seeds_file,
        output_root=args.output_root,
        days=days,
        mode=args.mode,
    )
    print("Private evaluation completed.")
    print(
        "Failed seed: "
        f"{summary['metric_summary']['any_failed_seed']}"
    )
    print(f"Summary: {batch_dir / 'batch_summary.json'}")


if __name__ == "__main__":
    main()
