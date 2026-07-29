from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from contracts import Decision
from simulation.inventory import (
    NetworkInventory,
    transfer,
    units,
    zone_usage,
)


@dataclass(frozen=True)
class ValidatedTransfers:
    accepted: list[dict[str, Any]]
    cost: float
    issues: list[str]


@dataclass(frozen=True)
class ValidatedOrders:
    accepted: dict[str, dict[str, int]]
    shipments: list[dict[str, Any]]
    cost: float
    issues: list[str]
    requested_positive_lines: int
    clipped_lines: int


def validate_prices(
    decision: Decision,
    previous: dict[str, dict[str, float]],
    shops: dict[str, dict[str, Any]],
    products: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, float]], list[str]]:
    result: dict[str, dict[str, float]] = {}
    issues: list[str] = []
    for shop in shops:
        result[shop] = {}
        requested = decision.prices.get(shop, {})
        for item in products:
            raw = requested.get(item)
            if (
                isinstance(raw, bool)
                or not isinstance(raw, (int, float))
                or not math.isfinite(float(raw))
                or float(raw) <= 0
            ):
                result[shop][item] = previous[shop][item]
                issues.append(f"{shop}/{item}: invalid price retained")
            else:
                result[shop][item] = round(float(raw), 2)
    return result, issues


def apply_transfers(
    decision: Decision,
    *,
    cash: float,
    inventories: NetworkInventory,
    shops: dict[str, dict[str, Any]],
    products: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> ValidatedTransfers:
    issues: list[str] = []
    accepted: list[dict[str, Any]] = []
    limit = config["transfer_limit_units_per_day"]
    unit_cost = config["transfer_cost_per_unit"]
    reserve = config["daily_rent_per_shop"] * len(shops)
    available_cash = max(0.0, cash - reserve)

    for index, request in enumerate(decision.transfers):
        if (
            request.from_shop not in shops
            or request.to_shop not in shops
            or request.product not in products
            or request.from_shop == request.to_shop
        ):
            issues.append(f"transfer[{index}]: invalid route or product")
            continue
        if request.quantity <= 0:
            continue
        product = products[request.product]
        used = zone_usage(inventories[request.to_shop], products)
        destination_space = (
            shops[request.to_shop]["capacity"][product["zone"]]
            - used.get(product["zone"], 0)
        )
        affordable = math.floor(available_cash / unit_cost)
        allowed = min(
            request.quantity,
            limit,
            affordable,
            destination_space,
            units(inventories[request.from_shop], request.product),
        )
        if allowed < request.quantity:
            issues.append(
                f"transfer[{index}]: requested {request.quantity}, accepted {allowed}"
            )
        if allowed <= 0:
            continue
        moved = transfer(
            inventories[request.from_shop],
            inventories[request.to_shop],
            product=request.product,
            requested=allowed,
        )
        cost = moved * unit_cost
        accepted.append(
            {
                "from_shop": request.from_shop,
                "to_shop": request.to_shop,
                "product": request.product,
                "quantity": moved,
                "cost": cost,
            }
        )
        limit -= moved
        available_cash -= cost
    return ValidatedTransfers(
        accepted=accepted,
        cost=round(sum(item["cost"] for item in accepted), 2),
        issues=issues,
    )


def validate_orders(
    decision: Decision,
    *,
    day: int,
    cash: float,
    inventories: NetworkInventory,
    pending: list[dict[str, Any]],
    shops: dict[str, dict[str, Any]],
    products: dict[str, dict[str, Any]],
    config: dict[str, Any],
    wholesale_multiplier: dict[str, float],
    extra_delay: bool,
) -> ValidatedOrders:
    accepted = {
        shop: {item: 0 for item in products} for shop in shops
    }
    shipments: list[dict[str, Any]] = []
    issues: list[str] = []
    supplier = {
        item: product["supplier_daily_limit"]
        for item, product in products.items()
    }
    projected = {
        shop: zone_usage(inventories[shop], products)
        for shop in shops
    }
    for shipment in pending:
        product = products[shipment["product"]]
        zone = product["zone"]
        projected[shipment["shop"]][zone] = (
            projected[shipment["shop"]].get(zone, 0)
            + shipment["quantity"]
        )
    reserve = config["daily_rent_per_shop"] * len(shops)
    available_cash = max(0.0, cash - reserve)
    requested_positive = 0
    clipped = 0

    for shop_id, shop in shops.items():
        requested_items = decision.orders.get(shop_id, {})
        for item_id, product in products.items():
            raw = requested_items.get(item_id, 0)
            if isinstance(raw, bool) or not isinstance(raw, int):
                issues.append(f"{shop_id}/{item_id}: order must be integer units")
                continue
            requested = max(0, raw)
            if requested:
                requested_positive += 1
            pack = product["pack_size"]
            packed = requested - requested % pack
            zone = product["zone"]
            capacity = (
                shop["capacity"][zone] - projected[shop_id].get(zone, 0)
            )
            unit_cost = round(
                product["unit_cost"] * wholesale_multiplier[item_id], 2
            )
            allowed = min(
                packed,
                max(0, capacity),
                supplier[item_id],
                math.floor(available_cash / unit_cost),
            )
            allowed -= allowed % pack
            if allowed < requested:
                clipped += int(requested > 0)
                issues.append(
                    f"{shop_id}/{item_id}: requested {requested}, accepted {allowed}"
                )
            if allowed <= 0:
                continue
            due = (
                day
                if day == 1 and config["opening_order_arrives_same_day"]
                else day
                + config["normal_delivery_days"]
                + int(extra_delay)
            )
            cost = round(allowed * unit_cost, 2)
            accepted[shop_id][item_id] = allowed
            shipments.append(
                {
                    "ordered_day": day,
                    "due_day": due,
                    "shop": shop_id,
                    "product": item_id,
                    "quantity": allowed,
                    "unit_cost": unit_cost,
                    "cost": cost,
                }
            )
            projected[shop_id][zone] = (
                projected[shop_id].get(zone, 0) + allowed
            )
            supplier[item_id] -= allowed
            available_cash -= cost
    return ValidatedOrders(
        accepted=accepted,
        shipments=shipments,
        cost=round(sum(item["cost"] for item in shipments), 2),
        issues=issues,
        requested_positive_lines=requested_positive,
        clipped_lines=clipped,
    )
