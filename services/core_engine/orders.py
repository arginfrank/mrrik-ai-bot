from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
import time

from shared.exchange.binance import to_binance_order_side
from shared.exchange.client import ExchangeClient
from shared.exchange.types import ExchangeOrder
from shared.models import Signal, Trade

from services.core_engine.ids import client_order_id
from services.core_engine.risk import ExecutionPlan


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
    """Place closePosition SL and TP legs or model3 setup."""
    close_side = to_binance_order_side(trade_side=trade.side, action="close")
    sl_id = client_order_id(trade_id=trade.id, purpose="sl")
    tp_ids: list[str] = []
    try:
        await exchange.place_stop_market(
            symbol=trade.symbol,
            side=close_side,
            stop_price=plan.stop_loss,
            client_order_id=sl_id,
            close_position=True,
        )
        for leg in plan.legs:
            tp_id = client_order_id(
                trade_id=trade.id, purpose="tp", leg_index=leg.leg_index
            )
            await exchange.place_take_profit_market(
                symbol=trade.symbol,
                side=close_side,
                qty=leg.qty,
                stop_price=leg.target_price,
                client_order_id=tp_id,
                reduce_only=True,
            )
            tp_ids.append(tp_id)
    except Exception:
        return OpenTradeResult(
            status="error",
            reason="protective order placement failed",
            sl_order_id=sl_id if not tp_ids or tp_ids else sl_id,
            tp_order_ids=tuple(tp_ids),
        )
    return OpenTradeResult(
        status="opened",
        sl_order_id=sl_id,
        tp_order_ids=tuple(tp_ids),
    )


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
