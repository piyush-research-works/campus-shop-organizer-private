from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from context_builder import build_context
from llm_manager import choose_plan
from memory import load_memory, save_memory
from policy import build_action


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Observation must be a JSON object.")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--action", type=Path, required=True)
    parser.add_argument("--memory-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    observation = read_object(args.observation)
    memory = load_memory(args.memory_dir)
    context = build_context(observation)
    plan, telemetry, source = choose_plan(context, args.memory_dir)
    action = build_action(observation, plan, telemetry, source)
    args.action.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.action.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(action, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(args.action)
    save_memory(
        args.memory_dir,
        memory,
        day=int(observation["day"]),
        plan=plan,
    )


if __name__ == "__main__":
    main()

