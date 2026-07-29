from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contracts import Decision


@dataclass(frozen=True)
class ShopDecision(Decision):
    shop_actions: list[dict[str, Any]] = field(default_factory=list)
