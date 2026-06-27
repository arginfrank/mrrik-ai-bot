from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from services.telegram_bot.constants import NOTIFY_STREAM
from services.telegram_bot.events import build_notify_user_payload
from services.telegram_bot.i18n import t


class EventPublisher(Protocol):
    async def publish(
        self,
        *,
        stream: str,
        event_type: str,
        payload: dict,
    ) -> object: ...


@dataclass(frozen=True)
class SchedulerRunResult:
    reminded_count: int = 0
    expired_count: int = 0


async def run_expiry_reminder_once(
    *,
    repository: object,
    publisher: EventPublisher,
    now_utc: datetime | None = None,
) -> SchedulerRunResult:
    """Notify users whose active subscriptions expire within 24h."""
    now = _utc_now(now_utc)
    subscriptions = repository.list_subscriptions_due_for_24h_reminder(now_utc=now)  # type: ignore[attr-defined]
    reminded_count = 0
    for subscription in subscriptions:
        if not _reminder_is_due(subscription, now):
            continue
        telegram_id, language = _subscription_recipient(subscription)
        await publisher.publish(
            stream=NOTIFY_STREAM,
            event_type="notify.user",
            payload=build_notify_user_payload(
                telegram_id=telegram_id,
                text=t("expiry_24h", language),
                lang=language,
            ),
        )
        repository.mark_subscription_reminded_24h(subscription)  # type: ignore[attr-defined]
        reminded_count += 1
    return SchedulerRunResult(reminded_count=reminded_count)


async def run_expiry_enforcement_once(
    *,
    repository: object,
    publisher: EventPublisher,
    now_utc: datetime | None = None,
) -> SchedulerRunResult:
    """Expire active subscriptions past ends_at and notify users."""
    now = _utc_now(now_utc)
    subscriptions = repository.list_subscriptions_due_for_expiry(now_utc=now)  # type: ignore[attr-defined]
    expired_count = 0
    for subscription in subscriptions:
        if not _expiry_is_due(subscription, now):
            continue
        telegram_id, language = _subscription_recipient(subscription)
        await publisher.publish(
            stream=NOTIFY_STREAM,
            event_type="notify.user",
            payload=build_notify_user_payload(
                telegram_id=telegram_id,
                text=t("subscription_expired", language),
                lang=language,
            ),
        )
        repository.mark_subscription_expired(subscription)  # type: ignore[attr-defined]
        expired_count += 1
    return SchedulerRunResult(expired_count=expired_count)


def _utc_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware")
    return value.astimezone(UTC)


def _reminder_is_due(subscription: Any, now: datetime) -> bool:
    ends_at = _aware_end(subscription)
    return (
        subscription.status == "active"
        and not subscription.reminded_24h
        and now < ends_at <= now + timedelta(hours=24)
    )


def _expiry_is_due(subscription: Any, now: datetime) -> bool:
    return subscription.status == "active" and _aware_end(subscription) <= now


def _aware_end(subscription: Any) -> datetime:
    ends_at = getattr(subscription, "ends_at", None)
    if ends_at is None or ends_at.tzinfo is None or ends_at.utcoffset() is None:
        raise ValueError("subscription ends_at must be timezone-aware")
    return ends_at.astimezone(UTC)


def _subscription_recipient(subscription: Any) -> tuple[int, str]:
    user = getattr(subscription, "user", None)
    if user is None:
        raise ValueError("subscription user relationship is required")
    return int(user.telegram_id), str(user.language or "en")
