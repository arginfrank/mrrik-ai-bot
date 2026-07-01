from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from services.core_engine.repository import CoreRepository
from shared.models import ExchangeCredential, Plan, Signal, Subscription, User, UserSetting


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1", reason="requires PostgreSQL integration database"
)


def test_repository_real_trade_lifecycle() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        unique = uuid4().int % 1_000_000_000
        user = User(telegram_id=9_000_000_000 + unique, language="en", is_blocked=False)
        plan = Plan(
            code=f"M7-{unique}",
            duration_days=30,
            price_usdt=Decimal("49"),
            is_active=True,
        )
        session.add_all([user, plan])
        session.flush()
        session.add_all(
            [
                Subscription(
                    user_id=user.id,
                    plan_id=plan.id,
                    status="active",
                    starts_at=datetime.now(UTC),
                    ends_at=datetime.now(UTC) + timedelta(days=1),
                ),
                UserSetting(user_id=user.id),
                ExchangeCredential(
                    user_id=user.id,
                    exchange="binance",
                    api_key_enc=b"encrypted-key",
                    api_secret_enc=b"encrypted-secret",
                    scope_verified=True,
                    is_valid=True,
                    hedge_enabled=True,
                ),
            ]
        )
        signal = Signal(
            symbol="HBARUSDT",
            side="LONG",
            entry=Decimal("0.07145"),
            stop_loss=Decimal("0.07077"),
            leverage=42,
            targets_raw=["0.07186"],
            targets_clean=["0.07186"],
            status="accepted",
        )
        session.add(signal)
        session.flush()
        repository = CoreRepository(session)

        repository.lock_user_for_execution(user.id)
        event_id = uuid4()
        assert repository.mark_event_processed(event_id) is True
        assert repository.mark_event_processed(event_id) is False
        assert repository.list_eligible_users_for_signal() == [user]
        assert repository.get_exchange_credentials(user.id) is not None
        assert repository.count_open_trades(user.id) == 0

        trade = repository.create_trade_from_plan(
            user_id=user.id,
            signal=signal,
            margin_usdt=Decimal("10"),
            notional_usdt=Decimal("420"),
            qty=Decimal("100"),
            leverage=42,
            liq_price=Decimal("0.0701"),
            legs=(
                SimpleNamespace(
                    leg_index=1,
                    target_price=Decimal("0.07186"),
                    qty=Decimal("100"),
                ),
            ),
        )
        assert repository.has_open_trade_for_signal(
            user_id=user.id, signal_id=signal.id
        )
        assert repository.count_open_trades(user.id) == 1
        repository.set_trade_entry_order(trade=trade, entry_order_id="entry-id")
        repository.mark_trade_opened(trade=trade, sl_order_id="sl-id")
        repository.set_leg_tp_order(leg=trade.legs[0], tp_order_id="tp-id")
        repository.mark_leg_filled(trade=trade, leg_index=1)
        assert repository.get_trade_by_client_order_id("sl-id") == trade
        assert repository.get_trade_leg_by_client_order_id("tp-id") == (
            trade,
            trade.legs[0],
        )
        repository.close_trade(
            trade=trade,
            closed_reason="all_tp",
            realized_pnl_usdt=Decimal("2"),
            realized_roi_pct=Decimal("20"),
            touched_tps=(1,),
        )
        assert trade.status == "closed"
        assert trade.realized_pnl_usdt == Decimal("2")
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
