from __future__ import annotations

import json
from typing import Any


def build_prompt(observation: dict[str, Any]) -> str:
    return (
        "You are the Blue Team manager of three campus shops. Return one "
        "structured daily decision for all shops. Your objective is to finish "
        "90 days with positive net worth while maintaining service across every "
        "location.\n\n"
        "Important rules:\n"
        "- Return only JSON matching the supplied schema.\n"
        "- Include all eight products for all three shops in orders and prices.\n"
        "- Orders are unit quantities and should be multiples of pack size.\n"
        "- Day 1 opening orders arrive before sales. Later orders take two days "
        "and may be delayed one extra day.\n"
        "- Pending deliveries are already paid for.\n"
        "- Rent is paid daily even when demand is low.\n"
        "- Exact elasticity and customer price cutoffs are hidden. A high price "
        "can reduce demand sharply or produce zero purchases.\n"
        "- If inventory remains but sales collapse after a price increase, "
        "consider lowering the price.\n"
        "- Demand buildups may be followed by festivals, breaks, weather, or "
        "location-specific collapses. Use public hints and protect fresh stock.\n"
        "- Milk, bread, and bananas expire quickly. Discount or reduce orders "
        "when a low-demand period may be approaching.\n"
        "- Do not memorize day numbers; each cycle uses a new hidden scenario.\n"
        "- Keep enough cash for rent and delivery lead time.\n"
        "- Use transfers only when the expected saved sales justify the cost.\n"
        "- Do not inspect files or run commands; use only the state below.\n"
        "- Keep reasoning under 120 words.\n\n"
        "STATE:\n"
        # The readable observation is saved separately. Compact JSON here
        # avoids paying for indentation on every daily model call.
        + json.dumps(
            observation,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
