from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
import logging

from shared.exchange.binance import to_binance_order_side, to_binance_position_side
from shared.exchange.client import ExchangeClient
from shared.exchange.types import MarkPrice, UserStreamEvent
from shared.models import Trade
from shared.signal.pnl import capped_loss_usdt_on_liquidation, pnl_usdt_for_fraction
from shared.signal.types import SignalSide

from services.core_engine.ids import client_order_id
from services.core_engine.repository import CoreRepository


LOGGER = logging.getLogger(__name__)
_BE_RETRY_DELAYS_SEC = (0.2, 0.5)


@dataclass(frozen=True)
class LifecycleResult:
    status: str
    trade_id: int | None = None
    leg_index: int | None = None
    closed_reason: str | None = None
    realized_pnl_usdt: Decimal | None = None
    realized_roi_pct: Decimal | None = None


async def handle_user_stream_event(
    *,
    event: UserStreamEvent,
    repository: CoreRepository,
    exchange: ExchangeClient,
    move_sl_to_be_after_tp1: bool,
) -> LifecycleResult:
    """Handle entry/TP/SL fills from an exchange user stream."""
    if event.execution_type == "CALCULATED":
        trade = _find_open_trade_by_symbol(repository, event.symbol)
        if trade is not None:
            return await handle_liquidation_update(trade=trade, repository=repository)
    if event.event_type != "ORDER_TRADE_UPDATE" or event.order_status != "FILLED":
        return LifecycleResult(status="ignored")
    if event.client_order_id is None:
        return LifecycleResult(status="ignored")

    leg_match = repository.get_trade_leg_by_client_order_id(event.client_order_id)
    if leg_match is not None:
        trade, leg = leg_match
        if leg.status == "filled":
            return LifecycleResult(
                status="ignored", trade_id=trade.id, leg_index=leg.leg_index
            )
        repository.mark_leg_filled(trade=trade, leg_index=leg.leg_index)
        filled_legs = [item for item in trade.legs if item.status == "filled"]
        if trade.legs and len(filled_legs) == len(trade.legs):
            if trade.sl_order_id:
                with suppress(Exception):
                    await exchange.cancel_algo_order(
                        client_order_id=trade.sl_order_id
                    )
            pnl = _blended_pnl(trade=trade, remaining_exit_price=None)
            closed = repository.close_trade(
                trade=trade,
                closed_reason="all_tp",
                realized_pnl_usdt=pnl,
                realized_roi_pct=_roi_from_pnl(trade, pnl),
                touched_tps=tuple(item.leg_index for item in filled_legs),
            )
            return _closed_result(closed, leg_index=leg.leg_index)

        if move_sl_to_be_after_tp1 and leg.leg_index == 1:
            remaining = Decimal(trade.qty) - sum(
                (
                    Decimal(item.qty)
                    for item in trade.legs
                    if item.status == "filled"
                ),
                Decimal("0"),
            )
            if remaining <= 0:
                LOGGER.info(
                    "event_type=break_even_stop status=skipped_no_remaining_qty "
                    "trade_id=%s symbol=%s",
                    trade.id,
                    trade.symbol,
                )
                return LifecycleResult(
                    status="leg_filled", trade_id=trade.id, leg_index=leg.leg_index
                )
            entry = _entry_price(trade)
            old_sl_id = trade.sl_order_id
            be_id = client_order_id(trade_id=trade.id, purpose="be_sl")
            placed = await _place_break_even_stop(
                exchange=exchange,
                trade=trade,
                qty=remaining,
                stop_price=entry,
                client_order_id=be_id,
            )
            confirmed = placed and await _confirm_open_order(
                exchange=exchange,
                symbol=trade.symbol,
                client_order_id=be_id,
            )
            if not confirmed:
                LOGGER.warning(
                    "event_type=break_even_stop status=unconfirmed "
                    "trade_id=%s symbol=%s old_client_order_id=%s",
                    trade.id,
                    trade.symbol,
                    old_sl_id,
                )
                return LifecycleResult(
                    status="leg_filled", trade_id=trade.id, leg_index=leg.leg_index
                )
            if old_sl_id and old_sl_id != be_id:
                try:
                    await exchange.cancel_algo_order(client_order_id=old_sl_id)
                except Exception:
                    LOGGER.warning(
                        "event_type=break_even_stop status=old_stop_cancel_failed "
                        "trade_id=%s symbol=%s old_client_order_id=%s",
                        trade.id,
                        trade.symbol,
                        old_sl_id,
                    )
            repository.set_trade_sl_order(trade=trade, sl_order_id=be_id)
        return LifecycleResult(
            status="leg_filled", trade_id=trade.id, leg_index=leg.leg_index
        )

    trade = repository.get_trade_by_client_order_id(event.client_order_id)
    if trade is None:
        return LifecycleResult(status="ignored")
    if event.client_order_id == trade.entry_order_id:
        return LifecycleResult(status="entry_filled", trade_id=trade.id)
    if event.client_order_id != trade.sl_order_id:
        return LifecycleResult(status="ignored", trade_id=trade.id)

    await _cancel_open_tps(trade=trade, exchange=exchange)
    entry = _entry_price(trade)
    stop_price = (
        event.last_filled_price
        if event.last_filled_price is not None and event.last_filled_price > 0
        else _signal_stop_loss(trade)
    )
    is_break_even = event.client_order_id == client_order_id(
        trade_id=trade.id, purpose="be_sl"
    ) or stop_price == entry
    pnl = _blended_pnl(trade=trade, remaining_exit_price=stop_price)
    reason = "be" if is_break_even else "sl"
    closed = repository.close_trade(
        trade=trade,
        closed_reason=reason,
        realized_pnl_usdt=pnl,
        realized_roi_pct=_roi_from_pnl(trade, pnl),
        touched_tps=tuple(sorted(trade.touched_tps or [])),
    )
    return _closed_result(closed)


async def handle_mark_price_for_model3(
    *,
    price: MarkPrice,
    repository: CoreRepository,
    exchange: ExchangeClient,
    model3_exit_roi_pct: Decimal,
) -> list[LifecycleResult]:
    """Close model-3 trades when their ROI threshold is crossed."""
    results: list[LifecycleResult] = []
    for trade in repository.list_open_trades():
        if trade.status != "open" or trade.symbol != price.symbol or trade.legs:
            continue
        settings_getter = getattr(repository, "get_user_settings", None)
        settings = (
            settings_getter(trade.user_id)
            if trade.user_id and callable(settings_getter)
            else None
        )
        exit_roi = (
            Decimal(settings.model3_exit_roi_pct)
            if settings is not None
            else model3_exit_roi_pct
        )
        entry = _entry_price(trade)
        move = exit_roi / (Decimal("100") * Decimal(trade.leverage))
        side = SignalSide(trade.side)
        threshold = entry * (
            Decimal("1") + move if side is SignalSide.LONG else Decimal("1") - move
        )
        crossed = (
            price.price >= threshold
            if side is SignalSide.LONG
            else price.price <= threshold
        )
        if not crossed:
            continue
        await exchange.close_position_market(
            symbol=trade.symbol,
            side=to_binance_order_side(trade_side=trade.side, action="close"),
            position_side=to_binance_position_side(trade_side=trade.side),
            qty=None,
            client_order_id=client_order_id(
                trade_id=trade.id, purpose="model3_exit"
            ),
        )
        if trade.sl_order_id:
            with suppress(Exception):
                await exchange.cancel_algo_order(client_order_id=trade.sl_order_id)
        pnl = Decimal(trade.margin_usdt) * exit_roi / Decimal("100")
        closed = repository.close_trade(
            trade=trade,
            closed_reason="model3_exit",
            realized_pnl_usdt=pnl,
            realized_roi_pct=exit_roi,
            touched_tps=(),
        )
        results.append(_closed_result(closed))
    return results


async def _place_break_even_stop(
    *,
    exchange: ExchangeClient,
    trade: Trade,
    qty: Decimal,
    stop_price: Decimal,
    client_order_id: str,
) -> bool:
    for attempt in range(len(_BE_RETRY_DELAYS_SEC) + 1):
        try:
            await exchange.place_stop_market(
                symbol=trade.symbol,
                side=to_binance_order_side(trade_side=trade.side, action="close"),
                position_side=to_binance_position_side(trade_side=trade.side),
                qty=qty,
                stop_price=stop_price,
                client_order_id=client_order_id,
            )
            return True
        except Exception:
            if attempt == len(_BE_RETRY_DELAYS_SEC):
                return False
            await asyncio.sleep(_BE_RETRY_DELAYS_SEC[attempt])
    return False


async def _confirm_open_order(
    *, exchange: ExchangeClient, symbol: str, client_order_id: str
) -> bool:
    for attempt in range(len(_BE_RETRY_DELAYS_SEC) + 1):
        try:
            open_orders = await exchange.get_open_algo_orders(symbol=symbol)
            if any(
                order.client_order_id == client_order_id for order in open_orders
            ):
                return True
        except Exception:
            pass
        if attempt < len(_BE_RETRY_DELAYS_SEC):
            await asyncio.sleep(_BE_RETRY_DELAYS_SEC[attempt])
    return False


async def handle_liquidation_update(
    *,
    trade: Trade,
    repository: CoreRepository,
) -> LifecycleResult:
    """Mark liquidation with loss capped at one isolated margin."""
    pnl = capped_loss_usdt_on_liquidation(margin_usdt=Decimal(trade.margin_usdt))
    closed = repository.close_trade(
        trade=trade,
        closed_reason="liquidation",
        realized_pnl_usdt=pnl,
        realized_roi_pct=Decimal("-100"),
        touched_tps=tuple(sorted(trade.touched_tps or [])),
    )
    return _closed_result(closed)


async def _cancel_open_tps(*, trade: Trade, exchange: ExchangeClient) -> None:
    for leg in trade.legs:
        if leg.status == "open" and leg.tp_order_id:
            with suppress(Exception):
                await exchange.cancel_algo_order(client_order_id=leg.tp_order_id)


def _blended_pnl(*, trade: Trade, remaining_exit_price: Decimal | None) -> Decimal:
    entry = _entry_price(trade)
    side = SignalSide(trade.side)
    total_qty = Decimal(trade.qty)
    if total_qty <= 0:
        raise ValueError("trade quantity must be positive")
    pnl = Decimal("0")
    for leg in trade.legs:
        if leg.status == "filled":
            exit_price = Decimal(leg.target_price)
        elif remaining_exit_price is not None:
            exit_price = remaining_exit_price
        else:
            continue
        pnl += pnl_usdt_for_fraction(
            margin_usdt=Decimal(trade.margin_usdt),
            position_fraction=Decimal(leg.qty) / total_qty,
            side=side,
            entry=entry,
            price=exit_price,
            leverage=trade.leverage,
        )
    if not trade.legs and remaining_exit_price is not None:
        pnl = pnl_usdt_for_fraction(
            margin_usdt=Decimal(trade.margin_usdt),
            position_fraction=Decimal("1"),
            side=side,
            entry=entry,
            price=remaining_exit_price,
            leverage=trade.leverage,
        )
    return max(pnl, -Decimal(trade.margin_usdt))


def _entry_price(trade: Trade) -> Decimal:
    signal = getattr(trade, "signal", None)
    if signal is None:
        raise ValueError("trade is missing its signal")
    return Decimal(signal.entry)


def _signal_stop_loss(trade: Trade) -> Decimal:
    signal = getattr(trade, "signal", None)
    if signal is None:
        raise ValueError("trade is missing its signal")
    return Decimal(signal.stop_loss)


def _roi_from_pnl(trade: Trade, pnl: Decimal) -> Decimal:
    margin = Decimal(trade.margin_usdt)
    return pnl / margin * Decimal("100")


def _closed_result(trade: Trade, leg_index: int | None = None) -> LifecycleResult:
    return LifecycleResult(
        status="closed",
        trade_id=trade.id,
        leg_index=leg_index,
        closed_reason=trade.closed_reason,
        realized_pnl_usdt=trade.realized_pnl_usdt,
        realized_roi_pct=trade.realized_roi_pct,
    )


def _find_open_trade_by_symbol(
    repository: CoreRepository, symbol: str | None
) -> Trade | None:
    if symbol is None:
        return None
    return next(
        (
            trade
            for trade in repository.list_open_trades()
            if trade.status == "open" and trade.symbol == symbol
        ),
        None,
    )
