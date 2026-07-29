from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contracts import Decision, Transfer
from io_utils import append_jsonl, read_json, write_json


@dataclass(frozen=True)
class ParticipantDecision(Decision):
    shop_actions: list[dict[str, str]] = field(default_factory=list)


class CommandAgentPolicy:
    """Calls one participant-owned process for each non-replayed day."""

    name = "participant_command_agent"

    def __init__(
        self,
        *,
        manifest_path: Path,
        stage_dir: Path,
        context_limit: int,
        memory_dir_override: Path | None = None,
        replay_decisions_dir: Path | None = None,
        replay_through_day: int = 0,
    ) -> None:
        self.manifest_path = manifest_path.resolve()
        self.manifest = read_json(self.manifest_path)
        self.stage_dir = stage_dir
        self.context_limit = int(context_limit)
        self.memory_dir_override = (
            memory_dir_override.resolve()
            if memory_dir_override is not None
            else None
        )
        self.replay_decisions_dir = replay_decisions_dir
        self.replay_through_day = int(replay_through_day)
        self.io_dir = stage_dir / "agent_io"
        self.io_dir.mkdir(parents=True, exist_ok=True)
        self.execution_log = stage_dir / "agent_execution.jsonl"
        self.execution_log.touch(exist_ok=False)

    def decide(self, observation: dict[str, Any]) -> ParticipantDecision:
        day = int(observation["day"])
        if (
            self.replay_decisions_dir is not None
            and day <= self.replay_through_day
        ):
            return self._replay(day)
        return self._invoke(day, observation)

    def _replay(self, day: int) -> ParticipantDecision:
        path = self.replay_decisions_dir / f"day_{day:03d}.json"
        payload = read_json(path)
        decision = self._decision_from_payload(
            payload,
            source="checkpoint_replay",
            accepted_actions_only=True,
        )
        append_jsonl(
            self.execution_log,
            {
                "day": day,
                "mode": "replay",
                "agent_called": False,
                "action_valid": True,
                "fallback_applied": False,
            },
        )
        return decision

    def _invoke(
        self,
        day: int,
        observation: dict[str, Any],
    ) -> ParticipantDecision:
        day_dir = self.io_dir / f"day_{day:03d}"
        day_dir.mkdir(parents=True, exist_ok=False)
        observation_path = day_dir / "observation.json"
        action_path = day_dir / "action.json"
        stdout_path = day_dir / "stdout.log"
        stderr_path = day_dir / "stderr.log"
        write_json(observation_path, observation)

        working_directory = self._working_directory()
        memory_dir = self._memory_directory()
        memory_dir.mkdir(parents=True, exist_ok=True)
        command = [
            self._expand(
                str(value),
                day=day,
                observation_path=observation_path,
                action_path=action_path,
                memory_dir=memory_dir,
                working_directory=working_directory,
            )
            for value in self.manifest["command"]
        ]
        timeout = int(self.manifest.get("timeout_seconds", 180))
        environment = os.environ.copy()
        environment.update(
            {
                "ORIGIN_ONE_DAY": str(day),
                "ORIGIN_ONE_CONTEXT_LIMIT": str(self.context_limit),
                "ORIGIN_ONE_OBSERVATION": str(observation_path),
                "ORIGIN_ONE_ACTION": str(action_path),
                "ORIGIN_ONE_MEMORY_DIR": str(memory_dir),
            }
        )
        for key, value in self.manifest.get("environment", {}).items():
            environment[str(key)] = str(value)

        started = time.perf_counter()
        exit_code: int | None = None
        issues: list[str] = []
        timed_out = False
        stdout = ""
        stderr = ""
        try:
            completed = subprocess.run(
                command,
                cwd=working_directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            if exit_code != 0:
                issues.append(f"agent exited with code {exit_code}")
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = self._as_text(exc.stdout)
            stderr = self._as_text(exc.stderr)
            issues.append(f"agent exceeded {timeout}-second timeout")
        except Exception as exc:
            issues.append(f"agent launch failed: {exc}")
        duration = round(time.perf_counter() - started, 6)
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")

        payload: dict[str, Any] | None = None
        if not issues:
            if not action_path.exists():
                issues.append("agent did not create action.json")
            else:
                try:
                    payload = json.loads(
                        action_path.read_text(encoding="utf-8")
                    )
                except Exception as exc:
                    issues.append(f"invalid action JSON: {exc}")
        telemetry: dict[str, Any] = {}
        if payload is not None:
            telemetry = payload.get("telemetry", {})
            issues.extend(
                self._validate_payload(payload, observation)
            )

        fallback = bool(issues)
        decision = (
            self._fallback(observation, issues)
            if fallback
            else self._decision_from_payload(
                payload or {},
                source="participant_agent",
            )
        )
        append_jsonl(
            self.execution_log,
            {
                "day": day,
                "mode": "agent",
                "agent_called": True,
                "agent_name": self.manifest.get(
                    "agent_name", "unnamed"
                ),
                "agent_version": self.manifest.get(
                    "agent_version", "unspecified"
                ),
                "command": command,
                "wall_time_seconds": duration,
                "timeout_seconds": timeout,
                "timed_out": timed_out,
                "exit_code": exit_code,
                "action_valid": not fallback,
                "fallback_applied": fallback,
                "issues": issues,
                "reported_telemetry": telemetry,
            },
        )
        return decision

    def _working_directory(self) -> Path:
        configured = self.manifest.get("working_directory", ".")
        path = Path(str(configured))
        if not path.is_absolute():
            path = self.manifest_path.parent / path
        path = path.resolve()
        if not path.is_dir():
            raise ValueError(
                f"Agent working directory does not exist: {path}"
            )
        return path

    def _memory_directory(self) -> Path:
        if self.memory_dir_override is not None:
            return self.memory_dir_override
        configured = self.manifest.get(
            "memory_directory", "memory"
        )
        path = Path(str(configured))
        if not path.is_absolute():
            path = self.manifest_path.parent / path
        return path.resolve()

    def _expand(
        self,
        value: str,
        *,
        day: int,
        observation_path: Path,
        action_path: Path,
        memory_dir: Path,
        working_directory: Path,
    ) -> str:
        replacements = {
            "{python}": sys.executable,
            "{day}": str(day),
            "{observation}": str(observation_path),
            "{action}": str(action_path),
            "{memory_dir}": str(memory_dir),
            "{working_directory}": str(working_directory),
            "{stage_dir}": str(self.stage_dir.resolve()),
        }
        for marker, replacement in replacements.items():
            value = value.replace(marker, replacement)
        return value

    def _validate_payload(
        self,
        payload: Any,
        observation: dict[str, Any],
    ) -> list[str]:
        if not isinstance(payload, dict):
            return ["action must be a JSON object"]
        allowed = {
            "orders",
            "prices",
            "transfers",
            "shop_actions",
            "reasoning",
            "telemetry",
        }
        issues = [
            f"unknown action field: {key}"
            for key in payload
            if key not in allowed
        ]
        shops = set(observation["shops"])
        products = set(observation["catalog"])
        orders = payload.get("orders")
        prices = payload.get("prices")
        if not isinstance(orders, dict) or set(orders) != shops:
            issues.append("orders must contain exactly all shops")
        if not isinstance(prices, dict) or set(prices) != shops:
            issues.append("prices must contain exactly all shops")
        if isinstance(orders, dict):
            for shop in shops:
                rows = orders.get(shop)
                if not isinstance(rows, dict) or set(rows) != products:
                    issues.append(
                        f"{shop}: orders must contain all products"
                    )
                    continue
                for product, quantity in rows.items():
                    if (
                        isinstance(quantity, bool)
                        or not isinstance(quantity, int)
                        or quantity < 0
                    ):
                        issues.append(
                            f"{shop}/{product}: invalid order quantity"
                        )
        if isinstance(prices, dict):
            for shop in shops:
                rows = prices.get(shop)
                if not isinstance(rows, dict) or set(rows) != products:
                    issues.append(
                        f"{shop}: prices must contain all products"
                    )
                    continue
                for product, price in rows.items():
                    if (
                        isinstance(price, bool)
                        or not isinstance(price, (int, float))
                        or not math.isfinite(float(price))
                        or float(price) <= 0
                    ):
                        issues.append(
                            f"{shop}/{product}: invalid price"
                        )
        transfers = payload.get("transfers", [])
        if not isinstance(transfers, list):
            issues.append("transfers must be a list")
        else:
            for index, transfer in enumerate(transfers):
                if (
                    not isinstance(transfer, dict)
                    or set(transfer)
                    != {
                        "from_shop",
                        "to_shop",
                        "product",
                        "quantity",
                    }
                    or transfer.get("from_shop") not in shops
                    or transfer.get("to_shop") not in shops
                    or transfer.get("from_shop")
                    == transfer.get("to_shop")
                    or transfer.get("product") not in products
                    or isinstance(transfer.get("quantity"), bool)
                    or not isinstance(transfer.get("quantity"), int)
                    or transfer.get("quantity", 0) <= 0
                ):
                    issues.append(f"transfer[{index}] is invalid")
        actions = payload.get("shop_actions", [])
        if not isinstance(actions, list):
            issues.append("shop_actions must be a list")
        else:
            for index, action in enumerate(actions):
                if (
                    not isinstance(action, dict)
                    or set(action) != {"shop", "action"}
                    or action.get("shop") not in shops
                    or action.get("action") not in {"close", "reopen"}
                ):
                    issues.append(f"shop_actions[{index}] is invalid")
        reasoning = payload.get("reasoning", "")
        if not isinstance(reasoning, str) or len(reasoning) > 2000:
            issues.append("reasoning must be a string of at most 2000 characters")
        telemetry = payload.get("telemetry")
        if not isinstance(telemetry, dict):
            issues.append("telemetry is required")
        else:
            calls = telemetry.get("llm_calls")
            context = telemetry.get("context_tokens")
            if (
                isinstance(calls, bool)
                or not isinstance(calls, int)
                or calls < 0
                or calls > 1
            ):
                issues.append("telemetry.llm_calls must be 0 or 1")
            if (
                isinstance(context, bool)
                or not isinstance(context, int)
                or context < 0
                or context > self.context_limit
            ):
                issues.append(
                    "telemetry.context_tokens exceeds the daily limit"
                )
        return issues

    @staticmethod
    def _decision_from_payload(
        payload: dict[str, Any],
        *,
        source: str,
        accepted_actions_only: bool = False,
    ) -> ParticipantDecision:
        transfers = [
            Transfer(
                from_shop=item["from_shop"],
                to_shop=item["to_shop"],
                product=item["product"],
                quantity=int(item["quantity"]),
            )
            for item in payload.get("transfers", [])
        ]
        actions = []
        for item in payload.get("shop_actions", []):
            if accepted_actions_only and not item.get("accepted", False):
                continue
            actions.append(
                {
                    "shop": item["shop"],
                    "action": item["action"],
                }
            )
        return ParticipantDecision(
            orders=payload["orders"],
            prices=payload["prices"],
            transfers=transfers,
            shop_actions=actions,
            reasoning=str(payload.get("reasoning", ""))[:2000],
            source=source,
        )

    @staticmethod
    def _fallback(
        observation: dict[str, Any],
        issues: list[str],
    ) -> ParticipantDecision:
        products = list(observation["catalog"])
        return ParticipantDecision(
            orders={
                shop: {product: 0 for product in products}
                for shop in observation["shops"]
            },
            prices={
                shop: {
                    product: float(price)
                    for product, price in details[
                        "current_prices"
                    ].items()
                }
                for shop, details in observation["shops"].items()
            },
            transfers=[],
            shop_actions=[],
            reasoning="Fallback: " + "; ".join(issues)[:1800],
            source="safe_fallback",
        )

    @staticmethod
    def _as_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
