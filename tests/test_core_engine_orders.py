from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

import services.core_engine.orders as orders_module
from services.core_engine.ids import client_order_id
from services.core_engine.orders import EntryGuard, _confirm_open_order, place_initial_orders
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
    def __init__(
        self,
        *,
        entry_status: str = "FILLED",
        fail_sl: bool = False,
        confirm_sl: bool = True,
        fail_tp_legs: frozenset[int] = frozenset(),
        fail_emergency_close: bool = False,
    ) -> None:
        self.calls: list[tuple[str, object]] = []
        self.entry_status = entry_status
        self.fail_sl = fail_sl
        self.confirm_sl = confirm_sl
        self.fail_tp_legs = fail_tp_legs
        self.fail_emergency_close = fail_emergency_close
        self.open_orders: list[ExchangeOrder] = []

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
        if self.fail_sl:
            raise RuntimeError("stop placement failed")
        order = _order(str(values["client_order_id"]), "NEW")
        if self.confirm_sl:
            self.open_orders.append(order)
        return order

    async def place_take_profit_market(self, **values: object) -> ExchangeOrder:
        self.calls.append(("tp", values))
        client_id = str(values["client_order_id"])
        leg_index = int(client_id.rsplit("-", maxsplit=1)[-1])
        if leg_index in self.fail_tp_legs:
            raise RuntimeError("sensitive exchange detail")
        order = _order(client_id, "NEW")
        self.open_orders.append(order)
        return order

    async def cancel_order(self, **values: object) -> None:
        self.calls.append(("cancel", values))

    async def get_position(self, **values: object):
        return None

    async def get_open_orders(self, **values: object) -> list[ExchangeOrder]:
        self.calls.append(("open_orders", values))
        return list(self.open_orders)

    async def get_open_algo_orders(self, **values: object) -> list[ExchangeOrder]:
        self.calls.append(("open_algo_orders", values))
        return list(self.open_orders)

    async def close_position_market(self, **values: object) -> ExchangeOrder:
        self.calls.append(("close", values))
        if self.fail_emergency_close:
            raise RuntimeError("emergency close failed")
        return _order(str(values["client_order_id"]))

    async def cancel_open_orders(self, **values: object) -> None:
        self.calls.append(("cancel_all", values))
        self.open_orders.clear()

    async def cancel_all_algo_orders(self, **values: object) -> None:
        self.calls.append(("cancel_all_algo", values))
        self.open_orders.clear()


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orders_module, "_ORDER_RETRY_DELAYS_SEC", (0, 0))
    monkeypatch.setattr(orders_module, "_CONFIRM_RETRY_DELAYS_SEC", (0, 0))


def _trade_and_plan(
    *, model3: bool = False, leg_count: int = 1, side: str = "LONG"
) -> tuple[Trade, Signal, ExecutionPlan]:
    signal = Signal(
        id=1,
        symbol="HBARUSDT",
        side=side,
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
        side=side,
        leverage=42,
        margin_usdt=Decimal("10"),
        notional_usdt=Decimal("420"),
        qty=Decimal("100"),
        status="pending_entry",
    )
    legs = () if model3 else tuple(
        ExecutionLegPlan(
            leg_index=index,
            target_price=Decimal("0.07145") + Decimal(index) * Decimal("0.00041"),
            fraction=Decimal("1") / Decimal(leg_count),
            qty=Decimal("100") / Decimal(leg_count),
        )
        for index in range(1, leg_count + 1)
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


def _run(
    fake: FakeExchange,
    *,
    entry_mode: str = "limit",
    model3: bool = False,
    leg_count: int = 1,
    side: str = "LONG",
):
    trade, signal, plan = _trade_and_plan(
        model3=model3, leg_count=leg_count, side=side
    )
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
        "open_algo_orders",
        "tp",
    ]
    assert result.status == "opened"
    assert result.entry_order_id == client_order_id(trade_id=trade.id, purpose="entry")
    assert result.sl_order_id == client_order_id(trade_id=trade.id, purpose="sl")
    sl_call = next(values for name, values in fake.calls if name == "sl")
    assert isinstance(sl_call, dict)
    assert sl_call["qty"] == Decimal("100")


@pytest.mark.parametrize(
    ("trade_side", "entry_side", "close_side"),
    (("LONG", "BUY", "SELL"), ("SHORT", "SELL", "BUY")),
)
def test_every_trade_order_uses_its_hedge_position_side(
    trade_side: str, entry_side: str, close_side: str
) -> None:
    fake = FakeExchange()
    _run(fake, side=trade_side)

    calls = {
        name: values
        for name, values in fake.calls
        if name in {"entry_limit", "sl", "tp"}
    }
    assert set(calls) == {"entry_limit", "sl", "tp"}
    assert all(
        isinstance(values, dict) and values["position_side"] == trade_side
        for values in calls.values()
    )
    assert calls["entry_limit"]["side"] == entry_side  # type: ignore[index]
    assert calls["sl"]["side"] == close_side  # type: ignore[index]
    assert calls["tp"]["side"] == close_side  # type: ignore[index]
    assert all("reduce_only" not in values for values in calls.values())  # type: ignore[operator]


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


def test_sl_placement_failure_emergency_closes_instead_of_opening() -> None:
    fake = FakeExchange(fail_sl=True)
    _, result = _run(fake)

    assert result.status == "emergency_closed"
    assert result.sl_order_id is None
    assert "close" in [name for name, _ in fake.calls]
    assert result.status != "opened"
    close_call = next(values for name, values in fake.calls if name == "close")
    assert isinstance(close_call, dict)
    assert close_call["side"] == "SELL"
    assert close_call["position_side"] == "LONG"


def test_unconfirmed_sl_emergency_closes_instead_of_opening() -> None:
    fake = FakeExchange(confirm_sl=False)
    _, result = _run(fake)

    assert result.status == "emergency_closed"
    assert result.sl_order_id is None
    assert "close" in [name for name, _ in fake.calls]


def test_confirm_open_order_checks_algo_orders_for_matching_client_id() -> None:
    fake = FakeExchange()
    fake.open_orders.append(_order("m7-9-sl", "NEW"))

    assert asyncio.run(
        _confirm_open_order(
            exchange=fake, symbol="HBARUSDT", client_order_id="m7-9-sl"
        )
    )
    assert not asyncio.run(
        _confirm_open_order(
            exchange=fake, symbol="HBARUSDT", client_order_id="m7-9-other"
        )
    )


def test_confirmed_sl_keeps_trade_open_when_one_tp_leg_fails() -> None:
    fake = FakeExchange(fail_tp_legs=frozenset({2}))
    trade, result = _run(fake, leg_count=3)

    assert result.status == "opened"
    assert result.sl_order_id == client_order_id(trade_id=trade.id, purpose="sl")
    assert "close" not in [name for name, _ in fake.calls]
    assert result.tp_order_ids == (
        client_order_id(trade_id=trade.id, purpose="tp", leg_index=1),
        client_order_id(trade_id=trade.id, purpose="tp", leg_index=3),
    )


def test_happy_path_confirms_sl_and_places_all_tps() -> None:
    fake = FakeExchange()
    trade, result = _run(fake, leg_count=3)

    assert result.status == "opened"
    assert result.sl_order_id == client_order_id(trade_id=trade.id, purpose="sl")
    assert result.tp_order_ids == tuple(
        client_order_id(trade_id=trade.id, purpose="tp", leg_index=index)
        for index in range(1, 4)
    )


def test_emergency_close_failure_is_loud_and_never_reports_opened() -> None:
    fake = FakeExchange(fail_sl=True, fail_emergency_close=True)
    _, result = _run(fake)

    assert result.status == "emergency_close_failed"
    assert result.sl_order_id is None
    assert result.status not in {"opened", "error"}
    assert "cancel_all" not in [name for name, _ in fake.calls]
