from __future__ import annotations

from decimal import Decimal
from typing import Any

from shared.models import Payment, User


def decimal_to_string(value: Decimal | None) -> str | None:
    """Return stable Decimal string or None."""
    return None if value is None else format(value, "f")


def build_payment_submitted_payload(payment: Payment) -> dict[str, Any]:
    """Build payment.submitted payload for Redis stream `payments`."""
    if payment.id is None or payment.user_id is None or payment.plan_id is None:
        raise ValueError("payment must be flushed before building its event")
    if payment.user is None:
        raise ValueError("payment user relationship is required")
    return {
        "payment_id": payment.id,
        "user_id": payment.user_id,
        "telegram_id": payment.user.telegram_id,
        "plan_id": payment.plan_id,
        "network": payment.network,
        "txid": payment.txid,
        "amount": decimal_to_string(payment.amount_expected),
    }


def build_notify_admin_payment_submitted_payload(
    *,
    admin_telegram_id: int,
    payment: Payment,
    user: User,
) -> dict[str, Any]:
    """Build notify.admin payload for a submitted payment."""
    text = (
        "Payment submitted for later review.\n"
        f"User ID: {user.id}\n"
        f"Telegram ID: {user.telegram_id}\n"
        f"Network: {payment.network}\n"
        f"TXID: {payment.txid}\n"
        f"Expected amount: {decimal_to_string(payment.amount_expected)} USDT\n"
        "No verification was performed in M5; the M6 admin panel and precheck will "
        "handle verification later."
    )
    return {
        "telegram_id": admin_telegram_id,
        "text": text,
        "lang": "en",
    }


def build_notify_user_payload(
    *,
    telegram_id: int,
    text: str,
    lang: str = "en",
) -> dict[str, Any]:
    """Build notify.user payload."""
    return {"telegram_id": telegram_id, "text": text, "lang": lang}
