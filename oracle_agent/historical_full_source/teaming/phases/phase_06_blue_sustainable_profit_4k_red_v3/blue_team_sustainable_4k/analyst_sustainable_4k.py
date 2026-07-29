from __future__ import annotations

import re
from typing import Any

from analyst_4k import ResilienceAnalyst4K


class SustainableDemandAnalyst4K(ResilienceAnalyst4K):
    """Uses decaying, location-scoped warnings instead of global fear."""

    SHOP_TERMS = {
        "hostel": ("hostel",),
        "academic": ("academic", "class"),
        "sports": ("sports", "game", "players", "spectators"),
    }
    PRODUCT_TERMS = {
        "exam": ("instant_noodles", "biscuits", "milk"),
        "break": ("milk", "bread", "bananas"),
        "travel": ("milk", "bread", "bananas"),
        "festival": ("milk", "bread", "bananas"),
        "closure": ("milk", "bread", "bananas"),
        "sports": ("bottled_water", "energy_drink", "bananas"),
        "game": ("bottled_water", "energy_drink", "bananas"),
        "weather": ("bottled_water", "energy_drink", "bananas"),
        "rain": ("bottled_water", "energy_drink", "bananas"),
    }
    WARNING_TERMS = (
        "break",
        "travel",
        "closure",
        "festival",
        "exam",
        "weather",
        "rain",
        "supply",
        "wholesale",
    )
    SEVERITY_RANK = {"normal": 0, "caution": 1, "severe": 2}

    def __init__(
        self,
        normal_delivery_days: int,
        config: dict[str, Any],
    ) -> None:
        self.config = config
        self.active_events: dict[str, dict[str, Any]] = {}
        self.seen_event_signatures: set[str] = set()
        super().__init__(
            normal_delivery_days,
            int(config["event_warning_memory_days"]),
        )

    def analyze(
        self,
        observation: dict[str, Any],
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        dashboard = super().analyze(observation, memory)
        event = dashboard["event_risk"]
        outlook = dashboard["financial_outlook"]
        runway = float(outlook["cash_runway_at_zero_sales"])
        projected = float(outlook["projected_cash_after_5_days"])
        two_days_rent = float(observation["rent"]["total_daily"]) * 2

        if (
            event["severity"] == "severe"
            or runway < 2.5
            or projected < two_days_rent
        ):
            risk = "downturn"
        elif event["severity"] == "caution" or runway < 4.5:
            risk = "warning"
        else:
            risk = "normal"
        dashboard["risk_state"] = risk
        dashboard["risk_reason"] = (
            "network_event"
            if event["severity"] != "normal"
            else "cash_runway"
            if risk != "normal"
            else "normal_operations"
        )
        return dashboard

    def _event_risk(
        self, observation: dict[str, Any]
    ) -> dict[str, Any]:
        day = int(observation["day"])
        signals = observation["today_signals"]
        footfall = signals.get("footfall_hint", {})
        bulletin = str(signals.get("weekly_bulletin") or "")
        announcements = list(signals.get("announcements", []))
        confidence_rows = list(
            signals.get("announcement_confidence", [])
        )

        self._remember_event(
            day=day,
            text=bulletin,
            source="weekly_bulletin",
            confidence=0.82,
        )
        for index, text in enumerate(announcements):
            confidence_row = (
                confidence_rows[index]
                if index < len(confidence_rows)
                else {}
            )
            if confidence_row.get("type") == "location_regime":
                continue
            confidence = {
                "high": 0.9,
                "medium": 0.7,
                "low": 0.4,
            }.get(confidence_row.get("confidence"), 0.55)
            self._remember_event(
                day=day,
                text=str(text),
                source="announcement",
                confidence=confidence,
            )

        minimum = float(self.config["event_minimum_confidence"])
        decay = float(self.config["event_confidence_decay_per_day"])
        active: list[dict[str, Any]] = []
        for signature, event in list(self.active_events.items()):
            age = day - int(event["first_seen_day"])
            confidence = max(
                0.0, float(event["initial_confidence"]) - decay * age
            )
            if day > int(event["expires_day"]) or confidence < minimum:
                del self.active_events[signature]
                continue
            active.append(
                {
                    **event,
                    "age_days": age,
                    "confidence": round(confidence, 2),
                }
            )

        direct_severity = {
            shop: (
                "severe"
                if hint == "very_low"
                else "caution"
                if hint == "low"
                else "normal"
            )
            for shop, hint in footfall.items()
        }
        shops = list(observation["shops"])
        products = list(observation["catalog"])
        line_severity = {
            shop: {
                product: direct_severity.get(shop, "normal")
                for product in products
            }
            for shop in shops
        }

        for event in active:
            scoped_shops = event["shops"] or shops
            scoped_products = event["products"] or products
            for shop in scoped_shops:
                if shop not in line_severity:
                    continue
                for product in scoped_products:
                    if product not in line_severity[shop]:
                        continue
                    if (
                        self.SEVERITY_RANK[
                            line_severity[shop][product]
                        ]
                        < self.SEVERITY_RANK["caution"]
                    ):
                        line_severity[shop][product] = "caution"

        shop_severity = {
            shop: max(
                product_levels.values(),
                key=lambda value: self.SEVERITY_RANK[value],
            )
            for shop, product_levels in line_severity.items()
        }
        severe_count = sum(
            value == "severe" for value in shop_severity.values()
        )
        if severe_count >= int(
            self.config["network_severe_shop_count"]
        ):
            severity = "severe"
        elif active or severe_count or any(
            value == "caution" for value in shop_severity.values()
        ):
            severity = "caution"
        else:
            severity = "normal"

        combined = " ".join([bulletin, *announcements]).lower()
        warning_terms = [
            term for term in self.WARNING_TERMS if term in combined
        ]
        caution_until = max(
            (int(event["expires_day"]) for event in active),
            default=day,
        )
        return {
            "severity": severity,
            "caution_until_day": caution_until,
            "days_remaining": max(0, caution_until - day),
            "reason": (
                active[0]["reason"] if active else "local_footfall"
                if severity != "normal"
                else ""
            ),
            "very_low_shops": [
                shop
                for shop, hint in footfall.items()
                if hint == "very_low"
            ],
            "price_pressure": "price-conscious" in combined,
            "supply_risk": any(
                phrase in combined
                for phrase in ("supply", "wholesale", "less reliable")
            ),
            "current_warning_terms": warning_terms,
            "shop_severity": shop_severity,
            "line_severity": line_severity,
            "active_events": [
                {
                    "reason": event["reason"],
                    "shops": event["shops"],
                    "products": event["products"],
                    "age_days": event["age_days"],
                    "confidence": event["confidence"],
                    "expires_day": event["expires_day"],
                }
                for event in active
            ],
        }

    def _remember_event(
        self,
        *,
        day: int,
        text: str,
        source: str,
        confidence: float,
    ) -> None:
        normalized = re.sub(r"\s+", " ", text.lower()).strip(" .")
        if not normalized or normalized.startswith(
            "no major campus-wide change"
        ):
            return
        matched = [
            term for term in self.WARNING_TERMS if term in normalized
        ]
        if not matched:
            return
        signature = f"{source}:{normalized}"
        if signature in self.seen_event_signatures:
            return
        if signature in self.active_events:
            return
        shops = [
            shop
            for shop, terms in self.SHOP_TERMS.items()
            if any(term in normalized for term in terms)
        ]
        products = sorted(
            {
                product
                for term, scoped_products in self.PRODUCT_TERMS.items()
                if term in normalized
                for product in scoped_products
            }
        )
        self.active_events[signature] = {
            "signature": signature,
            "first_seen_day": day,
            "expires_day": day
            + int(self.config["event_warning_memory_days"]),
            "initial_confidence": confidence,
            "source": source,
            "reason": matched[0],
            "shops": shops,
            "products": products,
        }
        self.seen_event_signatures.add(signature)
