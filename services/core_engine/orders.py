from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
import logging
import time

from shared.exchange.binance import to_binance_order_side
from shared.exchange.client import ExchangeClient
from shared.exchange.types import ExchangeOrder
from shared.models import Signal, Trade

from services.core_engine.ids import client_order_id
from services.core_engine.risk import ExecutionPlan


LOGGER = logging.getLogger(__name__)
_ORDER_RETRY_DELAYS_SEC = (0.2, 0.5)
_CONFIRM_RETRY_DELAYS_SEC = (0.2, 0.5)


@dataclass(frozen=True)
class EntryGuard:
    entry_fill_timeout_sec: int
    entry_max_deviation_pct: Decimal


@dataclass(frozen=True)
class OpenTradeResult:
    status: str
    reason: str | None = None
    entry_order_id: str | None = None
    sl_order_id: str | None = None
    tp_order_ids: tuple[str, ...] = ()


async def place_initial_orders(
    *,
    exchange: ExchangeClient,
    trade: Trade,
    signal: Signal,
    plan: ExecutionPlan,
    entry_mode: str,
    entry_guard: EntryGuard,
) -> OpenTradeResult:
    """Set leverage/margin, place entry, then protective orders after fill."""
    await exchange.set_leverage(symbol=trade.symbol, leverage=plan.leverage)
    await exchange.set_margin_type_isolated(symbol=trade.symbol)
    order_id = client_order_id(trade_id=trade.id, purpose="entry")
    side = to_binance_order_side(trade_side=trade.side, action="open")
    if entry_mode == "limit":
        entry_order = await exchange.place_entry_limit(
            symbol=trade.symbol,
            side=side,
            qty=plan.qty,
            price=plan.entry_price,
            client_order_id=order_id,
        )
    elif entry_mode == "market":
        entry_order = await exchange.place_entry_market(
            symbol=trade.symbol,
            side=side,
            qty=plan.qty,
            client_order_id=order_id,
        )
    else:
        return OpenTradeResult(status="error", reason="unsupported entry mode")

    filled = await _wait_for_entry_fill(
        exchange=exchange,
        trade=trade,
        plan=plan,
        entry_order=entry_order,
        guard=entry_guard,
    )
    if not filled:
        await exchange.cancel_order(symbol=trade.symbol, client_order_id=order_id)
        return OpenTradeResult(
            status="skipped",
            reason="entry guard failed",
            entry_order_id=order_id,
        )

    protected = await place_protective_orders(
        exchange=exchange,
        trade=trade,
        plan=plan,
    )
    return OpenTradeResult(
        status=protected.status,
        reason=protected.reason,
        entry_order_id=order_id,
        sl_order_id=protected.sl_order_id,
        tp_order_ids=protected.tp_order_ids,
    )


async def place_protective_orders(
    *,
    exchange: ExchangeClient,
    trade: Trade,
    plan: ExecutionPlan,
) -> OpenTradeResult:
    """Confirm a live closePosition SL before placing any optional TP legs."""
    close_side = to_binance_order_side(trade_side=trade.side, action="close")
    sl_id = client_order_id(trade_id=trade.id, purpose="sl")
    await _place_stop_market_with_retry(
        exchange=exchange,
        symbol=trade.symbol,
        side=close_side,
        stop_price=plan.stop_loss,
        client_order_id=sl_id,
    )
    sl_confirmed = await _confirm_open_order(
        exchange=exchange,
        symbol=trade.symbol,
        client_order_id=sl_id,
    )
    if not sl_confirmed:
        return await _emergency_close(
            exchange=exchange,
            trade=trade,
            close_side=close_side,
        )

    LOGGER.info(
        "event_type=stop_loss status=confirmed trade_id=%s symbol=%s client_order_id=%s",
        trade.id,
        trade.symbol,
        sl_id,
    )
    tp_ids: list[str] = []
    for leg in plan.legs:
        tp_id = client_order_id(
            trade_id=trade.id, purpose="tp", leg_index=leg.leg_index
        )
        placed = await _place_take_profit_with_retry(
            exchange=exchange,
            symbol=trade.symbol,
            side=close_side,
            qty=leg.qty,
            stop_price=leg.target_price,
            client_order_id=tp_id,
        )
        if not placed:
            LOGGER.warning(
                "event_type=take_profit status=placement_failed "
                "trade_id=%s symbol=%s leg_index=%s client_order_id=%s",
                trade.id,
                trade.symbol,
                leg.leg_index,
                tp_id,
            )
            continue
        tp_ids.append(tp_id)
    return OpenTradeResult(
        status="opened",
        sl_order_id=sl_id,
        tp_order_ids=tuple(tp_ids),
    )


async def _place_stop_market_with_retry(
    *,
    exchange: ExchangeClient,
    symbol: str,
    side: str,
    stop_price: Decimal,
    client_order_id: str,
) -> bool:
    for attempt in range(len(_ORDER_RETRY_DELAYS_SEC) + 1):
        try:
            await exchange.place_stop_market(
                symbol=symbol,
                side=side,
                stop_price=stop_price,
                client_order_id=client_order_id,
                close_position=True,
            )
            return True
        except Exception:
            if attempt == len(_ORDER_RETRY_DELAYS_SEC):
                return False
            await asyncio.sleep(_ORDER_RETRY_DELAYS_SEC[attempt])
    return False


async def _confirm_open_order(
    *,
    exchange: ExchangeClient,
    symbol: str,
    client_order_id: str,
) -> bool:
    for attempt in range(len(_CONFIRM_RETRY_DELAYS_SEC) + 1):
        try:
            open_orders = await exchange.get_open_orders(symbol=symbol)
            if any(
                order.client_order_id == client_order_id for order in open_orders
            ):
                return True
        except Exception:
            pass
        if attempt < len(_CONFIRM_RETRY_DELAYS_SEC):
            await asyncio.sleep(_CONFIRM_RETRY_DELAYS_SEC[attempt])
    return False


async def _place_take_profit_with_retry(
    *,
    exchange: ExchangeClient,
    symbol: str,
    side: str,
    qty: Decimal,
    stop_price: Decimal,
    client_order_id: str,
) -> bool:
    for attempt in range(len(_ORDER_RETRY_DELAYS_SEC) + 1):
        try:
            await exchange.place_take_profit_market(
                symbol=symbol,
                side=side,
                qty=qty,
                stop_price=stop_price,
                client_order_id=client_order_id,
                reduce_only=True,
            )
            return True
        except Exception:
            if attempt == len(_ORDER_RETRY_DELAYS_SEC):
                return False
            await asyncio.sleep(_ORDER_RETRY_DELAYS_SEC[attempt])
    return False


async def _emergency_close(
    *,
    exchange: ExchangeClient,
    trade: Trade,
    close_side: str,
) -> OpenTradeResult:
    emergency_id = client_order_id(trade_id=trade.id, purpose="emergency_close")
    for attempt in range(len(_ORDER_RETRY_DELAYS_SEC) + 1):
        try:
            await exchange.close_position_market(
                symbol=trade.symbol,
                side=close_side,
                qty=None,
                client_order_id=emergency_id,
            )
        except Exception:
            if attempt < len(_ORDER_RETRY_DELAYS_SEC):
                await asyncio.sleep(_ORDER_RETRY_DELAYS_SEC[attempt])
                continue
            LOGGER.critical(
                "event_type=emergency_close status=failed trade_id=%s symbol=%s",
                trade.id,
                trade.symbol,
            )
            return OpenTradeResult(
                status="emergency_close_failed",
                reason="stop_loss_unconfirmed_emergency_close_failed",
                sl_order_id=None,
            )

        LOGGER.error(
            "event_type=emergency_close status=closed trade_id=%s symbol=%s",
            trade.id,
            trade.symbol,
        )
        try:
            await exchange.cancel_open_orders(symbol=trade.symbol)
        except Exception:
            LOGGER.error(
                "event_type=emergency_close status=cleanup_failed "
                "trade_id=%s symbol=%s",
                trade.id,
                trade.symbol,
            )
        return OpenTradeResult(
            status="emergency_closed",
            reason="stop_loss_unconfirmed_emergency_close",
            sl_order_id=None,
        )

    raise AssertionError("unreachable emergency close retry state")


async def _wait_for_entry_fill(
    *,
    exchange: ExchangeClient,
    trade: Trade,
    plan: ExecutionPlan,
    entry_order: ExchangeOrder,
    guard: EntryGuard,
) -> bool:
    if entry_order.status == "FILLED":
        return True
    if entry_order.status in {"CANCELED", "EXPIRED", "REJECTED"}:
        return False
    if guard.entry_fill_timeout_sec <= 0:
        return False
    if guard.entry_max_deviation_pct < 0:
        return False

    latest_mark: list[Decimal | None] = [None]
    mark_stream = getattr(exchange, "mark_price_stream", None)
    watcher = (
        asyncio.create_task(_watch_mark_price(mark_stream, trade.symbol, latest_mark))
        if callable(mark_stream)
        else None
    )
    try:
        deadline = time.monotonic() + guard.entry_fill_timeout_sec
        while time.monotonic() < deadline:
            position = await exchange.get_position(symbol=trade.symbol)
            current_price = latest_mark[0]
            if current_price is None and position is not None:
                current_price = position.mark_price
            if (
                current_price is not None
                and _deviation_pct(current_price, plan.entry_price)
                > guard.entry_max_deviation_pct
            ):
                return False
            if position is not None and position.qty != 0:
                return True
            open_orders = await exchange.get_open_orders(symbol=trade.symbol)
            matching = next(
                (
                    order
                    for order in open_orders
                    if order.client_order_id == entry_order.client_order_id
                ),
                None,
            )
            if matching is not None and matching.status == "FILLED":
                return True
            await asyncio.sleep(0.1)
        return False
    finally:
        if watcher is not None:
            watcher.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await watcher


def _deviation_pct(current: Decimal, entry: Decimal) -> Decimal:
    if entry <= 0:
        return Decimal("Infinity")
    return abs(current - entry) / entry * Decimal("100")


async def _watch_mark_price(
    stream_factory: object,
    symbol: str,
    latest_mark: list[Decimal | None],
) -> None:
    stream = stream_factory([symbol])  # type: ignore[operator]
    async for mark_price in stream:
        latest_mark[0] = mark_price.price
