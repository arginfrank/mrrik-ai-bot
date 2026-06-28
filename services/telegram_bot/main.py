from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC
import logging
from typing import Any

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

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
NOTIFY_BACKOFF_INITIAL_SEC = 1.0
NOTIFY_BACKOFF_MAX_SEC = 30.0
NOTIFY_TIMEOUT_LOG_INTERVAL_SEC = 60.0


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
    notify_config = app_config.file.telegram_bot
    notify_task = asyncio.create_task(
        _supervise_notifications(
            redis_client=redis_client,
            bot=bot,
            group_name=notify_config.notify_group_name,
            consumer_name=notify_config.notify_consumer_name,
            read_count=notify_config.notify_read_count,
            block_ms=notify_config.notify_block_ms,
        ),
        name="notify-consumer-supervisor",
    )
    LOGGER.info("service=telegram_bot status=started")
    try:
        await dispatcher.start_polling(bot)  # type: ignore[attr-defined]
    finally:
        notify_task.cancel()
        await asyncio.gather(notify_task, return_exceptions=True)
        scheduler.shutdown(wait=False)
        await redis_client.aclose()
        await bot.session.close()
        engine.dispose()
        LOGGER.info("service=telegram_bot status=stopped")


async def _supervise_notifications(
    *,
    redis_client: Any,
    bot: Bot,
    group_name: str,
    consumer_name: str,
    read_count: int,
    block_ms: int,
) -> None:
    restart_delay = NOTIFY_BACKOFF_INITIAL_SEC
    while True:
        LOGGER.info(
            "stream=%s status=consumer_started group=%s consumer=%s",
            NOTIFY_STREAM,
            group_name,
            consumer_name,
        )
        try:
            await _consume_notifications(
                redis_client=redis_client,
                bot=bot,
                group_name=group_name,
                consumer_name=consumer_name,
                read_count=read_count,
                block_ms=block_ms,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.error(
                "stream=%s status=consumer_stopped error_type=%s "
                "restart_delay_sec=%s",
                NOTIFY_STREAM,
                type(error).__name__,
                restart_delay,
            )
            await asyncio.sleep(restart_delay)
            LOGGER.warning(
                "stream=%s status=consumer_restarting",
                NOTIFY_STREAM,
            )
            restart_delay = min(restart_delay * 2, NOTIFY_BACKOFF_MAX_SEC)


async def _consume_notifications(
    *,
    redis_client: Any,
    bot: Bot,
    group_name: str = "telegram-bot-notify",
    consumer_name: str = "telegram-bot-1",
    read_count: int = 100,
    block_ms: int = 5000,
) -> None:
    await _ensure_notify_consumer_group(redis_client=redis_client, group_name=group_name)
    await _recover_pending_notifications(
        redis_client=redis_client,
        bot=bot,
        group_name=group_name,
        consumer_name=consumer_name,
        read_count=read_count,
    )
    connection_delay = NOTIFY_BACKOFF_INITIAL_SEC
    last_timeout_log_at: float | None = None
    needs_pending_recovery = False

    while True:
        try:
            if needs_pending_recovery:
                await _recover_pending_notifications(
                    redis_client=redis_client,
                    bot=bot,
                    group_name=group_name,
                    consumer_name=consumer_name,
                    read_count=read_count,
                )
                needs_pending_recovery = False
            records = await redis_client.xreadgroup(
                groupname=group_name,
                consumername=consumer_name,
                streams={NOTIFY_STREAM: ">"},
                count=read_count,
                block=block_ms,
            )
            connection_delay = NOTIFY_BACKOFF_INITIAL_SEC
            for _stream, messages in records:
                for message_id, fields in messages:
                    await _process_notification_message(
                        redis_client=redis_client,
                        bot=bot,
                        group_name=group_name,
                        message_id=message_id,
                        fields=fields,
                    )
        except asyncio.CancelledError:
            raise
        except RedisTimeoutError:
            needs_pending_recovery = True
            now = asyncio.get_running_loop().time()
            if (
                last_timeout_log_at is None
                or now - last_timeout_log_at >= NOTIFY_TIMEOUT_LOG_INTERVAL_SEC
            ):
                LOGGER.debug(
                    "stream=%s status=waiting reason=redis_read_timeout",
                    NOTIFY_STREAM,
                )
                last_timeout_log_at = now
            continue
        except RedisConnectionError:
            needs_pending_recovery = True
            LOGGER.warning(
                "stream=%s status=connection_lost retry_delay_sec=%s",
                NOTIFY_STREAM,
                connection_delay,
            )
            await asyncio.sleep(connection_delay)
            connection_delay = min(
                connection_delay * 2,
                NOTIFY_BACKOFF_MAX_SEC,
            )
            continue
        except Exception as error:
            LOGGER.error(
                "stream=%s status=consumer_failed error_type=%s",
                NOTIFY_STREAM,
                type(error).__name__,
            )
            raise


async def _ensure_notify_consumer_group(*, redis_client: Any, group_name: str) -> None:
    try:
        # "$" intentionally starts a newly created group after pre-group history.
        await redis_client.xgroup_create(
            name=NOTIFY_STREAM,
            groupname=group_name,
            id="$",
            mkstream=True,
        )
    except ResponseError as error:
        if "BUSYGROUP" not in str(error).upper():
            raise


async def _recover_pending_notifications(
    *,
    redis_client: Any,
    bot: Bot,
    group_name: str,
    consumer_name: str,
    read_count: int,
) -> None:
    while True:
        pending = await redis_client.xpending_range(
            name=NOTIFY_STREAM,
            groupname=group_name,
            min="-",
            max="+",
            count=read_count,
            consumername=consumer_name,
        )
        message_ids = [
            item.get("message_id")
            for item in pending
            if isinstance(item, Mapping) and item.get("message_id") is not None
        ]
        if not message_ids:
            return
        messages = await redis_client.xclaim(
            name=NOTIFY_STREAM,
            groupname=group_name,
            consumername=consumer_name,
            min_idle_time=0,
            message_ids=message_ids,
        )
        if not messages:
            return
        for message_id, fields in messages:
            await _process_notification_message(
                redis_client=redis_client,
                bot=bot,
                group_name=group_name,
                message_id=message_id,
                fields=fields,
            )


async def _process_notification_message(
    *,
    redis_client: Any,
    bot: Bot,
    group_name: str,
    message_id: Any,
    fields: Any,
) -> None:
    raw_event = fields.get("event") if isinstance(fields, Mapping) else None
    if not isinstance(raw_event, str):
        LOGGER.warning("stream=%s status=poison_dropped reason=invalid_fields", NOTIFY_STREAM)
        await redis_client.xack(NOTIFY_STREAM, group_name, message_id)
        return
    try:
        event = await deliver_notify_event(bot=bot, raw_event_json=raw_event)
    except asyncio.CancelledError:
        raise
    except ValueError:
        LOGGER.warning("stream=%s status=poison_dropped reason=invalid_event", NOTIFY_STREAM)
        await redis_client.xack(NOTIFY_STREAM, group_name, message_id)
        return
    except Exception as error:
        LOGGER.error(
            "stream=%s status=delivery_failed error_type=%s",
            NOTIFY_STREAM,
            type(error).__name__,
        )
        raise
    await redis_client.xack(NOTIFY_STREAM, group_name, message_id)
    LOGGER.info(
        "event_type=%s telegram_id=%s status=delivered",
        event.type,
        event.payload["telegram_id"],
    )


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
