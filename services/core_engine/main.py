from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC
import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import redis.asyncio as redis

from services.core_engine.engine import (
    SIGNALS_STREAM,
    config_from_app_config,
    handle_signal_created,
)
from services.core_engine.events import (
    NOTIFY_STREAM,
    ORDERS_STREAM,
    build_notify_user_payload,
    build_trade_closed_payload,
    build_trade_leg_filled_payload,
    format_trade_closed_message,
)
from services.core_engine.lifecycle import (
    LifecycleResult,
    handle_mark_price_for_model3,
    handle_user_stream_event,
)
from services.core_engine.reconciliation import reconcile_open_trades
from services.core_engine.repository import (
    CoreRepository,
    make_engine_from_config,
    make_session_factory,
    session_scope,
)
from shared.bus import RedisStreamPublisher, event_from_json
from shared.config import load_config
from shared.crypto import decrypt_secret
from shared.exchange.binance import BinanceFuturesClient


LOGGER = logging.getLogger(__name__)
_SUPERVISOR_POLL_SECONDS = 5


class _RuntimeExchangeFactory:
    def __init__(self, fernet_key: str) -> None:
        self._fernet_key = fernet_key

    def create(
        self, *, api_key: str, api_secret: str, user_id: int | None = None
    ) -> BinanceFuturesClient:
        del user_id
        return BinanceFuturesClient(api_key=api_key, api_secret=api_secret)

    def create_for_credential(
        self, *, credential: Any, user_id: int
    ) -> BinanceFuturesClient:
        del user_id
        if credential is None or not credential.is_valid or not credential.scope_verified:
            raise ValueError("valid exchange credentials are required")
        return BinanceFuturesClient(
            api_key=decrypt_secret(credential.api_key_enc, self._fernet_key),
            api_secret=decrypt_secret(credential.api_secret_enc, self._fernet_key),
        )


async def run() -> None:
    """Run signal, exchange-stream, mark-price, and reconciliation workers."""
    app_config = load_config()
    if not app_config.env.database_url:
        raise RuntimeError("Missing required setting: DATABASE_URL")
    if not app_config.env.redis_url:
        raise RuntimeError("Missing required setting: REDIS_URL")
    if app_config.env.fernet_key is None:
        raise RuntimeError("Missing required setting: FERNET_KEY")

    fernet_key = app_config.env.fernet_key.get_secret_value()
    redis_client = redis.from_url(app_config.env.redis_url, decode_responses=True)
    publisher = RedisStreamPublisher(redis_client)
    database_engine = make_engine_from_config()
    session_factory = make_session_factory(database_engine)
    engine_config = config_from_app_config(app_config)
    exchange_factory = _RuntimeExchangeFactory(fernet_key)
    scheduler = AsyncIOScheduler(timezone=UTC)

    async def reconcile_job() -> None:
        try:
            with session_scope(session_factory) as session:
                result = await reconcile_open_trades(
                    repository=CoreRepository(session),
                    exchange_factory=exchange_factory,
                )
            LOGGER.info(
                "event_type=reconcile checked=%s repaired=%s errors=%s",
                result.checked_trades,
                result.repaired_orders,
                len(result.errors),
            )
        except Exception:
            LOGGER.error("event_type=reconcile status=failed")

    await reconcile_job()
    scheduler.add_job(
        reconcile_job,
        "interval",
        minutes=1,
        id="core_reconcile",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    try:
        await asyncio.gather(
            _consume_signals(
                redis_client=redis_client,
                session_factory=session_factory,
                publisher=publisher,
                config=engine_config,
                exchange_factory=exchange_factory,
                fernet_key=fernet_key,
            ),
            _supervise_user_streams(
                session_factory=session_factory,
                publisher=publisher,
                config=engine_config,
                fernet_key=fernet_key,
            ),
            _monitor_model3_prices(
                session_factory=session_factory,
                publisher=publisher,
                config=engine_config,
                exchange_factory=exchange_factory,
            ),
        )
    finally:
        scheduler.shutdown(wait=False)
        await redis_client.aclose()
        database_engine.dispose()


async def _consume_signals(
    *,
    redis_client: Any,
    session_factory: Any,
    publisher: RedisStreamPublisher,
    config: Any,
    exchange_factory: Any,
    fernet_key: str,
) -> None:
    last_id = "$"
    while True:
        records = await redis_client.xread(
            {SIGNALS_STREAM: last_id}, count=100, block=5000
        )
        for _stream, messages in records:
            for message_id, fields in messages:
                last_id = message_id
                raw_event = fields.get("event")
                if not isinstance(raw_event, str):
                    continue
                try:
                    event = event_from_json(raw_event)
                except ValueError:
                    continue
                if event.type != "signal.created":
                    continue
                try:
                    with session_scope(session_factory) as session:
                        result = await handle_signal_created(
                            event_id=event.event_id,
                            payload=event.payload,
                            repository=CoreRepository(session),
                            exchange_factory=exchange_factory,
                            publisher=publisher,
                            config=config,
                            fernet_key=fernet_key,
                            redis_client=redis_client,
                        )
                    LOGGER.info(
                        "event_type=signal.created signal_id=%s "
                        "opened=%s skipped=%s errors=%s",
                        event.payload.get("signal_id"),
                        result.opened_count,
                        result.skipped_count,
                        result.error_count,
                    )
                except Exception:
                    LOGGER.error(
                        "event_type=signal.created signal_id=%s status=failed",
                        event.payload.get("signal_id"),
                    )


async def _supervise_user_streams(
    *,
    session_factory: Any,
    publisher: RedisStreamPublisher,
    config: Any,
    fernet_key: str,
) -> None:
    tasks: dict[int, asyncio.Task[None]] = {}
    while True:
        for user_id, api_key, api_secret in _active_credentials(
            session_factory, fernet_key
        ):
            task = tasks.get(user_id)
            if task is not None and not task.done():
                continue
            tasks[user_id] = asyncio.create_task(
                _consume_user_stream(
                    user_id=user_id,
                    api_key=api_key,
                    api_secret=api_secret,
                    session_factory=session_factory,
                    publisher=publisher,
                    config=config,
                )
            )
        await asyncio.sleep(_SUPERVISOR_POLL_SECONDS)


def _active_credentials(
    session_factory: Any, fernet_key: str
) -> Iterator[tuple[int, str, str]]:
    with session_scope(session_factory) as session:
        repository = CoreRepository(session)
        users = {user.id: user for user in repository.list_eligible_users_for_signal()}
        for trade in repository.list_open_trades():
            if trade.user_id is not None and trade.user_id not in users:
                user = repository.get_user(trade.user_id)
                if user is not None:
                    users[user.id] = user
        for user in users.values():
            credential = repository.get_exchange_credentials(user.id)
            if credential is None or not credential.is_valid or not credential.scope_verified:
                continue
            try:
                yield (
                    user.id,
                    decrypt_secret(credential.api_key_enc, fernet_key),
                    decrypt_secret(credential.api_secret_enc, fernet_key),
                )
            except Exception:
                continue


async def _consume_user_stream(
    *,
    user_id: int,
    api_key: str,
    api_secret: str,
    session_factory: Any,
    publisher: RedisStreamPublisher,
    config: Any,
) -> None:
    exchange = BinanceFuturesClient(api_key=api_key, api_secret=api_secret)
    del api_key, api_secret
    try:
        async for event in exchange.user_stream():
            with session_scope(session_factory) as session:
                repository = CoreRepository(session)
                result = await handle_user_stream_event(
                    event=event,
                    repository=repository,
                    exchange=exchange,
                    move_sl_to_be_after_tp1=config.move_sl_to_be_after_tp1,
                )
                await _publish_lifecycle_result(
                    result=result,
                    repository=repository,
                    publisher=publisher,
                )
    except Exception:
        LOGGER.error("event_type=user_stream user_id=%s status=disconnected", user_id)


async def _monitor_model3_prices(
    *, session_factory: Any, publisher: RedisStreamPublisher, config: Any,
    exchange_factory: _RuntimeExchangeFactory
) -> None:
    public_exchange = BinanceFuturesClient(api_key="", api_secret="")
    while True:
        symbols = _model3_symbols(session_factory)
        if not symbols:
            await asyncio.sleep(_SUPERVISOR_POLL_SECONDS)
            continue
        try:
            async for mark_price in public_exchange.mark_price_stream(symbols):
                with session_scope(session_factory) as session:
                    repository = CoreRepository(session)
                    trades = [
                        trade
                        for trade in repository.list_open_trades()
                        if trade.status == "open"
                        and not trade.legs
                        and trade.symbol == mark_price.symbol
                    ]
                    for trade in trades:
                        exchange = exchange_factory.create_for_credential(
                            credential=repository.get_exchange_credentials(trade.user_id),
                            user_id=trade.user_id,
                        )
                        view = _TradeRepositoryView(repository, trade)
                        results = await handle_mark_price_for_model3(
                            price=mark_price,
                            repository=view,
                            exchange=exchange,
                            model3_exit_roi_pct=config.model3_exit_roi_pct,
                        )
                        for result in results:
                            await _publish_lifecycle_result(
                                result=result,
                                repository=repository,
                                publisher=publisher,
                            )
                if sorted(symbols) != sorted(_model3_symbols(session_factory)):
                    break
        except Exception:
            LOGGER.error("event_type=mark_price status=disconnected")
            await asyncio.sleep(_SUPERVISOR_POLL_SECONDS)


def _model3_symbols(session_factory: Any) -> list[str]:
    with session_scope(session_factory) as session:
        return sorted(
            {
                trade.symbol
                for trade in CoreRepository(session).list_open_trades()
                if trade.status == "open" and not trade.legs
            }
        )


async def _publish_lifecycle_result(
    *, result: LifecycleResult, repository: CoreRepository, publisher: Any
) -> None:
    if result.trade_id is None:
        return
    trade = repository.get_trade(result.trade_id)
    if trade is None:
        return
    if result.leg_index is not None:
        leg = next(
            (item for item in trade.legs if item.leg_index == result.leg_index), None
        )
        if leg is not None:
            await publisher.publish(
                stream=ORDERS_STREAM,
                event_type="trade.leg_filled",
                payload=build_trade_leg_filled_payload(trade=trade, leg=leg),
            )
    if result.status != "closed":
        return
    await publisher.publish(
        stream=ORDERS_STREAM,
        event_type="trade.closed",
        payload=build_trade_closed_payload(trade),
    )
    if trade.user_id is None:
        return
    user = repository.get_user(trade.user_id)
    if user is not None:
        await publisher.publish(
            stream=NOTIFY_STREAM,
            event_type="notify.user",
            payload=build_notify_user_payload(
                user=user, text=format_trade_closed_message(trade)
            ),
        )


class _TradeRepositoryView:
    def __init__(self, repository: CoreRepository, trade: Any) -> None:
        self._repository = repository
        self._trade = trade

    def list_open_trades(self) -> list[Any]:
        return [self._trade]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    with suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
