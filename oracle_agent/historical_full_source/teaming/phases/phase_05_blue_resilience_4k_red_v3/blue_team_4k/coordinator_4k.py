from __future__ import annotations

from pathlib import Path
from typing import Any

from analyst_4k import ResilienceAnalyst4K
from contracts import Decision
from financial_controller import FinancialRiskController
from io_utils import append_jsonl, write_json
from manager_4k import ResilientCodexManager4K
from memory import CompactMemory
from operations_4k import ResilientOperations4K


class BlueResilience4K:
    name = "blue_v2_4_resilience_4k"

    def __init__(
        self,
        *,
        analyst: ResilienceAnalyst4K,
        manager: ResilientCodexManager4K,
        finance: FinancialRiskController,
        operations: ResilientOperations4K,
        memory: CompactMemory,
        artifacts_dir: Path,
    ) -> None:
        self.analyst = analyst
        self.manager = manager
        self.finance = finance
        self.operations = operations
        self.memory = memory
        self.artifacts_dir = artifacts_dir

    def decide(self, observation: dict[str, Any]) -> Decision:
        day = int(observation["day"])
        memory = self.memory.update(observation)
        dashboard = self.analyst.analyze(observation, memory)
        guardrails = self.finance.evaluate(observation, dashboard)
        dashboard["operational_guardrails"] = guardrails
        source = "codex:gpt-5.4-mini"
        error = None
        try:
            manager_plan, source = self.manager.decide(dashboard)
        except Exception as exc:
            manager_plan = self.manager.fallback(dashboard)
            source = "fallback:blue_resilience_4k"
            error = str(exc)
        decision, operations = self.operations.execute(
            observation=observation,
            dashboard=dashboard,
            manager_plan=manager_plan,
            guardrails=guardrails,
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
                "shop_actions": decision.shop_actions,
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
                "event_severity": dashboard["event_risk"]["severity"],
                "cash": observation["shared_cash"],
                "cash_runway_days": guardrails["cash_runway_days"],
                "open_shops": guardrails["open_shops_before"],
                "posture": manager_plan["cash_posture"],
                "enforced_posture": guardrails[
                    "force_cash_posture"
                ],
                "order_cost": operations["order_cost"],
                "safe_order_budget": operations["order_budget"],
                "shop_actions": decision.shop_actions,
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
