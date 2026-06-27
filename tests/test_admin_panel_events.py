from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from services.admin_panel.events import (
    build_notify_admin_precheck_payload,
    build_notify_user_payment_approved_payload,
    build_notify_user_payment_rejected_payload,
    build_payment_approved_payload,
    build_payment_rejected_payload,
)
from shared.models import Payment, Subscription, User


def test_approved_payload_and_user_notification_include_utc_end() -> None:
    payment, subscription, user = _entities()

    payload = build_payment_approved_payload(payment=payment, subscription=subscription)
    notification = build_notify_user_payment_approved_payload(
        user=user,
        subscription=subscription,
    )

    assert payload["payment_id"] == 33
    assert payload["subscription_id"] == 55
    assert payload["user_id"] == 11
    assert payload["ends_at"] == "2026-02-01T12:00:00+00:00"
    assert "active until" in notification["text"]
    assert "2026-02-01T12:00:00+00:00 UTC" in notification["text"]


def test_rejected_payload_and_user_notification_instruct_resubmission() -> None:
    payment, _subscription, user = _entities()

    payload = build_payment_rejected_payload(payment=payment, reason="wrong wallet")
    notification = build_notify_user_payment_rejected_payload(
        user=user,
        reason="wrong wallet",
    )

    assert payload["payment_id"] == 33
    assert payload["reason"] == "wrong wallet"
    assert "resubmit a correct TXID" in notification["text"]


def test_admin_precheck_notification_contains_safe_evidence() -> None:
    payment, _subscription, _user = _entities()
    payment.amount_seen = Decimal("49")
    payment.confirmations = 20
    payment.explorer_url = "https://tronscan.org/#/transaction/tx-123"

    payload = build_notify_admin_precheck_payload(
        admin_telegram_id=99,
        payment=payment,
        precheck_result="pass",
        reason="on-chain evidence passed all checks",
    )

    assert payload["telegram_id"] == 99
    assert "Payment ID: 33" in payload["text"]
    assert "TRC20" in payload["text"]
    assert "tx-123" in payload["text"]
    assert "api_key" not in repr(payload).lower()
    assert "secret" not in repr(payload).lower()


def _entities() -> tuple[Payment, Subscription, User]:
    user = User(id=11, telegram_id=22, language="en")
    payment = Payment(
        id=33,
        user_id=11,
        plan_id=44,
        user=user,
        network="TRC20",
        to_address="wallet",
        amount_expected=Decimal("49.000000"),
        txid="tx-123",
        status="submitted",
    )
    subscription = Subscription(
        id=55,
        user_id=11,
        plan_id=44,
        status="active",
        starts_at=datetime(2026, 1, 2, 12, tzinfo=UTC),
        ends_at=datetime(2026, 2, 1, 12, tzinfo=UTC),
        reminded_24h=False,
    )
    return payment, subscription, user
