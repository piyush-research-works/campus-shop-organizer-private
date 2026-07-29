from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from io_utils import append_jsonl, write_json


class CodexManagerAgent:
    """One strategic model call; arithmetic is delegated to operations."""

    def __init__(
        self,
        *,
        schema_path: Path,
        sessions_dir: Path,
        token_log_path: Path,
        codex_command: str,
        model: str,
        reasoning_effort: str,
        timeout_seconds: int,
    ) -> None:
        self.schema_path = schema_path.resolve()
        self.sessions_dir = sessions_dir.resolve()
        self.token_log_path = token_log_path.resolve()
        self.codex_command = codex_command
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def decide(
        self, dashboard: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        day = int(dashboard["day"])
        session = self.sessions_dir / f"day_{day:03d}"
        session.mkdir(exist_ok=False)
        prompt = self._prompt(dashboard)
        write_json(session / "dashboard.json", dashboard)
        (session / "prompt.txt").write_text(prompt, encoding="utf-8")
        executable = shutil.which(self.codex_command)
        if executable is None:
            raise RuntimeError(
                f"Codex command not found: {self.codex_command}"
            )
        output = session / "manager_plan.json"
        command = [
            executable,
            "-c",
            f'model_reasoning_effort="{self.reasoning_effort}"',
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--ignore-user-config",
            "--json",
            "--output-schema",
            str(self.schema_path),
            "--output-last-message",
            str(output),
            "--model",
            self.model,
            "-",
        ]
        started = time.monotonic()
        completed = subprocess.run(
            command,
            cwd=session,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        (session / "codex_events.jsonl").write_text(
            completed.stdout, encoding="utf-8"
        )
        (session / "stderr.log").write_text(
            completed.stderr, encoding="utf-8"
        )
        usage = self._usage(completed.stdout)
        write_json(
            session / "session.json",
            {
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "return_code": completed.returncode,
                "duration_seconds": round(
                    time.monotonic() - started, 3
                ),
                "usage": usage,
            },
        )
        if usage:
            append_jsonl(
                self.token_log_path,
                {
                    "day": day,
                    "model": self.model,
                    "reasoning_effort": self.reasoning_effort,
                    "return_code": completed.returncode,
                    **usage,
                },
            )
        if completed.returncode != 0 or not output.exists():
            raise RuntimeError(
                f"Manager call failed: {completed.stderr.strip()}"
            )
        return read_json(output), f"codex:{self.model}"

    @staticmethod
    def fallback(dashboard: dict[str, Any]) -> dict[str, Any]:
        mode = (
            "protect"
            if dashboard["risk_state"] != "normal"
            or dashboard["memory"]["cash_trend"] < -500
            else "balanced"
        )
        return {
            "cash_posture": mode,
            "shop_modes": {
                shop: (
                    "clear_fresh"
                    if dashboard["risk_state"] != "normal"
                    else mode
                )
                for shop in dashboard["products"]
            },
            "price_actions": [],
            "stock_actions": [],
            "reasoning": "Deterministic balanced fallback manager plan.",
        }

    @staticmethod
    def _prompt(dashboard: dict[str, Any]) -> str:
        return (
            "You are the strategic Manager Agent for three campus shops. "
            "The Demand Analyst already calculated forecasts, cover, margins, "
            "expiry and memory. Do not calculate exact order quantities. "
            "Choose a cash posture, one mode per shop, and only exceptional "
            "price or stock actions. Omitted products use the safe operations "
            "recommendation. Protect rent cash during warnings/downturns. "
            "Do not broadly discount; use discount mainly for expiring stock. "
            "Use high stock priority for repeated stockouts with good margin. "
            "Return JSON matching the schema and reasoning under 100 words.\n"
            + json.dumps(
                dashboard,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    @staticmethod
    def _usage(events: str) -> dict[str, int] | None:
        totals = {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
        }
        found = False
        for line in events.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "turn.completed":
                continue
            usage = event.get("usage") or {}
            found = True
            for field in totals:
                totals[field] += int(usage.get(field, 0))
        if not found:
            return None
        totals["noncached_input_tokens"] = max(
            0,
            totals["input_tokens"] - totals["cached_input_tokens"],
        )
        totals["total_tokens"] = (
            totals["input_tokens"] + totals["output_tokens"]
        )
        return totals


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
