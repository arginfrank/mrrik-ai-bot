from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any

import redis.asyncio as redis
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from services.demo_engine.engine import (
    SIGNALS_STREAM,
    config_from_app_config,
    handle_mark_price,
    handle_signal_created,
)
from services.demo_engine.prices import stream_mark_prices
from services.demo_engine.repository import (
    DemoRepository,
    make_engine_from_config,
    make_session_factory,
    session_scope,
)
from shared.bus import RedisStreamPublisher, event_from_json
from shared.config import load_config


LOGGER = logging.getLogger(__name__)
SYMBOL_POLL_INTERVAL_SEC = 3
REDIS_TIMEOUT_RETRY_DELAY_SEC = 0.1
SIGNALS_GROUP_NAME = "demo-engine-signals"
SIGNALS_CONSUMER_NAME = "demo-engine-1"


async def run() -> None:
    """Run signal consumption and public mark-price tracking."""
    app_config = load_config()
    if not app_config.env.redis_url:
        raise RuntimeError("Missing required setting: REDIS_URL")
    if not app_config.env.database_url:
        raise RuntimeError("Missing required setting: DATABASE_URL")

    redis_client = redis.from_url(app_config.env.redis_url, decode_responses=True)
    publisher = RedisStreamPublisher(redis_client)
    engine = make_engine_from_config()
    session_factory = make_session_factory(engine)
    demo_config = config_from_app_config(app_config)
    try:
        await asyncio.gather(
            _consume_signals(
                redis_client=redis_client,
                session_factory=session_factory,
                publisher=publisher,
                config=demo_config,
            ),
            _track_prices(
                session_factory=session_factory,
                publisher=publisher,
                config=demo_config,
            ),
        )
    finally:
        await redis_client.aclose()
        engine.dispose()


async def _consume_signals(
    *,
    redis_client: Any,
    session_factory: Any,
    publisher: RedisStreamPublisher,
    config: Any,
    group_name: str = SIGNALS_GROUP_NAME,
    consumer_name: str = SIGNALS_CONSUMER_NAME,
) -> None:
    await _ensure_signal_consumer_group(
        redis_client=redis_client,
        group_name=group_name,
    )
    read_pending = True
    while True:
        try:
            records, read_pending = await _read_signal_records(
                redis_client=redis_client,
                group_name=group_name,
                consumer_name=consumer_name,
                read_pending=read_pending,
            )
        except asyncio.CancelledError:
            raise
        except RedisTimeoutError:
            LOGGER.debug(
                "event_type=signal.created status=waiting reason=redis_read_timeout"
            )
            await asyncio.sleep(REDIS_TIMEOUT_RETRY_DELAY_SEC)
            continue
        except Exception:
            LOGGER.exception(
                "event_type=signal.created status=read_failed stream=%s",
                SIGNALS_STREAM,
            )
            raise
        for _stream, messages in records:
            for message_id, fields in messages:
                raw_event = fields.get("event") if isinstance(fields, Mapping) else None
                if not isinstance(raw_event, str):
                    await redis_client.xack(SIGNALS_STREAM, group_name, message_id)
                    continue
                try:
                    event = event_from_json(raw_event)
                except ValueError:
                    await redis_client.xack(SIGNALS_STREAM, group_name, message_id)
                    continue
                if event.type != "signal.created":
                    await redis_client.xack(SIGNALS_STREAM, group_name, message_id)
                    continue
                try:
                    with session_scope(session_factory) as session:
                        result = await handle_signal_created(
                            payload=event.payload,
                            repository=DemoRepository(session),
                            publisher=publisher,
                            config=config,
                        )
                except Exception:
                    LOGGER.error(
                        "event_type=signal.created signal_id=%s status=failed",
                        event.payload.get("signal_id"),
                    )
                    continue
                if result.status == "retry" and result.ignored_reason == "signal_not_found":
                    LOGGER.warning(
                        "event_type=signal.created signal_id=%s status=retry "
                        "reason=signal_not_found acknowledged=false",
                        event.payload.get("signal_id"),
                    )
                    continue
                await redis_client.xack(SIGNALS_STREAM, group_name, message_id)
                LOGGER.info(
                    "event_type=signal.created signal_id=%s status=%s "
                    "opened_count=%s ignored_reason=%s",
                    event.payload.get("signal_id"),
                    result.status,
                    result.opened_count,
                    result.ignored_reason or "-",
                )


async def _ensure_signal_consumer_group(
    *, redis_client: Any, group_name: str
) -> None:
    try:
        await redis_client.xgroup_create(
            name=SIGNALS_STREAM,
            groupname=group_name,
            id="$",
            mkstream=True,
        )
    except ResponseError as error:
        if "BUSYGROUP" not in str(error).upper():
            raise


async def _read_signal_records(
    *,
    redis_client: Any,
    group_name: str,
    consumer_name: str,
    read_pending: bool,
) -> tuple[Any, bool]:
    if read_pending:
        pending = await redis_client.xreadgroup(
            groupname=group_name,
            consumername=consumer_name,
            streams={SIGNALS_STREAM: "0"},
            count=100,
        )
        if pending:
            return pending, False
    fresh = await redis_client.xreadgroup(
        groupname=group_name,
        consumername=consumer_name,
        streams={SIGNALS_STREAM: ">"},
        count=100,
        block=5000,
    )
    return fresh, True


async def _track_prices(
    *,
    session_factory: Any,
    publisher: RedisStreamPublisher,
    config: Any,
) -> None:
    while True:
        symbols = _open_symbols(session_factory)
        if not symbols:
            await asyncio.sleep(SYMBOL_POLL_INTERVAL_SEC)
            continue
        subscribed = tuple(symbols)
        loop = asyncio.get_running_loop()
        next_poll = loop.time() + SYMBOL_POLL_INTERVAL_SEC
        try:
            async for mark_price in stream_mark_prices(subscribed):
                try:
                    with session_scope(session_factory) as session:
                        result = await handle_mark_price(
                            symbol=mark_price.symbol,
                            price=mark_price.price,
                            repository=DemoRepository(session),
                            publisher=publisher,
                            config=config,
                        )
                except Exception:
                    LOGGER.error(
                        "event_type=mark_price symbol=%s status=failed",
                        mark_price.symbol,
                    )
                    continue
                LOGGER.info(
                    "event_type=mark_price symbol=%s status=%s "
                    "closed_count=%s ignored_reason=%s",
                    mark_price.symbol,
                    result.status,
                    result.closed_count,
                    result.ignored_reason or "-",
                )
                if loop.time() >= next_poll:
                    if tuple(_open_symbols(session_factory)) != subscribed:
                        break
                    next_poll = loop.time() + SYMBOL_POLL_INTERVAL_SEC
        except Exception:
            LOGGER.error("event_type=mark_price status=disconnected")
            await asyncio.sleep(SYMBOL_POLL_INTERVAL_SEC)


def _open_symbols(session_factory: Any) -> list[str]:
    with session_scope(session_factory) as session:
        return DemoRepository(session).list_open_demo_symbols()


def main() -> None:
    """Start the demo engine service."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
