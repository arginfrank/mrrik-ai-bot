from __future__ import annotations

from decimal import Decimal

from shared.signal.types import SignalSide


def signed_move_fraction(
    *,
    side: SignalSide,
    entry: Decimal,
    price: Decimal,
) -> Decimal:
    """Return signed price move as a fraction, not percent."""

    if entry <= 0:
        raise ValueError("entry must be positive")
    if side is SignalSide.LONG:
        return (price - entry) / entry
    return (entry - price) / entry


def roi_percent_on_margin(
    *,
    side: SignalSide,
    entry: Decimal,
    price: Decimal,
    leverage: int,
) -> Decimal:
    """Return ROI percent on margin: signed_move_fraction * leverage * 100."""

    _require_positive_leverage(leverage)
    return (
        signed_move_fraction(side=side, entry=entry, price=price)
        * Decimal(leverage)
        * Decimal("100")
    )


def pnl_usdt_for_fraction(
    *,
    margin_usdt: Decimal,
    position_fraction: Decimal,
    side: SignalSide,
    entry: Decimal,
    price: Decimal,
    leverage: int,
) -> Decimal:
    """Return PnL in USDT for a portion of the position."""

    if margin_usdt < 0:
        raise ValueError("margin_usdt must not be negative")
    if not Decimal("0") <= position_fraction <= Decimal("1"):
        raise ValueError("position_fraction must be between zero and one")
    _require_positive_leverage(leverage)
    return (
        margin_usdt
        * position_fraction
        * signed_move_fraction(side=side, entry=entry, price=price)
        * Decimal(leverage)
    )


def approximate_liquidation_price(
    *,
    side: SignalSide,
    entry: Decimal,
    leverage: int,
    maintenance_margin_rate: Decimal,
) -> Decimal:
    """Approximate isolated liquidation price from architecture section 9.2."""

    if entry <= 0:
        raise ValueError("entry must be positive")
    _require_positive_leverage(leverage)
    if maintenance_margin_rate < 0:
        raise ValueError("maintenance_margin_rate must not be negative")

    liquidation_move = Decimal("1") / Decimal(leverage) - maintenance_margin_rate
    if side is SignalSide.LONG:
        return entry * (Decimal("1") - liquidation_move)
    return entry * (Decimal("1") + liquidation_move)


def liquidation_happens_before_stop(
    *,
    side: SignalSide,
    entry: Decimal,
    stop_loss: Decimal,
    leverage: int,
    maintenance_margin_rate: Decimal,
) -> bool:
    """Return whether liquidation is reached before the configured SL."""

    liquidation_price = approximate_liquidation_price(
        side=side,
        entry=entry,
        leverage=leverage,
        maintenance_margin_rate=maintenance_margin_rate,
    )
    if side is SignalSide.LONG:
        return stop_loss < liquidation_price
    return stop_loss > liquidation_price


def capped_loss_usdt_on_liquidation(*, margin_usdt: Decimal) -> Decimal:
    """Return the full-margin loss as a negative value."""

    if margin_usdt < 0:
        raise ValueError("margin_usdt must not be negative")
    return -margin_usdt


def _require_positive_leverage(leverage: int) -> None:
    if leverage <= 0:
        raise ValueError("leverage must be positive")
