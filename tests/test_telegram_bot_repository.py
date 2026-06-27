from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from services.telegram_bot.repository import (
    TelegramBotRepository,
    make_engine_from_config,
    make_session_factory,
)
from shared.models import Subscription


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
