from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest

import services.core_engine.orders as orders_module
from services.core_engine.engine import CoreEngineConfig, handle_signal_created
from shared.crypto import encrypt_secret
from shared.exchange.types import ExchangeOrder, SymbolFilters
from shared.models import Signal, Trade, TradeLeg, User


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    async def publish(self, *, stream: str, event_type: str, payload: dict):
        self.events.append((stream, event_type, payload))


class FakeRedis:
    def __init__(self, enabled: set[str] | None = None) -> None:
        self.enabled = enabled or set()

    async def get(self, key: str):
        return "1" if key in self.enabled else None


class FakeExchange:
    def __init__(
        self,
        *,
        balance: Decimal = Decimal("100"),
        withdrawal_safe: bool = True,
        fail: bool = False,
        fail_sl: bool = False,
        fail_emergency_close: bool = False,
    ) -> None:
        self.balance = balance
        self.withdrawal_safe = withdrawal_safe
        self.fail = fail
        self.fail_sl = fail_sl
        self.fail_emergency_close = fail_emergency_close
        self.calls: list[str] = []
        self.open_orders: list[ExchangeOrder] = []

    async def verify_credentials(self) -> bool:
        return True

    async def verify_withdrawals_disabled(self) -> bool:
        return self.withdrawal_safe

    async def get_usdt_balance(self) -> Decimal:
        if self.fail:
            raise RuntimeError("api-secret-should-not-leak")
        return self.balance

    async def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        return SymbolFilters(
            symbol=symbol,
            step_size=Decimal("1"),
            tick_size=Decimal("0.00001"),
            min_qty=Decimal("1"),
            min_notional=Decimal("5"),
        )

    async def set_leverage(self, **values: object) -> None:
        pass

    async def set_margin_type_isolated(self, **values: object) -> None:
        pass

    async def place_entry_limit(self, **values: object) -> ExchangeOrder:
        return _order(str(values["client_order_id"]))

    async def place_entry_market(self, **values: object) -> ExchangeOrder:
        return _order(str(values["client_order_id"]))

    async def place_stop_market(self, **values: object) -> ExchangeOrder:
        self.calls.append("sl")
        if self.fail_sl:
            raise RuntimeError("stop placement failed")
        order = _order(str(values["client_order_id"]), status="NEW")
        self.open_orders.append(order)
        return order

    async def place_take_profit_market(self, **values: object) -> ExchangeOrder:
        order = _order(str(values["client_order_id"]), status="NEW")
        self.open_orders.append(order)
        return order

    async def get_open_orders(self, **values: object) -> list[ExchangeOrder]:
        return list(self.open_orders)

    async def get_open_algo_orders(self, **values: object) -> list[ExchangeOrder]:
        return list(self.open_orders)

    async def cancel_open_orders(self, **values: object) -> None:
        self.calls.append("cancel_all")
        self.open_orders.clear()

    async def cancel_all_algo_orders(self, **values: object) -> None:
        self.calls.append("cancel_all_algo")
        self.open_orders.clear()

    async def close_position_market(self, **values: object) -> ExchangeOrder:
        self.calls.append("close")
        if self.fail_emergency_close:
            raise RuntimeError("emergency close failed")
        return _order(str(values["client_order_id"]))


def _order(client_id: str, *, status: str = "FILLED") -> ExchangeOrder:
    return ExchangeOrder(
        exchange_order_id="1",
        client_order_id=client_id,
        symbol="HBARUSDT",
        side="BUY",
        order_type="MARKET",
        status=status,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orders_module, "_ORDER_RETRY_DELAYS_SEC", (0, 0))
    monkeypatch.setattr(orders_module, "_CONFIRM_RETRY_DELAYS_SEC", (0, 0))


class FakeFactory:
    def __init__(self, exchange: FakeExchange) -> None:
        self.exchange = exchange

    def create(self, *, api_key: str, api_secret: str, user_id: int):
        assert api_key == "key"
        assert api_secret == "secret"
        assert user_id == 2
        return self.exchange


class FakeRepository:
    def __init__(self, fernet_key: str) -> None:
        self.processed: set[object] = set()
        self.user = User(
            id=2, telegram_id=99, language="en", is_blocked=False
        )
        self.signal = Signal(
            id=1,
            symbol="HBARUSDT",
            side="LONG",
            entry=Decimal("0.07145"),
            stop_loss=Decimal("0.07077"),
            leverage=42,
            targets_raw=["0.07186"],
            targets_clean=["0.07186"],
            status="accepted",
        )
        self.settings = SimpleNamespace(
            fixed_margin_usdt=Decimal("10"),
            risk_model=1,
            model3_exit_roi_pct=Decimal("20"),
            max_concurrent=10,
            leverage_mode="signal",
            leverage_cap=None,
        )
        self.subscription: object | None = object()
        self.credentials = SimpleNamespace(
            api_key_enc=encrypt_secret("key", fernet_key),
            api_secret_enc=encrypt_secret("secret", fernet_key),
            is_valid=True,
            scope_verified=True,
            hedge_enabled=True,
        )
        self.open_count = 0
        self.duplicate = False
        self.trade: Trade | None = None

    def mark_event_processed(self, event_id: object) -> bool:
        if event_id in self.processed:
            return False
        self.processed.add(event_id)
        return True

    def get_signal(self, signal_id: int):
        return self.signal if signal_id == 1 else None

    def list_eligible_users_for_signal(self):
        return [self.user]

    def get_active_subscription(self, user_id: int):
        return self.subscription

    def has_open_trade_for_signal(self, **values: object) -> bool:
        return self.duplicate or (
            self.trade is not None
            and self.trade.status in {"pending_entry", "open"}
            and (
                self.trade.signal_id == values["signal_id"]
                or self.trade.signal is self.signal
            )
        )

    def get_user_settings(self, user_id: int):
        return self.settings

    def count_open_trades(self, user_id: int) -> int:
        return self.open_count

    def get_exchange_credentials(self, user_id: int):
        return self.credentials

    def create_trade_from_plan(self, *, signal: Signal, legs: tuple[object, ...], **values):
        trade = Trade(
            id=31,
            signal=signal,
            user_id=2,
            symbol=signal.symbol,
            side=signal.side,
            leverage=values["leverage"],
            margin_usdt=values["margin_usdt"],
            notional_usdt=values["notional_usdt"],
            qty=values["qty"],
            liq_price=values["liq_price"],
            status="pending_entry",
            touched_tps=[],
        )
        trade.legs = [
            TradeLeg(
                leg_index=leg.leg_index,
                target_price=leg.target_price,
                qty=leg.qty,
                status="open",
            )
            for leg in legs
        ]
        self.trade = trade
        return trade

    def set_trade_entry_order(self, *, trade: Trade, entry_order_id: str) -> None:
        trade.entry_order_id = entry_order_id

    def mark_trade_opened(self, *, trade: Trade, sl_order_id: str | None) -> None:
        trade.status = "open"
        trade.sl_order_id = sl_order_id

    def set_leg_tp_order(self, *, leg: TradeLeg, tp_order_id: str) -> None:
        leg.tp_order_id = tp_order_id

    def mark_trade_status(self, *, trade: Trade, status: str) -> None:
        trade.status = status

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


CONFIG = CoreEngineConfig(
    fixed_margin_usdt=Decimal("10"),
    risk_model=1,
    model2_weights=(Decimal("0.6"), Decimal("0.4")),
    model3_exit_roi_pct=Decimal("20"),
    move_sl_to_be_after_tp1=True,
    max_concurrent=10,
    entry_mode="limit",
    entry_fill_timeout_sec=0,
    entry_max_deviation_pct=Decimal("0.5"),
    maintenance_margin_rate=Decimal("0.005"),
)


def _handle(
    repository: FakeRepository,
    exchange: FakeExchange,
    fernet_key: str,
    *,
    redis_client: FakeRedis | None = None,
    event_id: str | None = None,
    config: CoreEngineConfig = CONFIG,
):
    publisher = FakePublisher()
    result = asyncio.run(
        handle_signal_created(
            event_id=event_id or str(uuid4()),
            payload={"signal_id": 1},
            repository=repository,
            exchange_factory=FakeFactory(exchange),
            publisher=publisher,
            config=config,
            fernet_key=fernet_key,
            redis_client=redis_client,
        )
    )
    return result, publisher


def test_signal_opens_trade_and_duplicate_event_is_ignored() -> None:
    key = Fernet.generate_key().decode()
    repository = FakeRepository(key)
    event_id = str(uuid4())
    result, publisher = _handle(repository, FakeExchange(), key, event_id=event_id)
    duplicate, _ = _handle(repository, FakeExchange(), key, event_id=event_id)

    assert result.opened_count == 1
    assert repository.trade is not None and repository.trade.status == "open"
    assert {event[1] for event in publisher.events} == {"trade.opened", "notify.user"}
    assert duplicate.ignored_reason == "duplicate event"


def test_signal_created_retries_until_signal_becomes_visible() -> None:
    key = Fernet.generate_key().decode()
    repository = FakeRepository(key)
    real_get_signal = repository.get_signal
    reads = 0

    def delayed_get_signal(signal_id: int) -> Signal | None:
        nonlocal reads
        reads += 1
        return None if reads == 1 else real_get_signal(signal_id)

    repository.get_signal = delayed_get_signal  # type: ignore[method-assign]
    publisher = FakePublisher()
    result = asyncio.run(
        handle_signal_created(
            event_id=str(uuid4()),
            payload={"signal_id": 1},
            repository=repository,
            exchange_factory=FakeFactory(FakeExchange()),
            publisher=publisher,
            config=replace(CONFIG, signal_lookup_retry_delays_sec=(0, 0, 0)),
            fernet_key=key,
        )
    )

    assert result.opened_count == 1
    assert reads == 2


def test_missing_signal_is_transient_and_event_is_not_marked_processed() -> None:
    key = Fernet.generate_key().decode()
    repository = FakeRepository(key)
    reads = 0

    def missing_signal(_signal_id: int) -> None:
        nonlocal reads
        reads += 1
        return None

    repository.get_signal = missing_signal  # type: ignore[method-assign]
    result = asyncio.run(
        handle_signal_created(
            event_id=str(uuid4()),
            payload={"signal_id": 1},
            repository=repository,
            exchange_factory=FakeFactory(FakeExchange()),
            publisher=FakePublisher(),
            config=replace(CONFIG, signal_lookup_retry_delays_sec=(0, 0, 0)),
            fernet_key=key,
        )
    )

    assert result.status == "retry"
    assert result.ignored_reason == "signal_not_found"
    assert reads == 4
    assert repository.processed == set()


def test_repeated_signal_with_different_event_id_does_not_open_twice() -> None:
    key = Fernet.generate_key().decode()
    repository = FakeRepository(key)
    first, _ = _handle(repository, FakeExchange(), key)
    first_trade = repository.trade
    repeated, publisher = _handle(repository, FakeExchange(), key)

    assert first.opened_count == 1
    assert repeated.opened_count == 0
    assert repeated.skipped_count == 1
    assert repository.trade is first_trade
    assert publisher.events[0][2]["reason"] == "duplicate open trade"


def test_global_and_per_user_kill_switches_skip() -> None:
    for redis_key in ("kill_switch:global", "kill_switch:user:2"):
        key = Fernet.generate_key().decode()
        result, publisher = _handle(
            FakeRepository(key),
            FakeExchange(),
            key,
            redis_client=FakeRedis({redis_key}),
        )
        assert result.skipped_count == 1
        assert publisher.events[0][2]["reason"] == "kill switch enabled"


def test_user_and_capital_prechecks_skip_with_explicit_reasons() -> None:
    cases: list[tuple[str, object]] = [
        ("inactive subscription", None),
        ("blocked user", True),
        ("missing credentials", None),
        ("max concurrent reached", 10),
    ]
    for reason, value in cases:
        key = Fernet.generate_key().decode()
        repository = FakeRepository(key)
        if reason == "inactive subscription":
            repository.subscription = value
        elif reason == "blocked user":
            repository.user.is_blocked = bool(value)
        elif reason == "missing credentials":
            repository.credentials = value
        else:
            repository.open_count = int(value)
        result, publisher = _handle(repository, FakeExchange(), key)
        assert result.skipped_count == 1
        assert publisher.events[0][2]["reason"] == reason


def test_invalid_scope_withdrawal_and_margin_prechecks_skip() -> None:
    key = Fernet.generate_key().decode()
    repository = FakeRepository(key)
    repository.credentials.scope_verified = False
    result, publisher = _handle(repository, FakeExchange(), key)
    assert result.skipped_count == 1
    assert publisher.events[0][2]["reason"] == "credentials not valid"

    key = Fernet.generate_key().decode()
    repository = FakeRepository(key)
    repository.credentials.hedge_enabled = False
    result, publisher = _handle(repository, FakeExchange(), key)
    assert result.skipped_count == 1
    assert publisher.events[0][2]["reason"] == "credentials not valid"

    key = Fernet.generate_key().decode()
    result, publisher = _handle(
        FakeRepository(key), FakeExchange(withdrawal_safe=False), key
    )
    assert result.skipped_count == 1
    assert publisher.events[0][2]["reason"] == "withdrawals not disabled"

    key = Fernet.generate_key().decode()
    result, publisher = _handle(
        FakeRepository(key), FakeExchange(balance=Decimal("9.99")), key
    )
    assert result.skipped_count == 1
    assert publisher.events[0][2]["reason"] == "insufficient free margin"


def test_exchange_exception_publishes_only_safe_error() -> None:
    key = Fernet.generate_key().decode()
    result, publisher = _handle(FakeRepository(key), FakeExchange(fail=True), key)

    assert result.error_count == 1
    assert publisher.events[-1][1] == "trade.error"
    assert publisher.events[-1][2]["reason"] == "exchange operation failed"
    assert "api-secret" not in repr(publisher.events)


def test_emergency_closed_trade_is_persisted_closed_and_notified() -> None:
    key = Fernet.generate_key().decode()
    repository = FakeRepository(key)
    exchange = FakeExchange(fail_sl=True)
    result, publisher = _handle(
        repository,
        exchange,
        key,
        config=replace(CONFIG, admin_telegram_ids=(777,)),
    )

    assert result.error_count == 1
    assert repository.trade is not None
    assert repository.trade.status == "closed"
    assert repository.trade.closed_reason == "aborted_no_protection"
    assert repository.trade.realized_pnl_usdt == Decimal("0")
    assert repository.trade.realized_roi_pct == Decimal("0")
    assert [event_type for _, event_type, _ in publisher.events] == [
        "trade.closed",
        "notify.user",
        "notify.admin",
    ]
    assert "aborted for safety" in publisher.events[1][2]["text"]


def test_emergency_close_failure_stays_open_for_reconciliation_and_alerts() -> None:
    key = Fernet.generate_key().decode()
    repository = FakeRepository(key)
    exchange = FakeExchange(fail_sl=True, fail_emergency_close=True)
    result, publisher = _handle(
        repository,
        exchange,
        key,
        config=replace(CONFIG, admin_telegram_ids=(777,)),
    )

    assert result.error_count == 1
    assert repository.trade is not None
    assert repository.trade.status == "open"
    assert repository.trade.sl_order_id is None
    assert [event_type for _, event_type, _ in publisher.events] == [
        "trade.error",
        "notify.admin",
    ]
    assert publisher.events[0][2]["reason"] == (
        "stop_loss_unconfirmed_emergency_close_failed"
    )
