from __future__ import annotations

from typing import Any


class DemandAnalyst:
    """Produces censored-demand forecasts and a compact manager dashboard."""

    def __init__(self) -> None:
        self.opening_priors: dict[str, dict[str, float]] = {}

    def analyze(
        self,
        observation: dict[str, Any],
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        if observation["day"] == 1:
            self.opening_priors = {
                shop: {
                    item: sum(bounds) / 2
                    for item, bounds in products.items()
                }
                for shop, products in observation[
                    "opening_market_hint"
                ].items()
            }
        risk = self._risk_state(observation)
        products: dict[str, dict[str, dict[str, Any]]] = {}
        for shop_id, shop in observation["shops"].items():
            products[shop_id] = {}
            for item_id, catalog in observation["catalog"].items():
                forecast, average, stockout_days = self._forecast(
                    observation, shop_id, item_id
                )
                on_hand = shop["inventory"][item_id]["units"]
                pending = sum(
                    shipment["quantity"]
                    for shipment in observation["pending_deliveries"]
                    if shipment["shop"] == shop_id
                    and shipment["product"] == item_id
                )
                expiring = sum(
                    lot["quantity"]
                    for lot in shop["inventory"][item_id]["lots"]
                    if lot["expires_day"] <= observation["day"] + 2
                )
                midpoint = sum(catalog["normal_price_range"]) / 2
                margin = midpoint - catalog["current_unit_cost"]
                products[shop_id][item_id] = {
                    "forecast_daily": round(forecast, 2),
                    "forecast_3_days": round(forecast * 3, 2),
                    "recent_sales_average": round(average, 2),
                    "stockout_days_last_5": stockout_days,
                    "on_hand": on_hand,
                    "pending": pending,
                    "inventory_position": on_hand + pending,
                    "days_cover": round(
                        (on_hand + pending) / max(0.5, forecast), 2
                    ),
                    "expiring_within_2_days": expiring,
                    "unit_margin_at_reference": round(margin, 2),
                    "pack_size": catalog["pack_size"],
                    "shelf_life_days": catalog["shelf_life_days"],
                }
        return {
            "cycle": observation["cycle"],
            "day": observation["day"],
            "cash": observation["shared_cash"],
            "daily_rent": observation["rent"]["total_daily"],
            "risk_state": risk,
            "signals": observation["today_signals"],
            "memory": memory,
            "products": products,
        }

    def _forecast(
        self,
        observation: dict[str, Any],
        shop: str,
        item: str,
    ) -> tuple[float, float, int]:
        prior = self.opening_priors.get(shop, {}).get(item, 0.5)
        history = observation["recent_history"][-5:]
        if not history:
            base = prior
            average = prior
            stockouts = 0
        else:
            adjusted = [
                day["sales"][shop][item]
                * (1.35 if day["stockout"][shop][item] else 1.0)
                for day in history
            ]
            weights = list(range(1, len(adjusted) + 1))
            average = sum(adjusted) / len(adjusted)
            base = sum(
                value * weight
                for value, weight in zip(
                    adjusted, weights, strict=True
                )
            ) / sum(weights)
            stockouts = sum(
                day["stockout"][shop][item] for day in history
            )
            base = max(base, prior * 0.65)
        signal = observation["today_signals"]["footfall_hint"][shop]
        base *= {
            "very_low": 0.25,
            "low": 0.65,
            "uncertain": 0.85,
            "normal": 1.0,
            "high": 1.15,
        }.get(signal, 1.0)
        weather = observation["today_signals"]["weather_forecast"]
        if weather == "hot" and item == "bottled_water":
            base *= 1.2
        if weather == "hot" and item == "energy_drink":
            base *= 1.1
        if weather == "rain" and shop == "sports":
            base *= 0.65
        if weather == "rain" and item == "instant_noodles":
            base *= 1.1
        return max(0.35, base), average, stockouts

    @staticmethod
    def _risk_state(observation: dict[str, Any]) -> str:
        if any(
            value == "very_low"
            for value in observation["today_signals"][
                "footfall_hint"
            ].values()
        ):
            return "downturn"
        text = " ".join(
            observation["today_signals"]["announcements"]
        ).lower()
        if any(
            word in text
            for word in ["travel", "break", "closure", "weather"]
        ):
            return "warning"
        return "normal"
