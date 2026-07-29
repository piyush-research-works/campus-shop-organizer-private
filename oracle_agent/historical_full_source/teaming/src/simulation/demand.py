from __future__ import annotations

import math
from typing import Any

from simulation.inventory import NetworkInventory, units


def calculate_demand(
    *,
    scenario: dict[str, Any],
    scenario_day: dict[str, Any],
    red_config: dict[str, Any],
    products: dict[str, dict[str, Any]],
    complements: list[dict[str, Any]],
    shops: dict[str, dict[str, Any]],
    weekday_multipliers: dict[str, dict[str, float]],
    inventories: NetworkInventory,
    prices: dict[str, dict[str, float]],
) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    hidden = scenario_day["hidden"]
    calibration = scenario["cycle_calibration"]

    for shop_id, shop in shops.items():
        active_products = sum(
            units(inventories[shop_id], product) > 0
            for product in products
        )
        choice = _choice(active_products, red_config)
        result[shop_id] = {}
        for item_id, product in products.items():
            latent = (
                shop["base_sales"][item_id]
                * red_config["demand"]["base_scale"]
                * calibration["base_variation"][shop_id][item_id]
                * weekday_multipliers[shop_id][scenario_day["weekday"]]
                * hidden["shop_multiplier"][shop_id]
                * hidden["product_multiplier"][item_id]
                * hidden["shop_product_multiplier"][shop_id][item_id]
                * hidden["noise"][shop_id][item_id]
                * choice
                * _complement(
                    shop_id=shop_id,
                    item_id=item_id,
                    complements=complements,
                    products=products,
                    inventories=inventories,
                    prices=prices,
                    hidden=hidden,
                )
            )
            price_ratio = prices[shop_id][item_id] / product[
                "reference_price"
            ]
            elasticity = (
                product["elasticity"]
                * red_config["demand"]["elasticity_scale"]
                * shop["price_sensitivity"]
                * calibration["elasticity_variation"][shop_id][item_id]
            )
            elasticity_factor = max(
                0.05, min(1.4, price_ratio ** (-elasticity))
            )
            soft = hidden["soft_price_ratio"][shop_id][item_id]
            choke = hidden["choke_price_ratio"][shop_id][item_id]
            if price_ratio <= soft:
                affordability = 1.0
                affordability_state = "normal"
            elif price_ratio >= choke:
                affordability = 0.0
                affordability_state = "hard_cutoff"
            else:
                affordability = (
                    (choke - price_ratio) / (choke - soft)
                ) ** 1.5
                affordability_state = "soft_decline"

            draw = hidden["rounding_draw"][shop_id][item_id]
            market_demand = _round(latent, draw)
            after_elasticity = _round(latent * elasticity_factor, draw)
            final_demand = _round(
                latent * elasticity_factor * affordability, draw
            )
            result[shop_id][item_id] = {
                "latent_float": round(latent, 6),
                "market_demand": market_demand,
                "price_eligible_demand": final_demand,
                "price_refused_units": max(
                    0, market_demand - final_demand
                ),
                "elasticity_refused_units": max(
                    0, market_demand - after_elasticity
                ),
                "affordability_refused_units": max(
                    0, after_elasticity - final_demand
                ),
                "hard_cutoff_refused_units": (
                    after_elasticity
                    if affordability_state == "hard_cutoff"
                    else 0
                ),
                "discount_uplift_units": max(
                    0, final_demand - market_demand
                ),
                "selling_price": round(prices[shop_id][item_id], 2),
                "reference_price": product["reference_price"],
                "price_ratio": round(price_ratio, 6),
                "elasticity_factor": round(elasticity_factor, 6),
                "soft_price_ratio": soft,
                "choke_price_ratio": choke,
                "affordability_factor": round(affordability, 6),
                "affordability_state": affordability_state,
                "choice_multiplier": round(choice, 6),
            }
    return result


def _choice(active_products: int, config: dict[str, Any]) -> float:
    demand = config["demand"]
    ideal = demand["choice_ideal_products"]
    if active_products == ideal:
        return 1.05
    if active_products < ideal:
        raw = 1 - (
            ideal - active_products
        ) * demand["choice_missing_penalty"]
    else:
        raw = 1 - (
            active_products - ideal
        ) * demand["choice_excess_penalty"]
    return max(demand["choice_minimum_multiplier"], raw)


def _complement(
    *,
    shop_id: str,
    item_id: str,
    complements: list[dict[str, Any]],
    products: dict[str, dict[str, Any]],
    inventories: NetworkInventory,
    prices: dict[str, dict[str, float]],
    hidden: dict[str, Any],
) -> float:
    multiplier = 1.0
    for relation in complements:
        if item_id not in relation["items"]:
            continue
        other = next(
            product
            for product in relation["items"]
            if product != item_id
        )
        available = units(inventories[shop_id], other) > 0
        affordable = (
            prices[shop_id][other] / products[other]["reference_price"]
            <= hidden["soft_price_ratio"][shop_id][other]
        )
        strength = relation["strength"]
        multiplier *= (
            1 + strength
            if available and affordable
            else 1 - strength * 0.5
        )
    return multiplier


def _round(value: float, draw: float) -> int:
    floored = math.floor(max(0.0, value))
    return floored + int(draw < value - floored)
