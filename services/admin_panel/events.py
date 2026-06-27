from __future__ import annotations

from datetime import UTC
from decimal import Decimal
from typing import Any

from shared.models import Payment, Subscription, User


def decimal_to_string(value: Decimal | None) -> str | None:
    """Return stable Decimal string or None."""
    return None if value is None else format(value, "f")


def build_payment_approved_payload(
    *,
    payment: Payment,
    subscription: Subscription,
) -> dict[str, Any]:
    """Build payment.approved payload."""
    _require_payment_identity(payment)
    if subscription.id is None or subscription.ends_at is None:
        raise ValueError("active subscription identity and end time are required")
    return {
        "payment_id": payment.id,
        "subscription_id": subscription.id,
        "user_id": payment.user_id,
        "plan_id": payment.plan_id,
        "network": payment.network,
        "txid": payment.txid,
        "amount": decimal_to_string(payment.amount_expected),
        "ends_at": subscription.ends_at.astimezone(UTC).isoformat(),
    }


def build_payment_rejected_payload(*, payment: Payment, reason: str) -> dict[str, Any]:
    """Build payment.rejected payload."""
    _require_payment_identity(payment)
    return {
        "payment_id": payment.id,
        "user_id": payment.user_id,
        "plan_id": payment.plan_id,
        "network": payment.network,
        "txid": payment.txid,
        "amount": decimal_to_string(payment.amount_expected),
        "reason": reason,
    }


def build_notify_user_payment_approved_payload(
    *,
    user: User,
    subscription: Subscription,
) -> dict[str, Any]:
    """Build notify.user payload for approved payment."""
    if subscription.ends_at is None:
        raise ValueError("active subscription end time is required")
    ends_at = subscription.ends_at.astimezone(UTC).isoformat()
    return {
        "telegram_id": user.telegram_id,
        "text": f"Your subscription is active until {ends_at} UTC.",
        "lang": user.language,
    }


def build_notify_user_payment_rejected_payload(
    *,
    user: User,
    reason: str,
) -> dict[str, Any]:
    """Build notify.user payload for rejected payment."""
    return {
        "telegram_id": user.telegram_id,
        "text": (
            f"Payment rejected: {reason}. Service was not activated. "
            "Please resubmit a correct TXID."
        ),
        "lang": user.language,
    }


def build_notify_admin_precheck_payload(
    *,
    admin_telegram_id: int,
    payment: Payment,
    precheck_result: str,
    reason: str,
) -> dict[str, Any]:
    """Build notify.admin payload for precheck evidence."""
    _require_payment_identity(payment)
    text = (
        "Payment precheck completed.\n"
        f"Payment ID: {payment.id}\n"
        f"User ID: {payment.user_id}\n"
        f"Network: {payment.network}\n"
        f"TXID: {payment.txid}\n"
        f"Expected amount: {decimal_to_string(payment.amount_expected)} USDT\n"
        f"Amount seen: {decimal_to_string(payment.amount_seen)} USDT\n"
        f"Confirmations: {payment.confirmations}\n"
        f"Result: {precheck_result}\n"
        f"Reason: {reason}\n"
        f"Explorer: {payment.explorer_url or 'unavailable'}"
    )
    return {"telegram_id": admin_telegram_id, "text": text, "lang": "en"}


def _require_payment_identity(payment: Payment) -> None:
    if payment.id is None or payment.user_id is None or payment.plan_id is None:
        raise ValueError("payment must be flushed before building its event")
