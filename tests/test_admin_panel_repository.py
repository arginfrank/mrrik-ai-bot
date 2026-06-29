from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from uuid import uuid4

import pytest
from sqlalchemy import select

from services.admin_panel.repository import (
    AdminPanelRepository,
    make_engine_from_config,
    make_session_factory,
)
from shared.models import (
    AuditLog,
    DemoAccount,
    DemoTrade,
    ExchangeCredential,
    Payment,
    Plan,
    Signal,
    Subscription,
    Trade,
    User,
    UserSetting,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="PostgreSQL repository tests require RUN_DB_TESTS=1",
)


def test_admin_panel_repository_payment_and_anomaly_lifecycle() -> None:
    engine = make_engine_from_config()
    session = make_session_factory(engine)()
    transaction = session.begin()
    try:
        repository = AdminPanelRepository(session)
        plans = list(session.scalars(select(Plan).order_by(Plan.duration_days)))
        assert len(plans) >= 2
        short_plan, long_plan = plans[:2]
        user = User(
            telegram_id=uuid4().int % 9_000_000_000_000_000,
            username="m6-test",
            language="en",
        )
        session.add(user)
        session.flush()

        pending_approval = Subscription(user=user, plan=short_plan, status="pending")
        approval_payment = Payment(
            user=user,
            plan=short_plan,
            network="TRC20",
            to_address="tron-wallet",
            amount_expected=short_plan.price_usdt,
            txid=f"m6-approve-{uuid4()}",
            status="submitted",
        )
        pending_rejection = Subscription(user=user, plan=long_plan, status="pending")
        rejection_payment = Payment(
            user=user,
            plan=long_plan,
            network="BEP20",
            to_address="evm-wallet",
            amount_expected=long_plan.price_usdt,
            txid=f"m6-reject-{uuid4()}",
            status="submitted",
        )
        session.add_all(
            (pending_approval, approval_payment, pending_rejection, rejection_payment)
        )
        session.add_all(
            (
                ExchangeCredential(
                    user=user,
                    api_key_enc=b"encrypted-key",
                    api_secret_enc=b"encrypted-secret",
                    scope_verified=True,
                    is_valid=True,
                ),
                UserSetting(user=user, risk_model=2),
                DemoAccount(user=user, balance_usdt=Decimal("1004.20")),
                DemoTrade(
                    user=user,
                    symbol="ETHUSDT",
                    side="LONG",
                    leverage=10,
                    margin_usdt=Decimal("10"),
                    notional_usdt=Decimal("100"),
                    qty=Decimal("0.05"),
                    status="closed",
                    realized_pnl_usdt=Decimal("4.20"),
                    realized_roi_pct=Decimal("42"),
                    touched_tps=[1],
                    closed_reason="all_tp",
                    opened_at=datetime(2026, 1, 1, 10, tzinfo=UTC),
                    closed_at=datetime(2026, 1, 1, 11, tzinfo=UTC),
                ),
                Trade(
                    user=user,
                    symbol="BTCUSDT",
                    side="LONG",
                    leverage=10,
                    margin_usdt=Decimal("10"),
                    notional_usdt=Decimal("100"),
                    qty=Decimal("0.001"),
                    status="closed",
                    realized_pnl_usdt=Decimal("2.50"),
                    realized_roi_pct=Decimal("25"),
                    touched_tps=[1],
                    closed_reason="all_tp",
                    opened_at=datetime(2026, 1, 1, 10, tzinfo=UTC),
                    closed_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
                ),
            )
        )
        session.flush()

        queue = repository.list_payment_queue()
        queue_ids = [payment.id for payment in queue]
        assert queue_ids.index(approval_payment.id) < queue_ids.index(rejection_payment.id)

        repository.update_payment_precheck(
            payment=approval_payment,
            precheck_result="pass",
            amount_seen=Decimal("49"),
            confirmations=25,
            explorer_url="https://tronscan.org/#/transaction/test",
        )
        assert approval_payment.precheck_result == "pass"
        assert approval_payment.amount_seen == Decimal("49")

        approved_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
        approved_payment, active_subscription = repository.approve_payment(
            payment=approval_payment,
            admin_telegram_id=999,
            now_utc=approved_at,
        )
        assert approved_payment.status == "approved"
        assert approved_payment.decided_by == 999
        assert approved_payment.decided_at == approved_at
        assert active_subscription.status == "active"
        assert active_subscription.starts_at == approved_at
        assert active_subscription.ends_at == approved_at + timedelta(
            days=short_plan.duration_days
        )
        assert active_subscription.reminded_24h is False

        rejected_at = datetime(2026, 1, 2, 12, tzinfo=UTC)
        rejected_payment = repository.reject_payment(
            payment=rejection_payment,
            admin_telegram_id=999,
            reason="wrong wallet",
            now_utc=rejected_at,
        )
        assert rejected_payment.status == "rejected"
        assert rejected_payment.decided_at == rejected_at
        assert pending_rejection.status == "rejected"

        rejected_signal = _signal(status="rejected", notes=None)
        alerted_signal = _signal(status="accepted", notes={"alert": True})
        clean_signal = _signal(status="accepted", notes={"alert": False})
        session.add_all((rejected_signal, alerted_signal, clean_signal))
        session.flush()

        anomalies = repository.list_signal_anomalies()
        assert rejected_signal in anomalies
        assert alerted_signal in anomalies
        assert clean_signal not in anomalies

        all_payments = repository.list_payments()
        assert approval_payment in all_payments
        assert rejection_payment in all_payments

        summaries = repository.list_user_summaries()
        summary = next(item for item in summaries if item.user.id == user.id)
        assert summary.credential_valid is True
        assert summary.risk_model == 2
        assert summary.demo_balance_usdt == Decimal("1004.20")
        assert summary.demo_wins == 1

        closed_trades = repository.list_trades(history=True, user_id=user.id)
        assert len(closed_trades) == 1
        assert closed_trades[0].symbol == "BTCUSDT"

        repository.set_user_blocked(user=user, blocked=True)
        assert user.is_blocked is True

        metrics = repository.get_overview_metrics(
            now_utc=datetime(2026, 1, 1, 23, tzinfo=UTC)
        )
        assert metrics.pending_payments == 0
        assert metrics.realized_pnl_today == Decimal("2.50")

        audit_actions = set(session.scalars(select(AuditLog.action)))
        assert {"payment.approve", "payment.reject"} <= audit_actions
    finally:
        if transaction.is_active:
            transaction.rollback()
        session.close()
        engine.dispose()


def _signal(*, status: str, notes: dict[str, object] | None) -> Signal:
    return Signal(
        source_msg_id=uuid4().int % 9_000_000_000_000_000,
        symbol="BTCUSDT",
        side="LONG",
        entry=Decimal("100"),
        stop_loss=Decimal("99"),
        leverage=10,
        targets_raw=["101"],
        targets_clean=["101"],
        sanitizer_notes=notes,
        status=status,
        reject_reason="test rejection" if status == "rejected" else None,
    )
