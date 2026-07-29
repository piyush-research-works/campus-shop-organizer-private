from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "official_runner"))
sys.path.insert(0, str(ROOT / "official_scoring"))

from private_evaluator.run import load_seed_set  # noqa: E402
from scoring.formula import score_participant  # noqa: E402


class PrivateRepositoryTests(unittest.TestCase):
    def test_final_seed_set_has_three_unique_seeds(self) -> None:
        path = (
            ROOT
            / "official_runner"
            / "private_inputs"
            / "final_seeds.json"
        )
        payload = load_seed_set(path)
        values = [item["value"] for item in payload["seeds"]]
        self.assertEqual(len(values), 3)
        self.assertEqual(len(set(values)), 3)

    def test_oracle_manifest_uses_contract_adapter(self) -> None:
        manifest = json.loads(
            (
                ROOT
                / "oracle_agent"
                / "runnable"
                / "agent_manifest.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["agent_name"], "oracle_blue_v2_7_anchor_first"
        )

    def test_official_weighting_uses_average_and_worst(self) -> None:
        seed = {
            "days_requested": 60,
            "days_completed": 60,
            "completed": True,
            "failed_seed": False,
            "bankrupt": False,
            "liquidation_profit": 0,
            "service_rate": 0,
            "minimum_shop_service_rate": 0,
            "spoilage_rate": 1,
            "open_shop_days": 0,
            "forced_closure_count": 0,
            "fallback_days": 0,
        }
        batch = {
            "seed_results": [
                {"seed_id": "one", **seed},
                {"seed_id": "two", **seed},
                {"seed_id": "three", **seed},
            ]
        }
        result = score_participant(
            "qa",
            batch,
            {
                "design_and_code": 0,
                "final_explanation": 0,
            },
        )
        self.assertEqual(result["final_score"], 0.0)


if __name__ == "__main__":
    unittest.main()

