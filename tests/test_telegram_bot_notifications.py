from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

import services.telegram_bot.main as telegram_bot_main
from services.telegram_bot.constants import NOTIFY_STREAM
from services.telegram_bot.notifications import (
    deliver_notify_event,
    deliver_notify_payload,
    parse_notify_event,
)
from shared.bus import event_to_json, make_event


class FakeBot:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.sent: list[dict[str, object]] = []
        self.error = error

    async def send_message(self, *, chat_id: int, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append({"chat_id": chat_id, "text": text})


class FakeNotifyRedis:
    def __init__(
        self,
        *,
        reads: list[Any] | None = None,
        pending: list[list[dict[str, Any]]] | None = None,
        claimed: list[tuple[str, dict[str, str]]] | None = None,
        group_exists: bool = False,
    ) -> None:
        self.reads = list(reads or [])
        self.pending = list(pending or [])
        self.claimed = list(claimed or [])
        self.group_exists = group_exists
        self.group_create_calls: list[dict[str, Any]] = []
        self.read_group_calls: list[dict[str, Any]] = []
        self.claim_calls: list[dict[str, Any]] = []
        self.acked: list[tuple[str, str, str]] = []
        self.call_order: list[str] = []

    async def xgroup_create(self, **kwargs: Any) -> bool:
        self.group_create_calls.append(kwargs)
        if self.group_exists:
            raise ResponseError("BUSYGROUP Consumer Group name already exists")
        return True

    async def xpending_range(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.call_order.append("pending")
        return self.pending.pop(0) if self.pending else []

    async def xclaim(self, **kwargs: Any) -> list[tuple[str, dict[str, str]]]:
        self.call_order.append("claim")
        self.claim_calls.append(kwargs)
        messages = list(self.claimed)
        self.claimed.clear()
        return messages

    async def xreadgroup(self, **kwargs: Any) -> Any:
        self.call_order.append("read")
        self.read_group_calls.append(kwargs)
        if not self.reads:
            raise asyncio.CancelledError
        result = self.reads.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.call_order.append("ack")
        self.acked.append((stream, group, message_id))
        return 1


def test_parse_notify_event() -> None:
    event = make_event(
        "notify.user",
        {"telegram_id": 123, "text": "Hello", "lang": "en"},
    )
    assert parse_notify_event(event_to_json(event)) == event


@pytest.mark.parametrize("event_type", ("notify.user", "notify.admin"))
def test_deliver_notify_event_calls_bot(event_type: str) -> None:
    bot = FakeBot()
    raw = event_to_json(
        make_event(
            event_type,
            {"telegram_id": 456, "text": "Review", "buttons": []},
        )
    )

    asyncio.run(deliver_notify_event(bot=bot, raw_event_json=raw))  # type: ignore[arg-type]

    assert bot.sent == [{"chat_id": 456, "text": "Review"}]


def test_deliver_payload_ignores_buttons_safely() -> None:
    bot = FakeBot()
    asyncio.run(
        deliver_notify_payload(
            bot=bot,  # type: ignore[arg-type]
            payload={"telegram_id": 789, "text": "Safe", "buttons": [{"x": 1}]},
        )
    )
    assert bot.sent == [{"chat_id": 789, "text": "Safe"}]


@pytest.mark.parametrize(
    "raw",
    (
        "not-json",
        event_to_json(make_event("other.event", {"telegram_id": 1, "text": "x"})),
        event_to_json(make_event("notify.user", {"telegram_id": 1})),
        event_to_json(make_event("notify.user", {"telegram_id": "1", "text": "x"})),
    ),
)
def test_malformed_notify_payload_raises(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_notify_event(raw)


def test_consumer_group_delivers_and_acks_without_logging_text(caplog: Any) -> None:
    raw = event_to_json(
        make_event(
            "notify.user",
            {"telegram_id": 456, "text": "sensitive-message-text"},
        )
    )
    redis_client = FakeNotifyRedis(
        reads=[[(NOTIFY_STREAM, [("1-0", {"event": raw})])]],
        group_exists=True,
    )
    bot = FakeBot()

    with caplog.at_level(logging.INFO), pytest.raises(asyncio.CancelledError):
        asyncio.run(
            telegram_bot_main._consume_notifications(
                redis_client=redis_client,
                bot=bot,  # type: ignore[arg-type]
            )
        )

    assert redis_client.group_create_calls == [
        {
            "name": NOTIFY_STREAM,
            "groupname": "telegram-bot-notify",
            "id": "$",
            "mkstream": True,
        }
    ]
    assert redis_client.read_group_calls[0] == {
        "groupname": "telegram-bot-notify",
        "consumername": "telegram-bot-1",
        "streams": {NOTIFY_STREAM: ">"},
        "count": 100,
        "block": 5000,
    }
    assert redis_client.acked == [(NOTIFY_STREAM, "telegram-bot-notify", "1-0")]
    assert bot.sent == [{"chat_id": 456, "text": "sensitive-message-text"}]
    assert "event_type=notify.user telegram_id=456 status=delivered" in caplog.text
    assert "sensitive-message-text" not in caplog.text


def test_consumer_treats_redis_timeout_as_idle_and_continues() -> None:
    raw = event_to_json(
        make_event("notify.user", {"telegram_id": 7, "text": "After timeout"})
    )
    redis_client = FakeNotifyRedis(
        reads=[
            RedisTimeoutError("read timed out"),
            [(NOTIFY_STREAM, [("2-0", {"event": raw})])],
        ]
    )
    bot = FakeBot()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            telegram_bot_main._consume_notifications(
                redis_client=redis_client,
                bot=bot,  # type: ignore[arg-type]
            )
        )

    assert bot.sent == [{"chat_id": 7, "text": "After timeout"}]
    assert redis_client.acked == [(NOTIFY_STREAM, "telegram-bot-notify", "2-0")]


def test_consumer_recovers_pending_before_reading_new_messages() -> None:
    raw = event_to_json(
        make_event("notify.user", {"telegram_id": 8, "text": "Recovered"})
    )
    redis_client = FakeNotifyRedis(
        pending=[[{"message_id": "3-0"}], []],
        claimed=[("3-0", {"event": raw})],
    )
    bot = FakeBot()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            telegram_bot_main._consume_notifications(
                redis_client=redis_client,
                bot=bot,  # type: ignore[arg-type]
            )
        )

    assert redis_client.call_order[:4] == ["pending", "claim", "ack", "pending"]
    assert redis_client.call_order[4] == "read"
    assert bot.sent == [{"chat_id": 8, "text": "Recovered"}]
    assert redis_client.acked == [(NOTIFY_STREAM, "telegram-bot-notify", "3-0")]


def test_consumer_acks_poison_but_not_transient_delivery_failure() -> None:
    poison_redis = FakeNotifyRedis(
        reads=[[(NOTIFY_STREAM, [("4-0", {"unexpected": "field"})])]]
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            telegram_bot_main._consume_notifications(
                redis_client=poison_redis,
                bot=FakeBot(),  # type: ignore[arg-type]
            )
        )
    assert poison_redis.acked == [(NOTIFY_STREAM, "telegram-bot-notify", "4-0")]

    raw = event_to_json(
        make_event("notify.user", {"telegram_id": 9, "text": "Retry me"})
    )
    failing_redis = FakeNotifyRedis(
        reads=[[(NOTIFY_STREAM, [("5-0", {"event": raw})])]]
    )
    with pytest.raises(RuntimeError, match="telegram unavailable"):
        asyncio.run(
            telegram_bot_main._consume_notifications(
                redis_client=failing_redis,
                bot=FakeBot(error=RuntimeError("telegram unavailable")),  # type: ignore[arg-type]
            )
        )
    assert failing_redis.acked == []


def test_notify_supervisor_restarts_consumer(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    delays: list[float] = []

    async def flaky_consumer(**_kwargs: Any) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("failed")
        raise asyncio.CancelledError

    async def no_wait(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(telegram_bot_main, "_consume_notifications", flaky_consumer)
    monkeypatch.setattr(telegram_bot_main.asyncio, "sleep", no_wait)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            telegram_bot_main._supervise_notifications(
                redis_client=object(),
                bot=FakeBot(),  # type: ignore[arg-type]
                group_name="telegram-bot-notify",
                consumer_name="telegram-bot-1",
                read_count=100,
                block_ms=5000,
            )
        )

    assert attempts == 2
    assert delays == [1.0]
