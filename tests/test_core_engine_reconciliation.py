from __future__ import annotations

import asyncio
from decimal import Decimal

from services.core_engine.ids import client_order_id
from services.core_engine.reconciliation import reconcile_open_trades
from shared.exchange.types import ExchangeOrder, PositionSnapshot
from shared.models import Signal, Trade, TradeLeg


class FakeRepository:
    def __init__(self, trade: Trade) -> None:
        self.trade = trade
        self.closed_reason: str | None = None

    def list_open_trades(self) -> list[Trade]:
        return [self.trade]

    def get_exchange_credentials(self, user_id: int):
        del user_id
        return object()

    def set_trade_sl_order(self, *, trade: Trade, sl_order_id: str) -> None:
        trade.sl_order_id = sl_order_id

    def set_leg_tp_order(self, *, leg: TradeLeg, tp_order_id: str) -> None:
        leg.tp_order_id = tp_order_id

    def close_trade(self, *, trade: Trade, **values: object) -> Trade:
        self.closed_reason = str(values["closed_reason"])
        trade.status = "closed"
        trade.closed_reason = self.closed_reason
        return trade


class FakeExchange:
    def __init__(self, *, has_position: bool, orders: list[ExchangeOrder]) -> None:
        self.has_position = has_position
        self.orders = orders
        self.writes: list[tuple[str, dict[str, object]]] = []

    async def get_position(self, *, symbol: str):
        if not self.has_position:
            return None
        return PositionSnapshot(
            symbol=symbol,
            qty=Decimal("100"),
            entry_price=Decimal("0.07145"),
            mark_price=Decimal("0.072"),
            liquidation_price=Decimal("0.0701"),
            unrealized_pnl=Decimal("1"),
        )

    async def get_open_algo_orders(self, *, symbol: str):
        del symbol
        return self.orders

    async def place_stop_market(self, **values: object) -> None:
        self.writes.append(("sl", values))

    async def place_take_profit_market(self, **values: object) -> None:
        self.writes.append(("tp", values))


class Factory:
    def __init__(self, exchange: FakeExchange) -> None:
        self.exchange = exchange

    def create_for_credential(self, *, credential: object, user_id: int):
        del credential, user_id
        return self.exchange


def _trade() -> Trade:
    signal = Signal(
        id=1,
        symbol="HBARUSDT",
        side="LONG",
        entry=Decimal("0.07145"),
        stop_loss=Decimal("0.07077"),
        leverage=42,
        targets_raw=[],
        targets_clean=[],
        status="accepted",
    )
    return Trade(
        id=21,
        signal=signal,
        user_id=2,
        symbol="HBARUSDT",
        side="LONG",
        leverage=42,
        margin_usdt=Decimal("10"),
        notional_usdt=Decimal("420"),
        qty=Decimal("100"),
        status="open",
        touched_tps=[],
        legs=[
            TradeLeg(
                leg_index=1,
                target_price=Decimal("0.07186"),
                qty=Decimal("100"),
                status="open",
            )
        ],
    )


def test_missing_sl_and_tp_are_repaired_with_deterministic_ids() -> None:
    trade = _trade()
    repository = FakeRepository(trade)
    exchange = FakeExchange(has_position=True, orders=[])
    result = asyncio.run(
        reconcile_open_trades(
            repository=repository, exchange_factory=Factory(exchange)
        )
    )

    assert result.repaired_orders == 2
    assert trade.sl_order_id == client_order_id(trade_id=trade.id, purpose="sl")
    assert trade.legs[0].tp_order_id == client_order_id(
        trade_id=trade.id, purpose="tp", leg_index=1
    )
    assert {write[1]["client_order_id"] for write in exchange.writes} == {
        trade.sl_order_id,
        trade.legs[0].tp_order_id,
    }


def test_missing_exchange_truth_closes_unknown_and_records_note() -> None:
    trade = _trade()
    repository = FakeRepository(trade)
    result = asyncio.run(
        reconcile_open_trades(
            repository=repository,
            exchange_factory=Factory(FakeExchange(has_position=False, orders=[])),
        )
    )

    assert result.closed_missing_positions == 1
    assert repository.closed_reason == "reconciled_closed_unknown"
    assert result.errors
