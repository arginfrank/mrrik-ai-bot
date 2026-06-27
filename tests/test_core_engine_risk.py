from __future__ import annotations

from decimal import Decimal

import pytest

from services.core_engine.risk import (
    ExecutionPlanError,
    build_execution_plan,
    round_price_conservative,
)
from shared.exchange.types import SymbolFilters
from shared.signal.pnl import pnl_usdt_for_fraction
from shared.signal.types import SignalSide


HBAR_FILTERS = SymbolFilters(
    symbol="HBARUSDT",
    step_size=Decimal("1"),
    tick_size=Decimal("0.00001"),
    min_qty=Decimal("1"),
    min_notional=Decimal("5"),
)
WEIGHTS = (
    Decimal("0.60"),
    Decimal("0.20"),
    Decimal("0.10"),
    Decimal("0.07"),
    Decimal("0.03"),
)


def _plan(**overrides: object):
    values = {
        "side": SignalSide.LONG,
        "entry": Decimal("0.07145"),
        "stop_loss": Decimal("0.07077"),
        "leverage": 42,
        "targets": (
            Decimal("0.07186"),
            Decimal("0.07238"),
            Decimal("0.07296"),
            Decimal("0.07309"),
        ),
        "margin_usdt": Decimal("10"),
        "risk_model": 1,
        "model2_weights": WEIGHTS,
        "model3_exit_roi_pct": Decimal("20"),
        "filters": HBAR_FILTERS,
        "maintenance_margin_rate": Decimal("0.005"),
    }
    values.update(overrides)
    return build_execution_plan(**values)


def test_hbar_model1_plan_uses_decimal_exchange_sizes() -> None:
    plan = _plan()

    assert plan.qty == Decimal("5878")
    assert plan.notional_usdt == Decimal("420")
    assert len(plan.legs) == 4
    assert sum((leg.qty for leg in plan.legs), Decimal("0")) == plan.qty
    assert all(isinstance(leg.qty, Decimal) for leg in plan.legs)


def test_tiny_legs_merge_with_note() -> None:
    plan = _plan(
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        leverage=1,
        margin_usdt=Decimal("10"),
        targets=(Decimal("101"), Decimal("102"), Decimal("103"), Decimal("104")),
        filters=SymbolFilters(
            symbol="TESTUSDT",
            step_size=Decimal("0.01"),
            tick_size=Decimal("0.1"),
            min_qty=Decimal("0.01"),
            min_notional=Decimal("5"),
        ),
    )

    assert len(plan.legs) < 4
    assert plan.notes
    assert sum((leg.qty for leg in plan.legs), Decimal("0")) == plan.qty


def test_total_quantity_and_min_notional_guards() -> None:
    filters = SymbolFilters(
        symbol="TESTUSDT",
        step_size=Decimal("1"),
        tick_size=Decimal("0.1"),
        min_qty=Decimal("2"),
        min_notional=Decimal("500"),
    )
    with pytest.raises(ExecutionPlanError, match="quantity"):
        _plan(
            entry=Decimal("100"),
            stop_loss=Decimal("90"),
            targets=(Decimal("101"),),
            leverage=1,
            filters=filters,
        )
    with pytest.raises(ExecutionPlanError, match="notional"):
        _plan(
            entry=Decimal("100"),
            stop_loss=Decimal("90"),
            targets=(Decimal("101"),),
            leverage=1,
            filters=SymbolFilters(
                symbol="TESTUSDT",
                step_size=Decimal("0.01"),
                tick_size=Decimal("0.1"),
                min_qty=Decimal("0.01"),
                min_notional=Decimal("500"),
            ),
        )


def test_model2_is_tp1_weighted_and_model3_has_no_legs() -> None:
    model2 = _plan(risk_model=2)
    model3 = _plan(risk_model=3)

    assert model2.legs[0].fraction > model2.legs[1].fraction
    assert model3.legs == ()
    assert model3.model3_exit_price is not None


def test_price_rounding_is_conservative() -> None:
    assert round_price_conservative(
        side=SignalSide.LONG,
        purpose="tp",
        price=Decimal("1.239"),
        tick_size=Decimal("0.01"),
    ) == Decimal("1.23")
    assert round_price_conservative(
        side=SignalSide.LONG,
        purpose="sl",
        price=Decimal("1.239"),
        tick_size=Decimal("0.01"),
    ) == Decimal("1.23")
    assert round_price_conservative(
        side=SignalSide.SHORT,
        purpose="sl",
        price=Decimal("1.231"),
        tick_size=Decimal("0.01"),
    ) == Decimal("1.24")


def test_hbar_stop_loss_is_about_minus_40_percent_of_margin() -> None:
    pnl = pnl_usdt_for_fraction(
        margin_usdt=Decimal("10"),
        position_fraction=Decimal("1"),
        side=SignalSide.LONG,
        entry=Decimal("0.07145"),
        price=Decimal("0.07077"),
        leverage=42,
    )

    assert Decimal("-4.1") < pnl < Decimal("-3.9")
