from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import logging
from typing import Any, Protocol

from aiogram import F, Router
from aiogram.filters import Command, CommandStart

from services.demo_engine.stats import compute_demo_stats, format_demo_stats
from services.telegram_bot.constants import (
    CALLBACK_DEMO_RESET,
    CALLBACK_DEMO_RESET_CANCEL,
    CALLBACK_DEMO_RESET_CONFIRM,
    CALLBACK_LANGUAGE_PREFIX,
    CALLBACK_MAIN_PREFIX,
    CALLBACK_NETWORK_PREFIX,
    CALLBACK_PLAN_PREFIX,
    CALLBACK_SETTINGS_PREFIX,
    NETWORK_BEP20,
    NETWORK_POLYGON,
    NETWORK_TRC20,
    NOTIFY_STREAM,
    PAYMENTS_STREAM,
    SUPPORTED_PAYMENT_NETWORKS,
)
from services.telegram_bot.credentials import (
    ApiCredentialFormatError,
    encrypt_api_credentials,
    parse_api_credentials,
)
from services.telegram_bot.events import (
    build_notify_admin_payment_submitted_payload,
    build_payment_submitted_payload,
)
from services.telegram_bot.i18n import normalize_language, t
from services.telegram_bot.keyboards import (
    back_to_main_keyboard,
    demo_reset_confirmation_keyboard,
    demo_stats_keyboard,
    language_keyboard,
    main_menu_keyboard,
    networks_keyboard,
    plans_keyboard,
    settings_keyboard,
    wallet_keyboard,
)
from services.telegram_bot.states import (
    ApiCredentialStates,
    SettingsStates,
    SubscribeStates,
)


LOGGER = logging.getLogger(__name__)


class EventPublisher(Protocol):
    async def publish(
        self,
        *,
        stream: str,
        event_type: str,
        payload: dict,
    ) -> object: ...


@dataclass(frozen=True)
class TelegramBotConfig:
    start_balance_usdt: Decimal
    wallet_trc20: str
    wallet_bep20: str
    wallet_polygon: str
    fernet_key: str
    admin_telegram_ids: tuple[int, ...]


def config_from_app_config(app_config: object) -> TelegramBotConfig:
    """Read bot defaults and secrets from existing shared.config.load_config result."""
    file_config = getattr(app_config, "file")
    env_config = getattr(app_config, "env")
    admin_value = getattr(env_config, "admin_telegram_ids", None)
    admin_ids = (
        tuple(int(item.strip()) for item in admin_value.split(",") if item.strip())
        if admin_value
        else ()
    )
    return TelegramBotConfig(
        start_balance_usdt=Decimal(getattr(file_config.demo, "start_balance_usdt")),
        wallet_trc20=_secret_value(getattr(env_config, "wallet_trc20", None)),
        wallet_bep20=_secret_value(getattr(env_config, "wallet_bep20", None)),
        wallet_polygon=_secret_value(getattr(env_config, "wallet_polygon", None)),
        fernet_key=_secret_value(getattr(env_config, "fernet_key", None)),
        admin_telegram_ids=admin_ids,
    )


def create_router(
    *,
    repository_factory: Any,
    publisher: EventPublisher,
    config: TelegramBotConfig,
) -> Router:
    """Create and register all customer bot handlers."""
    router = Router(name="telegram_bot")

    async def start(message: Any) -> None:
        await handle_start(message=message, repository_factory=repository_factory)

    async def language(callback: Any, state: Any) -> None:
        await handle_language(
            callback=callback,
            state=state,
            repository_factory=repository_factory,
        )

    async def main_menu(callback: Any, state: Any) -> None:
        await handle_main_menu(callback=callback, state=state)

    async def subscribe(callback: Any) -> None:
        await handle_subscribe(
            callback=callback,
            repository_factory=repository_factory,
        )

    async def plan(callback: Any, state: Any) -> None:
        await handle_plan(
            callback=callback,
            state=state,
            repository_factory=repository_factory,
        )

    async def network(callback: Any, state: Any) -> None:
        await handle_network(
            callback=callback,
            state=state,
            repository_factory=repository_factory,
            config=config,
        )

    async def txid(message: Any, state: Any) -> None:
        await handle_txid(
            message=message,
            state=state,
            repository_factory=repository_factory,
            publisher=publisher,
            config=config,
        )

    async def demo(event: Any) -> None:
        await handle_demo(
            event=event,
            repository_factory=repository_factory,
            config=config,
        )

    async def demo_reset(callback: Any) -> None:
        await handle_demo_reset(callback=callback)

    async def demo_reset_confirm(callback: Any) -> None:
        await handle_demo_reset_confirm(
            callback=callback,
            repository_factory=repository_factory,
            config=config,
        )

    async def demo_reset_cancel(callback: Any) -> None:
        await handle_demo_reset_cancel(callback=callback)

    async def subscription(event: Any) -> None:
        await handle_my_subscription(
            event=event,
            repository_factory=repository_factory,
        )

    async def connect_api(callback: Any, state: Any) -> None:
        await handle_connect_api(callback=callback, state=state)

    async def api_credentials(message: Any, state: Any) -> None:
        await handle_api_credentials(
            message=message,
            state=state,
            repository_factory=repository_factory,
            config=config,
        )

    async def settings(event: Any) -> None:
        await handle_settings(
            event=event,
            repository_factory=repository_factory,
        )

    async def fixed_margin_prompt(callback: Any, state: Any) -> None:
        await handle_fixed_margin_prompt(callback=callback, state=state)

    async def fixed_margin(message: Any, state: Any) -> None:
        await handle_fixed_margin(
            message=message,
            state=state,
            repository_factory=repository_factory,
        )

    async def risk_model(callback: Any) -> None:
        await handle_risk_model(
            callback=callback,
            repository_factory=repository_factory,
        )

    async def help_handler(event: Any) -> None:
        await handle_help(event=event)

    router.message.register(start, CommandStart())
    router.message.register(demo, Command("demo"))
    router.message.register(subscribe, Command("subscribe"))
    router.message.register(subscription, Command("status"))
    router.message.register(help_handler, Command("help"))
    router.callback_query.register(
        language,
        F.data.startswith(CALLBACK_LANGUAGE_PREFIX),
    )
    router.callback_query.register(
        main_menu,
        F.data == f"{CALLBACK_MAIN_PREFIX}menu",
    )
    router.callback_query.register(
        subscribe,
        F.data == f"{CALLBACK_MAIN_PREFIX}subscribe",
    )
    router.callback_query.register(
        demo,
        F.data == f"{CALLBACK_MAIN_PREFIX}demo",
    )
    router.callback_query.register(
        demo_reset,
        F.data == CALLBACK_DEMO_RESET,
    )
    router.callback_query.register(
        demo_reset_confirm,
        F.data == CALLBACK_DEMO_RESET_CONFIRM,
    )
    router.callback_query.register(
        demo_reset_cancel,
        F.data == CALLBACK_DEMO_RESET_CANCEL,
    )
    router.callback_query.register(
        subscription,
        F.data == f"{CALLBACK_MAIN_PREFIX}subscription",
    )
    router.callback_query.register(
        connect_api,
        F.data == f"{CALLBACK_MAIN_PREFIX}api",
    )
    router.callback_query.register(
        settings,
        F.data == f"{CALLBACK_MAIN_PREFIX}settings",
    )
    router.callback_query.register(
        help_handler,
        F.data == f"{CALLBACK_MAIN_PREFIX}help",
    )
    router.callback_query.register(plan, F.data.startswith(CALLBACK_PLAN_PREFIX))
    router.callback_query.register(network, F.data.startswith(CALLBACK_NETWORK_PREFIX))
    router.callback_query.register(
        fixed_margin_prompt,
        F.data == f"{CALLBACK_SETTINGS_PREFIX}fixed_margin",
    )
    router.callback_query.register(
        risk_model,
        F.data.startswith(f"{CALLBACK_SETTINGS_PREFIX}risk:"),
    )
    router.message.register(txid, SubscribeStates.waiting_for_txid)
    router.message.register(api_credentials, ApiCredentialStates.waiting_for_credentials)
    router.message.register(fixed_margin, SettingsStates.waiting_for_fixed_margin)
    return router


async def handle_start(*, message: Any, repository_factory: Any) -> None:
    telegram_id, username = _identity(message)
    with _repository_context(repository_factory) as repository:
        repository.get_or_create_user(
            telegram_id=telegram_id,
            username=username,
            language="en",
        )
    await message.answer(
        f"{t('welcome')}\n\n{t('choose_language')}",
        reply_markup=language_keyboard(),
    )


async def handle_language(
    *,
    callback: Any,
    state: Any,
    repository_factory: Any,
) -> None:
    language = normalize_language(_callback_value(callback, CALLBACK_LANGUAGE_PREFIX))
    telegram_id, _username = _identity(callback)
    with _repository_context(repository_factory) as repository:
        repository.set_language(telegram_id=telegram_id, language=language)
    await state.clear()
    await _edit_callback(
        callback,
        t("main_menu_title", language),
        reply_markup=main_menu_keyboard(),
    )
    await _ack(callback)


async def handle_main_menu(*, callback: Any, state: Any) -> None:
    await state.clear()
    await _edit_callback(
        callback,
        t("main_menu_title"),
        reply_markup=main_menu_keyboard(),
    )
    await _ack(callback)


async def handle_subscribe(*, callback: Any, repository_factory: Any) -> None:
    with _repository_context(repository_factory) as repository:
        plans = repository.list_active_plans()
    await _respond(
        callback,
        f"{t('subscribe_intro')}\n\n{t('choose_plan')}",
        reply_markup=plans_keyboard(plans),
    )
    await _ack_if_callback(callback)


async def handle_plan(
    *,
    callback: Any,
    state: Any,
    repository_factory: Any,
) -> None:
    code = _callback_value(callback, CALLBACK_PLAN_PREFIX).upper()
    with _repository_context(repository_factory) as repository:
        plan = repository.get_plan_by_code(code)
    if plan is None or not plan.is_active:
        await _ack(callback, text="This plan is unavailable.", show_alert=True)
        return
    await state.update_data(plan_code=plan.code)
    await _edit_callback(
        callback,
        t("choose_network"),
        reply_markup=networks_keyboard(),
    )
    await _ack(callback)


async def handle_network(
    *,
    callback: Any,
    state: Any,
    repository_factory: Any,
    config: TelegramBotConfig,
) -> None:
    network = _callback_value(callback, CALLBACK_NETWORK_PREFIX).upper()
    if network not in SUPPORTED_PAYMENT_NETWORKS:
        await _ack(callback, text="Unsupported payment network.", show_alert=True)
        return
    data = await state.get_data()
    plan_code = str(data.get("plan_code", ""))
    with _repository_context(repository_factory) as repository:
        plan = repository.get_plan_by_code(plan_code)
    if plan is None or not plan.is_active:
        await _ack(callback, text="Choose a plan again.", show_alert=True)
        return
    wallet_address = _wallet_for_network(config, network)
    if not wallet_address:
        await _ack(callback, text="This payment network is unavailable.", show_alert=True)
        return
    await state.update_data(
        plan_code=plan.code,
        network=network,
        wallet_address=wallet_address,
    )
    await state.set_state(SubscribeStates.waiting_for_txid)
    await _edit_callback(
        callback,
        t(
            "payment_instructions",
            amount=_decimal_text(plan.price_usdt),
            network=network,
            wallet=wallet_address,
        ),
        reply_markup=wallet_keyboard(network=network, wallet_address=wallet_address),
    )
    await _ack(callback)


async def handle_txid(
    *,
    message: Any,
    state: Any,
    repository_factory: Any,
    publisher: EventPublisher,
    config: TelegramBotConfig,
) -> None:
    txid = str(getattr(message, "text", "") or "").strip()
    if not txid:
        await message.answer("Send a non-empty transaction ID (TXID).")
        return
    data = await state.get_data()
    plan_code = str(data.get("plan_code", ""))
    network = str(data.get("network", ""))
    wallet_address = str(data.get("wallet_address", ""))
    telegram_id, username = _identity(message)
    with _repository_context(repository_factory) as repository:
        user = repository.get_or_create_user(
            telegram_id=telegram_id,
            username=username,
        )
        plan = repository.get_plan_by_code(plan_code)
        if plan is None:
            raise ValueError("selected plan no longer exists")
        _subscription, payment = repository.create_pending_subscription_and_payment(
            user=user,
            plan=plan,
            network=network,
            to_address=wallet_address,
            txid=txid,
        )
        payment_payload = build_payment_submitted_payload(payment)
        admin_payloads = [
            build_notify_admin_payment_submitted_payload(
                admin_telegram_id=admin_id,
                payment=payment,
                user=user,
            )
            for admin_id in config.admin_telegram_ids
        ]
    await publisher.publish(
        stream=PAYMENTS_STREAM,
        event_type="payment.submitted",
        payload=payment_payload,
    )
    for payload in admin_payloads:
        await publisher.publish(
            stream=NOTIFY_STREAM,
            event_type="notify.admin",
            payload=payload,
        )
    await state.clear()
    await message.answer(
        t("payment_submitted"),
        reply_markup=back_to_main_keyboard(),
    )


async def handle_demo(
    *,
    event: Any,
    repository_factory: Any,
    config: TelegramBotConfig,
) -> None:
    telegram_id, username = _identity(event)
    with _repository_context(repository_factory) as repository:
        user = repository.get_or_create_user(
            telegram_id=telegram_id,
            username=username,
        )
        text = _demo_card_text(repository=repository, user=user, config=config)
    await _respond(
        event,
        text,
        reply_markup=demo_stats_keyboard(),
        skip_unchanged=True,
    )
    await _ack_if_callback(event)


async def handle_demo_reset(*, callback: Any) -> None:
    await _edit_callback(
        callback,
        t("demo_reset_confirmation"),
        reply_markup=demo_reset_confirmation_keyboard(),
    )
    await _ack(callback)


async def handle_demo_reset_cancel(*, callback: Any) -> None:
    await _edit_callback(
        callback,
        t("demo_reset_cancelled"),
        reply_markup=None,
    )
    await _ack(callback)


async def handle_demo_reset_confirm(
    *,
    callback: Any,
    repository_factory: Any,
    config: TelegramBotConfig,
) -> None:
    telegram_id, username = _identity(callback)
    with _repository_context(repository_factory) as repository:
        user = repository.get_or_create_user(
            telegram_id=telegram_id,
            username=username,
        )
        deleted_count = repository.reset_demo_for_user(user.id)
        text = _demo_card_text(repository=repository, user=user, config=config)
        LOGGER.info(
            "Reset demo for user_id=%s; deleted %s trade(s)",
            user.id,
            deleted_count,
        )
    await _edit_callback(callback, text, reply_markup=demo_stats_keyboard())
    await _ack(callback)


async def handle_my_subscription(*, event: Any, repository_factory: Any) -> None:
    telegram_id, username = _identity(event)
    with _repository_context(repository_factory) as repository:
        user = repository.get_or_create_user(
            telegram_id=telegram_id,
            username=username,
        )
        subscription = repository.get_active_subscription(user.id)
        if subscription is None:
            subscription = repository.get_latest_subscription(user.id)
        text = _subscription_text(subscription, user.language)
    await _respond(event, text, reply_markup=back_to_main_keyboard())
    await _ack_if_callback(event)


async def handle_connect_api(*, callback: Any, state: Any) -> None:
    await state.set_state(ApiCredentialStates.waiting_for_credentials)
    await _edit_callback(
        callback,
        t("connect_api_warning"),
        reply_markup=back_to_main_keyboard(),
    )
    await _ack(callback)


async def handle_api_credentials(
    *,
    message: Any,
    state: Any,
    repository_factory: Any,
    config: TelegramBotConfig,
) -> None:
    raw_text = str(getattr(message, "text", "") or "")
    try:
        await message.delete()
    except Exception:
        pass
    try:
        parsed = parse_api_credentials(raw_text)
    except ApiCredentialFormatError:
        await message.answer(t("api_credentials_invalid_format"))
        return
    api_key_enc, api_secret_enc = encrypt_api_credentials(
        api_key=parsed.api_key,
        api_secret=parsed.api_secret,
        fernet_key=config.fernet_key,
    )
    telegram_id, username = _identity(message)
    with _repository_context(repository_factory) as repository:
        user = repository.get_or_create_user(
            telegram_id=telegram_id,
            username=username,
        )
        repository.store_exchange_credentials(
            user=user,
            api_key_enc=api_key_enc,
            api_secret_enc=api_secret_enc,
        )
    await state.clear()
    await message.answer(
        t("api_credentials_received"),
        reply_markup=back_to_main_keyboard(),
    )


async def handle_settings(*, event: Any, repository_factory: Any) -> None:
    telegram_id, username = _identity(event)
    with _repository_context(repository_factory) as repository:
        user = repository.get_or_create_user(
            telegram_id=telegram_id,
            username=username,
        )
        settings = repository.get_or_create_user_settings(user=user)
        text = _settings_text(settings, user.language)
    await _respond(event, text, reply_markup=settings_keyboard())
    await _ack_if_callback(event)


async def handle_risk_model(*, callback: Any, repository_factory: Any) -> None:
    raw_model = _callback_value(callback, f"{CALLBACK_SETTINGS_PREFIX}risk:")
    try:
        risk_model = int(raw_model)
    except ValueError:
        await _ack(callback, text="Invalid risk model.", show_alert=True)
        return
    telegram_id, username = _identity(callback)
    with _repository_context(repository_factory) as repository:
        user = repository.get_or_create_user(
            telegram_id=telegram_id,
            username=username,
        )
        settings = repository.update_risk_model(user=user, risk_model=risk_model)
        text = _settings_text(settings, user.language)
    await _edit_callback(callback, text, reply_markup=settings_keyboard())
    await _ack(callback, text="Risk model updated.")


async def handle_fixed_margin_prompt(*, callback: Any, state: Any) -> None:
    await state.set_state(SettingsStates.waiting_for_fixed_margin)
    await _edit_callback(
        callback,
        "Send the fixed margin amount in USDT as a positive number.",
        reply_markup=back_to_main_keyboard(),
    )
    await _ack(callback)


async def handle_fixed_margin(
    *,
    message: Any,
    state: Any,
    repository_factory: Any,
) -> None:
    try:
        value = Decimal(str(getattr(message, "text", "") or "").strip())
    except InvalidOperation:
        await message.answer("Enter a valid positive USDT amount.")
        return
    if not value.is_finite() or value <= 0:
        await message.answer("Enter a valid positive USDT amount.")
        return
    telegram_id, username = _identity(message)
    with _repository_context(repository_factory) as repository:
        user = repository.get_or_create_user(
            telegram_id=telegram_id,
            username=username,
        )
        settings = repository.update_fixed_margin(
            user=user,
            fixed_margin_usdt=value,
        )
        text = _settings_text(settings, user.language)
    await state.clear()
    await message.answer(text, reply_markup=settings_keyboard())


async def handle_help(*, event: Any) -> None:
    await _respond(event, t("help"), reply_markup=back_to_main_keyboard())
    await _ack_if_callback(event)


def _demo_card_text(
    *,
    repository: Any,
    user: Any,
    config: TelegramBotConfig,
) -> str:
    repository.get_or_create_demo_account(
        user=user,
        start_balance_usdt=config.start_balance_usdt,
    )
    get_stats = getattr(repository, "get_demo_stats", None)
    if callable(get_stats):
        try:
            stats_text = format_demo_stats(compute_demo_stats(get_stats(user.id)))
        except (KeyError, TypeError, ValueError):
            stats_text = t("demo_stats_unavailable", user.language)
    else:
        stats_text = t("demo_stats_unavailable", user.language)
    return f"{t('demo_enabled', user.language)}\n\n{stats_text}"


@contextmanager
def _repository_context(factory: Any) -> Iterator[Any]:
    resource = factory() if callable(factory) else factory
    if hasattr(resource, "__enter__") and hasattr(resource, "__exit__"):
        with resource as repository:
            yield repository
    else:
        yield resource


def _identity(event: Any) -> tuple[int, str | None]:
    from_user = getattr(event, "from_user", None)
    if from_user is None:
        raise ValueError("Telegram event is missing from_user")
    return int(from_user.id), getattr(from_user, "username", None)


def _callback_value(callback: Any, prefix: str) -> str:
    data = str(getattr(callback, "data", "") or "")
    if not data.startswith(prefix):
        raise ValueError("unexpected callback data")
    return data[len(prefix) :]


def _wallet_for_network(config: TelegramBotConfig, network: str) -> str:
    return {
        NETWORK_TRC20: config.wallet_trc20,
        NETWORK_BEP20: config.wallet_bep20,
        NETWORK_POLYGON: config.wallet_polygon,
    }[network]


def _subscription_text(subscription: Any | None, language: str) -> str:
    if subscription is None:
        return t("my_subscription_none", language)
    status = str(subscription.status)
    if status == "pending":
        return t("my_subscription_pending", language)
    ends_at = getattr(subscription, "ends_at", None)
    formatted_end = "unknown"
    if ends_at is not None:
        if ends_at.tzinfo is None or ends_at.utcoffset() is None:
            raise ValueError("subscription end must be timezone-aware")
        formatted_end = ends_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
    if status == "active" and (
        ends_at is None or ends_at.astimezone(UTC) > datetime.now(UTC)
    ):
        return t("my_subscription_active", language, ends_at=formatted_end)
    return t("my_subscription_expired", language, ends_at=formatted_end)


def _settings_text(settings: Any, language: str) -> str:
    return t(
        "settings_title",
        language,
        fixed_margin=_decimal_text(settings.fixed_margin_usdt),
        risk_model=settings.risk_model,
        max_concurrent=settings.max_concurrent,
    )


async def _respond(
    event: Any,
    text: str,
    *,
    reply_markup: Any,
    skip_unchanged: bool = False,
) -> None:
    message = getattr(event, "message", None)
    if message is not None:
        if skip_unchanged and getattr(message, "text", None) == text:
            return
        await message.edit_text(text, reply_markup=reply_markup)
    else:
        await event.answer(text, reply_markup=reply_markup)


async def _edit_callback(callback: Any, text: str, *, reply_markup: Any) -> None:
    message = getattr(callback, "message", None)
    if message is None:
        raise ValueError("callback has no accessible message")
    await message.edit_text(text, reply_markup=reply_markup)


async def _ack_if_callback(event: Any) -> None:
    if getattr(event, "message", None) is not None:
        await _ack(event)


async def _ack(
    callback: Any,
    *,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    await callback.answer(text=text, show_alert=show_alert)


def _secret_value(value: Any) -> str:
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if callable(getter) else value)


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")
