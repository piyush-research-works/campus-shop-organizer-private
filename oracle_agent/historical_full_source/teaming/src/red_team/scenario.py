from __future__ import annotations

import random
from copy import deepcopy
from typing import Any


WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def generate_hidden_scenario(
    *,
    cycle: int,
    seed: int,
    days: int,
    red_config: dict[str, Any],
    products: dict[str, dict[str, Any]],
    shops: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Create one unknown but reproducible challenge for a Blue Team run."""
    rng = random.Random(seed)
    events = _build_reversal_events(rng, red_config, days)
    scenario_days: list[dict[str, Any]] = []
    product_ids = list(products)
    shop_ids = list(shops)
    cycle_calibration = {
        "base_variation": {
            shop: {
                item: round(rng.uniform(0.9, 1.1), 6)
                for item in product_ids
            }
            for shop in shop_ids
        },
        "elasticity_variation": {
            shop: {
                item: round(rng.uniform(0.92, 1.08), 6)
                for item in product_ids
            }
            for shop in shop_ids
        },
    }

    for day in range(1, days + 1):
        weekday = WEEKDAYS[(day - 1) % 7]
        shop_multiplier = {shop: 1.0 for shop in shop_ids}
        product_multiplier = {item: 1.0 for item in product_ids}
        shop_product_multiplier = {
            shop: {item: 1.0 for item in product_ids}
            for shop in shop_ids
        }
        public_announcements: list[str] = []
        footfall_hint = {shop: "normal" for shop in shop_ids}
        active_event_names: list[str] = []

        for event in events:
            phase = _event_phase(event, day)
            if phase is None:
                continue
            active_event_names.append(f"{event['type']}:{phase}")
            _apply_event(
                event=event,
                phase=phase,
                red_config=red_config,
                shop_multiplier=shop_multiplier,
                product_multiplier=product_multiplier,
                shop_product_multiplier=shop_product_multiplier,
                footfall_hint=footfall_hint,
            )
            warning_day = (
                event["drop_start"]
                - red_config["demand"]["event_warning_lead_days"]
            )
            if warning_day <= day < event["drop_end"]:
                public_announcements.append(event["public_hint"])

        for shop_id in shop_ids:
            if rng.random() < red_config["demand"][
                "random_low_day_probability"
            ]:
                multiplier = rng.uniform(
                    red_config["demand"][
                        "random_low_day_multiplier_min"
                    ],
                    red_config["demand"][
                        "random_low_day_multiplier_max"
                    ],
                )
                shop_multiplier[shop_id] *= multiplier
                footfall_hint[shop_id] = (
                    "low" if rng.random() < 0.72 else "uncertain"
                )

        actual_weather, forecast, confidence = _weather(rng)
        if actual_weather == "rain":
            shop_multiplier["sports"] *= 0.55
            shop_multiplier["academic"] *= 0.85
            product_multiplier["bottled_water"] *= 0.78
            product_multiplier["instant_noodles"] *= 1.1
        elif actual_weather == "hot":
            product_multiplier["bottled_water"] *= 1.25
            product_multiplier["energy_drink"] *= 1.12

        budget_pressure = (
            rng.random()
            < red_config["demand"]["budget_pressure_probability"]
        )
        if budget_pressure and rng.random() < 0.65:
            public_announcements.append(
                "Students appear more price-conscious than usual today."
            )
        soft_ratio = (
            red_config["demand"]["budget_pressure_soft_ratio"]
            if budget_pressure
            else red_config["demand"]["soft_price_ratio"]
        )
        choke_ratio = (
            red_config["demand"]["budget_pressure_choke_ratio"]
            if budget_pressure
            else red_config["demand"]["choke_price_ratio"]
        )

        wholesale = {item: 1.0 for item in product_ids}
        for item_id in product_ids:
            if rng.random() < red_config["demand"][
                "wholesale_spike_probability"
            ]:
                wholesale[item_id] = round(
                    rng.uniform(
                        red_config["demand"]["wholesale_spike_min"],
                        red_config["demand"]["wholesale_spike_max"],
                    ),
                    4,
                )

        scenario_days.append(
            {
                "day": day,
                "weekday": weekday,
                "public": {
                    "weather_forecast": forecast,
                    "weather_confidence": confidence,
                    "footfall_hint": footfall_hint,
                    "announcements": sorted(set(public_announcements)),
                    "weekly_bulletin": _weekly_bulletin(
                        day, events, red_config
                    ),
                },
                "hidden": {
                    "actual_weather": actual_weather,
                    "active_events": active_event_names,
                    "shop_multiplier": shop_multiplier,
                    "product_multiplier": product_multiplier,
                    "shop_product_multiplier": shop_product_multiplier,
                    "budget_pressure": budget_pressure,
                    "soft_price_ratio": {
                        shop: {
                            item: round(
                                soft_ratio * rng.uniform(0.97, 1.03), 5
                            )
                            for item in product_ids
                        }
                        for shop in shop_ids
                    },
                    "choke_price_ratio": {
                        shop: {
                            item: round(
                                max(
                                    soft_ratio + 0.03,
                                    choke_ratio * rng.uniform(0.97, 1.03),
                                ),
                                5,
                            )
                            for item in product_ids
                        }
                        for shop in shop_ids
                    },
                    "wholesale_multiplier": wholesale,
                    "extra_delivery_delay": rng.random()
                    < red_config["extra_delay_probability"],
                    "noise": {
                        shop: {
                            item: round(
                                rng.uniform(
                                    1
                                    - red_config["demand"][
                                        "noise_amplitude"
                                    ],
                                    1
                                    + red_config["demand"][
                                        "noise_amplitude"
                                    ],
                                ),
                                6,
                            )
                            for item in product_ids
                        }
                        for shop in shop_ids
                    },
                    "rounding_draw": {
                        shop: {
                            item: round(rng.random(), 6)
                            for item in product_ids
                        }
                        for shop in shop_ids
                    },
                },
            }
        )

    return {
        "cycle": cycle,
        "seed": seed,
        "cycle_calibration": cycle_calibration,
        "days": scenario_days,
        "hidden_event_schedule": events,
    }


def _build_reversal_events(
    rng: random.Random, red_config: dict[str, Any], days: int
) -> list[dict[str, Any]]:
    build_days = red_config["demand"]["reversal_buildup_days"]
    drop_days = red_config["demand"]["reversal_drop_days"]
    definitions = [
        {
            "type": "festival_travel",
            "products": ["milk", "bread", "bananas"],
            "shops": ["hostel", "academic", "sports"],
            "public_hint": (
                "Festival activity is rising, but some students may travel "
                "away from campus soon."
            ),
        },
        {
            "type": "exam_then_break",
            "products": ["instant_noodles", "biscuits", "milk"],
            "shops": ["hostel", "academic"],
            "public_hint": (
                "Study activity is high, followed by a possible short campus break."
            ),
        },
        {
            "type": "sports_then_weather",
            "products": ["bottled_water", "energy_drink", "bananas"],
            "shops": ["sports"],
            "public_hint": (
                "Sports attendance is strong, although later outdoor activity "
                "may depend on weather."
            ),
        },
    ]
    configured_windows = red_config.get("event_start_windows")
    starts = (
        [
            rng.randint(
                configured_windows[definition["type"]][0],
                configured_windows[definition["type"]][1],
            )
            for definition in definitions
        ]
        if configured_windows
        else [
            rng.randint(12, 20),
            rng.randint(39, 49),
            rng.randint(67, 76),
        ]
    )
    events = []
    for start, definition in zip(starts, definitions):
        drop_start = start + build_days
        events.append(
            {
                **definition,
                "buildup_start": start,
                "buildup_end": drop_start - 1,
                "drop_start": drop_start,
                "drop_end": min(days + 1, drop_start + drop_days),
            }
        )
    return events


def _event_phase(event: dict[str, Any], day: int) -> str | None:
    if event["buildup_start"] <= day <= event["buildup_end"]:
        return "buildup"
    if event["drop_start"] <= day < event["drop_end"]:
        return "drop"
    return None


def _apply_event(
    *,
    event: dict[str, Any],
    phase: str,
    red_config: dict[str, Any],
    shop_multiplier: dict[str, float],
    product_multiplier: dict[str, float],
    shop_product_multiplier: dict[str, dict[str, float]],
    footfall_hint: dict[str, str],
) -> None:
    if phase == "buildup":
        for shop in event["shops"]:
            shop_multiplier[shop] *= 1.08
            footfall_hint[shop] = "high"
        for product in event["products"]:
            product_multiplier[product] *= red_config["demand"][
                "reversal_buildup_multiplier"
            ]
        return

    drop = red_config["demand"]["reversal_drop_multiplier"]
    for shop in event["shops"]:
        shop_multiplier[shop] *= drop
        footfall_hint[shop] = "very_low"
    if event["type"] == "festival_travel":
        for product in event["products"]:
            product_multiplier[product] *= 0.55
    elif event["type"] == "sports_then_weather":
        for product in event["products"]:
            shop_product_multiplier["sports"][product] *= 0.45


def _weather(rng: random.Random) -> tuple[str, str, str]:
    actual = rng.choices(
        ["normal", "hot", "rain"], weights=[0.65, 0.18, 0.17], k=1
    )[0]
    confidence = rng.choices(
        ["high", "medium", "low"], weights=[0.45, 0.4, 0.15], k=1
    )[0]
    forecast = actual
    wrong_probability = {"high": 0.08, "medium": 0.2, "low": 0.4}[
        confidence
    ]
    if rng.random() < wrong_probability:
        forecast = rng.choice(
            [value for value in ["normal", "hot", "rain"] if value != actual]
        )
    return actual, forecast, confidence


def _weekly_bulletin(
    day: int, events: list[dict[str, Any]], red_config: dict[str, Any]
) -> str | None:
    if (day - 1) % 7 != 0:
        return None
    end = day + 6
    upcoming = [
        event["type"].replace("_", " ")
        for event in events
        if day <= event["drop_start"] <= end
    ]
    if upcoming:
        return (
            "Campus conditions may shift this week around: "
            + ", ".join(upcoming)
            + "."
        )
    return "No major campus-wide change has been confirmed for this week."
