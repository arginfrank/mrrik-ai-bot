from __future__ import annotations

import asyncio
import logging
from typing import Any

import redis.asyncio as redis
from telethon import TelegramClient, events
from telethon.sessions import StringSession

from services.signal_ingestor.processor import process_source_message
from services.signal_ingestor.repository import (
    SignalRepository,
    make_engine_from_config,
    make_session_factory,
    session_scope,
)
from shared.bus import RedisStreamPublisher
from shared.config import load_config


LOGGER = logging.getLogger(__name__)


async def run() -> None:
    """Run the Telethon signal ingestion service until disconnected."""
    settings = load_config().env
    if settings.tg_api_id is None:
        raise RuntimeError("Missing required setting: TG_API_ID")
    if settings.tg_api_hash is None:
        raise RuntimeError("Missing required setting: TG_API_HASH")
    if settings.tg_userbot_session is None:
        raise RuntimeError("Missing required setting: TG_USERBOT_SESSION")
    if settings.source_channel_id is None:
        raise RuntimeError("Missing required setting: SOURCE_CHANNEL_ID")
    if not settings.redis_url:
        raise RuntimeError("Missing required setting: REDIS_URL")
    if not settings.database_url:
        raise RuntimeError("Missing required setting: DATABASE_URL")

    client = TelegramClient(
        StringSession(settings.tg_userbot_session.get_secret_value()),
        settings.tg_api_id,
        settings.tg_api_hash.get_secret_value(),
    )
    redis_client = redis.from_url(settings.redis_url)
    publisher = RedisStreamPublisher(redis_client)
    engine = make_engine_from_config()
    session_factory = make_session_factory(engine)

    async def handle_message(event: Any) -> None:
        text = event.raw_text
        if not text:
            return

        message_id = event.id
        try:
            with session_scope(session_factory) as session:
                result = await process_source_message(
                    text=text,
                    source_msg_id=message_id,
                    repository=SignalRepository(session),
                    publisher=publisher,
                )
        except Exception:
            LOGGER.error("message_id=%s status=failed", message_id)
            return

        LOGGER.info(
            "message_id=%s status=%s signal_id=%s event_type=%s",
            message_id,
            result.status,
            result.signal_id,
            result.event_type,
        )

    client.add_event_handler(
        handle_message,
        events.NewMessage(chats=settings.source_channel_id),
    )

    try:
        await client.start()
        LOGGER.info("signal_ingestor status=connected")
        await client.run_until_disconnected()
    finally:
        await client.disconnect()
        await redis_client.aclose()
        engine.dispose()


def main() -> None:
    """Start the signal ingestor service."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
