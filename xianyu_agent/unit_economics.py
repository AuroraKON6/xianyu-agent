"""Contribution profit calculation (WP06).

Expected unit contribution profit (master plan 8.3):

    sale proceeds estimate
      - paid procurement cost
      - supplier freight
      - own packaging / delivery share
      - platform + payment fees
      - expected return loss
      - after-sales reserve
      - attributable per-order costs (acquisition, model usage)
    = contribution profit

Rules that this module must never break:
- A missing cost is ``unknown`` and the profit becomes ``unknown`` with
  the reasons attached. Nothing is ever filled with 0 to make a profit
  appear (``not_applicable`` components are skipped -- that is a stated
  fact, not a guess).
- Scenarios (conservative / base / optimistic) are built only from
  caller-provided overrides. Without range inputs the three scenarios
  are identical and the output says so.
- Platform fees come from current account+category terms passed by the
  caller; they are computed as ``price * rate + fixed`` only when a
  rate/fixed pair is provided, and are marked ``estimated`` in the
  output.
- Return loss is ``probability * loss_amount`` when both are provided,
  or a direct amount; never a guessed default.
- All money is Decimal; floats never enter the calculation.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .commerce.contracts import (
    PRICE_STATE_KNOWN,
    PRICE_STATE_NOT_APPLICABLE,
    PRICE_STATE_UNKNOWN,
    Money,
    money_from_mapping,
)

ECONOMICS_RULE_VERSION = "unit-economics-v1"

SCENARIO_CONSERVATIVE = "conservative"
SCENARIO_BASE = "base"
SCENARIO_OPTIMISTIC = "optimistic"
SCENARIOS = (SCENARIO_CONSERVATIVE, SCENARIO_BASE, SCENARIO_OPTIMISTIC)

OPTIONAL_COMPONENTS = ("installation", "after_sales_reserve", "attributable_costs")
REQUIRED_COMPONENTS = (
    "sale_price",
    "procurement_cost",
    "supplier_freight",
    "packaging",
    "platform_fee",
    "expected_return_loss",
)


class EconomicsError(ValueError):
    pass


def _money(value: Any, *, field_name: str, default_not_applicable: bool = False) -> Money:
    if value is None:
        if default_not_applicable:
            return Money.not_applicable()
        return Money.unknown(f"{field_name} not provided")
    return money_from_mapping(value, field_name=field_name)


def sum_components(components: Mapping[str, Money]) -> Money:
    """Sum known money; skip not_applicable; propagate unknown reasons."""
    known: List[Money] = []
    unknown_reasons: List[str] = []
    currency: Optional[str] = None
    for name, money in components.items():
        if money.state == PRICE_STATE_NOT_APPLICABLE:
            continue
        if money.state == PRICE_STATE_UNKNOWN:
            unknown_reasons.append(f"{name}: {money.reason or 'unknown'}")
            continue
        if currency is None:
            currency = money.currency
        elif money.currency != currency:
            raise EconomicsError(f"currency mismatch across components ({currency} vs {money.currency})")
        known.append(money)
    if unknown_reasons:
        return Money.unknown("; ".join(unknown_reasons))
    total = Money.known(Decimal("0"), currency or "CNY")
    for money in known:
        total = total.add(money)
    return total


def compute_platform_fee(
    sale_price: Money,
    *,
    rate: Optional[str],
    fixed: Optional[str],
) -> Money:
    """price * rate + fixed; unknown when either parameter is missing."""
    if rate is None and fixed is None:
        return Money.unknown("platform fee terms not provided (need current account+category rate/fixed)")
    price = sale_price
    if price.state != PRICE_STATE_KNOWN:
        return Money.unknown("sale price unknown, cannot compute platform fee")
    total = Decimal("0")
    if rate is not None:
        total += price.amount * Decimal(str(rate))  # type: ignore[union-attr]
    if fixed is not None:
        total += Decimal(str(fixed))
    return Money.known(total, price.currency)


def compute_return_loss(
    *,
    probability: Optional[str],
    loss_amount: Optional[Mapping[str, Any]],
) -> Money:
    if probability is not None and loss_amount is not None:
        loss = money_from_mapping(loss_amount, field_name="loss_amount")
        if loss.state != PRICE_STATE_KNOWN:
            return Money.unknown("loss_amount unknown")
        if Decimal(str(probability)) < 0 or Decimal(str(probability)) > 1:
            raise EconomicsError("return probability must be within [0, 1]")
        return Money.known(Decimal(str(probability)) * loss.amount, loss.currency)  # type: ignore[union-attr]
    if loss_amount is not None:
        return money_from_mapping(loss_amount, field_name="loss_amount")
    return Money.unknown("return loss inputs not provided")


def compute_unit_economics(
    *,
    sale_price: Optional[Mapping[str, Any]],
    procurement_cost: Optional[Mapping[str, Any]],
    supplier_freight: Optional[Mapping[str, Any]],
    packaging: Optional[Mapping[str, Any]],
    platform_fee: Optional[Mapping[str, Any]] = None,
    platform_fee_rate: Optional[str] = None,
    platform_fee_fixed: Optional[str] = None,
    expected_return_loss: Optional[Mapping[str, Any]] = None,
    return_probability: Optional[str] = None,
    return_loss_amount: Optional[Mapping[str, Any]] = None,
    installation: Optional[Mapping[str, Any]] = None,
    after_sales_reserve: Optional[Mapping[str, Any]] = None,
    attributable_costs: Optional[Mapping[str, Any]] = None,
    moq: Optional[int] = None,
    quantity: int = 1,
    scenario_overrides: Optional[Mapping[str, Mapping[str, Any]]] = None,
    target_margin: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute three-scenario contribution economics.

    ``scenario_overrides`` keys are scenario names; each value maps
    component names to scenario-specific Money mappings (e.g. a lower
    conservative sale price taken from the observed market range).
    """
    if quantity <= 0:
        raise EconomicsError("quantity must be positive")
    overrides = dict(scenario_overrides or {})

    def scenario_value(scenario: str, component: str, base_value: Any) -> Any:
        return overrides.get(scenario, {}).get(component, base_value)

    procurement = _money(procurement_cost, field_name="procurement_cost")
    freight = _money(supplier_freight, field_name="supplier_freight")
    if freight.state == PRICE_STATE_KNOWN and freight.amount == 0:
        freight_note = "supplier states freight included"
    else:
        freight_note = None

    scenarios: Dict[str, Dict[str, Any]] = {}
    for scenario in SCENARIOS:
        sale = money_from_mapping(
            scenario_value(scenario, "sale_price", sale_price), field_name="sale_price"
        )
        fee = money_from_mapping(
            scenario_value(scenario, "platform_fee", platform_fee), field_name="platform_fee"
        )
        if fee.state == PRICE_STATE_UNKNOWN and fee.reason and "not provided" in fee.reason:
            fee = compute_platform_fee(
                sale,
                rate=platform_fee_rate,
                fixed=platform_fee_fixed,
            )
        if fee.state == PRICE_STATE_KNOWN:
            fee = Money.known(fee.amount, fee.currency)  # normalize

        ret_loss = money_from_mapping(
            scenario_value(scenario, "expected_return_loss", expected_return_loss),
            field_name="expected_return_loss",
        )
        if ret_loss.state == PRICE_STATE_UNKNOWN and expected_return_loss is None and return_probability is None and return_loss_amount is None and overrides.get(scenario, {}).get("expected_return_loss") is None and overrides.get(scenario, {}).get("return_probability") is None:
            ret_loss = Money.unknown("return loss inputs not provided")
        elif ret_loss.state == PRICE_STATE_UNKNOWN:
            prob = scenario_value(scenario, "return_probability", return_probability)
            loss_amt = scenario_value(scenario, "return_loss_amount", return_loss_amount)
            ret_loss = compute_return_loss(probability=prob, loss_amount=loss_amt)

        components: Dict[str, Money] = {
            "sale_price": sale,
            "procurement_cost": procurement,
            "supplier_freight": freight,
            "packaging": _money(scenario_value(scenario, "packaging", packaging), field_name="packaging"),
            "platform_fee": fee,
            "expected_return_loss": ret_loss,
            "installation": _money(scenario_value(scenario, "installation", installation), field_name="installation", default_not_applicable=True),
            "after_sales_reserve": _money(scenario_value(scenario, "after_sales_reserve", after_sales_reserve), field_name="after_sales_reserve", default_not_applicable=True),
            "attributable_costs": _money(scenario_value(scenario, "attributable_costs", attributable_costs), field_name="attributable_costs", default_not_applicable=True),
        }

        inflow = components["sale_price"]
        outflow = sum_components(
            {name: value for name, value in components.items() if name != "sale_price"}
        )
        if inflow.state == PRICE_STATE_UNKNOWN or outflow.state == PRICE_STATE_UNKNOWN:
            profit = Money.unknown(
                "; ".join(
                    part
                    for part in (
                        inflow.reason or "" if inflow.state == PRICE_STATE_UNKNOWN else "",
                        outflow.reason or "" if outflow.state == PRICE_STATE_UNKNOWN else "",
                    )
                    if part
                )
                or "inputs unknown"
            )
        else:
            profit = Money.known(inflow.amount - outflow.amount, inflow.currency)  # type: ignore[union-attr]

        margin = (
            (profit.amount / inflow.amount)  # type: ignore[union-attr]
            if profit.state == PRICE_STATE_KNOWN and inflow.state == PRICE_STATE_KNOWN and inflow.amount != 0
            else None
        )

        # break-even / minimum viable sale price from known cost structure
        fixed_out = sum_components(
            {
                name: value
                for name, value in components.items()
                if name not in ("sale_price", "platform_fee")
            }
        )
        if (
            fee.state == PRICE_STATE_KNOWN
            and fixed_out.state == PRICE_STATE_KNOWN
            and platform_fee_rate is not None
            and Decimal(str(platform_fee_rate)) < 1
        ):
            rate = Decimal(str(platform_fee_rate))
            fixed_fee = Decimal(str(platform_fee_fixed or "0"))
            break_even = (fixed_out.amount + fixed_fee) / (Decimal("1") - rate)  # type: ignore[union-attr]
            currency = fee.currency
        elif fixed_out.state == PRICE_STATE_KNOWN:
            break_even = fixed_out.amount  # type: ignore[union-attr]
            currency = fixed_out.currency
        else:
            break_even = None
            currency = inflow.currency if inflow.state == PRICE_STATE_KNOWN else "CNY"

        min_viable = break_even
        if break_even is not None and target_margin is not None and platform_fee_rate is not None:
            rate = Decimal(str(platform_fee_rate))
            fixed_fee = Decimal(str(platform_fee_fixed or "0"))
            desired = Decimal(str(target_margin))
            if rate + desired < 1:
                min_viable = (fixed_out.amount + fixed_fee) / (Decimal("1") - rate - desired)  # type: ignore[union-attr]

        scenarios[scenario] = {
            "components": {name: value.to_dict() for name, value in components.items()},
            "contribution_profit": profit.to_dict(),
            "contribution_margin": str(margin.quantize(Decimal("0.0001"))) if margin is not None else None,
            "break_even_price": Money.known(break_even, currency).to_dict() if break_even is not None else Money.unknown("cost structure incomplete").to_dict(),
            "min_viable_price": Money.known(min_viable, currency).to_dict() if min_viable is not None else None,
            "status": profit.state,
            "missing": [
                f"{name}: {value.reason}"
                for name, value in components.items()
                if value.state == PRICE_STATE_UNKNOWN
            ],
        }
        if freight_note and scenario == SCENARIO_BASE:
            scenarios[scenario]["notes"] = [freight_note]

    # cash occupied by one procurement batch
    unit_cash_parts = {
        "procurement": procurement,
        "freight": freight,
    }
    unit_cash = sum_components(unit_cash_parts)
    if unit_cash.state == PRICE_STATE_KNOWN:
        cash_occupied = Money.known(unit_cash.amount * Decimal(max(moq or 1, quantity)), unit_cash.currency)
    else:
        cash_occupied = unit_cash

    scenario_ids = {
        name: scenarios[name]["contribution_profit"] for name in SCENARIOS
    }
    distinct = len({v.get("amount") for v in scenario_ids.values()}) > 1
    evidence_gaps: List[str] = []
    for name in SCENARIOS:
        evidence_gaps.extend(f"{name}/{gap}" for gap in scenarios[name]["missing"])

    return {
        "rule_version": ECONOMICS_RULE_VERSION,
        "currency": procurement.currency if procurement.state == PRICE_STATE_KNOWN else "CNY",
        "quantity": quantity,
        "moq": moq,
        "scenarios": scenarios,
        "scenarios_distinct": distinct,
        "scenarios_note": None
        if distinct
        else "no range inputs provided; conservative/base/optimistic are identical",
        "cash_occupied": cash_occupied.to_dict(),
        "evidence_gaps": sorted(set(evidence_gaps)),
        "computed_with_estimated_platform_fee": platform_fee_rate is not None,
    }
