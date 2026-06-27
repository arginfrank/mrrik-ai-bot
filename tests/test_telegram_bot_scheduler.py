from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from services.telegram_bot.scheduler import (
    run_expiry_enforcement_once,
    run_expiry_reminder_once,
)


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def publish(
        self,
        *,
        stream: str,
        event_type: str,
        payload: dict,
    ) -> object:
        event = {"stream": stream, "event_type": event_type, "payload": payload}
        self.events.append(event)
        return event


class FakeRepository:
    def __init__(self, subscriptions: list[object]) -> None:
        self.subscriptions = subscriptions
        self.reminded: list[object] = []
        self.expired: list[object] = []

    def list_subscriptions_due_for_24h_reminder(self, *, now_utc: datetime) -> list[object]:
        return list(self.subscriptions)

    def mark_subscription_reminded_24h(self, subscription: object) -> None:
        subscription.reminded_24h = True
        self.reminded.append(subscription)

    def list_subscriptions_due_for_expiry(self, *, now_utc: datetime) -> list[object]:
        return list(self.subscriptions)

    def mark_subscription_expired(self, subscription: object) -> None:
        subscription.status = "expired"
        self.expired.append(subscription)


def test_reminder_publishes_and_marks_only_due_subscription() -> None:
    now = datetime(2026, 6, 27, 12, tzinfo=UTC)
    due = _subscription(ends_at=now + timedelta(hours=12))
    already_reminded = _subscription(
        ends_at=now + timedelta(hours=10),
        reminded_24h=True,
    )
    repository = FakeRepository([due, already_reminded])
    publisher = FakePublisher()

    result = asyncio.run(
        run_expiry_reminder_once(
            repository=repository,
            publisher=publisher,
            now_utc=now,
        )
    )

    assert result.reminded_count == 1
    assert repository.reminded == [due]
    assert len(publisher.events) == 1
    assert publisher.events[0]["event_type"] == "notify.user"
    assert "no new trades" in str(publisher.events[0]["payload"]).lower()


def test_expiry_publishes_and_marks_only_past_subscription() -> None:
    now = datetime(2026, 6, 27, 12, tzinfo=UTC)
    past = _subscription(ends_at=now - timedelta(seconds=1))
    future = _subscription(ends_at=now + timedelta(seconds=1))
    repository = FakeRepository([past, future])
    publisher = FakePublisher()

    result = asyncio.run(
        run_expiry_enforcement_once(
            repository=repository,
            publisher=publisher,
            now_utc=now,
        )
    )

    assert result.expired_count == 1
    assert repository.expired == [past]
    assert past.status == "expired"
    assert future.status == "active"
    assert len(publisher.events) == 1
    assert "SL/TP" in str(publisher.events[0]["payload"])


def test_scheduler_rejects_naive_now() -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            run_expiry_reminder_once(
                repository=FakeRepository([]),
                publisher=FakePublisher(),
                now_utc=datetime(2026, 6, 27, 12),
            )
        )


def _subscription(
    *,
    ends_at: datetime,
    reminded_24h: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        status="active",
        ends_at=ends_at,
        reminded_24h=reminded_24h,
        user=SimpleNamespace(telegram_id=123, language="en"),
    )
