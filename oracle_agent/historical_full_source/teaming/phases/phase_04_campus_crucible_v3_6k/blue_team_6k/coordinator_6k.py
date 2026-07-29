from __future__ import annotations

from pathlib import Path
from typing import Any

from analyst_6k import DemandAnalyst6K
from budgeted_manager import BudgetedCodexManagerAgent
from contracts import Decision
from io_utils import append_jsonl, write_json
from memory import CompactMemory
from operations_6k import OperationsRiskAgent6K


class BlueTeam6K:
    name = "blue_team_v2_3_context_6k"

    def __init__(
        self,
        *,
        analyst: DemandAnalyst6K,
        manager: BudgetedCodexManagerAgent,
        operations: OperationsRiskAgent6K,
        memory: CompactMemory,
        artifacts_dir: Path,
    ) -> None:
        self.analyst = analyst
        self.manager = manager
        self.operations = operations
        self.memory = memory
        self.artifacts_dir = artifacts_dir

    def decide(self, observation: dict[str, Any]) -> Decision:
        day = int(observation["day"])
        memory = self.memory.update(observation)
        dashboard = self.analyst.analyze(observation, memory)
        source = "codex:gpt-5.4-mini"
        error = None
        try:
            manager_plan, source = self.manager.decide(dashboard)
        except Exception as exc:
            manager_plan = self.manager.fallback(dashboard)
            source = "fallback:blue_6k_manager"
            error = str(exc)
        decision, operations = self.operations.execute(
            observation=observation,
            dashboard=dashboard,
            manager_plan=manager_plan,
            source=source,
        )
        write_json(
            self.artifacts_dir
            / "analyst_dashboards"
            / f"day_{day:03d}.json",
            dashboard,
        )
        write_json(
            self.artifacts_dir
            / "manager_plans"
            / f"day_{day:03d}.json",
            {**manager_plan, "source": source, "error": error},
        )
        write_json(
            self.artifacts_dir
            / "operations_plans"
            / f"day_{day:03d}.json",
            {
                **operations,
                "orders": decision.orders,
                "prices": decision.prices,
                "shop_actions": getattr(
                    decision, "shop_actions", []
                ),
            },
        )
        append_jsonl(
            self.artifacts_dir / "compact_memory.jsonl",
            {"day": day, **memory},
        )
        context = self.manager.last_manifest
        append_jsonl(
            self.artifacts_dir / "live_progress.jsonl",
            {
                "day": day,
                "source": source,
                "risk": dashboard["risk_state"],
                "cash": observation["shared_cash"],
                "open_shops": [
                    shop
                    for shop, state in observation.get(
                        "shop_status", {}
                    ).items()
                    if state["open"]
                ],
                "posture": manager_plan["cash_posture"],
                "order_cost": operations["order_cost"],
                "transfer_units": operations["transfers"]["units"],
                "critical_packs": operations[
                    "accepted_critical_packs"
                ],
                "shop_actions": getattr(
                    decision, "shop_actions", []
                ),
                "context_tokens": context.get(
                    "estimated_prompt_tokens"
                ),
                "context_limit": context.get("token_limit"),
                "context_passed": context.get(
                    "hard_limit_passed", False
                ),
            },
        )
        return decision
