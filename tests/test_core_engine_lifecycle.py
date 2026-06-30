from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

import services.core_engine.lifecycle as lifecycle_module
from services.core_engine.ids import client_order_id
from services.core_engine.lifecycle import (
    handle_liquidation_update,
    handle_mark_price_for_model3,
    handle_user_stream_event,
)
from shared.exchange.types import ExchangeOrder, MarkPrice, UserStreamEvent
from shared.models import Signal, Trade, TradeLeg


class FakeRepository:
    def __init__(self, trade: Trade, events: list[str] | None = None) -> None:
        self.trade = trade
        self.events = events

    def get_trade_leg_by_client_order_id(self, value: str):
        for leg in self.trade.legs:
            if leg.tp_order_id == value:
                return self.trade, leg
        return None

    def get_trade_by_client_order_id(self, value: str):
        if value in {self.trade.entry_order_id, self.trade.sl_order_id}:
            return self.trade
        return None

    def mark_leg_filled(self, *, trade: Trade, leg_index: int):
        leg = next(item for item in trade.legs if item.leg_index == leg_index)
        leg.status = "filled"
        trade.touched_tps = sorted(set(trade.touched_tps or []).union({leg_index}))
        return leg

    def set_trade_sl_order(self, *, trade: Trade, sl_order_id: str) -> None:
        if self.events is not None:
            self.events.append("store_sl")
        trade.sl_order_id = sl_order_id

    def close_trade(self, *, trade: Trade, **values: object) -> Trade:
        trade.status = "closed"
        trade.closed_reason = str(values["closed_reason"])
        trade.realized_pnl_usdt = values["realized_pnl_usdt"]  # type: ignore[assignment]
        trade.realized_roi_pct = values["realized_roi_pct"]  # type: ignore[assignment]
        trade.touched_tps = list(values["touched_tps"])  # type: ignore[arg-type]
        for leg in trade.legs:
            if leg.status == "open":
                leg.status = "canceled"
        return trade

    def list_open_trades(self) -> list[Trade]:
        return [self.trade] if self.trade.status == "open" else []

    def get_user_settings(self, user_id: int):
        del user_id
        return None


class FakeExchange:
    def __init__(
        self,
        *,
        fail_stop: bool = False,
        events: list[str] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fail_stop = fail_stop
        self.events = events
        self.open_orders: list[ExchangeOrder] = []

    async def cancel_order(self, **values: object) -> None:
        self.calls.append(("cancel", values))
        if self.events is not None:
            self.events.append("cancel_old_sl")
        client_id = str(values["client_order_id"])
        self.open_orders = [
            order for order in self.open_orders if order.client_order_id != client_id
        ]

    async def place_stop_market(self, **values: object) -> ExchangeOrder:
        self.calls.append(("sl", values))
        if self.events is not None:
            self.events.append("place_new_sl")
        if self.fail_stop:
            raise RuntimeError("break-even stop failed")
        order = _order(str(values["client_order_id"]), status="NEW")
        self.open_orders.append(order)
        return order

    async def get_open_orders(self, **values: object) -> list[ExchangeOrder]:
        self.calls.append(("open_orders", values))
        if self.events is not None:
            self.events.append("confirm_new_sl")
        return list(self.open_orders)

    async def close_position_market(self, **values: object) -> ExchangeOrder:
        self.calls.append(("close", values))
        return _order(str(values["client_order_id"]))


def _order(client_id: str, *, status: str = "FILLED") -> ExchangeOrder:
    return ExchangeOrder(
        exchange_order_id="1",
        client_order_id=client_id,
        symbol="HBARUSDT",
        side="SELL",
        order_type="MARKET",
        status=status,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lifecycle_module, "_BE_RETRY_DELAYS_SEC", (0, 0))


def _trade(*, leg_count: int = 2, model3: bool = False) -> Trade:
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
    trade = Trade(
        id=11,
        signal=signal,
        user_id=2,
        symbol="HBARUSDT",
        side="LONG",
        leverage=42,
        margin_usdt=Decimal("10"),
        notional_usdt=Decimal("420"),
        qty=Decimal("100"),
        entry_order_id=client_order_id(trade_id=11, purpose="entry"),
        sl_order_id=client_order_id(trade_id=11, purpose="sl"),
        status="open",
        touched_tps=[],
    )
    if not model3:
        trade.legs = [
            TradeLeg(
                leg_index=index,
                target_price=Decimal("0.07145") + Decimal(index) * Decimal("0.001"),
                qty=Decimal("100") / Decimal(leg_count),
                tp_order_id=client_order_id(
                    trade_id=11, purpose="tp", leg_index=index
                ),
                status="open",
            )
            for index in range(1, leg_count + 1)
        ]
    return trade


def _fill(client_id: str, price: Decimal) -> UserStreamEvent:
    return UserStreamEvent(
        event_type="ORDER_TRADE_UPDATE",
        client_order_id=client_id,
        order_status="FILLED",
        execution_type="TRADE",
        last_filled_price=price,
    )


def test_tp1_fill_marks_leg_and_can_move_stop_to_break_even() -> None:
    trade = _trade()
    repository = FakeRepository(trade)
    exchange = FakeExchange()
    result = asyncio.run(
        handle_user_stream_event(
            event=_fill(trade.legs[0].tp_order_id or "", trade.legs[0].target_price),
            repository=repository,
            exchange=exchange,
            move_sl_to_be_after_tp1=True,
        )
    )

    assert result.status == "leg_filled"
    assert trade.legs[0].status == "filled"
    assert [name for name, _ in exchange.calls] == ["sl", "open_orders", "cancel"]
    assert trade.sl_order_id == client_order_id(trade_id=trade.id, purpose="be_sl")
    assert exchange.calls[0][1]["stop_price"] == trade.signal.entry


def test_be_stop_placement_failure_keeps_old_stop_and_stored_id() -> None:
    trade = _trade()
    old_sl_id = trade.sl_order_id
    repository = FakeRepository(trade)
    exchange = FakeExchange(fail_stop=True)

    result = asyncio.run(
        handle_user_stream_event(
            event=_fill(trade.legs[0].tp_order_id or "", trade.legs[0].target_price),
            repository=repository,
            exchange=exchange,
            move_sl_to_be_after_tp1=True,
        )
    )

    assert result.status == "leg_filled"
    assert trade.sl_order_id == old_sl_id
    assert "cancel" not in [name for name, _ in exchange.calls]


def test_be_stop_is_confirmed_before_old_stop_cancel_and_storage_update() -> None:
    events: list[str] = []
    trade = _trade()
    repository = FakeRepository(trade, events)
    exchange = FakeExchange(events=events)

    asyncio.run(
        handle_user_stream_event(
            event=_fill(trade.legs[0].tp_order_id or "", trade.legs[0].target_price),
            repository=repository,
            exchange=exchange,
            move_sl_to_be_after_tp1=True,
        )
    )

    assert events == [
        "place_new_sl",
        "confirm_new_sl",
        "cancel_old_sl",
        "store_sl",
    ]
    assert trade.sl_order_id == client_order_id(trade_id=trade.id, purpose="be_sl")


def test_all_tps_close_with_blended_exchange_prices() -> None:
    trade = _trade()
    trade.legs[0].status = "filled"
    trade.touched_tps = [1]
    repository = FakeRepository(trade)
    result = asyncio.run(
        handle_user_stream_event(
            event=_fill(trade.legs[1].tp_order_id or "", trade.legs[1].target_price),
            repository=repository,
            exchange=FakeExchange(),
            move_sl_to_be_after_tp1=True,
        )
    )

    assert result.closed_reason == "all_tp"
    assert result.realized_pnl_usdt is not None
    assert result.realized_pnl_usdt > 0
    assert result.realized_roi_pct != Decimal("999")


def test_sl_and_be_fill_cancel_open_tps() -> None:
    for break_even in (False, True):
        trade = _trade()
        if break_even:
            trade.legs[0].status = "filled"
            trade.touched_tps = [1]
            trade.sl_order_id = client_order_id(trade_id=trade.id, purpose="be_sl")
        exchange = FakeExchange()
        price = trade.signal.entry if break_even else trade.signal.stop_loss
        result = asyncio.run(
            handle_user_stream_event(
                event=_fill(trade.sl_order_id or "", price),
                repository=FakeRepository(trade),
                exchange=exchange,
                move_sl_to_be_after_tp1=True,
            )
        )
        assert result.closed_reason == ("be" if break_even else "sl")
        assert "cancel" in [name for name, _ in exchange.calls]


def test_liquidation_caps_agld_style_loss_at_one_margin() -> None:
    trade = _trade()
    result = asyncio.run(
        handle_liquidation_update(trade=trade, repository=FakeRepository(trade))
    )

    assert result.closed_reason == "liquidation"
    assert result.realized_pnl_usdt == Decimal("-10")
    assert result.realized_roi_pct == Decimal("-100")


def test_model3_mark_price_closes_at_roi_threshold() -> None:
    trade = _trade(model3=True)
    repository = FakeRepository(trade)
    exchange = FakeExchange()
    result = asyncio.run(
        handle_mark_price_for_model3(
            price=MarkPrice(symbol="HBARUSDT", price=Decimal("0.072")),
            repository=repository,
            exchange=exchange,
            model3_exit_roi_pct=Decimal("20"),
        )
    )

    assert result[0].closed_reason == "model3_exit"
    assert result[0].realized_roi_pct == Decimal("20")
    assert [name for name, _ in exchange.calls][0] == "close"
