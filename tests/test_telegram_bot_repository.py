from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from services.telegram_bot.repository import (
    TelegramBotRepository,
    make_engine_from_config,
    make_session_factory,
)
from shared.models import (
    DemoAccount,
    DemoTrade,
    DemoTradeLeg,
    Signal,
    Subscription,
    User,
    UserSetting,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="PostgreSQL repository tests require RUN_DB_TESTS=1",
)


def test_telegram_bot_repository_m5_lifecycle() -> None:
    engine = make_engine_from_config()
    session = make_session_factory(engine)()
    transaction = session.begin()
    try:
        repository = TelegramBotRepository(session)
        telegram_id = uuid4().int % 9_000_000_000_000_000

        user = repository.get_or_create_user(
            telegram_id=telegram_id,
            username="first",
        )
        same_user = repository.get_or_create_user(
            telegram_id=telegram_id,
            username="second",
        )
        assert same_user.id == user.id
        assert same_user.username == "second"

        repository.set_language(telegram_id=telegram_id, language="en")
        assert user.language == "en"

        plans = repository.list_active_plans()
        assert [plan.code for plan in plans] == ["P30", "P90"]
        plan = repository.get_plan_by_code("P30")
        assert plan is not None

        subscription, payment = repository.create_pending_subscription_and_payment(
            user=user,
            plan=plan,
            network="TRC20",
            to_address="test-wallet",
            txid=f"test-{uuid4()}",
        )
        assert subscription.status == "pending"
        assert subscription.starts_at is None
        assert subscription.ends_at is None
        assert payment.status == "submitted"
        assert payment.amount_expected == plan.price_usdt
        assert payment.network == "TRC20"
        assert payment.id is not None

        with pytest.raises(IntegrityError):
            with session.begin_nested():
                repository.create_pending_subscription_and_payment(
                    user=user,
                    plan=plan,
                    network=payment.network,
                    to_address="test-wallet",
                    txid=str(payment.txid),
                )

        first_demo = repository.get_or_create_demo_account(
            user=user,
            start_balance_usdt=Decimal("1000"),
        )
        second_demo = repository.get_or_create_demo_account(
            user=user,
            start_balance_usdt=Decimal("500"),
        )
        assert first_demo is second_demo
        assert second_demo.start_balance_usdt == Decimal("1000")

        settings = repository.get_or_create_user_settings(user=user)
        repository.update_fixed_margin(
            user=user,
            fixed_margin_usdt=Decimal("12.5"),
        )
        repository.update_risk_model(user=user, risk_model=3)
        assert settings.fixed_margin_usdt == Decimal("12.5")
        assert settings.risk_model == 3

        credential = repository.store_exchange_credentials(
            user=user,
            api_key_enc=b"encrypted-key-1",
            api_secret_enc=b"encrypted-secret-1",
        )
        same_credential = repository.store_exchange_credentials(
            user=user,
            api_key_enc=b"encrypted-key-2",
            api_secret_enc=b"encrypted-secret-2",
        )
        assert same_credential.id == credential.id
        assert same_credential.api_key_enc == b"encrypted-key-2"
        assert same_credential.scope_verified is False
        assert same_credential.is_valid is False
        assert same_credential.hedge_enabled is False
        repository.set_credential_validation(
            user=user,
            is_valid=True,
            scope_verified=True,
            hedge_enabled=True,
        )
        assert same_credential.scope_verified is True
        assert same_credential.is_valid is True
        assert same_credential.hedge_enabled is True

        now = datetime.now(UTC)
        near = Subscription(
            user=user,
            plan=plan,
            status="active",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(hours=12),
            reminded_24h=False,
        )
        far = Subscription(
            user=user,
            plan=plan,
            status="active",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=2),
            reminded_24h=False,
        )
        past = Subscription(
            user=user,
            plan=plan,
            status="active",
            starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(seconds=1),
            reminded_24h=False,
        )
        already_reminded = Subscription(
            user=user,
            plan=plan,
            status="active",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(hours=6),
            reminded_24h=True,
        )
        session.add_all((near, far, past, already_reminded))
        session.flush()

        due_reminders = repository.list_subscriptions_due_for_24h_reminder(
            now_utc=now
        )
        assert near in due_reminders
        assert far not in due_reminders
        assert already_reminded not in due_reminders
        repository.mark_subscription_reminded_24h(near)
        assert near.reminded_24h is True

        due_expiry = repository.list_subscriptions_due_for_expiry(now_utc=now)
        assert past in due_expiry
        assert near not in due_expiry
        repository.mark_subscription_expired(past)
        assert past.status == "expired"
    finally:
        if transaction.is_active:
            transaction.rollback()
        session.close()
        engine.dispose()


def test_reset_demo_for_user_deletes_only_target_users_demo_data() -> None:
    engine = make_engine_from_config()
    session = make_session_factory(engine)()
    transaction = session.begin()
    try:
        repository = TelegramBotRepository(session)
        target_user = User(
            telegram_id=uuid4().int % 9_000_000_000_000_000,
            language="en",
        )
        other_user = User(
            telegram_id=uuid4().int % 9_000_000_000_000_000,
            language="en",
        )
        session.add_all((target_user, other_user))
        session.flush()

        target_settings = UserSetting(user_id=target_user.id)
        target_account = DemoAccount(
            user_id=target_user.id,
            start_balance_usdt=Decimal("1000"),
            balance_usdt=Decimal("975"),
        )
        other_account = DemoAccount(
            user_id=other_user.id,
            start_balance_usdt=Decimal("1000"),
            balance_usdt=Decimal("1025"),
        )
        target_signal = _signal(source_msg_id=uuid4().int % 9_000_000_000_000_000)
        other_signal = _signal(source_msg_id=uuid4().int % 9_000_000_000_000_000)
        session.add_all(
            (
                target_settings,
                target_account,
                other_account,
                target_signal,
                other_signal,
            )
        )
        session.flush()

        target_trades = [
            _demo_trade(user_id=target_user.id, signal_id=target_signal.id),
            _demo_trade(user_id=target_user.id, signal_id=target_signal.id),
        ]
        other_trade = _demo_trade(
            user_id=other_user.id,
            signal_id=other_signal.id,
        )
        session.add_all((*target_trades, other_trade))
        session.flush()
        target_trade_ids = [trade.id for trade in target_trades]
        session.add_all(
            (
                DemoTradeLeg(
                    demo_trade_id=target_trades[0].id,
                    leg_index=1,
                    target_price=Decimal("110"),
                    qty=Decimal("0.5"),
                ),
                DemoTradeLeg(
                    demo_trade_id=target_trades[1].id,
                    leg_index=1,
                    target_price=Decimal("110"),
                    qty=Decimal("0.5"),
                ),
                DemoTradeLeg(
                    demo_trade_id=other_trade.id,
                    leg_index=1,
                    target_price=Decimal("110"),
                    qty=Decimal("0.5"),
                ),
            )
        )
        session.flush()

        deleted_count = repository.reset_demo_for_user(target_user.id)

        assert deleted_count == 2
        assert list(
            session.scalars(
                select(DemoTrade).where(DemoTrade.user_id == target_user.id)
            )
        ) == []
        assert list(
            session.scalars(
                select(DemoTradeLeg).where(
                    DemoTradeLeg.demo_trade_id.in_(target_trade_ids)
                )
            )
        ) == []
        assert list(
            session.scalars(
                select(DemoTrade).where(DemoTrade.user_id == other_user.id)
            )
        ) == [other_trade]
        assert list(
            session.scalars(
                select(DemoTradeLeg).where(
                    DemoTradeLeg.demo_trade_id == other_trade.id
                )
            )
        )
        assert session.get(Signal, target_signal.id) is target_signal
        assert session.get(Signal, other_signal.id) is other_signal
        assert session.get(UserSetting, target_user.id) is target_settings
        assert session.get(DemoAccount, target_user.id).balance_usdt == Decimal("1000")
        assert session.get(DemoAccount, other_user.id).balance_usdt == Decimal("1025")
    finally:
        if transaction.is_active:
            transaction.rollback()
        session.close()
        engine.dispose()


def _signal(*, source_msg_id: int) -> Signal:
    return Signal(
        source_msg_id=source_msg_id,
        symbol="ETHUSDT",
        side="LONG",
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        leverage=10,
        targets_raw=["110"],
        targets_clean=["110"],
        sanitizer_notes={},
        status="accepted",
    )


def _demo_trade(*, user_id: int, signal_id: int) -> DemoTrade:
    return DemoTrade(
        user_id=user_id,
        signal_id=signal_id,
        symbol="ETHUSDT",
        side="LONG",
        leverage=10,
        margin_usdt=Decimal("10"),
        notional_usdt=Decimal("100"),
        qty=Decimal("1"),
        liq_price=Decimal("90"),
        status="open",
        touched_tps=[],
    )
