from decimal import Decimal

import pytest

from shared.signal import (
    SignalSide,
    SymbolFilters,
    equal_fractions,
    floor_to_step,
    model2_fractions,
    round_price_to_tick,
    size_position,
)


def test_floor_to_step() -> None:
    assert floor_to_step(Decimal("1.23456"), Decimal("0.001")) == Decimal("1.234")


def test_equal_fractions_for_four_targets_sum_exactly_to_one() -> None:
    fractions = equal_fractions(4)

    assert fractions == (Decimal("0.25"),) * 4
    assert sum(fractions, Decimal("0")) == Decimal("1")


def test_model2_fractions_truncate_and_renormalize_for_three_targets() -> None:
    fractions = model2_fractions(3)

    assert fractions[0] == Decimal("0.60") / Decimal("0.90")
    assert fractions[1] == Decimal("0.20") / Decimal("0.90")
    assert sum(fractions, Decimal("0")) == Decimal("1")


def test_hbar_position_sizing_returns_four_positive_legs() -> None:
    result = size_position(
        margin_usdt=Decimal("10"),
        leverage=42,
        entry=Decimal("0.07145"),
        targets=(
            Decimal("0.07186"),
            Decimal("0.07238"),
            Decimal("0.07296"),
            Decimal("0.07309"),
        ),
        filters=SymbolFilters(
            step_size=Decimal("0.1"),
            tick_size=Decimal("0.00001"),
            min_qty=Decimal("0.1"),
            min_notional=Decimal("5"),
        ),
        side=SignalSide.LONG,
    )

    assert result.qty > 0
    assert result.notional_usdt == Decimal("420")
    assert len(result.legs) == 4
    assert all(leg.qty > 0 for leg in result.legs)
    assert sum((leg.qty for leg in result.legs), Decimal("0")) == result.qty
    assert result.notes == ()


def test_final_tiny_leg_merges_into_previous_leg_with_note() -> None:
    result = size_position(
        margin_usdt=Decimal("10"),
        leverage=10,
        entry=Decimal("100"),
        targets=(Decimal("110"), Decimal("120"), Decimal("130")),
        filters=SymbolFilters(
            step_size=Decimal("0.01"),
            tick_size=Decimal("0.1"),
            min_qty=Decimal("0.01"),
            min_notional=Decimal("5"),
        ),
        side=SignalSide.LONG,
        fractions=(Decimal("0.80"), Decimal("0.19"), Decimal("0.01")),
    )

    assert len(result.legs) == 2
    assert result.legs[-1].fraction == Decimal("0.20")
    assert sum((leg.qty for leg in result.legs), Decimal("0")) == result.qty
    assert len(result.notes) == 1
    assert "merged leg 3 into leg 2" in result.notes[0]


def test_first_leg_below_minimum_raises() -> None:
    with pytest.raises(ValueError, match="first leg"):
        size_position(
            margin_usdt=Decimal("10"),
            leverage=10,
            entry=Decimal("100"),
            targets=(Decimal("110"), Decimal("120")),
            filters=SymbolFilters(
                step_size=Decimal("0.01"),
                tick_size=Decimal("0.1"),
                min_qty=Decimal("0.01"),
                min_notional=Decimal("5"),
            ),
            side=SignalSide.LONG,
            fractions=(Decimal("0.01"), Decimal("0.99")),
        )


def test_conservative_tick_rounding() -> None:
    assert round_price_to_tick(
        price=Decimal("101.234"),
        tick_size=Decimal("0.1"),
        side=SignalSide.LONG,
        order_kind="tp",
    ) == Decimal("101.2")
    assert round_price_to_tick(
        price=Decimal("98.234"),
        tick_size=Decimal("0.1"),
        side=SignalSide.SHORT,
        order_kind="tp",
    ) == Decimal("98.3")


def test_invalid_custom_fractions_raise() -> None:
    with pytest.raises(ValueError, match="sum exactly"):
        size_position(
            margin_usdt=Decimal("10"),
            leverage=10,
            entry=Decimal("100"),
            targets=(Decimal("110"), Decimal("120")),
            filters=SymbolFilters(
                step_size=Decimal("0.01"),
                tick_size=Decimal("0.1"),
                min_qty=Decimal("0.01"),
                min_notional=Decimal("5"),
            ),
            side=SignalSide.LONG,
            fractions=(Decimal("0.50"), Decimal("0.49")),
        )
