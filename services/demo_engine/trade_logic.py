from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from shared.signal.pnl import (
    approximate_liquidation_price,
    capped_loss_usdt_on_liquidation,
    pnl_usdt_for_fraction,
)
from shared.signal.sizing import equal_fractions, model2_fractions
from shared.signal.types import SignalSide


@dataclass(frozen=True)
class DemoLegPlan:
    leg_index: int
    target_price: Decimal
    fraction: Decimal
    qty: Decimal


@dataclass(frozen=True)
class DemoOpenPlan:
    margin_usdt: Decimal
    notional_usdt: Decimal
    qty: Decimal
    liq_price: Decimal
    legs: tuple[DemoLegPlan, ...]
    fields_realism_applied: dict[str, bool]


@dataclass(frozen=True)
class DemoCloseDecision:
    should_close: bool
    closed_reason: str | None = None
    exit_price: Decimal | None = None
    realized_roi_pct: Decimal | None = None
    realized_pnl_usdt: Decimal | None = None
    touched_tps: tuple[int, ...] = field(default_factory=tuple)
    filled_leg_indices: tuple[int, ...] = field(default_factory=tuple)


def crosses_take_profit(*, side: SignalSide, price: Decimal, target: Decimal) -> bool:
    """LONG: price >= target. SHORT: price <= target."""
    return price >= target if side is SignalSide.LONG else price <= target


def crosses_stop_loss(*, side: SignalSide, price: Decimal, stop_loss: Decimal) -> bool:
    """LONG: price <= SL. SHORT: price >= SL."""
    return price <= stop_loss if side is SignalSide.LONG else price >= stop_loss


def crosses_liquidation(*, side: SignalSide, price: Decimal, liq_price: Decimal) -> bool:
    """LONG: price <= liq. SHORT: price >= liq."""
    return price <= liq_price if side is SignalSide.LONG else price >= liq_price


def current_stop_loss(
    *,
    original_stop_loss: Decimal,
    entry: Decimal,
    touched_tps: tuple[int, ...],
    move_sl_to_be_after_tp1: bool,
) -> Decimal:
    """Return BE stop after TP1 if enabled and touched."""
    if move_sl_to_be_after_tp1 and 1 in touched_tps:
        return entry
    return original_stop_loss


def model3_exit_price(
    *,
    side: SignalSide,
    entry: Decimal,
    leverage: int,
    model3_exit_roi_pct: Decimal,
) -> Decimal:
    """Return price threshold for model 3 ROI exit."""
    if entry <= 0:
        raise ValueError("entry must be positive")
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    if model3_exit_roi_pct <= 0:
        raise ValueError("model3_exit_roi_pct must be positive")
    move = model3_exit_roi_pct / (Decimal("100") * Decimal(leverage))
    multiplier = Decimal("1") + move if side is SignalSide.LONG else Decimal("1") - move
    return entry * multiplier


def build_demo_open_plan(
    *,
    side: SignalSide,
    entry: Decimal,
    stop_loss: Decimal,
    leverage: int,
    targets: tuple[Decimal, ...],
    margin_usdt: Decimal,
    risk_model: int,
    model3_exit_roi_pct: Decimal,
    model2_weights: tuple[Decimal, ...],
    maintenance_margin_rate: Decimal,
    include_commission: bool,
    include_funding: bool,
    include_slippage: bool,
) -> DemoOpenPlan:
    """Build a virtual demo open plan without exchange REST calls."""
    if entry <= 0:
        raise ValueError("entry must be positive")
    if stop_loss <= 0:
        raise ValueError("stop_loss must be positive")
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    if margin_usdt <= 0:
        raise ValueError("margin_usdt must be positive")
    if risk_model not in {1, 2, 3}:
        raise ValueError("risk_model must be 1, 2, or 3")
    if risk_model in {1, 2} and not targets:
        raise ValueError("risk models 1 and 2 require at least one target")

    notional_usdt = margin_usdt * Decimal(leverage)
    qty = notional_usdt / entry
    if risk_model == 1:
        fractions = equal_fractions(len(targets))
    elif risk_model == 2:
        fractions = model2_fractions(len(targets), model2_weights)
    else:
        model3_exit_price(
            side=side,
            entry=entry,
            leverage=leverage,
            model3_exit_roi_pct=model3_exit_roi_pct,
        )
        fractions = ()

    legs = (
        tuple(
            DemoLegPlan(
                leg_index=index,
                target_price=target,
                fraction=fraction,
                qty=qty * fraction,
            )
            for index, (target, fraction) in enumerate(
                zip(targets, fractions, strict=True),
                start=1,
            )
        )
        if risk_model in {1, 2}
        else ()
    )
    return DemoOpenPlan(
        margin_usdt=margin_usdt,
        notional_usdt=notional_usdt,
        qty=qty,
        liq_price=approximate_liquidation_price(
            side=side,
            entry=entry,
            leverage=leverage,
            maintenance_margin_rate=maintenance_margin_rate,
        ),
        legs=legs,
        fields_realism_applied={
            "include_commission": include_commission,
            "include_funding": include_funding,
            "include_slippage": include_slippage,
        },
    )


def evaluate_price_tick(
    *,
    side: SignalSide,
    entry: Decimal,
    original_stop_loss: Decimal,
    leverage: int,
    margin_usdt: Decimal,
    liq_price: Decimal,
    current_price: Decimal,
    risk_model: int,
    model3_exit_roi_pct: Decimal,
    move_sl_to_be_after_tp1: bool,
    open_legs: tuple[DemoLegPlan, ...],
    touched_tps: tuple[int, ...],
    maintenance_margin_rate: Decimal,
) -> DemoCloseDecision:
    """Evaluate one mark-price tick for one open demo trade."""
    if maintenance_margin_rate < 0:
        raise ValueError("maintenance_margin_rate must not be negative")
    if margin_usdt <= 0:
        raise ValueError("margin_usdt must be positive")
    if risk_model not in {1, 2, 3}:
        raise ValueError("risk_model must be 1, 2, or 3")

    known_tps = tuple(sorted(set(touched_tps)))
    if crosses_liquidation(side=side, price=current_price, liq_price=liq_price):
        return DemoCloseDecision(
            should_close=True,
            closed_reason="liquidation",
            exit_price=liq_price,
            realized_roi_pct=Decimal("-100"),
            realized_pnl_usdt=capped_loss_usdt_on_liquidation(
                margin_usdt=margin_usdt
            ),
            touched_tps=known_tps,
        )

    if risk_model == 3:
        threshold = model3_exit_price(
            side=side,
            entry=entry,
            leverage=leverage,
            model3_exit_roi_pct=model3_exit_roi_pct,
        )
        if crosses_take_profit(side=side, price=current_price, target=threshold):
            pnl = pnl_usdt_for_fraction(
                margin_usdt=margin_usdt,
                position_fraction=Decimal("1"),
                side=side,
                entry=entry,
                price=threshold,
                leverage=leverage,
            )
            return _closed_decision(
                reason="model3_exit",
                exit_price=threshold,
                pnl=pnl,
                margin_usdt=margin_usdt,
                touched_tps=known_tps,
                filled_leg_indices=(),
            )
    else:
        newly_filled = tuple(
            leg.leg_index
            for leg in open_legs
            if leg.leg_index not in known_tps
            and crosses_take_profit(
                side=side,
                price=current_price,
                target=leg.target_price,
            )
        )
        updated_tps = tuple(sorted(set((*known_tps, *newly_filled))))
        if open_legs and all(leg.leg_index in updated_tps for leg in open_legs):
            pnl = _blended_pnl(
                legs=open_legs,
                touched_tps=updated_tps,
                remaining_exit_price=None,
                margin_usdt=margin_usdt,
                side=side,
                entry=entry,
                leverage=leverage,
            )
            return _closed_decision(
                reason="all_tp",
                exit_price=current_price,
                pnl=pnl,
                margin_usdt=margin_usdt,
                touched_tps=updated_tps,
                filled_leg_indices=newly_filled,
            )

        active_stop = current_stop_loss(
            original_stop_loss=original_stop_loss,
            entry=entry,
            touched_tps=updated_tps,
            move_sl_to_be_after_tp1=move_sl_to_be_after_tp1,
        )
        if crosses_stop_loss(side=side, price=current_price, stop_loss=active_stop):
            pnl = _blended_pnl(
                legs=open_legs,
                touched_tps=updated_tps,
                remaining_exit_price=active_stop,
                margin_usdt=margin_usdt,
                side=side,
                entry=entry,
                leverage=leverage,
            )
            reason = "be" if active_stop == entry else "sl"
            return _closed_decision(
                reason=reason,
                exit_price=active_stop,
                pnl=pnl,
                margin_usdt=margin_usdt,
                touched_tps=updated_tps,
                filled_leg_indices=newly_filled,
            )

        return DemoCloseDecision(
            should_close=False,
            touched_tps=updated_tps,
            filled_leg_indices=newly_filled,
        )

    active_stop = current_stop_loss(
        original_stop_loss=original_stop_loss,
        entry=entry,
        touched_tps=known_tps,
        move_sl_to_be_after_tp1=move_sl_to_be_after_tp1,
    )
    if crosses_stop_loss(side=side, price=current_price, stop_loss=active_stop):
        pnl = pnl_usdt_for_fraction(
            margin_usdt=margin_usdt,
            position_fraction=Decimal("1"),
            side=side,
            entry=entry,
            price=active_stop,
            leverage=leverage,
        )
        return _closed_decision(
            reason="be" if active_stop == entry else "sl",
            exit_price=active_stop,
            pnl=pnl,
            margin_usdt=margin_usdt,
            touched_tps=known_tps,
            filled_leg_indices=(),
        )
    return DemoCloseDecision(should_close=False, touched_tps=known_tps)


def _blended_pnl(
    *,
    legs: tuple[DemoLegPlan, ...],
    touched_tps: tuple[int, ...],
    remaining_exit_price: Decimal | None,
    margin_usdt: Decimal,
    side: SignalSide,
    entry: Decimal,
    leverage: int,
) -> Decimal:
    pnl = Decimal("0")
    for leg in legs:
        if leg.leg_index in touched_tps:
            exit_price = leg.target_price
        elif remaining_exit_price is not None:
            exit_price = remaining_exit_price
        else:
            continue
        pnl += pnl_usdt_for_fraction(
            margin_usdt=margin_usdt,
            position_fraction=leg.fraction,
            side=side,
            entry=entry,
            price=exit_price,
            leverage=leverage,
        )
    return pnl


def _closed_decision(
    *,
    reason: str,
    exit_price: Decimal,
    pnl: Decimal,
    margin_usdt: Decimal,
    touched_tps: tuple[int, ...],
    filled_leg_indices: tuple[int, ...],
) -> DemoCloseDecision:
    return DemoCloseDecision(
        should_close=True,
        closed_reason=reason,
        exit_price=exit_price,
        realized_roi_pct=pnl / margin_usdt * Decimal("100"),
        realized_pnl_usdt=pnl,
        touched_tps=touched_tps,
        filled_leg_indices=filled_leg_indices,
    )
