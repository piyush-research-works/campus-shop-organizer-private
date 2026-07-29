from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


MODEL = "gpt-5.4-mini"
REASONING_EFFORT = "low"
TOKEN_LIMIT = 3800


def fallback_plan(context: dict[str, Any]) -> dict[str, Any]:
    cash = float(context["cash"])
    rent = max(1.0, float(context["daily_rent"]))
    runway = cash / rent
    posture = "lean" if runway < 6 else "balanced"
    hints = context.get("signals", {}).get("footfall_hint", {})
    shop_focus = {}
    for shop in context["shops"]:
        hint = str(hints.get(shop, "normal")).lower()
        shop_focus[shop] = (
            "service" if "high" in hint and posture != "lean" else posture
        )
    return {
        "cash_posture": posture,
        "shop_focus": shop_focus,
        "reasoning": "Deterministic fallback based on cash runway and signals.",
    }


def _usage(events: str) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0}
    found = False
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "turn.completed":
            continue
        found = True
        usage = event.get("usage") or {}
        for field in totals:
            totals[field] += int(usage.get(field, 0))
    if not found:
        return totals
    return totals


def choose_plan(
    context: dict[str, Any], memory_dir: Path
) -> tuple[dict[str, Any], dict[str, int], str]:
    configured = os.environ.get("CODEX_COMMAND", "").strip()
    executable = shutil.which(configured) if configured else None
    if executable is None:
        return fallback_plan(context), {
            "llm_calls": 0,
            "context_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }, "deterministic_fallback"

    day = int(context["day"])
    session = memory_dir / "sessions" / f"day_{day:03d}"
    session.mkdir(parents=True, exist_ok=True)
    output = session / "manager_plan.json"
    prompt = (
        "You manage three campus shops. Use only the supplied public facts. "
        "Protect rent cash, react to local signals, and avoid excess fresh "
        "stock. Select a cash posture and shop focus. Return schema-valid "
        "JSON with brief reasoning.\n"
        + json.dumps(context, separators=(",", ":"), ensure_ascii=False)
    )
    command = [
        executable,
        "-c",
        f'model_reasoning_effort="{REASONING_EFFORT}"',
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--ignore-user-config",
        "--json",
        "--output-schema",
        str(Path(__file__).with_name("manager_plan.schema.json")),
        "--output-last-message",
        str(output),
        "--model",
        MODEL,
        "-",
    ]
    completed = subprocess.run(
        command,
        cwd=session,
        input=prompt,
        text=True,
        capture_output=True,
        timeout=150,
        check=False,
    )
    (session / "codex_events.jsonl").write_text(
        completed.stdout, encoding="utf-8"
    )
    (session / "stderr.log").write_text(
        completed.stderr, encoding="utf-8"
    )
    usage = _usage(completed.stdout)
    telemetry = {
        "llm_calls": 1,
        "context_tokens": usage["input_tokens"] + usage["output_tokens"],
        **usage,
    }
    if (
        completed.returncode != 0
        or not output.exists()
        or telemetry["context_tokens"] > TOKEN_LIMIT
    ):
        return fallback_plan(context), telemetry, "codex_failed_fallback"
    plan = json.loads(output.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        return fallback_plan(context), telemetry, "codex_invalid_fallback"
    return plan, telemetry, f"codex:{MODEL}"

