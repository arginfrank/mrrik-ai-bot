from __future__ import annotations

from typing import Any

from aiogram import Bot

from shared.bus import EventEnvelope, event_from_json


_SUPPORTED_NOTIFY_EVENTS = {"notify.user", "notify.admin"}


def parse_notify_event(raw_event_json: str) -> EventEnvelope:
    """Parse one notify stream event."""
    try:
        event = event_from_json(raw_event_json)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid notify event envelope") from error
    if event.type not in _SUPPORTED_NOTIFY_EVENTS:
        raise ValueError(f"unsupported notify event type: {event.type}")
    _validated_delivery_values(event.payload)
    return event


async def deliver_notify_payload(*, bot: Bot, payload: dict[str, Any]) -> None:
    """Deliver one notify.user or notify.admin payload to Telegram."""
    telegram_id, text = _validated_delivery_values(payload)
    await bot.send_message(chat_id=telegram_id, text=text)


async def deliver_notify_event(*, bot: Bot, raw_event_json: str) -> EventEnvelope:
    """Parse and deliver one notify event."""
    event = parse_notify_event(raw_event_json)
    await deliver_notify_payload(bot=bot, payload=event.payload)
    return event


def _validated_delivery_values(payload: dict[str, Any]) -> tuple[int, str]:
    telegram_id = payload.get("telegram_id")
    text = payload.get("text")
    if isinstance(telegram_id, bool) or not isinstance(telegram_id, int):
        raise ValueError("notify payload telegram_id must be an integer")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("notify payload text must be a non-empty string")
    return telegram_id, text
