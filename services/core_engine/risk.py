from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from shared.exchange.types import SymbolFilters
from shared.signal.pnl import approximate_liquidation_price
from shared.signal.sizing import equal_fractions, model2_fractions
from shared.signal.types import SignalSide


@dataclass(frozen=True)
class ExecutionLegPlan:
    leg_index: int
    target_price: Decimal
    fraction: Decimal
    qty: Decimal


@dataclass(frozen=True)
class ExecutionPlan:
    margin_usdt: Decimal
    leverage: int
    notional_usdt: Decimal
    qty: Decimal
    entry_price: Decimal
    stop_loss: Decimal
    liq_price: Decimal
    legs: tuple[ExecutionLegPlan, ...]
    risk_model: int
    model3_exit_price: Decimal | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


class ExecutionPlanError(ValueError):
    """Raised when a signal cannot be executed safely."""


def round_qty_down(*, qty: Decimal, step_size: Decimal) -> Decimal:
    if qty < 0:
        raise ExecutionPlanError("quantity must not be negative")
    if step_size <= 0:
        raise ExecutionPlanError("step size must be positive")
    return (qty / step_size).to_integral_value(rounding=ROUND_FLOOR) * step_size


def round_price_conservative(
    *,
    side: SignalSide,
    purpose: str,
    price: Decimal,
    tick_size: Decimal,
) -> Decimal:
    """Round TP/SL/entry prices conservatively."""
    if price <= 0 or tick_size <= 0:
        raise ExecutionPlanError("price and tick size must be positive")
    if purpose not in {"entry", "tp", "sl"}:
        raise ExecutionPlanError("purpose must be entry, tp, or sl")
    rounding = ROUND_FLOOR if side is SignalSide.LONG else ROUND_CEILING
    return (price / tick_size).to_integral_value(rounding=rounding) * tick_size


def build_execution_plan(
    *,
    side: SignalSide,
    entry: Decimal,
    stop_loss: Decimal,
    leverage: int,
    targets: tuple[Decimal, ...],
    margin_usdt: Decimal,
    risk_model: int,
    model2_weights: tuple[Decimal, ...],
    model3_exit_roi_pct: Decimal,
    filters: SymbolFilters,
    maintenance_margin_rate: Decimal,
) -> ExecutionPlan:
    """Build an exchange-ready plan, merging tiny legs when needed."""
    _validate_inputs(
        side=side,
        entry=entry,
        stop_loss=stop_loss,
        leverage=leverage,
        targets=targets,
        margin_usdt=margin_usdt,
        risk_model=risk_model,
        filters=filters,
        maintenance_margin_rate=maintenance_margin_rate,
    )
    entry_price = round_price_conservative(
        side=side, purpose="entry", price=entry, tick_size=filters.tick_size
    )
    rounded_stop = round_price_conservative(
        side=side, purpose="sl", price=stop_loss, tick_size=filters.tick_size
    )
    notional = margin_usdt * Decimal(leverage)
    qty = round_qty_down(qty=notional / entry_price, step_size=filters.step_size)
    if qty < filters.min_qty or qty <= 0:
        raise ExecutionPlanError("total quantity is below minQty")
    if qty * entry_price < filters.min_notional:
        raise ExecutionPlanError("total notional is below minNotional")

    liq_price = approximate_liquidation_price(
        side=side,
        entry=entry_price,
        leverage=leverage,
        maintenance_margin_rate=maintenance_margin_rate,
    )
    if risk_model == 3:
        if model3_exit_roi_pct <= 0:
            raise ExecutionPlanError("model 3 exit ROI must be positive")
        move = model3_exit_roi_pct / (Decimal("100") * Decimal(leverage))
        raw_exit = entry_price * (
            Decimal("1") + move if side is SignalSide.LONG else Decimal("1") - move
        )
        return ExecutionPlan(
            margin_usdt=margin_usdt,
            leverage=leverage,
            notional_usdt=notional,
            qty=qty,
            entry_price=entry_price,
            stop_loss=rounded_stop,
            liq_price=liq_price,
            legs=(),
            risk_model=risk_model,
            model3_exit_price=round_price_conservative(
                side=side,
                purpose="tp",
                price=raw_exit,
                tick_size=filters.tick_size,
            ),
        )

    try:
        fractions = (
            equal_fractions(len(targets))
            if risk_model == 1
            else model2_fractions(len(targets), model2_weights)
        )
    except ValueError as error:
        raise ExecutionPlanError(str(error)) from error
    quantities = [
        round_qty_down(qty=qty * fraction, step_size=filters.step_size)
        for fraction in fractions
    ]
    quantities[0] += qty - sum(quantities, Decimal("0"))
    legs, notes = _merge_tiny_legs(
        side=side,
        targets=targets,
        fractions=fractions,
        quantities=quantities,
        filters=filters,
    )
    return ExecutionPlan(
        margin_usdt=margin_usdt,
        leverage=leverage,
        notional_usdt=notional,
        qty=qty,
        entry_price=entry_price,
        stop_loss=rounded_stop,
        liq_price=liq_price,
        legs=legs,
        risk_model=risk_model,
        notes=notes,
    )


def _merge_tiny_legs(
    *,
    side: SignalSide,
    targets: tuple[Decimal, ...],
    fractions: tuple[Decimal, ...],
    quantities: list[Decimal],
    filters: SymbolFilters,
) -> tuple[tuple[ExecutionLegPlan, ...], tuple[str, ...]]:
    legs: list[ExecutionLegPlan] = []
    notes: list[str] = []
    pending_index: int | None = None
    pending_target = Decimal("0")
    pending_fraction = Decimal("0")
    pending_qty = Decimal("0")

    for index, (target, fraction, leg_qty) in enumerate(
        zip(targets, fractions, quantities, strict=True), start=1
    ):
        rounded_target = round_price_conservative(
            side=side, purpose="tp", price=target, tick_size=filters.tick_size
        )
        if pending_index is None:
            pending_index = index
            pending_target = rounded_target
        pending_fraction += fraction
        pending_qty += leg_qty
        valid = (
            pending_qty >= filters.min_qty
            and pending_qty * pending_target >= filters.min_notional
        )
        if not valid:
            continue

        if legs and index != pending_index:
            previous = legs[-1]
            legs[-1] = ExecutionLegPlan(
                leg_index=previous.leg_index,
                target_price=previous.target_price,
                fraction=previous.fraction + pending_fraction,
                qty=previous.qty + pending_qty,
            )
            notes.append(
                f"merged legs {pending_index}-{index} into leg "
                f"{previous.leg_index}: below exchange minimums"
            )
        else:
            legs.append(
                ExecutionLegPlan(
                    leg_index=pending_index,
                    target_price=pending_target,
                    fraction=pending_fraction,
                    qty=pending_qty,
                )
            )
            if index != pending_index:
                notes.append(
                    f"merged legs {pending_index}-{index}: below exchange minimums"
                )
        pending_index = None
        pending_fraction = Decimal("0")
        pending_qty = Decimal("0")

    if pending_index is not None:
        if not legs:
            raise ExecutionPlanError("no TP leg satisfies exchange minimums")
        previous = legs[-1]
        legs[-1] = ExecutionLegPlan(
            leg_index=previous.leg_index,
            target_price=previous.target_price,
            fraction=previous.fraction + pending_fraction,
            qty=previous.qty + pending_qty,
        )
        notes.append(
            f"merged leg {pending_index} into leg {previous.leg_index}: "
            "below exchange minimums"
        )
    return tuple(legs), tuple(notes)


def _validate_inputs(
    *,
    side: SignalSide,
    entry: Decimal,
    stop_loss: Decimal,
    leverage: int,
    targets: tuple[Decimal, ...],
    margin_usdt: Decimal,
    risk_model: int,
    filters: SymbolFilters,
    maintenance_margin_rate: Decimal,
) -> None:
    if entry <= 0 or stop_loss <= 0 or margin_usdt <= 0:
        raise ExecutionPlanError("entry, stop loss, and margin must be positive")
    if leverage <= 0:
        raise ExecutionPlanError("leverage must be positive")
    if risk_model not in {1, 2, 3}:
        raise ExecutionPlanError("risk model must be 1, 2, or 3")
    if risk_model in {1, 2} and not targets:
        raise ExecutionPlanError("risk models 1 and 2 require targets")
    if filters.step_size <= 0 or filters.tick_size <= 0:
        raise ExecutionPlanError("exchange filters must be positive")
    if filters.min_qty < 0 or filters.min_notional < 0:
        raise ExecutionPlanError("exchange minimums must not be negative")
    if maintenance_margin_rate < 0:
        raise ExecutionPlanError("maintenance margin rate must not be negative")
    if side is SignalSide.LONG and stop_loss >= entry:
        raise ExecutionPlanError("LONG stop loss must be below entry")
    if side is SignalSide.SHORT and stop_loss <= entry:
        raise ExecutionPlanError("SHORT stop loss must be above entry")
    if risk_model in {1, 2}:
        wrong_side = (
            any(target <= entry for target in targets)
            if side is SignalSide.LONG
            else any(target >= entry for target in targets)
        )
        if wrong_side:
            raise ExecutionPlanError("target is on the wrong side of entry")
