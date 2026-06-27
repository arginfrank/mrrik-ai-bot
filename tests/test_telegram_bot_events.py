from __future__ import annotations

from decimal import Decimal

from services.telegram_bot.events import (
    build_notify_admin_payment_submitted_payload,
    build_notify_user_payload,
    build_payment_submitted_payload,
)
from shared.models import Payment, User


def test_payment_submitted_payload_contains_safe_required_fields() -> None:
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

    payload = build_payment_submitted_payload(payment)

    assert payload == {
        "payment_id": 33,
        "user_id": 11,
        "telegram_id": 22,
        "plan_id": 44,
        "network": "TRC20",
        "txid": "tx-123",
        "amount": "49.000000",
    }


def test_admin_and_user_notifications_contain_no_credentials() -> None:
    user = User(id=11, telegram_id=22, language="en")
    payment = Payment(
        id=33,
        user_id=11,
        plan_id=44,
        user=user,
        network="BEP20",
        to_address="wallet",
        amount_expected=Decimal("129"),
        txid="tx-456",
        status="submitted",
    )
    admin_payload = build_notify_admin_payment_submitted_payload(
        admin_telegram_id=99,
        payment=payment,
        user=user,
    )
    user_payload = build_notify_user_payload(telegram_id=22, text="Safe text")

    assert admin_payload["telegram_id"] == 99
    assert "Telegram ID: 22" in admin_payload["text"]
    assert "M6" in admin_payload["text"]
    assert user_payload == {"telegram_id": 22, "text": "Safe text", "lang": "en"}
    assert "api_key" not in repr((admin_payload, user_payload)).lower()
    assert "api_secret" not in repr((admin_payload, user_payload)).lower()
