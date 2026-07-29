from __future__ import annotations

from collections import defaultdict
from typing import Any


Inventory = dict[str, list[dict[str, Any]]]
NetworkInventory = dict[str, Inventory]


def empty_network(
    shop_ids: list[str], product_ids: list[str]
) -> NetworkInventory:
    return {
        shop: {item: [] for item in product_ids}
        for shop in shop_ids
    }


def units(inventory: Inventory, product: str) -> int:
    return sum(lot["quantity"] for lot in inventory[product])


def add_lot(
    inventory: Inventory,
    *,
    product: str,
    quantity: int,
    ordered_day: int,
    arrival_day: int,
    shelf_life_days: int,
    unit_cost: float,
) -> None:
    if quantity <= 0:
        return
    inventory[product].append(
        {
            "quantity": quantity,
            "ordered_day": ordered_day,
            "arrival_day": arrival_day,
            "expires_day": arrival_day + shelf_life_days,
            "unit_cost": round(float(unit_cost), 2),
        }
    )


def expire(
    inventory: Inventory, day: int
) -> tuple[dict[str, int], dict[str, float]]:
    spoiled_units: dict[str, int] = {}
    spoiled_cost: dict[str, float] = {}
    for product, lots in inventory.items():
        kept = []
        spoiled_units[product] = 0
        spoiled_cost[product] = 0.0
        for lot in lots:
            if lot["expires_day"] <= day:
                spoiled_units[product] += lot["quantity"]
                spoiled_cost[product] += (
                    lot["quantity"] * lot["unit_cost"]
                )
            else:
                kept.append(lot)
        inventory[product] = kept
        spoiled_cost[product] = round(spoiled_cost[product], 2)
    return spoiled_units, spoiled_cost


def sell(
    inventory: Inventory, product: str, requested: int
) -> tuple[int, float]:
    remaining = requested
    sold = 0
    cost = 0.0
    lots = sorted(inventory[product], key=lambda lot: lot["expires_day"])
    for lot in lots:
        if remaining <= 0:
            break
        take = min(remaining, lot["quantity"])
        lot["quantity"] -= take
        remaining -= take
        sold += take
        cost += take * lot["unit_cost"]
    inventory[product] = [lot for lot in lots if lot["quantity"] > 0]
    return sold, round(cost, 2)


def transfer(
    source: Inventory,
    destination: Inventory,
    *,
    product: str,
    requested: int,
) -> int:
    remaining = requested
    moved = 0
    lots = sorted(source[product], key=lambda lot: lot["expires_day"])
    for lot in lots:
        if remaining <= 0:
            break
        take = min(remaining, lot["quantity"])
        destination[product].append(
            {
                **lot,
                "quantity": take,
            }
        )
        lot["quantity"] -= take
        remaining -= take
        moved += take
    source[product] = [lot for lot in lots if lot["quantity"] > 0]
    return moved


def zone_usage(
    inventory: Inventory, products: dict[str, dict[str, Any]]
) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for item_id, product in products.items():
        result[product["zone"]] += units(inventory, item_id)
    return dict(result)


def snapshot(inventory: Inventory) -> dict[str, Any]:
    return {
        product: {
            "units": units(inventory, product),
            "lots": [dict(lot) for lot in lots if lot["quantity"] > 0],
        }
        for product, lots in inventory.items()
    }


def value(inventory: Inventory) -> float:
    return round(
        sum(
            lot["quantity"] * lot["unit_cost"]
            for lots in inventory.values()
            for lot in lots
        ),
        2,
    )
