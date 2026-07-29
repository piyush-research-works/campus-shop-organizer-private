from __future__ import annotations

import random
from typing import Any

from red_team.scenario import generate_hidden_scenario


def generate_crucible_scenario(
    *,
    cycle: int,
    seed: int,
    days: int,
    red_config: dict[str, Any],
    products: dict[str, dict[str, Any]],
    shops: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Layer bounded V3 regimes and coupled shocks on Red V2."""
    scenario = generate_hidden_scenario(
        cycle=cycle,
        seed=seed,
        days=days,
        red_config=red_config,
        products=products,
        shops=shops,
    )
    config = red_config["crucible"]
    rng = random.Random(seed ^ 0xC4A5C4DE)
    shop_ids = list(shops)
    product_ids = list(products)
    regimes = _build_regimes(rng, days, shop_ids, config)
    shocks = _build_coupled_shocks(
        rng, days, shop_ids, product_ids, config
    )
    competitors = _build_competitor_windows(
        rng, days, shop_ids, product_ids, config
    )
    decoys = _build_decoys(rng, days, shop_ids, product_ids, config)

    for scenario_day in scenario["days"]:
        day = int(scenario_day["day"])
        public = scenario_day["public"]
        hidden = scenario_day["hidden"]
        public.setdefault("announcement_confidence", [])

        for regime in regimes:
            if (
                regime["shop"] in shop_ids
                and regime["start"] <= day <= regime["end"]
            ):
                hidden["shop_multiplier"][regime["shop"]] *= regime[
                    "multiplier"
                ]
                hidden["active_events"].append(
                    f"location_regime:{regime['shop']}:{regime['state']}"
                )
                if day == regime["start"] and rng.random() < config[
                    "signal_reliability"
                ]:
                    public["announcements"].append(regime["public_hint"])
                    public["announcement_confidence"].append(
                        {
                            "type": "location_regime",
                            "confidence": regime["confidence"],
                        }
                    )

        for shock in shocks:
            phase = _shock_phase(shock, day)
            if phase is None:
                continue
            hidden["active_events"].append(
                f"coupled_shock:{shock['id']}:{phase}"
            )
            multiplier = (
                config["coupled_surge_multiplier"]
                if phase == "surge"
                else config["coupled_drop_multiplier"]
            )
            for shop in shock["shops"]:
                for product in shock["products"]:
                    hidden["shop_product_multiplier"][shop][
                        product
                    ] *= multiplier
            for product in shock["products"]:
                hidden["wholesale_multiplier"][product] = max(
                    hidden["wholesale_multiplier"][product],
                    config["coupled_wholesale_multiplier"],
                )
            if phase == "surge":
                hidden["extra_delivery_delay"] = True
            if shock["warning_day"] <= day <= shock["surge_end"]:
                _add_bounded_signal(
                    rng=rng,
                    public=public,
                    truthful=shock["public_hint"],
                    misleading=shock["misleading_hint"],
                    reliability=config["signal_reliability"],
                    signal_type="coupled_shock",
                )

        for competitor in competitors:
            if competitor["start"] <= day <= competitor["end"]:
                for product in competitor["products"]:
                    hidden["shop_product_multiplier"][
                        competitor["shop"]
                    ][product] *= config["competitor_demand_multiplier"]
                hidden["active_events"].append(
                    f"competitor:{competitor['shop']}"
                )
                if day == competitor["start"]:
                    _add_bounded_signal(
                        rng=rng,
                        public=public,
                        truthful=competitor["public_hint"],
                        misleading=(
                            "No meaningful competitor activity is expected."
                        ),
                        reliability=config["signal_reliability"],
                        signal_type="competitor",
                    )

        for decoy in decoys:
            if decoy["day"] == day:
                public["announcements"].append(decoy["text"])
                public["announcement_confidence"].append(
                    {"type": "unconfirmed", "confidence": "low"}
                )

        public["announcements"] = sorted(
            set(public["announcements"])
        )

    scenario["crucible"] = {
        "version": "campus_crucible_v3_6k",
        "regime_schedule": regimes,
        "coupled_shocks": shocks,
        "competitor_windows": competitors,
        "decoy_announcements": decoys,
        "signal_reliability": config["signal_reliability"],
    }
    return scenario


def _build_regimes(
    rng: random.Random,
    days: int,
    shops: list[str],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    regimes = []
    definitions = {
        "low": (
            config["regime_low_multiplier"],
            "Activity around {shop} appears quieter than normal.",
        ),
        "normal": (
            1.0,
            "Activity around {shop} appears broadly normal.",
        ),
        "high": (
            config["regime_high_multiplier"],
            "Activity around {shop} appears stronger than normal.",
        ),
    }
    for shop in shops:
        start = 1
        previous = None
        while start <= days:
            length = rng.randint(
                config["regime_min_days"],
                config["regime_max_days"],
            )
            states = ["low", "normal", "high"]
            weights = [0.28, 0.44, 0.28]
            state = rng.choices(states, weights=weights, k=1)[0]
            if state == previous:
                state = rng.choice(
                    [value for value in states if value != previous]
                )
            multiplier, hint = definitions[state]
            regimes.append(
                {
                    "shop": shop,
                    "state": state,
                    "start": start,
                    "end": min(days, start + length - 1),
                    "multiplier": multiplier,
                    "confidence": rng.choice(["medium", "medium", "low"]),
                    "public_hint": hint.format(shop=shop),
                }
            )
            previous = state
            start += length
    return regimes


def _build_coupled_shocks(
    rng: random.Random,
    days: int,
    shops: list[str],
    products: list[str],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    safe_windows = [(4, 6), (24, 26), (43, 45)]
    rng.shuffle(safe_windows)
    shocks = []
    for index, (low, high) in enumerate(
        safe_windows[: config["coupled_shock_count"]], start=1
    ):
        start = min(days - 6, rng.randint(low, high))
        surge_end = start + config["coupled_surge_days"] - 1
        drop_end = surge_end + config["coupled_drop_days"]
        selected_shop = rng.choice(shops)
        selected_products = rng.sample(
            products, k=config["coupled_products_per_shock"]
        )
        shocks.append(
            {
                "id": index,
                "shops": [selected_shop],
                "products": selected_products,
                "warning_day": max(1, start - 2),
                "surge_start": start,
                "surge_end": surge_end,
                "drop_start": surge_end + 1,
                "drop_end": min(days, drop_end),
                "public_hint": (
                    f"Demand near {selected_shop} may rise for "
                    f"{', '.join(selected_products)}, but supply timing "
                    "could be less reliable."
                ),
                "misleading_hint": (
                    f"Conditions near {selected_shop} are expected to "
                    "remain stable."
                ),
            }
        )
    return shocks


def _build_competitor_windows(
    rng: random.Random,
    days: int,
    shops: list[str],
    products: list[str],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    windows = []
    for index in range(config["competitor_window_count"]):
        start = rng.randint(8 + index * 20, 17 + index * 20)
        length = rng.randint(
            config["competitor_min_days"],
            config["competitor_max_days"],
        )
        shop = rng.choice(shops)
        selected = rng.sample(
            products, k=config["competitor_products_per_window"]
        )
        windows.append(
            {
                "shop": shop,
                "products": selected,
                "start": start,
                "end": min(days, start + length - 1),
                "public_hint": (
                    f"A nearby seller may be discounting "
                    f"{', '.join(selected)} around {shop}."
                ),
            }
        )
    return windows


def _build_decoys(
    rng: random.Random,
    days: int,
    shops: list[str],
    products: list[str],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    selected_days = rng.sample(
        range(5, days - 2), k=config["decoy_announcement_count"]
    )
    return [
        {
            "day": day,
            "text": (
                f"Unconfirmed discussion suggests {rng.choice(products)} "
                f"demand may change near {rng.choice(shops)}."
            ),
        }
        for day in sorted(selected_days)
    ]


def _shock_phase(shock: dict[str, Any], day: int) -> str | None:
    if shock["surge_start"] <= day <= shock["surge_end"]:
        return "surge"
    if shock["drop_start"] <= day <= shock["drop_end"]:
        return "drop"
    return None


def _add_bounded_signal(
    *,
    rng: random.Random,
    public: dict[str, Any],
    truthful: str,
    misleading: str,
    reliability: float,
    signal_type: str,
) -> None:
    text = truthful if rng.random() < reliability else misleading
    public["announcements"].append(text)
    public["announcement_confidence"].append(
        {"type": signal_type, "confidence": "medium"}
    )
