from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import Engine

from services.demo_engine.repository import (
    DemoRepository,
    make_engine_from_config,
    make_session_factory,
    session_scope,
)
from services.demo_engine.trade_logic import DemoLegPlan
from shared.models import DemoAccount, DemoTrade, DemoTradeLeg, Signal, User, UserSetting


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="PostgreSQL repository tests require RUN_DB_TESTS=1",
)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    database_engine = make_engine_from_config()
    try:
        yield database_engine
    finally:
        database_engine.dispose()


def test_demo_trade_repository_lifecycle(engine: Engine) -> None:
    marker = uuid4().int % 9_000_000_000_000_000
    session_factory = make_session_factory(engine)
    user_id: int | None = None
    signal_id: int | None = None
    try:
        with session_scope(session_factory) as session:
            user = User(telegram_id=marker, language="en")
            session.add(user)
            session.flush()
            user_id = user.id
            session.add(
                UserSetting(
                    user_id=user.id,
                    fixed_margin_usdt=Decimal("10"),
                    risk_model=1,
                    model3_exit_roi_pct=Decimal("20"),
                    max_concurrent=10,
                    leverage_mode="signal",
                )
            )
            signal = Signal(
                source_msg_id=marker,
                symbol="ETHUSDT",
                side="LONG",
                entry=Decimal("100"),
                stop_loss=Decimal("95"),
                leverage=10,
                targets_raw=["110", "120"],
                targets_clean=["110", "120"],
                sanitizer_notes={},
                status="accepted",
            )
            session.add(signal)
            session.flush()
            signal_id = signal.id
            repository = DemoRepository(session)
            account = repository.get_or_create_demo_account(
                user_id=user.id,
                start_balance_usdt=Decimal("1000"),
            )
            trade = repository.create_open_demo_trade(
                account=account,
                signal=signal,
                margin_usdt=Decimal("10"),
                notional_usdt=Decimal("100"),
                qty=Decimal("1"),
                liq_price=Decimal("90.5"),
                legs=(
                    DemoLegPlan(1, Decimal("110"), Decimal("0.5"), Decimal("0.5")),
                    DemoLegPlan(2, Decimal("120"), Decimal("0.5"), Decimal("0.5")),
                ),
                fields_realism_applied={
                    "include_commission": False,
                    "include_funding": False,
                    "include_slippage": False,
                },
            )
            assert trade.id is not None
            assert len(trade.legs) == 2
            assert repository.has_open_demo_trade(user_id=user.id, signal_id=signal.id)
            repository.mark_demo_legs_filled(demo_trade=trade, leg_indices=(1,))
            repository.close_demo_trade(
                demo_trade=trade,
                closed_reason="be",
                realized_roi_pct=Decimal("5"),
                realized_pnl_usdt=Decimal("0.5"),
                touched_tps=(1,),
            )

        with session_factory() as session:
            trade = session.get(DemoTrade, trade.id)
            account = session.get(DemoAccount, user_id)
            assert trade is not None
            assert account is not None
            assert trade.status == "closed"
            assert trade.closed_reason == "be"
            assert trade.realized_pnl_usdt == Decimal("0.5")
            assert trade.touched_tps == [1]
            assert account.balance_usdt == Decimal("1000.5")
            legs = list(
                session.query(DemoTradeLeg)
                .filter(DemoTradeLeg.demo_trade_id == trade.id)
                .order_by(DemoTradeLeg.leg_index)
            )
            assert [leg.status for leg in legs] == ["filled", "cancelled"]
    finally:
        if user_id is not None and signal_id is not None:
            with session_scope(session_factory) as session:
                trade_ids = list(
                    session.scalars(
                        DemoTrade.__table__.select()
                        .with_only_columns(DemoTrade.id)
                        .where(DemoTrade.user_id == user_id)
                    )
                )
                if trade_ids:
                    session.execute(
                        delete(DemoTradeLeg).where(
                            DemoTradeLeg.demo_trade_id.in_(trade_ids)
                        )
                    )
                session.execute(delete(DemoTrade).where(DemoTrade.user_id == user_id))
                session.execute(delete(DemoAccount).where(DemoAccount.user_id == user_id))
                session.execute(delete(UserSetting).where(UserSetting.user_id == user_id))
                session.execute(delete(Signal).where(Signal.id == signal_id))
                session.execute(delete(User).where(User.id == user_id))
