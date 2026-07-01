from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import inspect
import logging
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from shared.crypto import decrypt_secret
from shared.exchange.binance import to_binance_order_side, to_binance_position_side
from shared.monitoring import build_admin_alert_payload
from shared.signal.types import SignalSide

from services.core_engine.events import (
    build_notify_user_payload,
    build_trade_closed_payload,
    build_trade_error_payload,
    build_trade_opened_payload,
    format_trade_open_message,
    format_trade_skipped_message,
)
from services.core_engine.ids import client_order_id
from services.core_engine.orders import EntryGuard, place_initial_orders
from services.core_engine.risk import ExecutionPlanError, build_execution_plan


LOGGER = logging.getLogger(__name__)
SIGNALS_STREAM = "signals"
ORDERS_STREAM = "orders"
NOTIFY_STREAM = "notify"
_GLOBAL_KILL_SWITCH_KEY = "kill_switch:global"
_USER_KILL_SWITCH_PREFIX = "kill_switch:user:"


class EventPublisher(Protocol):
    async def publish(
        self, *, stream: str, event_type: str, payload: dict[str, Any]
    ) -> object: ...


@dataclass(frozen=True)
class CoreEngineConfig:
    fixed_margin_usdt: Decimal
    risk_model: int
    model2_weights: tuple[Decimal, ...]
    model3_exit_roi_pct: Decimal
    move_sl_to_be_after_tp1: bool
    max_concurrent: int
    entry_mode: str
    entry_fill_timeout_sec: int
    entry_max_deviation_pct: Decimal
    maintenance_margin_rate: Decimal
    leverage_cap: int | None = None
    signal_lookup_retry_delays_sec: tuple[float, ...] = (0.05, 0.15, 0.4)
    admin_telegram_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class HandleSignalResult:
    status: str
    opened_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    ignored_reason: str | None = None


def config_from_app_config(app_config: object) -> CoreEngineConfig:
    """Read execution defaults from shared.config.load_config result."""
    file_config = getattr(app_config, "file")
    risk = getattr(file_config, "risk")
    execution = getattr(file_config, "execution")
    retry = getattr(file_config, "retry", None)
    env_config = getattr(app_config, "env", None)
    admin_value = getattr(env_config, "admin_telegram_ids", None)
    admin_ids = (
        tuple(int(item.strip()) for item in admin_value.split(",") if item.strip())
        if admin_value
        else ()
    )
    return CoreEngineConfig(
        fixed_margin_usdt=Decimal(risk.fixed_margin_usdt),
        risk_model=int(risk.default_model),
        model2_weights=tuple(Decimal(value) for value in risk.model2_weights),
        model3_exit_roi_pct=Decimal(risk.model3_exit_roi_pct),
        move_sl_to_be_after_tp1=bool(risk.move_sl_to_be_after_tp1),
        max_concurrent=int(risk.max_concurrent),
        entry_mode=str(execution.entry_mode),
        entry_fill_timeout_sec=int(execution.entry_fill_timeout_sec),
        entry_max_deviation_pct=Decimal(execution.entry_max_deviation_pct),
        maintenance_margin_rate=Decimal(execution.maintenance_margin_rate_default),
        signal_lookup_retry_delays_sec=tuple(
            float(delay)
            for delay in getattr(
                retry,
                "signal_lookup_delays_sec",
                (0.05, 0.15, 0.4),
            )
        ),
        admin_telegram_ids=admin_ids,
    )


async def handle_signal_created(
    *,
    event_id: str,
    payload: dict,
    repository: object,
    exchange_factory: object,
    publisher: EventPublisher,
    config: CoreEngineConfig,
    fernet_key: str,
    redis_client: object | None = None,
) -> HandleSignalResult:
    """Open real trades for all eligible users for one signal.created event."""
    signal_id = _positive_int(payload.get("signal_id"))
    if signal_id is None:
        return HandleSignalResult(status="ignored", ignored_reason="missing signal_id")
    signal = await _get_signal_with_retry(
        repository=repository,
        signal_id=signal_id,
        delays=config.signal_lookup_retry_delays_sec,
    )
    if signal is None:
        return HandleSignalResult(status="retry", ignored_reason="signal_not_found")
    if not repository.mark_event_processed(_event_uuid(event_id)):  # type: ignore[attr-defined]
        return HandleSignalResult(status="ignored", ignored_reason="duplicate event")
    if signal.status != "accepted":
        return HandleSignalResult(status="ignored", ignored_reason="signal not accepted")

    opened = 0
    skipped = 0
    errors = 0
    users = repository.list_eligible_users_for_signal()  # type: ignore[attr-defined]
    for user in users:
        user_id = int(user.id)
        user_locker = getattr(repository, "lock_user_for_execution", None)
        if callable(user_locker):
            user_locker(user_id)
        reason = await _precheck_reason(
            repository=repository,
            redis_client=redis_client,
            user=user,
            signal_id=signal_id,
            config=config,
        )
        if reason is not None:
            await _publish_skip(
                publisher=publisher, user=user, signal=signal, reason=reason
            )
            skipped += 1
            continue

        credentials = repository.get_exchange_credentials(user_id)  # type: ignore[attr-defined]
        if credentials is None:
            await _publish_skip(
                publisher=publisher,
                user=user,
                signal=signal,
                reason="missing credentials",
            )
            skipped += 1
            continue
        if not credentials.is_valid or not credentials.scope_verified:
            await _publish_skip(
                publisher=publisher,
                user=user,
                signal=signal,
                reason="credentials not valid",
            )
            skipped += 1
            continue

        trade = None
        exchange = None
        try:
            try:
                api_key = decrypt_secret(credentials.api_key_enc, fernet_key)
                api_secret = decrypt_secret(credentials.api_secret_enc, fernet_key)
            except Exception:
                await _publish_skip(
                    publisher=publisher,
                    user=user,
                    signal=signal,
                    reason="credentials not valid",
                )
                skipped += 1
                continue
            exchange = await _create_exchange(
                exchange_factory,
                api_key=api_key,
                api_secret=api_secret,
                user_id=user_id,
            )
            del api_key, api_secret
            if not await exchange.verify_credentials():
                await _publish_skip(
                    publisher=publisher,
                    user=user,
                    signal=signal,
                    reason="credentials not valid",
                )
                skipped += 1
                continue
            if not await exchange.verify_withdrawals_disabled():
                await _publish_skip(
                    publisher=publisher,
                    user=user,
                    signal=signal,
                    reason="withdrawals not disabled",
                )
                skipped += 1
                continue

            settings = repository.get_user_settings(user_id)  # type: ignore[attr-defined]
            margin = (
                Decimal(settings.fixed_margin_usdt)
                if settings is not None
                else config.fixed_margin_usdt
            )
            if await exchange.get_usdt_balance() < margin:
                await _publish_skip(
                    publisher=publisher,
                    user=user,
                    signal=signal,
                    reason="insufficient free margin",
                )
                skipped += 1
                continue
            leverage = _effective_leverage(signal.leverage, settings, config)
            risk_model = int(settings.risk_model) if settings else config.risk_model
            model3_exit_roi = (
                Decimal(settings.model3_exit_roi_pct)
                if settings
                else config.model3_exit_roi_pct
            )
            try:
                filters = await exchange.get_symbol_filters(signal.symbol)
                plan = build_execution_plan(
                    side=SignalSide(signal.side),
                    entry=Decimal(signal.entry),
                    stop_loss=Decimal(signal.stop_loss),
                    leverage=leverage,
                    targets=tuple(Decimal(value) for value in signal.targets_clean),
                    margin_usdt=margin,
                    risk_model=risk_model,
                    model2_weights=config.model2_weights,
                    model3_exit_roi_pct=model3_exit_roi,
                    filters=filters,
                    maintenance_margin_rate=config.maintenance_margin_rate,
                )
            except (ExecutionPlanError, KeyError, TypeError, ValueError):
                await _publish_skip(
                    publisher=publisher,
                    user=user,
                    signal=signal,
                    reason="exchange filters failed",
                )
                skipped += 1
                continue

            trade = repository.create_trade_from_plan(  # type: ignore[attr-defined]
                user_id=user_id,
                signal=signal,
                margin_usdt=plan.margin_usdt,
                notional_usdt=plan.notional_usdt,
                qty=plan.qty,
                leverage=plan.leverage,
                liq_price=plan.liq_price,
                legs=plan.legs,
            )
            order_result = await place_initial_orders(
                exchange=exchange,
                trade=trade,
                signal=signal,
                plan=plan,
                entry_mode=config.entry_mode,
                entry_guard=EntryGuard(
                    entry_fill_timeout_sec=config.entry_fill_timeout_sec,
                    entry_max_deviation_pct=config.entry_max_deviation_pct,
                ),
            )
            if order_result.entry_order_id:
                repository.set_trade_entry_order(  # type: ignore[attr-defined]
                    trade=trade, entry_order_id=order_result.entry_order_id
                )
            if order_result.status == "skipped":
                _mark_trade_status(repository, trade, "skipped")
                await _publish_skip(
                    publisher=publisher,
                    user=user,
                    signal=signal,
                    reason=order_result.reason or "entry guard failed",
                )
                skipped += 1
                continue
            if order_result.status == "emergency_closed":
                closed_trade = repository.close_trade(  # type: ignore[attr-defined]
                    trade=trade,
                    closed_reason="aborted_no_protection",
                    realized_pnl_usdt=Decimal("0"),
                    realized_roi_pct=Decimal("0"),
                    touched_tps=(),
                )
                LOGGER.error(
                    "event_type=trade_open status=emergency_closed "
                    "trade_id=%s user_id=%s symbol=%s",
                    trade.id,
                    user_id,
                    trade.symbol,
                )
                await _publish_safely(
                    publisher=publisher,
                    stream=ORDERS_STREAM,
                    event_type="trade.closed",
                    payload=build_trade_closed_payload(closed_trade),
                )
                await _publish_safely(
                    publisher=publisher,
                    stream=NOTIFY_STREAM,
                    event_type="notify.user",
                    payload=build_notify_user_payload(
                        user=user,
                        text=(
                            f"{trade.symbol} trade aborted for safety: "
                            "no stop-loss could be confirmed."
                        ),
                    ),
                )
                await _publish_admin_alerts(
                    publisher=publisher,
                    admin_telegram_ids=config.admin_telegram_ids,
                    text=(
                        f"Trade {trade.id} ({trade.symbol}) was emergency-closed "
                        "because no stop-loss could be confirmed."
                    ),
                )
                errors += 1
                continue
            if order_result.status == "emergency_close_failed":
                repository.mark_trade_opened(  # type: ignore[attr-defined]
                    trade=trade, sl_order_id=None
                )
                LOGGER.critical(
                    "event_type=trade_open status=emergency_close_failed "
                    "trade_id=%s user_id=%s symbol=%s",
                    trade.id,
                    user_id,
                    trade.symbol,
                )
                await _publish_safely(
                    publisher=publisher,
                    stream=ORDERS_STREAM,
                    event_type="trade.error",
                    payload=build_trade_error_payload(
                        user_id=user_id,
                        signal_id=signal.id,
                        symbol=signal.symbol,
                        reason=(
                            order_result.reason
                            or "stop_loss_unconfirmed_emergency_close_failed"
                        ),
                    ),
                )
                await _publish_admin_alerts(
                    publisher=publisher,
                    admin_telegram_ids=config.admin_telegram_ids,
                    text=(
                        f"CRITICAL: trade {trade.id} ({trade.symbol}) may be open "
                        "without a confirmed stop-loss; emergency close failed."
                    ),
                )
                errors += 1
                continue
            if order_result.status != "opened":
                await _close_after_protection_failure(exchange=exchange, trade=trade)
                _mark_trade_status(repository, trade, "error")
                await _publish_error(
                    publisher=publisher,
                    user_id=user_id,
                    signal=signal,
                    reason=order_result.reason or "exchange operation failed",
                )
                errors += 1
                continue

            repository.mark_trade_opened(  # type: ignore[attr-defined]
                trade=trade, sl_order_id=order_result.sl_order_id
            )
            legs_by_index = {leg.leg_index: leg for leg in trade.legs}
            placed_tp_ids = set(order_result.tp_order_ids)
            for plan_leg in plan.legs:
                tp_id = client_order_id(
                    trade_id=trade.id,
                    purpose="tp",
                    leg_index=plan_leg.leg_index,
                )
                if tp_id in placed_tp_ids:
                    repository.set_leg_tp_order(  # type: ignore[attr-defined]
                        leg=legs_by_index[plan_leg.leg_index], tp_order_id=tp_id
                    )
            opened += 1
            try:
                await publisher.publish(
                    stream=ORDERS_STREAM,
                    event_type="trade.opened",
                    payload=build_trade_opened_payload(trade),
                )
                await publisher.publish(
                    stream=NOTIFY_STREAM,
                    event_type="notify.user",
                    payload=build_notify_user_payload(
                        user=user, text=format_trade_open_message(trade)
                    ),
                )
            except Exception:
                errors += 1
        except Exception:
            if trade is not None:
                if exchange is not None and trade.status != "open":
                    await _close_after_protection_failure(
                        exchange=exchange, trade=trade
                    )
                _mark_trade_status(repository, trade, "error")
            await _publish_error(
                publisher=publisher,
                user_id=user_id,
                signal=signal,
                reason="exchange operation failed",
            )
            errors += 1

    if not users:
        return HandleSignalResult(status="ignored", ignored_reason="no eligible users")
    return HandleSignalResult(
        status="processed",
        opened_count=opened,
        skipped_count=skipped,
        error_count=errors,
    )


async def _get_signal_with_retry(
    *, repository: object, signal_id: int, delays: tuple[float, ...]
) -> Any | None:
    signal = repository.get_signal(signal_id)  # type: ignore[attr-defined]
    for delay in delays:
        if signal is not None:
            break
        await asyncio.sleep(delay)
        signal = repository.get_signal(signal_id)  # type: ignore[attr-defined]
    return signal


async def _precheck_reason(
    *, repository: object, redis_client: object | None, user: Any, signal_id: int,
    config: CoreEngineConfig
) -> str | None:
    if await _kill_switch_enabled(redis_client, _GLOBAL_KILL_SWITCH_KEY):
        return "kill switch enabled"
    if await _kill_switch_enabled(
        redis_client, f"{_USER_KILL_SWITCH_PREFIX}{user.id}"
    ):
        return "kill switch enabled"
    if bool(user.is_blocked):
        return "blocked user"
    if repository.get_active_subscription(user.id) is None:  # type: ignore[attr-defined]
        return "inactive subscription"
    if repository.has_open_trade_for_signal(  # type: ignore[attr-defined]
        user_id=user.id, signal_id=signal_id
    ):
        return "duplicate open trade"
    settings = repository.get_user_settings(user.id)  # type: ignore[attr-defined]
    maximum = int(settings.max_concurrent) if settings else config.max_concurrent
    if repository.count_open_trades(user.id) >= maximum:  # type: ignore[attr-defined]
        return "max concurrent reached"
    return None


async def _publish_skip(
    *, publisher: EventPublisher, user: Any, signal: Any, reason: str
) -> None:
    await _publish_error(
        publisher=publisher,
        user_id=user.id,
        signal=signal,
        reason=reason,
    )
    await publisher.publish(
        stream=NOTIFY_STREAM,
        event_type="notify.user",
        payload=build_notify_user_payload(
            user=user,
            text=format_trade_skipped_message(symbol=signal.symbol, reason=reason),
        ),
    )


async def _publish_error(
    *, publisher: EventPublisher, user_id: int, signal: Any, reason: str
) -> None:
    await publisher.publish(
        stream=ORDERS_STREAM,
        event_type="trade.error",
        payload=build_trade_error_payload(
            user_id=user_id,
            signal_id=signal.id,
            symbol=signal.symbol,
            reason=reason,
        ),
    )


async def _publish_safely(
    *,
    publisher: EventPublisher,
    stream: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    try:
        await publisher.publish(
            stream=stream,
            event_type=event_type,
            payload=payload,
        )
    except Exception:
        LOGGER.error("event_type=%s status=publish_failed", event_type)


async def _publish_admin_alerts(
    *,
    publisher: EventPublisher,
    admin_telegram_ids: tuple[int, ...],
    text: str,
) -> None:
    for admin_id in admin_telegram_ids:
        await _publish_safely(
            publisher=publisher,
            stream=NOTIFY_STREAM,
            event_type="notify.admin",
            payload=build_admin_alert_payload(
                admin_telegram_id=admin_id,
                text=text,
                severity="critical",
            ),
        )


async def _create_exchange(
    factory: object, *, api_key: str, api_secret: str, user_id: int
) -> Any:
    creator = getattr(factory, "create", None)
    if callable(creator):
        parameters = inspect.signature(creator).parameters
        values: dict[str, Any] = {"api_key": api_key, "api_secret": api_secret}
        if "user_id" in parameters:
            values["user_id"] = user_id
        candidate = creator(**values)
    elif callable(factory):
        parameters = inspect.signature(factory).parameters
        values = {"api_key": api_key, "api_secret": api_secret}
        if "user_id" in parameters:
            values["user_id"] = user_id
        candidate = factory(**values)
    else:
        raise TypeError("exchange_factory is not callable")
    if inspect.isawaitable(candidate):
        candidate = await candidate
    return candidate


async def _kill_switch_enabled(redis_client: object | None, key: str) -> bool:
    if redis_client is None:
        return False
    value = redis_client.get(key)  # type: ignore[attr-defined]
    if inspect.isawaitable(value):
        value = await value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    return str(value).lower() in {"1", "true", "on", "yes"}


def _effective_leverage(signal_leverage: int, settings: Any, config: CoreEngineConfig) -> int:
    cap = config.leverage_cap
    if settings is not None and settings.leverage_mode == "cap":
        cap = settings.leverage_cap
    return min(signal_leverage, int(cap)) if cap is not None else signal_leverage


def _event_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError):
        return uuid5(NAMESPACE_URL, str(value))


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return None
    return converted if converted > 0 else None


def _mark_trade_status(repository: object, trade: Any, status: str) -> None:
    marker = getattr(repository, "mark_trade_status", None)
    if callable(marker):
        marker(trade=trade, status=status)
    else:
        trade.status = status


async def _close_after_protection_failure(*, exchange: Any, trade: Any) -> None:
    try:
        await exchange.close_position_market(
            symbol=trade.symbol,
            side=to_binance_order_side(trade_side=trade.side, action="close"),
            position_side=to_binance_position_side(trade_side=trade.side),
            qty=None,
            client_order_id=client_order_id(trade_id=trade.id, purpose="close"),
        )
        await exchange.cancel_open_orders(symbol=trade.symbol)
    except Exception:
        LOGGER.critical(
            "event_type=protection_failure_close status=failed "
            "trade_id=%s symbol=%s",
            trade.id,
            trade.symbol,
        )
        return
