from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC
import logging
from typing import Any

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import redis.asyncio as redis

from services.telegram_bot.constants import NOTIFY_STREAM
from services.telegram_bot.handlers import config_from_app_config, create_router
from services.telegram_bot.notifications import deliver_notify_event
from services.telegram_bot.repository import (
    TelegramBotRepository,
    make_engine_from_config,
    make_session_factory,
    session_scope,
)
from services.telegram_bot.scheduler import (
    run_expiry_enforcement_once,
    run_expiry_reminder_once,
)
from shared.bus import RedisStreamPublisher
from shared.config import load_config


LOGGER = logging.getLogger(__name__)


def create_dispatcher(
    *,
    repository_factory: object,
    publisher: object,
    config: object,
) -> object:
    """Build a dispatcher without starting Telegram or Redis network activity."""
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_router(
            repository_factory=repository_factory,
            publisher=publisher,  # type: ignore[arg-type]
            config=config,  # type: ignore[arg-type]
        )
    )
    return dispatcher


async def run() -> None:
    """Run Telegram polling, notify delivery, and subscription expiry jobs."""
    app_config = load_config()
    token = _required_secret(
        getattr(app_config.env, "telegram_bot_token", None),
        "TELEGRAM_BOT_TOKEN",
    )
    _required_text(app_config.env.database_url, "DATABASE_URL")
    redis_url = _required_text(app_config.env.redis_url, "REDIS_URL")
    _required_secret(getattr(app_config.env, "fernet_key", None), "FERNET_KEY")

    bot = Bot(token=token)
    redis_client = redis.from_url(redis_url, decode_responses=True)
    publisher = RedisStreamPublisher(redis_client)
    engine = make_engine_from_config()
    session_factory = make_session_factory(engine)

    @contextmanager
    def repository_factory() -> Iterator[TelegramBotRepository]:
        with session_scope(session_factory) as session:
            yield TelegramBotRepository(session)

    bot_config = config_from_app_config(app_config)
    dispatcher = create_dispatcher(
        repository_factory=repository_factory,
        publisher=publisher,
        config=bot_config,
    )
    scheduler = AsyncIOScheduler(timezone=UTC)

    async def reminder_job() -> None:
        try:
            with repository_factory() as repository:
                await run_expiry_reminder_once(
                    repository=repository,
                    publisher=publisher,
                )
        except Exception:
            LOGGER.exception("job=expiry_reminder status=failed")

    async def enforcement_job() -> None:
        try:
            with repository_factory() as repository:
                await run_expiry_enforcement_once(
                    repository=repository,
                    publisher=publisher,
                )
        except Exception:
            LOGGER.exception("job=expiry_enforcement status=failed")

    scheduler.add_job(
        reminder_job,
        "interval",
        minutes=5,
        id="expiry_reminder",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        enforcement_job,
        "interval",
        minutes=1,
        id="expiry_enforcement",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    notify_task = asyncio.create_task(
        _consume_notifications(redis_client=redis_client, bot=bot),
        name="notify-consumer",
    )
    LOGGER.info("service=telegram_bot status=started")
    try:
        await dispatcher.start_polling(bot)  # type: ignore[attr-defined]
    finally:
        scheduler.shutdown(wait=False)
        notify_task.cancel()
        await asyncio.gather(notify_task, return_exceptions=True)
        await redis_client.aclose()
        await bot.session.close()
        engine.dispose()
        LOGGER.info("service=telegram_bot status=stopped")


async def _consume_notifications(*, redis_client: Any, bot: Bot) -> None:
    last_id = "$"
    while True:
        records = await redis_client.xread(
            {NOTIFY_STREAM: last_id},
            count=100,
            block=5000,
        )
        for _stream, messages in records:
            for message_id, fields in messages:
                last_id = message_id
                raw_event = fields.get("event")
                if not isinstance(raw_event, str):
                    LOGGER.warning("stream=notify status=invalid_fields")
                    continue
                try:
                    await deliver_notify_event(bot=bot, raw_event_json=raw_event)
                except ValueError:
                    LOGGER.warning("stream=notify status=invalid_event")
                except Exception:
                    LOGGER.exception("stream=notify status=delivery_failed")


def _required_secret(value: Any, name: str) -> str:
    if value is None:
        raise RuntimeError(f"Missing required setting: {name}")
    getter = getattr(value, "get_secret_value", None)
    secret = str(getter() if callable(getter) else value)
    if not secret:
        raise RuntimeError(f"Missing required setting: {name}")
    return secret


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError(f"Missing required setting: {name}")
    return text


def main() -> None:
    """Start the customer Telegram bot service."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
