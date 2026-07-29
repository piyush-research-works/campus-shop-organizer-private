from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

from contracts import Policy
from io_utils import append_jsonl, write_json
from simulation.demand import calculate_demand
from simulation.inventory import (
    NetworkInventory,
    add_lot,
    empty_network,
    expire,
    snapshot,
    sell,
    units,
    value,
)
from simulation.observation import build_observation
from simulation.validation import (
    apply_transfers,
    validate_orders,
    validate_prices,
)


class CycleSimulator:
    def __init__(
        self,
        *,
        cycle: int,
        config: dict[str, Any],
        strategy: dict[str, Any],
        products: dict[str, dict[str, Any]],
        complements: list[dict[str, Any]],
        shops: dict[str, dict[str, Any]],
        weekday_multipliers: dict[str, dict[str, float]],
        scenario: dict[str, Any],
        extension: Any | None = None,
    ) -> None:
        self.cycle = cycle
        self.config = config
        self.strategy = strategy
        self.products = products
        self.complements = complements
        self.shops = shops
        self.weekday_multipliers = weekday_multipliers
        self.scenario = scenario
        self.extension = extension

    def run(self, policy: Policy, cycle_dir: Path) -> dict[str, Any]:
        cycle_dir.mkdir(parents=True, exist_ok=True)
        observations_dir = cycle_dir / "observations"
        decisions_dir = cycle_dir / "decisions"
        observations_dir.mkdir(exist_ok=False)
        decisions_dir.mkdir(exist_ok=False)

        inventories = empty_network(
            list(self.shops), list(self.products)
        )
        prices = {
            shop: {
                item: float(product["reference_price"])
                for item, product in self.products.items()
            }
            for shop in self.shops
        }
        pending: list[dict[str, Any]] = []
        cash = float(self.config["starting_shared_cash"])
        history: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        product_rows: list[dict[str, Any]] = []
        price_ratios: list[float] = []
        minimum_cash = cash
        total_requested_lines = 0
        total_clipped_lines = 0
        total_fresh_purchases = 0

        daily_jsonl = cycle_dir / "daily_results.jsonl"
        inventory_ledger = cycle_dir / "inventory_ledger.jsonl"
        cash_ledger = cycle_dir / "cash_ledger.jsonl"
        decision_trace = cycle_dir / "decision_trace.jsonl"
        for path in [
            daily_jsonl,
            inventory_ledger,
            cash_ledger,
            decision_trace,
        ]:
            path.touch(exist_ok=False)

        for scenario_day in self.scenario["days"][: self.config["days_per_cycle"]]:
            day = int(scenario_day["day"])
            start_cash = cash
            arrived, pending = self._deliver(
                day, pending, inventories, inventory_ledger
            )
            spoiled_units: dict[str, dict[str, int]] = {}
            spoiled_cost: dict[str, dict[str, float]] = {}
            for shop in self.shops:
                quantities, costs = expire(inventories[shop], day)
                spoiled_units[shop] = quantities
                spoiled_cost[shop] = costs
                for item, quantity in quantities.items():
                    if quantity:
                        append_jsonl(
                            inventory_ledger,
                            {
                                "cycle": self.cycle,
                                "day": day,
                                "event": "expired",
                                "shop": shop,
                                "product": item,
                                "quantity": quantity,
                                "cost": costs[item],
                            },
                        )

            wholesale = scenario_day["hidden"]["wholesale_multiplier"]
            observation = build_observation(
                cycle=self.cycle,
                day=day,
                cash=cash,
                inventories=inventories,
                prices=prices,
                pending=pending,
                products=self.products,
                shops=self.shops,
                config=self.config,
                strategy=self.strategy,
                public_signal=scenario_day["public"],
                wholesale_multiplier=wholesale,
                history=history,
            )
            if self.extension is not None:
                observation.update(
                    self.extension.observation_fields(
                        day=day,
                        cash=cash,
                        history=history,
                    )
                )
                observation["rent"]["total_daily"] = (
                    self.extension.current_daily_rent()
                )
            write_json(
                observations_dir / f"day_{day:03d}.json", observation
            )
            decision = policy.decide(observation)
            lifecycle_result = {
                "cash_cost": 0.0,
                "issues": [],
                "actions": [],
            }
            if self.extension is not None:
                decision, lifecycle_result = (
                    self.extension.apply_shop_actions(
                        day=day,
                        decision=decision,
                        cash=cash,
                        observation=observation,
                    )
                )
                cash -= lifecycle_result["cash_cost"]
            decision_payload = {
                "cycle": self.cycle,
                "day": day,
                "source": decision.source,
                "orders": decision.orders,
                "prices": decision.prices,
                "transfers": [
                    {
                        "from_shop": transfer.from_shop,
                        "to_shop": transfer.to_shop,
                        "product": transfer.product,
                        "quantity": transfer.quantity,
                    }
                    for transfer in decision.transfers
                ],
                "shop_actions": lifecycle_result["actions"],
                "reasoning": decision.reasoning,
            }
            write_json(
                decisions_dir / f"day_{day:03d}.json",
                decision_payload,
            )

            prices, price_issues = validate_prices(
                decision, prices, self.shops, self.products
            )
            transfers = apply_transfers(
                decision,
                cash=cash,
                inventories=inventories,
                shops=self.shops,
                products=self.products,
                config=self.config,
            )
            cash -= transfers.cost
            for transfer_record in transfers.accepted:
                append_jsonl(
                    inventory_ledger,
                    {
                        "cycle": self.cycle,
                        "day": day,
                        "event": "transfer",
                        **transfer_record,
                    },
                )

            orders = validate_orders(
                decision,
                day=day,
                cash=cash,
                inventories=inventories,
                pending=pending,
                shops=self.shops,
                products=self.products,
                config=self.config,
                wholesale_multiplier=wholesale,
                extra_delay=scenario_day["hidden"][
                    "extra_delivery_delay"
                ],
            )
            total_requested_lines += orders.requested_positive_lines
            total_clipped_lines += orders.clipped_lines
            cash -= orders.cost
            for shipment in orders.shipments:
                if self.products[shipment["product"]][
                    "shelf_life_days"
                ] <= 4:
                    total_fresh_purchases += shipment["quantity"]
                append_jsonl(
                    inventory_ledger,
                    {
                        "cycle": self.cycle,
                        "day": day,
                        "event": "order_placed",
                        **shipment,
                    },
                )
                if shipment["due_day"] == day:
                    self._add_shipment(inventories, shipment)
                    append_jsonl(
                        inventory_ledger,
                        {
                            "cycle": self.cycle,
                            "day": day,
                            "event": "opening_delivery",
                            **shipment,
                        },
                    )
                else:
                    pending.append(shipment)

            demand_arguments = {
                "scenario": self.scenario,
                "scenario_day": scenario_day,
                "red_config": self.config,
                "products": self.products,
                "complements": self.complements,
                "shops": self.shops,
                "weekday_multipliers": self.weekday_multipliers,
                "inventories": inventories,
                "prices": prices,
            }
            diagnostics = (
                self.extension.calculate_demand(**demand_arguments)
                if self.extension is not None
                else calculate_demand(**demand_arguments)
            )
            sales: dict[str, dict[str, int]] = {}
            lost: dict[str, dict[str, int]] = {}
            revenue_by_shop: dict[str, float] = {}
            cogs_by_shop: dict[str, float] = {}
            for shop in self.shops:
                sales[shop] = {}
                lost[shop] = {}
                shop_revenue = 0.0
                shop_cogs = 0.0
                for item in self.products:
                    requested = diagnostics[shop][item][
                        "price_eligible_demand"
                    ]
                    sold, cogs = (
                        sell(inventories[shop], item, requested)
                        if self.extension is None
                        or self.extension.shop_is_open(shop)
                        else (0, 0.0)
                    )
                    sales[shop][item] = sold
                    lost[shop][item] = requested - sold
                    shop_revenue += sold * prices[shop][item]
                    shop_cogs += cogs
                    diagnostic_record = {
                        "cycle": self.cycle,
                        "day": day,
                        "weekday": scenario_day["weekday"],
                        "shop": shop,
                        "product": item,
                        "sold": sold,
                        "stockout_lost_units": requested - sold,
                        "ending_inventory": units(
                            inventories[shop], item
                        ),
                        "active_events": scenario_day["hidden"][
                            "active_events"
                        ],
                        **diagnostics[shop][item],
                    }
                    product_rows.append(diagnostic_record)
                    price_ratios.append(
                        diagnostics[shop][item]["price_ratio"]
                    )
                revenue_by_shop[shop] = round(shop_revenue, 2)
                cogs_by_shop[shop] = round(shop_cogs, 2)

            revenue = round(sum(revenue_by_shop.values()), 2)
            cogs = round(sum(cogs_by_shop.values()), 2)
            spoilage = round(
                sum(
                    sum(costs.values())
                    for costs in spoiled_cost.values()
                ),
                2,
            )
            cash += revenue
            rent_result = (
                self.extension.settle_rent(
                    day=day,
                    cash=cash,
                    revenue_by_shop=revenue_by_shop,
                )
                if self.extension is not None
                else {
                    "cash_after": (
                        cash
                        - self.config["daily_rent_per_shop"]
                        * len(self.shops)
                    ),
                    "rent_paid": (
                        self.config["daily_rent_per_shop"]
                        * len(self.shops)
                    ),
                    "forced_closures": [],
                }
            )
            cash = rent_result["cash_after"]
            rent = rent_result["rent_paid"]
            minimum_cash = min(minimum_cash, cash)
            daily_profit = round(
                revenue
                - cogs
                - spoilage
                - rent
                - transfers.cost
                - lifecycle_result["cash_cost"],
                2,
            )
            inventory_value = round(
                sum(value(inventory) for inventory in inventories.values()),
                2,
            )
            pending_value = round(
                sum(shipment["cost"] for shipment in pending), 2
            )
            net_worth = round(cash + inventory_value + pending_value, 2)
            issues = (
                price_issues
                + transfers.issues
                + orders.issues
                + lifecycle_result["issues"]
            )

            public_record = {
                "day": day,
                "weekday": scenario_day["weekday"],
                "prices": prices,
                "sales": sales,
                "stockout": {
                    shop: {
                        item: lost[shop][item] > 0
                        for item in self.products
                    }
                    for shop in self.shops
                },
                "spoiled_units": spoiled_units,
                "ending_inventory": {
                    shop: {
                        item: units(inventories[shop], item)
                        for item in self.products
                    }
                    for shop in self.shops
                },
                "cash": round(cash, 2),
                "revenue_by_shop": revenue_by_shop,
                "accepted_orders": orders.accepted,
                "pending_delivery_count": len(pending),
            }
            if self.extension is not None:
                public_record["shop_lifecycle"] = (
                    self.extension.public_state()
                )
            history.append(public_record)
            record = {
                **public_record,
                "cycle": self.cycle,
                "visible_signals": scenario_day["public"],
                "decision_source": decision.source,
                "decision_reasoning": decision.reasoning,
                "requested_orders": decision.orders,
                "accepted_transfers": transfers.accepted,
                "arrivals": arrived,
                "pending_deliveries": [dict(item) for item in pending],
                "stockout_lost_units": lost,
                "demand_totals": self._demand_totals(diagnostics),
                "order_cost": orders.cost,
                "transfer_cost": transfers.cost,
                "revenue": revenue,
                "cost_of_goods_sold": cogs,
                "spoilage_cost": spoilage,
                "rent": rent,
                "shop_action_cost": lifecycle_result["cash_cost"],
                "forced_closures": rent_result["forced_closures"],
                "daily_profit": daily_profit,
                "inventory_value": inventory_value,
                "pending_value": pending_value,
                "net_worth": net_worth,
                "validation_issues": issues,
            }
            records.append(record)
            if self.extension is not None:
                self.extension.after_day(
                    day=day,
                    record=record,
                    diagnostics=diagnostics,
                )
                record["shop_lifecycle_after"] = (
                    self.extension.public_state()
                )
            participant_record = {
                key: value
                for key, value in record.items()
                if key not in {"stockout_lost_units", "demand_totals"}
            }
            append_jsonl(daily_jsonl, participant_record)
            append_jsonl(
                cash_ledger,
                {
                    "cycle": self.cycle,
                    "day": day,
                    "starting_cash": round(start_cash, 2),
                    "purchase_outflow": orders.cost,
                    "transfer_outflow": transfers.cost,
                    "sales_inflow": revenue,
                    "rent_outflow": rent,
                    "shop_action_outflow": lifecycle_result[
                        "cash_cost"
                    ],
                    "ending_cash": round(cash, 2),
                },
            )
            append_jsonl(
                inventory_ledger,
                {
                    "cycle": self.cycle,
                    "day": day,
                    "event": "end_of_day_value",
                    "inventory_value": inventory_value,
                    "pending_value": pending_value,
                },
            )
            append_jsonl(
                decision_trace,
                {
                    **decision_payload,
                    "validation_issues": issues,
                    "accepted_orders": orders.accepted,
                    "accepted_transfers": transfers.accepted,
                },
            )
            if cash < 0:
                break

        summary = self._summary(
            policy=policy,
            cash=cash,
            inventories=inventories,
            pending=pending,
            records=records,
            product_rows=product_rows,
            price_ratios=price_ratios,
            minimum_cash=minimum_cash,
            requested_lines=total_requested_lines,
            clipped_lines=total_clipped_lines,
            fresh_purchases=total_fresh_purchases,
        )
        if self.extension is not None:
            summary = self.extension.enrich_summary(
                summary=summary,
                cash=cash,
                inventories=inventories,
                pending=pending,
                products=self.products,
                records=records,
            )
        write_json(cycle_dir / "summary.json", summary)
        self._write_csv_outputs(cycle_dir, records, product_rows)
        return summary

    def _deliver(
        self,
        day: int,
        pending: list[dict[str, Any]],
        inventories: NetworkInventory,
        ledger: Path,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        arrived = [item for item in pending if item["due_day"] <= day]
        remaining = [item for item in pending if item["due_day"] > day]
        for shipment in arrived:
            self._add_shipment(inventories, shipment)
            append_jsonl(
                ledger,
                {
                    "cycle": self.cycle,
                    "day": day,
                    "event": "delivery_arrived",
                    **shipment,
                },
            )
        return arrived, remaining

    def _add_shipment(
        self,
        inventories: NetworkInventory,
        shipment: dict[str, Any],
    ) -> None:
        product = self.products[shipment["product"]]
        add_lot(
            inventories[shipment["shop"]],
            product=shipment["product"],
            quantity=shipment["quantity"],
            ordered_day=shipment["ordered_day"],
            arrival_day=shipment["due_day"],
            shelf_life_days=product["shelf_life_days"],
            unit_cost=shipment["unit_cost"],
        )

    @staticmethod
    def _demand_totals(
        diagnostics: dict[str, dict[str, dict[str, Any]]]
    ) -> dict[str, int]:
        rows = [
            detail
            for shop in diagnostics.values()
            for detail in shop.values()
        ]
        return {
            "market_demand": sum(row["market_demand"] for row in rows),
            "price_eligible_demand": sum(
                row["price_eligible_demand"] for row in rows
            ),
            "price_refused_units": sum(
                row["price_refused_units"] for row in rows
            ),
            "hard_cutoff_refused_units": sum(
                row["hard_cutoff_refused_units"] for row in rows
            ),
        }

    def _summary(
        self,
        *,
        policy: Policy,
        cash: float,
        inventories: NetworkInventory,
        pending: list[dict[str, Any]],
        records: list[dict[str, Any]],
        product_rows: list[dict[str, Any]],
        price_ratios: list[float],
        minimum_cash: float,
        requested_lines: int,
        clipped_lines: int,
        fresh_purchases: int,
    ) -> dict[str, Any]:
        market = sum(row["market_demand"] for row in product_rows)
        eligible = sum(
            row["price_eligible_demand"] for row in product_rows
        )
        sold = sum(row["sold"] for row in product_rows)
        lost = sum(row["stockout_lost_units"] for row in product_rows)
        refused = sum(
            row["price_refused_units"] for row in product_rows
        )
        hard_refused = sum(
            row["hard_cutoff_refused_units"] for row in product_rows
        )
        spoiled = sum(
            sum(sum(items.values()) for items in record["spoiled_units"].values())
            for record in records
        )
        ending_inventory = round(
            sum(value(inventory) for inventory in inventories.values()), 2
        )
        ending_pending = round(
            sum(item["cost"] for item in pending), 2
        )
        ending_net_worth = round(
            cash + ending_inventory + ending_pending, 2
        )
        service_by_shop = {}
        for shop in self.shops:
            shop_rows = [row for row in product_rows if row["shop"] == shop]
            shop_demand = sum(
                row["price_eligible_demand"] for row in shop_rows
            )
            shop_sold = sum(row["sold"] for row in shop_rows)
            service_by_shop[shop] = self._ratio(shop_sold, shop_demand)
        summary = {
            "cycle": self.cycle,
            "policy": policy.name,
            "days_completed": len(records),
            "starting_cash": self.config["starting_shared_cash"],
            "ending_cash": round(cash, 2),
            "minimum_cash": round(minimum_cash, 2),
            "ending_inventory_value": ending_inventory,
            "ending_pending_value": ending_pending,
            "ending_net_worth": ending_net_worth,
            "accounting_profit": round(
                ending_net_worth - self.config["starting_shared_cash"], 2
            ),
            "revenue": round(sum(record["revenue"] for record in records), 2),
            "purchase_outflow": round(
                sum(record["order_cost"] for record in records), 2
            ),
            "cost_of_goods_sold": round(
                sum(record["cost_of_goods_sold"] for record in records), 2
            ),
            "rent": round(sum(record["rent"] for record in records), 2),
            "spoilage_cost": round(
                sum(record["spoilage_cost"] for record in records), 2
            ),
            "market_demand_units": market,
            "price_eligible_demand_units": eligible,
            "units_sold": sold,
            "stockout_lost_units": lost,
            "price_refused_units": refused,
            "hard_cutoff_refused_units": hard_refused,
            "service_rate": self._ratio(sold, eligible),
            "service_rate_by_shop": service_by_shop,
            "minimum_shop_service_rate": min(
                service_by_shop.values(), default=1.0
            ),
            "market_capture_rate": self._ratio(sold, market),
            "price_refusal_rate": self._ratio(refused, market),
            "spoiled_units": spoiled,
            "fresh_purchase_units": fresh_purchases,
            "spoilage_rate": self._ratio(spoiled, fresh_purchases),
            "profitable_day_percentage": self._ratio(
                sum(record["daily_profit"] > 0 for record in records),
                len(records),
            ),
            "median_price_reference_ratio": round(
                statistics.median(price_ratios), 4
            )
            if price_ratios
            else 1.0,
            "order_clip_rate": self._ratio(
                clipped_lines, requested_lines
            ),
            "validation_issue_count": sum(
                len(record["validation_issues"]) for record in records
            ),
            "codex_decision_days": sum(
                record["decision_source"].startswith("codex:")
                for record in records
            ),
            "fallback_days": sum(
                record["decision_source"].startswith("fallback:")
                for record in records
            ),
            "bankrupt": cash < 0,
        }
        criteria = self.config["success_criteria"]
        checks = {
            "completed_all_days": (
                summary["days_completed"]
                >= criteria["minimum_days_completed"]
            ),
            "minimum_profit": (
                summary["accounting_profit"]
                >= criteria["minimum_accounting_profit"]
            ),
            "overall_service": (
                summary["service_rate"]
                >= criteria["minimum_overall_service_rate"]
            ),
            "weakest_shop_service": (
                summary["minimum_shop_service_rate"]
                >= criteria["minimum_shop_service_rate"]
            ),
            "spoilage_control": (
                summary["spoilage_rate"]
                <= criteria["maximum_spoilage_rate"]
            ),
            "price_refusal_control": (
                summary["price_refusal_rate"]
                <= criteria["maximum_price_refusal_rate"]
            ),
            "no_hard_cutoff_pricing": (
                summary["hard_cutoff_refused_units"]
                <= criteria["maximum_hard_cutoff_refused_units"]
            ),
            "solvent": not summary["bankrupt"],
        }
        summary["challenge_checks"] = checks
        summary["challenge_pass"] = all(checks.values())
        summary["balanced_success"] = summary["challenge_pass"]
        return summary

    @staticmethod
    def _ratio(numerator: int | float, denominator: int | float) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    def _write_csv_outputs(
        self,
        cycle_dir: Path,
        records: list[dict[str, Any]],
        product_rows: list[dict[str, Any]],
    ) -> None:
        daily_fields = [
            "cycle",
            "day",
            "weekday",
            "decision_source",
            "cash",
            "revenue",
            "order_cost",
            "cost_of_goods_sold",
            "rent",
            "spoilage_cost",
            "daily_profit",
            "inventory_value",
            "pending_value",
            "net_worth",
            "units_sold",
            "validation_issue_count",
        ]
        with (cycle_dir / "daily_results.csv").open(
            "x", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=daily_fields)
            writer.writeheader()
            for record in records:
                writer.writerow(
                    {
                        "cycle": record["cycle"],
                        "day": record["day"],
                        "weekday": record["weekday"],
                        "decision_source": record["decision_source"],
                        "cash": record["cash"],
                        "revenue": record["revenue"],
                        "order_cost": record["order_cost"],
                        "cost_of_goods_sold": record[
                            "cost_of_goods_sold"
                        ],
                        "rent": record["rent"],
                        "spoilage_cost": record["spoilage_cost"],
                        "daily_profit": record["daily_profit"],
                        "inventory_value": record["inventory_value"],
                        "pending_value": record["pending_value"],
                        "net_worth": record["net_worth"],
                        "units_sold": sum(
                            sum(items.values())
                            for items in record["sales"].values()
                        ),
                        "validation_issue_count": len(
                            record["validation_issues"]
                        ),
                    }
                )
