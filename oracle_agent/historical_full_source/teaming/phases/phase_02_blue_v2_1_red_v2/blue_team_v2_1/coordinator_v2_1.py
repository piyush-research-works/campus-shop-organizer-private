from __future__ import annotations

from pathlib import Path
from typing import Any

from analyst_v2_1 import DemandAnalystV21
from contracts import Decision
from io_utils import append_jsonl, write_json
from manager_agent import CodexManagerAgent
from memory import CompactMemory
from operations_v2_1 import OperationsRiskAgentV21


class BlueTeamV21:
    name = "blue_team_v2_1"

    def __init__(
        self,
        *,
        analyst: DemandAnalystV21,
        manager: CodexManagerAgent,
        operations: OperationsRiskAgentV21,
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
            source = "fallback:blue_v2_1_manager"
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
            },
        )
        append_jsonl(
            self.artifacts_dir / "compact_memory.jsonl",
            {"day": day, **memory},
        )
        append_jsonl(
            self.artifacts_dir / "live_progress.jsonl",
            {
                "day": day,
                "source": source,
                "risk": dashboard["risk_state"],
                "cash": observation["shared_cash"],
                "posture": manager_plan["cash_posture"],
                "shop_modes": manager_plan["shop_modes"],
                "order_cost": operations["order_cost"],
                "candidate_packs": operations["candidate_packs"],
                "accepted_packs": operations["accepted_packs"],
                "rejected_packs": operations["rejected_packs"],
            },
        )
        return decision
