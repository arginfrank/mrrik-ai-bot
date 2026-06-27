from __future__ import annotations

from dataclasses import fields
from decimal import Decimal

from services.demo_engine.trade_logic import (
    DemoLegPlan,
    build_demo_open_plan,
    crosses_liquidation,
    crosses_stop_loss,
    crosses_take_profit,
    evaluate_price_tick,
    model3_exit_price,
)
from shared.signal.types import SignalSide


D = Decimal


def test_take_profit_and_stop_crossings_for_both_sides() -> None:
    assert crosses_take_profit(side=SignalSide.LONG, price=D("101"), target=D("101"))
    assert crosses_take_profit(side=SignalSide.SHORT, price=D("99"), target=D("99"))
    assert crosses_stop_loss(side=SignalSide.LONG, price=D("98"), stop_loss=D("99"))
    assert crosses_stop_loss(side=SignalSide.SHORT, price=D("102"), stop_loss=D("101"))
    assert crosses_liquidation(side=SignalSide.LONG, price=D("90"), liq_price=D("91"))


def test_liquidation_precedes_stop_and_caps_loss_at_margin() -> None:
    decision = evaluate_price_tick(
        side=SignalSide.LONG,
        entry=D("100"),
        original_stop_loss=D("90"),
        leverage=10,
        margin_usdt=D("10"),
        liq_price=D("90.5"),
        current_price=D("89"),
        risk_model=1,
        model3_exit_roi_pct=D("20"),
        move_sl_to_be_after_tp1=True,
        open_legs=(DemoLegPlan(1, D("110"), D("1"), D("1")),),
        touched_tps=(),
        maintenance_margin_rate=D("0.005"),
    )

    assert decision.closed_reason == "liquidation"
    assert decision.realized_pnl_usdt == D("-10")
    assert decision.realized_roi_pct == D("-100")


def test_hbar_normal_stop_is_not_full_liquidation() -> None:
    plan = build_demo_open_plan(
        side=SignalSide.LONG,
        entry=D("0.07145"),
        stop_loss=D("0.07077"),
        leverage=42,
        targets=(D("0.07186"),),
        margin_usdt=D("10"),
        risk_model=1,
        model3_exit_roi_pct=D("20"),
        model2_weights=(D("1"),),
        maintenance_margin_rate=D("0.005"),
        include_commission=False,
        include_funding=False,
        include_slippage=False,
    )
    decision = evaluate_price_tick(
        side=SignalSide.LONG,
        entry=D("0.07145"),
        original_stop_loss=D("0.07077"),
        leverage=42,
        margin_usdt=D("10"),
        liq_price=plan.liq_price,
        current_price=D("0.07077"),
        risk_model=1,
        model3_exit_roi_pct=D("20"),
        move_sl_to_be_after_tp1=True,
        open_legs=plan.legs,
        touched_tps=(),
        maintenance_margin_rate=D("0.005"),
    )

    assert decision.closed_reason == "sl"
    assert abs(decision.realized_pnl_usdt - D("-3.9972")) < D("0.0001")
    assert decision.realized_pnl_usdt > D("-10")


def test_model1_all_targets_produces_blended_positive_result() -> None:
    plan = _open_plan(risk_model=1)
    decision = _evaluate(plan, current_price=D("120"), risk_model=1)

    assert decision.closed_reason == "all_tp"
    assert decision.touched_tps == (1, 2)
    assert decision.realized_pnl_usdt > 0


def test_tp1_then_break_even_blends_filled_and_remaining_legs() -> None:
    plan = _open_plan(risk_model=1)
    first = _evaluate(plan, current_price=D("110"), risk_model=1)
    assert first.should_close is False
    assert first.filled_leg_indices == (1,)

    second = _evaluate(
        plan,
        current_price=D("100"),
        risk_model=1,
        touched_tps=first.touched_tps,
    )
    assert second.closed_reason == "be"
    assert second.touched_tps == (1,)
    assert second.realized_pnl_usdt == D("5")


def test_model2_uses_tp1_weighted_fractions() -> None:
    plan = _open_plan(risk_model=2)

    assert plan.legs[0].fraction == D("0.75")
    assert plan.legs[1].fraction == D("0.25")


def test_model3_has_no_legs_and_closes_at_roi_threshold() -> None:
    plan = _open_plan(risk_model=3)
    threshold = model3_exit_price(
        side=SignalSide.LONG,
        entry=D("100"),
        leverage=10,
        model3_exit_roi_pct=D("20"),
    )
    decision = _evaluate(plan, current_price=threshold, risk_model=3)

    assert plan.legs == ()
    assert decision.closed_reason == "model3_exit"
    assert decision.realized_roi_pct == D("20")
    assert decision.realized_pnl_usdt == D("2")


def test_trade_plans_and_decisions_never_contain_float_values() -> None:
    plan = _open_plan(risk_model=1)
    decision = _evaluate(plan, current_price=D("120"), risk_model=1)

    assert all(not isinstance(getattr(plan, item.name), float) for item in fields(plan))
    assert all(not isinstance(getattr(decision, item.name), float) for item in fields(decision))


def _open_plan(*, risk_model: int):
    return build_demo_open_plan(
        side=SignalSide.LONG,
        entry=D("100"),
        stop_loss=D("95"),
        leverage=10,
        targets=(D("110"), D("120")),
        margin_usdt=D("10"),
        risk_model=risk_model,
        model3_exit_roi_pct=D("20"),
        model2_weights=(D("0.60"), D("0.20")),
        maintenance_margin_rate=D("0.005"),
        include_commission=False,
        include_funding=False,
        include_slippage=False,
    )


def _evaluate(
    plan,
    *,
    current_price: Decimal,
    risk_model: int,
    touched_tps: tuple[int, ...] = (),
):
    return evaluate_price_tick(
        side=SignalSide.LONG,
        entry=D("100"),
        original_stop_loss=D("95"),
        leverage=10,
        margin_usdt=D("10"),
        liq_price=plan.liq_price,
        current_price=current_price,
        risk_model=risk_model,
        model3_exit_roi_pct=D("20"),
        move_sl_to_be_after_tp1=True,
        open_legs=plan.legs,
        touched_tps=touched_tps,
        maintenance_margin_rate=D("0.005"),
    )
