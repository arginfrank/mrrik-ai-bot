from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from shared.signal.types import SignalSide


@dataclass(frozen=True)
class SymbolFilters:
    step_size: Decimal
    tick_size: Decimal
    min_qty: Decimal
    min_notional: Decimal


@dataclass(frozen=True)
class SizedLeg:
    leg_index: int
    target_price: Decimal
    fraction: Decimal
    qty: Decimal
    notional: Decimal


@dataclass(frozen=True)
class SizingResult:
    qty: Decimal
    notional_usdt: Decimal
    margin_usdt: Decimal
    leverage: int
    legs: tuple[SizedLeg, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Floor value to exchange step size."""

    if step <= 0:
        raise ValueError("step must be positive")
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def round_price_to_tick(
    *,
    price: Decimal,
    tick_size: Decimal,
    side: SignalSide,
    order_kind: str,
) -> Decimal:
    """Round prices conservatively.

    order_kind must be "tp" or "sl".
    TP slightly nearer is allowed.
    SL must never be tighter than the signal.
    LONG TP floors, LONG SL floors.
    SHORT TP ceilings, SHORT SL ceilings.
    """

    if price <= 0:
        raise ValueError("price must be positive")
    if tick_size <= 0:
        raise ValueError("tick_size must be positive")
    if order_kind not in {"tp", "sl"}:
        raise ValueError('order_kind must be "tp" or "sl"')
    rounding = ROUND_FLOOR if side is SignalSide.LONG else ROUND_CEILING
    return (price / tick_size).to_integral_value(rounding=rounding) * tick_size


def equal_fractions(count: int) -> tuple[Decimal, ...]:
    """Return equal model-1 fractions summing to 1."""

    if count <= 0:
        raise ValueError("count must be positive")
    fraction = Decimal("1") / Decimal(count)
    return (fraction,) * (count - 1) + (Decimal("1") - fraction * (count - 1),)


def model2_fractions(
    count: int,
    weights: tuple[Decimal, ...] = (
        Decimal("0.60"),
        Decimal("0.20"),
        Decimal("0.10"),
        Decimal("0.07"),
        Decimal("0.03"),
    ),
) -> tuple[Decimal, ...]:
    """Return truncated and renormalized model-2 fractions."""

    if count <= 0:
        raise ValueError("count must be positive")
    if count > len(weights):
        raise ValueError("not enough model-2 weights for target count")
    selected = weights[:count]
    if any(weight <= 0 for weight in selected):
        raise ValueError("model-2 weights must be positive")
    total = sum(selected, Decimal("0"))
    normalized = tuple(weight / total for weight in selected[:-1])
    return normalized + (Decimal("1") - sum(normalized, Decimal("0")),)


def size_position(
    *,
    margin_usdt: Decimal,
    leverage: int,
    entry: Decimal,
    targets: tuple[Decimal, ...],
    filters: SymbolFilters,
    side: SignalSide,
    fractions: tuple[Decimal, ...] | None = None,
) -> SizingResult:
    """Compute exchange-sized quantity and per-target legs.

    Use notional = margin * leverage.
    Use qty = floor_to_step(notional / entry, step_size).
    If a leg is below minQty or minNotional, merge it into the previous leg and
    record a note. Never silently drop profit logic.
    """

    _validate_sizing_inputs(
        margin_usdt=margin_usdt,
        leverage=leverage,
        entry=entry,
        targets=targets,
        filters=filters,
    )
    selected_fractions = equal_fractions(len(targets)) if fractions is None else fractions
    _validate_fractions(selected_fractions, len(targets))

    notional_usdt = margin_usdt * Decimal(leverage)
    qty = floor_to_step(notional_usdt / entry, filters.step_size)
    executable_notional = qty * entry
    if qty <= 0 or qty < filters.min_qty:
        raise ValueError("total quantity is below minQty")
    if executable_notional < filters.min_notional:
        raise ValueError("total notional is below minNotional")

    leg_quantities = [floor_to_step(qty * fraction, filters.step_size) for fraction in selected_fractions]
    residual = qty - sum(leg_quantities, Decimal("0"))
    leg_quantities[0] += residual

    legs: list[SizedLeg] = []
    notes: list[str] = []
    for leg_index, (target, fraction, leg_qty) in enumerate(
        zip(targets, selected_fractions, leg_quantities, strict=True),
        start=1,
    ):
        rounded_target = round_price_to_tick(
            price=target,
            tick_size=filters.tick_size,
            side=side,
            order_kind="tp",
        )
        leg_notional = leg_qty * rounded_target
        is_too_small = (
            leg_qty <= 0
            or leg_qty < filters.min_qty
            or leg_notional < filters.min_notional
        )
        if is_too_small:
            if not legs:
                raise ValueError("first leg is below exchange minimums")
            previous = legs[-1]
            merged_qty = previous.qty + leg_qty
            legs[-1] = SizedLeg(
                leg_index=previous.leg_index,
                target_price=previous.target_price,
                fraction=previous.fraction + fraction,
                qty=merged_qty,
                notional=merged_qty * previous.target_price,
            )
            notes.append(f"merged leg {leg_index} into leg {previous.leg_index}: below exchange minimums")
            continue

        legs.append(
            SizedLeg(
                leg_index=leg_index,
                target_price=rounded_target,
                fraction=fraction,
                qty=leg_qty,
                notional=leg_notional,
            )
        )

    return SizingResult(
        qty=qty,
        notional_usdt=notional_usdt,
        margin_usdt=margin_usdt,
        leverage=leverage,
        legs=tuple(legs),
        notes=tuple(notes),
    )


def _validate_sizing_inputs(
    *,
    margin_usdt: Decimal,
    leverage: int,
    entry: Decimal,
    targets: tuple[Decimal, ...],
    filters: SymbolFilters,
) -> None:
    if margin_usdt <= 0:
        raise ValueError("margin_usdt must be positive")
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    if entry <= 0:
        raise ValueError("entry must be positive")
    if not targets:
        raise ValueError("at least one target is required")
    if any(target <= 0 for target in targets):
        raise ValueError("targets must be positive")
    if filters.step_size <= 0 or filters.tick_size <= 0:
        raise ValueError("step_size and tick_size must be positive")
    if filters.min_qty < 0 or filters.min_notional < 0:
        raise ValueError("exchange minimums must not be negative")


def _validate_fractions(fractions: tuple[Decimal, ...], target_count: int) -> None:
    if len(fractions) != target_count:
        raise ValueError("fraction count must match target count")
    if any(fraction <= 0 for fraction in fractions):
        raise ValueError("fractions must be positive")
    if sum(fractions, Decimal("0")) != Decimal("1"):
        raise ValueError("fractions must sum exactly to 1")
