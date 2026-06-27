from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import inspect
from typing import Any

from shared.exchange.binance import to_binance_order_side
from shared.exchange.client import ExchangeClient

from services.core_engine.ids import client_order_id
from services.core_engine.repository import CoreRepository


@dataclass(frozen=True)
class ReconcileResult:
    checked_trades: int
    closed_missing_positions: int
    repaired_orders: int
    errors: tuple[str, ...] = ()


async def reconcile_open_trades(
    *,
    repository: CoreRepository,
    exchange_factory: object,
) -> ReconcileResult:
    """Reconcile DB open trades against exchange positions/open orders."""
    checked = 0
    closed_missing = 0
    repaired = 0
    errors: list[str] = []
    for trade in repository.list_open_trades():
        checked += 1
        try:
            exchange = await _make_exchange_for_trade(
                exchange_factory=exchange_factory,
                repository=repository,
                trade=trade,
            )
            position = await exchange.get_position(symbol=trade.symbol)
            open_orders = await exchange.get_open_orders(symbol=trade.symbol)
            open_ids = {order.client_order_id for order in open_orders}
            if position is None and not open_orders:
                repository.close_trade(
                    trade=trade,
                    closed_reason="reconciled_closed_unknown",
                    realized_pnl_usdt=Decimal("0"),
                    realized_roi_pct=Decimal("0"),
                    touched_tps=tuple(sorted(trade.touched_tps or [])),
                )
                closed_missing += 1
                errors.append(
                    f"trade {trade.id}: exchange position and orders were missing"
                )
                continue
            if position is None:
                continue
            signal = trade.signal
            if signal is None:
                errors.append(f"trade {trade.id}: signal was missing")
                continue
            close_side = to_binance_order_side(
                trade_side=trade.side, action="close"
            )
            sl_id = trade.sl_order_id or client_order_id(
                trade_id=trade.id, purpose="sl"
            )
            if sl_id not in open_ids:
                stop_price = (
                    Decimal(signal.entry)
                    if sl_id == client_order_id(trade_id=trade.id, purpose="be_sl")
                    else Decimal(signal.stop_loss)
                )
                await exchange.place_stop_market(
                    symbol=trade.symbol,
                    side=close_side,
                    stop_price=stop_price,
                    client_order_id=sl_id,
                    close_position=True,
                )
                repository.set_trade_sl_order(trade=trade, sl_order_id=sl_id)
                repaired += 1
            for leg in trade.legs:
                if leg.status != "open":
                    continue
                tp_id = leg.tp_order_id or client_order_id(
                    trade_id=trade.id, purpose="tp", leg_index=leg.leg_index
                )
                if tp_id in open_ids:
                    continue
                await exchange.place_take_profit_market(
                    symbol=trade.symbol,
                    side=close_side,
                    qty=Decimal(leg.qty),
                    stop_price=Decimal(leg.target_price),
                    client_order_id=tp_id,
                    reduce_only=True,
                )
                repository.set_leg_tp_order(leg=leg, tp_order_id=tp_id)
                repaired += 1
        except Exception:
            errors.append(f"trade {trade.id}: reconciliation failed")
    return ReconcileResult(
        checked_trades=checked,
        closed_missing_positions=closed_missing,
        repaired_orders=repaired,
        errors=tuple(errors),
    )


async def _make_exchange_for_trade(
    *, exchange_factory: object, repository: CoreRepository, trade: Any
) -> ExchangeClient:
    credential_getter = getattr(repository, "get_exchange_credentials", None)
    credential = (
        credential_getter(trade.user_id) if callable(credential_getter) else None
    )
    creator = getattr(exchange_factory, "create_for_credential", None)
    if callable(creator):
        candidate = creator(credential=credential, user_id=trade.user_id)
    else:
        creator = getattr(exchange_factory, "create_for_user", None)
        if callable(creator):
            candidate = creator(user_id=trade.user_id)
        elif callable(exchange_factory):
            candidate = _call_factory(exchange_factory, trade, credential)
        else:
            raise TypeError("exchange_factory is not callable")
    if inspect.isawaitable(candidate):
        candidate = await candidate
    return candidate


def _call_factory(factory: Any, trade: Any, credential: Any) -> Any:
    parameters = inspect.signature(factory).parameters
    if not parameters:
        return factory()
    if "trade" in parameters or "credential" in parameters:
        values: dict[str, Any] = {}
        if "trade" in parameters:
            values["trade"] = trade
        if "credential" in parameters:
            values["credential"] = credential
        if "user_id" in parameters:
            values["user_id"] = trade.user_id
        return factory(**values)
    if "user_id" in parameters:
        return factory(user_id=trade.user_id)
    return factory(trade.user_id)
