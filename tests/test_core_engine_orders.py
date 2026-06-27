from __future__ import annotations

import asyncio
from decimal import Decimal

from services.core_engine.ids import client_order_id
from services.core_engine.orders import EntryGuard, place_initial_orders
from services.core_engine.risk import ExecutionLegPlan, ExecutionPlan
from shared.exchange.types import ExchangeOrder
from shared.models import Signal, Trade


def _order(client_id: str, status: str = "FILLED") -> ExchangeOrder:
    return ExchangeOrder(
        exchange_order_id="1",
        client_order_id=client_id,
        symbol="HBARUSDT",
        side="BUY",
        order_type="MARKET",
        status=status,  # type: ignore[arg-type]
    )


class FakeExchange:
    def __init__(self, *, entry_status: str = "FILLED", fail_tp: bool = False) -> None:
        self.calls: list[tuple[str, object]] = []
        self.entry_status = entry_status
        self.fail_tp = fail_tp

    async def set_leverage(self, **values: object) -> None:
        self.calls.append(("leverage", values))

    async def set_margin_type_isolated(self, **values: object) -> None:
        self.calls.append(("isolated", values))

    async def place_entry_limit(self, **values: object) -> ExchangeOrder:
        self.calls.append(("entry_limit", values))
        return _order(str(values["client_order_id"]), self.entry_status)

    async def place_entry_market(self, **values: object) -> ExchangeOrder:
        self.calls.append(("entry_market", values))
        return _order(str(values["client_order_id"]), self.entry_status)

    async def place_stop_market(self, **values: object) -> ExchangeOrder:
        self.calls.append(("sl", values))
        return _order(str(values["client_order_id"]))

    async def place_take_profit_market(self, **values: object) -> ExchangeOrder:
        self.calls.append(("tp", values))
        if self.fail_tp:
            raise RuntimeError("sensitive exchange detail")
        return _order(str(values["client_order_id"]))

    async def cancel_order(self, **values: object) -> None:
        self.calls.append(("cancel", values))

    async def get_position(self, **values: object):
        return None

    async def get_open_orders(self, **values: object) -> list[ExchangeOrder]:
        return []


def _trade_and_plan(*, model3: bool = False) -> tuple[Trade, Signal, ExecutionPlan]:
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
        id=9,
        signal_id=1,
        user_id=2,
        symbol="HBARUSDT",
        side="LONG",
        leverage=42,
        margin_usdt=Decimal("10"),
        notional_usdt=Decimal("420"),
        qty=Decimal("100"),
        status="pending_entry",
    )
    legs = () if model3 else (
        ExecutionLegPlan(
            leg_index=1,
            target_price=Decimal("0.07186"),
            fraction=Decimal("1"),
            qty=Decimal("100"),
        ),
    )
    plan = ExecutionPlan(
        margin_usdt=Decimal("10"),
        leverage=42,
        notional_usdt=Decimal("420"),
        qty=Decimal("100"),
        entry_price=Decimal("0.07145"),
        stop_loss=Decimal("0.07077"),
        liq_price=Decimal("0.0701"),
        legs=legs,
        risk_model=3 if model3 else 1,
    )
    return trade, signal, plan


def _run(fake: FakeExchange, *, entry_mode: str = "limit", model3: bool = False):
    trade, signal, plan = _trade_and_plan(model3=model3)
    result = asyncio.run(
        place_initial_orders(
            exchange=fake,
            trade=trade,
            signal=signal,
            plan=plan,
            entry_mode=entry_mode,
            entry_guard=EntryGuard(
                entry_fill_timeout_sec=0,
                entry_max_deviation_pct=Decimal("0.5"),
            ),
        )
    )
    return trade, result


def test_order_sequence_and_deterministic_ids() -> None:
    fake = FakeExchange()
    trade, result = _run(fake)

    assert [name for name, _ in fake.calls] == [
        "leverage",
        "isolated",
        "entry_limit",
        "sl",
        "tp",
    ]
    assert result.status == "opened"
    assert result.entry_order_id == client_order_id(trade_id=trade.id, purpose="entry")
    assert result.sl_order_id == client_order_id(trade_id=trade.id, purpose="sl")


def test_limit_guard_cancels_unfilled_entry() -> None:
    fake = FakeExchange(entry_status="NEW")
    _, result = _run(fake)

    assert result.status == "skipped"
    assert [name for name, _ in fake.calls][-1] == "cancel"


def test_market_mode_is_explicit_and_model3_has_no_tp() -> None:
    fake = FakeExchange()
    _, result = _run(fake, entry_mode="market", model3=True)

    assert result.status == "opened"
    assert "entry_market" in [name for name, _ in fake.calls]
    assert "tp" not in [name for name, _ in fake.calls]


def test_protective_failure_is_safe() -> None:
    fake = FakeExchange(fail_tp=True)
    _, result = _run(fake)

    assert result.status == "error"
    assert result.reason == "protective order placement failed"
    assert "sensitive" not in result.reason
