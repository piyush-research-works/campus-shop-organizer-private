from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Transfer:
    from_shop: str
    to_shop: str
    product: str
    quantity: int


@dataclass(frozen=True)
class Decision:
    orders: dict[str, dict[str, int]]
    prices: dict[str, dict[str, float]]
    transfers: list[Transfer] = field(default_factory=list)
    reasoning: str = ""
    source: str = "policy"


class Policy(Protocol):
    name: str

    def decide(self, observation: dict[str, Any]) -> Decision:
        ...
