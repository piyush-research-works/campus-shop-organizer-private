from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from blue_team.prompt import build_prompt
from contracts import Decision, Transfer
from io_utils import append_jsonl, write_json


class CodexBluePolicy:
    name = "blue_codex_agent"

    def __init__(
        self,
        *,
        schema_path: Path,
        sessions_dir: Path,
        token_log_path: Path,
        model: str,
        reasoning_effort: str,
        codex_command: str,
        timeout_seconds: int,
    ) -> None:
        self.schema_path = schema_path.resolve()
        self.sessions_dir = sessions_dir.resolve()
        self.token_log_path = token_log_path.resolve()
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.codex_command = codex_command
        self.timeout_seconds = timeout_seconds
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.token_log_path.parent.mkdir(parents=True, exist_ok=True)

    def decide(self, observation: dict[str, Any]) -> Decision:
        executable = shutil.which(self.codex_command)
        if executable is None:
            raise RuntimeError(
                f"Codex command not found: {self.codex_command}"
            )
        day = int(observation["day"])
        session_dir = self.sessions_dir / f"day_{day:03d}"
        session_dir.mkdir(exist_ok=False)
        prompt = build_prompt(observation)
        output_path = session_dir / "decision.json"
        write_json(session_dir / "observation.json", observation)
        (session_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
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
            str(output_path),
            "--model",
            self.model,
            "-",
        ]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=session_dir,
                text=True,
                capture_output=True,
                input=prompt,
                timeout=self.timeout_seconds,
                check=False,
            )
        except Exception as exc:
            stdout = self._exception_text(exc, "stdout")
            stderr = self._exception_text(exc, "stderr") or str(exc)
            (session_dir / "codex_events.jsonl").write_text(
                stdout, encoding="utf-8"
            )
            (session_dir / "stderr.log").write_text(stderr, encoding="utf-8")
            write_json(
                session_dir / "session.json",
                self._metadata(
                    return_code=None,
                    duration=time.monotonic() - started,
                    error=str(exc),
                    usage=self._usage(stdout),
                ),
            )
            raise

        (session_dir / "codex_events.jsonl").write_text(
            completed.stdout, encoding="utf-8"
        )
        (session_dir / "stderr.log").write_text(
            completed.stderr, encoding="utf-8"
        )
        usage = self._usage(completed.stdout)
        write_json(
            session_dir / "session.json",
            self._metadata(
                return_code=completed.returncode,
                duration=time.monotonic() - started,
                error=(
                    None
                    if completed.returncode == 0
                    else completed.stderr.strip()
                ),
                usage=usage,
            ),
        )
        if usage is not None:
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
        if completed.returncode != 0:
            raise RuntimeError(
                f"codex exec failed ({completed.returncode}): "
                f"{completed.stderr.strip()}"
            )
        if not output_path.exists():
            raise RuntimeError("Codex did not create a decision file.")
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        return Decision(
            orders={
                shop: {
                    item: int(quantity)
                    for item, quantity in items.items()
                }
                for shop, items in payload["orders"].items()
            },
            prices={
                shop: {
                    item: float(price)
                    for item, price in items.items()
                }
                for shop, items in payload["prices"].items()
            },
            transfers=[
                Transfer(
                    from_shop=item["from_shop"],
                    to_shop=item["to_shop"],
                    product=item["product"],
                    quantity=int(item["quantity"]),
                )
                for item in payload["transfers"]
            ],
            reasoning=payload.get("reasoning", ""),
            source=f"codex:{self.model}",
        )

    def _metadata(
        self,
        *,
        return_code: int | None,
        duration: float,
        error: str | None,
        usage: dict[str, int] | None,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "return_code": return_code,
            "duration_seconds": round(duration, 3),
            "error": error,
            "usage": usage,
        }

    @staticmethod
    def _usage(events: str) -> dict[str, int] | None:
        fields = {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
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
            usage = event.get("usage")
            if usage is None:
                continue
            found = True
            for field in fields:
                fields[field] += int(usage.get(field, 0))
        if not found:
            return None
        fields["noncached_input_tokens"] = max(
            0, fields["input_tokens"] - fields["cached_input_tokens"]
        )
        fields["total_tokens"] = (
            fields["input_tokens"] + fields["output_tokens"]
        )
        return fields

    @staticmethod
    def _exception_text(exc: Exception, attribute: str) -> str:
        value = getattr(exc, attribute, "") or ""
        return (
            value.decode("utf-8", errors="replace")
            if isinstance(value, bytes)
            else str(value)
        )
