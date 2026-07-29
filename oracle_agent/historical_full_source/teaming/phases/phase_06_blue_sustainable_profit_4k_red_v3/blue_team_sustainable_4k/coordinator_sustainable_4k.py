from __future__ import annotations

from pathlib import Path
from typing import Any

from contracts import Decision
from io_utils import append_jsonl, write_json
from memory import CompactMemory

from analyst_sustainable_4k import SustainableDemandAnalyst4K
from financial_controller_sustainable import (
    SustainableFinancialController,
)
from manager_sustainable_4k import SustainableCodexManager4K
from operations_sustainable_4k import SustainableOperations4K


class BlueSustainableProfit4K:
    name = "blue_v2_5_sustainable_profit_4k"

    def __init__(
        self,
        *,
        analyst: SustainableDemandAnalyst4K,
        manager: SustainableCodexManager4K,
        finance: SustainableFinancialController,
        operations: SustainableOperations4K,
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
            source = "fallback:blue_sustainable_profit_4k"
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
                "anchor_shop": guardrails["anchor_shop"],
                "active_shops": guardrails["active_shops_after"],
                "reopening_escrow": guardrails["reopening_escrow"],
                "posture": manager_plan["cash_posture"],
                "enforced_posture": guardrails[
                    "force_cash_posture"
                ],
                "order_cost": operations["order_cost"],
                "order_budget": operations["order_budget"],
                "survival_order_floor": operations[
                    "survival_order_floor"
                ],
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
