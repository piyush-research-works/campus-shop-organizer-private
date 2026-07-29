from __future__ import annotations

import math
from typing import Any


CORE = {
    "hostel": ["instant_noodles", "biscuits", "bottled_water", "milk"],
    "academic": ["bottled_water", "biscuits", "instant_noodles", "bananas"],
    "sports": ["bottled_water", "energy_drink", "biscuits", "bananas"],
}


def _history(observation: dict[str, Any]) -> list[dict[str, Any]]:
    return list(observation.get("recent_history", []))[-7:]


def _average_sales(
    observation: dict[str, Any], shop: str, product: str
) -> float:
    history = _history(observation)
    if not history:
        return 0.0
    weights = list(range(1, len(history) + 1))
    total = sum(weights)
    return sum(
        weight
        * float(day.get("sales", {}).get(shop, {}).get(product, 0))
        for weight, day in zip(weights, history)
    ) / total


def _stockout_count(
    observation: dict[str, Any], shop: str, product: str
) -> int:
    return sum(
        bool(day.get("stockout", {}).get(shop, {}).get(product, False))
        for day in _history(observation)[-5:]
    )


def _shop_revenue(observation: dict[str, Any], shop: str) -> float:
    history = _history(observation)
    if not history:
        return 0.0
    return sum(
        float(day.get("revenue_by_shop", {}).get(shop, 0.0))
        for day in history
    ) / len(history)


def _pending(
    observation: dict[str, Any], shop: str, product: str
) -> int:
    return sum(
        int(item.get("quantity", 0))
        for item in observation.get("pending_deliveries", [])
        if item.get("shop") == shop and item.get("product") == product
    )


def _opening_forecast(
    observation: dict[str, Any], shop: str, product: str, pack: int
) -> float:
    hints = observation.get("opening_market_hint") or {}
    value = str(hints.get(shop, {}).get(product, "low")).lower()
    if "high" in value:
        return 1.8 * pack
    if "medium" in value:
        return 1.0 * pack
    return 0.25 * pack


def _signal_multiplier(observation: dict[str, Any], shop: str) -> float:
    hints = observation.get("today_signals", {}).get("footfall_hint", {})
    value = str(hints.get(shop, "normal")).lower()
    if "very_low" in value or "severe" in value:
        return 0.35
    if "low" in value:
        return 0.65
    if "high" in value:
        return 1.25
    return 1.0


def _choose_anchor(observation: dict[str, Any]) -> str:
    open_shops = [
        shop
        for shop, status in observation["shop_status"].items()
        if status["open"]
    ]
    if not open_shops:
        return "hostel"
    if not _history(observation):
        return "hostel"
    return max(open_shops, key=lambda shop: _shop_revenue(observation, shop))


def _shop_actions(
    observation: dict[str, Any], anchor: str
) -> list[dict[str, str]]:
    day = int(observation["day"])
    cash = float(observation["shared_cash"])
    rent = float(observation["rent"]["per_shop"])
    status = observation["shop_status"]
    open_shops = [shop for shop, value in status.items() if value["open"]]
    actions: list[dict[str, str]] = []
    if day >= 8 and len(open_shops) > 1 and cash < rent * len(open_shops) * 5:
        candidates = [shop for shop in open_shops if shop != anchor]
        weakest = min(candidates, key=lambda shop: _shop_revenue(observation, shop))
        if _shop_revenue(observation, weakest) < rent * 0.75:
            actions.append({"shop": weakest, "action": "close"})
            return actions
    reopening_cost = float(
        observation.get("closure_rules", {}).get("reopening_cost", 300)
    )
    closed = [
        shop
        for shop, value in status.items()
        if not value["open"] and value.get("eligible_to_reopen")
    ]
    if (
        closed
        and day <= 52
        and cash > reopening_cost + rent * (len(open_shops) + 1) * 9 + 400
    ):
        target = max(closed, key=lambda shop: _shop_revenue(observation, shop))
        actions.append({"shop": target, "action": "reopen"})
    return actions


def build_action(
    observation: dict[str, Any],
    plan: dict[str, Any],
    telemetry: dict[str, int],
    source: str,
) -> dict[str, Any]:
    catalog = observation["catalog"]
    shops = observation["shops"]
    anchor = _choose_anchor(observation)
    actions = _shop_actions(observation, anchor)
    closing = {
        item["shop"] for item in actions if item["action"] == "close"
    }
    reopening = {
        item["shop"] for item in actions if item["action"] == "reopen"
    }
    active = {
        shop
        for shop, state in observation["shop_status"].items()
        if state["open"] and shop not in closing
    } | reopening

    prices: dict[str, dict[str, float]] = {}
    candidates: list[dict[str, Any]] = []
    remaining: dict[tuple[str, str], int] = {}
    for shop_id, shop in shops.items():
        prices[shop_id] = {}
        for zone, capacity in shop["capacity"].items():
            pending_zone = sum(
                _pending(observation, shop_id, product)
                for product, item in catalog.items()
                if item["zone"] == zone
            )
            remaining[(shop_id, zone)] = max(
                0,
                int(capacity)
                - int(shop["used_capacity"].get(zone, 0))
                - pending_zone,
            )
        for product, item in catalog.items():
            low, high = map(float, item["normal_price_range"])
            stockouts = _stockout_count(observation, shop_id, product)
            price = (low + high) / 2
            if product in CORE.get(shop_id, []) and stockouts:
                price = min(high, price * 1.025)
            lots = shop["inventory"][product].get("lots", [])
            if any(
                int(lot.get("days_remaining", lot.get("shelf_life", 99))) <= 2
                for lot in lots
            ):
                price = max(low, price * 0.92)
            prices[shop_id][product] = round(price, 2)
            if shop_id not in active:
                continue

            pack = int(item["pack_size"])
            average = _average_sales(observation, shop_id, product)
            if int(observation["day"]) == 1:
                forecast = _opening_forecast(
                    observation, shop_id, product, pack
                )
            else:
                forecast = average * _signal_multiplier(
                    observation, shop_id
                )
                if stockouts:
                    forecast += min(1.0, stockouts / 3) * 0.65 * pack
            perishable = int(item["shelf_life_days"]) <= 5
            cover = 1.25 if perishable else 3.0
            if _signal_multiplier(observation, shop_id) < 0.7:
                cover *= 0.5 if perishable else 0.75
            desired = forecast * cover
            available = (
                int(shop["inventory"][product]["units"])
                + _pending(observation, shop_id, product)
            )
            pack_need = max(0, math.ceil((desired - available) / pack))
            if pack_need:
                margin = price - float(item["current_unit_cost"])
                core_bonus = 18 if product in CORE.get(shop_id, []) else 0
                anchor_bonus = 24 if shop_id == anchor else 0
                candidates.append(
                    {
                        "shop": shop_id,
                        "product": product,
                        "zone": item["zone"],
                        "quantity": pack,
                        "pack_cost": pack
                        * float(item["current_unit_cost"]),
                        "pack_need": pack_need,
                        "score": (
                            core_bonus
                            + anchor_bonus
                            + 9 * stockouts
                            + max(0.0, margin)
                        ),
                    }
                )

    open_after = max(1, len(active))
    cash = float(observation["shared_cash"])
    rent = float(observation["rent"]["per_shop"])
    closed_count = len(shops) - open_after
    recovery_reserve = 300 if closed_count else 0
    rent_reserve = rent * open_after * (
        4 if cash < rent * open_after * 8 else 3
    )
    free_cash = max(0.0, cash - rent_reserve - recovery_reserve)
    risk_fraction = 0.24 if free_cash < 500 else 0.40
    budget = free_cash * risk_fraction
    if anchor in active and free_cash >= 500:
        budget = max(budget, min(200.0, free_cash))

    orders = {
        shop: {product: 0 for product in catalog} for shop in shops
    }
    spent = 0.0
    for item in sorted(candidates, key=lambda value: value["score"], reverse=True):
        for _ in range(item["pack_need"]):
            key = (item["shop"], item["zone"])
            if item["quantity"] > remaining[key]:
                break
            if spent + item["pack_cost"] > budget:
                break
            orders[item["shop"]][item["product"]] += item["quantity"]
            remaining[key] -= item["quantity"]
            spent += item["pack_cost"]

    return {
        "orders": orders,
        "prices": prices,
        "transfers": [],
        "shop_actions": actions,
        "telemetry": telemetry,
        "reasoning": (
            f"{source}; anchor={anchor}; active={sorted(active)}; "
            f"reserved={rent_reserve + recovery_reserve:.2f}; "
            f"ordered={spent:.2f}; "
            f"{plan.get('reasoning', 'anchor-first operations')}."
        )[:500],
    }

