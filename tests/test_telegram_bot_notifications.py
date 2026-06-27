from __future__ import annotations

import asyncio

import pytest

from services.telegram_bot.notifications import (
    deliver_notify_event,
    deliver_notify_payload,
    parse_notify_event,
)
from shared.bus import event_to_json, make_event


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_message(self, *, chat_id: int, text: str) -> None:
        self.sent.append({"chat_id": chat_id, "text": text})


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
