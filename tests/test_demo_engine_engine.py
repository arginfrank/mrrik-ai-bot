from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from services.demo_engine.engine import (
    DemoEngineConfig,
    handle_mark_price,
    handle_signal_created,
)
from shared.models import DemoAccount, DemoTrade, DemoTradeLeg, Signal, User, UserSetting


D = Decimal
CONFIG = DemoEngineConfig(
    start_balance_usdt=D("1000"),
    fixed_margin_usdt=D("10"),
    risk_model=1,
    model2_weights=(D("0.60"), D("0.20"), D("0.10"), D("0.07"), D("0.03")),
    model3_exit_roi_pct=D("20"),
    move_sl_to_be_after_tp1=True,
    maintenance_margin_rate=D("0.005"),
    include_commission=False,
    include_funding=False,
    include_slippage=False,
)


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, **kwargs: Any) -> object:
        self.published.append(kwargs)
        return object()


class FakeRepository:
    def __init__(self, *, account_count: int = 1, risk_model: int = 1) -> None:
        self.signal = Signal(
            id=7,
            symbol="ETHUSDT",
            side="LONG",
            entry=D("100"),
            stop_loss=D("95"),
            leverage=10,
            targets_raw=["110", "120"],
            targets_clean=["110", "120"],
            sanitizer_notes={},
            status="accepted",
        )
        self.users = {
            index: User(id=index, telegram_id=1000 + index, language="en")
            for index in range(1, account_count + 1)
        }
        self.accounts = {
            index: DemoAccount(
                user_id=index,
                start_balance_usdt=D("1000"),
                balance_usdt=D("1000"),
            )
            for index in self.users
        }
        self.settings = {
            index: UserSetting(
                user_id=index,
                fixed_margin_usdt=D("10"),
                risk_model=risk_model,
                model3_exit_roi_pct=D("20"),
                max_concurrent=10,
                leverage_mode="signal",
            )
            for index in self.users
        }
        self.trades: list[DemoTrade] = []
        self._next_id = 20

    def get_signal(self, signal_id: int) -> Signal | None:
        return self.signal if signal_id == self.signal.id else None

    def list_demo_accounts(self) -> list[DemoAccount]:
        return list(self.accounts.values())

    def get_user_settings(self, user_id: int) -> UserSetting | None:
        return self.settings.get(user_id)

    def get_user(self, user_id: int) -> User | None:
        return self.users.get(user_id)

    def has_open_demo_trade(self, *, user_id: int, signal_id: int) -> bool:
        return any(
            trade.user_id == user_id
            and trade.signal_id == signal_id
            and trade.status == "open"
            for trade in self.trades
        )

    def create_open_demo_trade(self, **kwargs: Any) -> DemoTrade:
        signal = kwargs["signal"]
        account = kwargs["account"]
        trade = DemoTrade(
            id=self._next_id,
            signal_id=signal.id,
            user_id=account.user_id,
            symbol=signal.symbol,
            side=signal.side,
            leverage=signal.leverage,
            margin_usdt=kwargs["margin_usdt"],
            notional_usdt=kwargs["notional_usdt"],
            qty=kwargs["qty"],
            liq_price=kwargs["liq_price"],
            status="open",
            touched_tps=[],
            fields_realism_applied=kwargs["fields_realism_applied"],
        )
        trade.signal = signal
        trade.legs = [
            DemoTradeLeg(
                id=100 + plan.leg_index,
                leg_index=plan.leg_index,
                target_price=plan.target_price,
                qty=plan.qty,
                status="open",
            )
            for plan in kwargs["legs"]
        ]
        self._next_id += 1
        self.trades.append(trade)
        return trade

    def list_open_demo_trades_by_symbol(self, symbol: str) -> list[DemoTrade]:
        return [
            trade
            for trade in self.trades
            if trade.symbol == symbol and trade.status == "open"
        ]

    def mark_demo_legs_filled(
        self,
        *,
        demo_trade: DemoTrade,
        leg_indices: tuple[int, ...],
    ) -> None:
        for leg in demo_trade.legs:
            if leg.leg_index in leg_indices:
                leg.status = "filled"
        demo_trade.touched_tps = sorted(
            set(demo_trade.touched_tps or []).union(leg_indices)
        )

    def close_demo_trade(self, *, demo_trade: DemoTrade, **kwargs: Any) -> DemoTrade:
        demo_trade.status = "closed"
        demo_trade.closed_reason = kwargs["closed_reason"]
        demo_trade.realized_roi_pct = kwargs["realized_roi_pct"]
        demo_trade.realized_pnl_usdt = kwargs["realized_pnl_usdt"]
        demo_trade.touched_tps = list(kwargs["touched_tps"])
        self.accounts[demo_trade.user_id].balance_usdt += kwargs["realized_pnl_usdt"]
        return demo_trade

    def get_or_create_demo_account(
        self, *, user_id: int, start_balance_usdt: Decimal
    ) -> DemoAccount:
        return self.accounts[user_id]


def test_signal_created_opens_and_publishes_open_and_notify() -> None:
    repository = FakeRepository()
    publisher = FakePublisher()

    result = asyncio.run(
        handle_signal_created(
            payload={"signal_id": 7},
            repository=repository,
            publisher=publisher,
            config=CONFIG,
        )
    )

    assert result.opened_count == 1
    assert [event["event_type"] for event in publisher.published] == [
        "demo.opened",
        "notify.user",
    ]
    assert "Result" not in publisher.published[1]["payload"]["text"]


def test_signal_created_with_no_accounts_publishes_nothing() -> None:
    repository = FakeRepository(account_count=0)
    publisher = FakePublisher()

    result = asyncio.run(
        handle_signal_created(
            payload={"signal_id": 7},
            repository=repository,
            publisher=publisher,
            config=CONFIG,
        )
    )

    assert result.ignored_reason == "no_demo_accounts"
    assert publisher.published == []


def test_duplicate_open_trade_is_skipped() -> None:
    repository, _publisher = _opened_repository()
    publisher = FakePublisher()

    result = asyncio.run(
        handle_signal_created(
            payload={"signal_id": 7},
            repository=repository,
            publisher=publisher,
            config=CONFIG,
        )
    )

    assert result.ignored_reason == "duplicate_open_trade"
    assert publisher.published == []


def test_tp1_fill_is_silent_mid_trade() -> None:
    repository, _publisher = _opened_repository()
    publisher = FakePublisher()

    result = asyncio.run(
        handle_mark_price(
            symbol="ETHUSDT",
            price=D("110"),
            repository=repository,
            publisher=publisher,
            config=CONFIG,
        )
    )

    assert result.closed_count == 0
    assert repository.trades[0].legs[0].status == "filled"
    assert publisher.published == []


def test_all_targets_close_publishes_one_close_and_one_notify() -> None:
    repository, _publisher = _opened_repository()
    publisher = FakePublisher()

    result = asyncio.run(
        handle_mark_price(
            symbol="ETHUSDT",
            price=D("120"),
            repository=repository,
            publisher=publisher,
            config=CONFIG,
        )
    )

    assert result.closed_count == 1
    assert repository.trades[0].closed_reason == "all_tp"
    assert [event["event_type"] for event in publisher.published] == [
        "demo.closed",
        "notify.user",
    ]


def test_liquidation_close_event_has_capped_loss() -> None:
    repository, _publisher = _opened_repository()
    publisher = FakePublisher()

    asyncio.run(
        handle_mark_price(
            symbol="ETHUSDT",
            price=D("89"),
            repository=repository,
            publisher=publisher,
            config=CONFIG,
        )
    )

    assert repository.trades[0].closed_reason == "liquidation"
    assert repository.trades[0].realized_pnl_usdt == D("-10")
    assert publisher.published[0]["payload"]["realized_pnl_usdt"] == "-10"


def test_model3_threshold_closes_without_tp_legs() -> None:
    repository, _publisher = _opened_repository(risk_model=3)
    publisher = FakePublisher()

    asyncio.run(
        handle_mark_price(
            symbol="ETHUSDT",
            price=D("102"),
            repository=repository,
            publisher=publisher,
            config=CONFIG,
        )
    )

    assert repository.trades[0].legs == []
    assert repository.trades[0].closed_reason == "model3_exit"
    assert len(publisher.published) == 2


def _opened_repository(*, risk_model: int = 1) -> tuple[FakeRepository, FakePublisher]:
    repository = FakeRepository(risk_model=risk_model)
    publisher = FakePublisher()
    asyncio.run(
        handle_signal_created(
            payload={"signal_id": 7},
            repository=repository,
            publisher=publisher,
            config=CONFIG,
        )
    )
    return repository, publisher
