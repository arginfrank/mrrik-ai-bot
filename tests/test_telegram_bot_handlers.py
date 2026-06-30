from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from cryptography.fernet import Fernet

from services.telegram_bot.handlers import (
    TelegramBotConfig,
    handle_api_credentials,
    handle_demo,
    handle_demo_reset,
    handle_demo_reset_cancel,
    handle_demo_reset_confirm,
    handle_language,
    handle_network,
    handle_plan,
    handle_risk_model,
    handle_start,
    handle_txid,
)
from services.telegram_bot.states import SubscribeStates
from shared.models import Payment, Plan, Subscription, User, UserSetting


class FakeState:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.state: object | None = None
        self.cleared = False

    async def update_data(self, **kwargs: Any) -> None:
        self.data.update(kwargs)

    async def get_data(self) -> dict[str, Any]:
        return dict(self.data)

    async def set_state(self, state: object) -> None:
        self.state = state

    async def clear(self) -> None:
        self.data.clear()
        self.state = None
        self.cleared = True


class FakeMessage:
    def __init__(self, text: str = "", *, telegram_id: int = 100) -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=telegram_id, username="tester")
        self.answers: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.deleted = False

    async def answer(self, text: str, *, reply_markup: object | None = None) -> None:
        self.answers.append({"text": text, "reply_markup": reply_markup})

    async def edit_text(self, text: str, *, reply_markup: object | None = None) -> None:
        self.edits.append({"text": text, "reply_markup": reply_markup})

    async def delete(self) -> None:
        self.deleted = True


class FakeCallback:
    def __init__(self, data: str, *, telegram_id: int = 100) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=telegram_id, username="tester")
        self.message = FakeMessage(telegram_id=telegram_id)
        self.acks: list[dict[str, Any]] = []

    async def answer(
        self,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        self.acks.append({"text": text, "show_alert": show_alert})


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(
        self,
        *,
        stream: str,
        event_type: str,
        payload: dict,
    ) -> object:
        event = {"stream": stream, "event_type": event_type, "payload": payload}
        self.events.append(event)
        return event


class FakeRepository:
    def __init__(self) -> None:
        self.user = User(id=1, telegram_id=100, username="tester", language="en")
        self.plan = Plan(
            id=2,
            code="P30",
            duration_days=30,
            price_usdt=Decimal("49"),
            is_active=True,
        )
        self.settings = UserSetting(
            user_id=1,
            fixed_margin_usdt=Decimal("10"),
            risk_model=1,
            model3_exit_roi_pct=Decimal("20"),
            max_concurrent=10,
            leverage_mode="signal",
        )
        self.created_users = 0
        self.demo_account_calls = 0
        self.demo_stats_calls = 0
        self.demo_reset_user_ids: list[int] = []
        self.demo_account = SimpleNamespace(user_id=self.user.id)
        self.credentials: tuple[bytes, bytes] | None = None
        self.payment: Payment | None = None

    def get_or_create_user(self, **kwargs: Any) -> User:
        self.created_users += 1
        self.user.telegram_id = kwargs["telegram_id"]
        return self.user

    def set_language(self, *, telegram_id: int, language: str) -> User:
        assert telegram_id == self.user.telegram_id
        self.user.language = language
        return self.user

    def list_active_plans(self) -> list[Plan]:
        return [self.plan]

    def get_plan_by_code(self, code: str) -> Plan | None:
        return self.plan if code == self.plan.code else None

    def create_pending_subscription_and_payment(self, **kwargs: Any) -> tuple[Subscription, Payment]:
        subscription = Subscription(
            id=3,
            user_id=self.user.id,
            plan_id=self.plan.id,
            status="pending",
        )
        self.payment = Payment(
            id=4,
            user_id=self.user.id,
            plan_id=self.plan.id,
            user=self.user,
            plan=self.plan,
            network=kwargs["network"],
            to_address=kwargs["to_address"],
            amount_expected=self.plan.price_usdt,
            txid=kwargs["txid"],
            status="submitted",
        )
        return subscription, self.payment

    def get_or_create_demo_account(self, **kwargs: Any) -> object:
        self.demo_account_calls += 1
        return self.demo_account

    def get_demo_stats(self, user_id: int) -> dict[str, Any]:
        self.demo_stats_calls += 1
        return {
            "start_balance_usdt": Decimal("1000"),
            "current_balance_usdt": Decimal("1000"),
            "fixed_margin_usdt": Decimal("10"),
            "trades": [],
        }

    def reset_demo_for_user(self, user_id: int) -> int:
        self.demo_reset_user_ids.append(user_id)
        return 3

    def store_exchange_credentials(
        self,
        *,
        user: User,
        api_key_enc: bytes,
        api_secret_enc: bytes,
    ) -> object:
        self.credentials = (api_key_enc, api_secret_enc)
        return SimpleNamespace(scope_verified=False, is_valid=False)

    def update_risk_model(self, *, user: User, risk_model: int) -> UserSetting:
        self.settings.risk_model = risk_model
        return self.settings


def test_start_creates_user_and_returns_language_selection() -> None:
    repository = FakeRepository()
    message = FakeMessage()

    asyncio.run(handle_start(message=message, repository_factory=lambda: repository))

    assert repository.created_users == 1
    assert "Choose your language" in message.answers[0]["text"]


def test_language_callback_persists_and_shows_main_menu() -> None:
    repository = FakeRepository()
    callback = FakeCallback("lang:en")
    state = FakeState()

    asyncio.run(
        handle_language(
            callback=callback,
            state=state,
            repository_factory=lambda: repository,
        )
    )

    assert repository.user.language == "en"
    assert callback.message.edits[0]["text"] == "Main menu"


def test_plan_network_and_txid_submission_flow() -> None:
    repository = FakeRepository()
    publisher = FakePublisher()
    state = FakeState()
    config = _config(admin_ids=(900, 901))
    plan_callback = FakeCallback("plan:P30")
    network_callback = FakeCallback("network:TRC20")

    asyncio.run(
        handle_plan(
            callback=plan_callback,
            state=state,
            repository_factory=lambda: repository,
        )
    )
    asyncio.run(
        handle_network(
            callback=network_callback,
            state=state,
            repository_factory=lambda: repository,
            config=config,
        )
    )

    assert state.state == SubscribeStates.waiting_for_txid
    assert state.data["wallet_address"] == "trc-wallet"
    assert "send the transaction id" in network_callback.message.edits[0]["text"].lower()

    message = FakeMessage("txid-123")
    asyncio.run(
        handle_txid(
            message=message,
            state=state,
            repository_factory=lambda: repository,
            publisher=publisher,
            config=config,
        )
    )

    assert repository.payment is not None
    assert repository.payment.status == "submitted"
    assert [event["event_type"] for event in publisher.events] == [
        "payment.submitted",
        "notify.admin",
        "notify.admin",
    ]
    assert "awaiting review" in message.answers[0]["text"].lower()


def test_api_credentials_are_deleted_encrypted_and_stored() -> None:
    repository = FakeRepository()
    state = FakeState()
    message = FakeMessage("api-key\napi-secret")

    asyncio.run(
        handle_api_credentials(
            message=message,
            state=state,
            repository_factory=lambda: repository,
            config=_config(),
        )
    )

    assert message.deleted is True
    assert repository.credentials is not None
    assert b"api-key" not in repository.credentials[0]
    assert b"api-secret" not in repository.credentials[1]
    assert "received" in message.answers[0]["text"].lower()


def test_invalid_api_credentials_are_deleted_and_rejected() -> None:
    repository = FakeRepository()
    message = FakeMessage("one-value")

    asyncio.run(
        handle_api_credentials(
            message=message,
            state=FakeState(),
            repository_factory=lambda: repository,
            config=_config(),
        )
    )

    assert message.deleted is True
    assert repository.credentials is None
    assert "invalid format" in message.answers[0]["text"].lower()


def test_demo_handler_displays_stats() -> None:
    repository = FakeRepository()
    message = FakeMessage()

    asyncio.run(
        handle_demo(
            event=message,
            repository_factory=lambda: repository,
            config=_config(),
        )
    )

    text = message.answers[0]["text"]
    assert "Demo account ENABLED" in text
    assert "OPEN and CLOSE alerts for future signals" in text
    assert "Demo stats" in text
    callbacks = {
        button.callback_data
        for row in message.answers[0]["reply_markup"].inline_keyboard
        for button in row
    }
    assert callbacks == {"main:demo", "demo:reset", "main:menu"}


def test_demo_refresh_reuses_idempotent_account_and_rebuilds_stats() -> None:
    repository = FakeRepository()
    callback = FakeCallback("main:demo")

    asyncio.run(
        handle_demo(
            event=FakeMessage(),
            repository_factory=lambda: repository,
            config=_config(),
        )
    )
    asyncio.run(
        handle_demo(
            event=callback,
            repository_factory=lambda: repository,
            config=_config(),
        )
    )

    assert repository.demo_account_calls == 2
    assert callback.message.edits[0]["text"].endswith("Net profit: +0.00 USDT (+0.0%)")
    assert callback.acks == [{"text": None, "show_alert": False}]


def test_demo_refresh_acknowledges_unchanged_stats_without_invalid_edit() -> None:
    repository = FakeRepository()
    message = FakeMessage()
    asyncio.run(
        handle_demo(
            event=message,
            repository_factory=lambda: repository,
            config=_config(),
        )
    )
    callback = FakeCallback("main:demo")
    callback.message.text = message.answers[0]["text"]

    asyncio.run(
        handle_demo(
            event=callback,
            repository_factory=lambda: repository,
            config=_config(),
        )
    )

    assert repository.demo_stats_calls == 2
    assert callback.message.edits == []
    assert callback.acks == [{"text": None, "show_alert": False}]


def test_demo_reset_requires_confirmation_before_deleting() -> None:
    repository = FakeRepository()
    callback = FakeCallback("demo:reset")

    asyncio.run(handle_demo_reset(callback=callback))

    assert repository.demo_reset_user_ids == []
    edit = callback.message.edits[0]
    assert edit["text"] == (
        "⚠️ This will permanently delete all your demo trades and reset your demo "
        "balance to the starting amount. Your settings (margin, risk model, leverage) "
        "are kept. Continue?"
    )
    callbacks = {
        button.callback_data
        for row in edit["reply_markup"].inline_keyboard
        for button in row
    }
    assert callbacks == {"demo:reset:confirm", "demo:reset:cancel"}


def test_demo_reset_cancel_edits_message_without_deleting() -> None:
    callback = FakeCallback("demo:reset:cancel")

    asyncio.run(handle_demo_reset_cancel(callback=callback))

    assert callback.message.edits == [
        {"text": "Reset cancelled.", "reply_markup": None}
    ]
    assert callback.acks == [{"text": None, "show_alert": False}]


def test_demo_reset_confirm_deletes_and_displays_zeroed_stats() -> None:
    repository = FakeRepository()
    callback = FakeCallback("demo:reset:confirm")

    asyncio.run(
        handle_demo_reset_confirm(
            callback=callback,
            repository_factory=lambda: repository,
            config=_config(),
        )
    )

    assert repository.demo_reset_user_ids == [repository.user.id]
    assert callback.message.edits[0]["text"].endswith(
        "Net profit: +0.00 USDT (+0.0%)"
    )
    assert callback.acks == [{"text": None, "show_alert": False}]


def test_settings_risk_model_callback_updates_model() -> None:
    repository = FakeRepository()
    callback = FakeCallback("settings:risk:3")

    asyncio.run(
        handle_risk_model(
            callback=callback,
            repository_factory=lambda: repository,
        )
    )

    assert repository.settings.risk_model == 3
    assert "Risk model: 3" in callback.message.edits[0]["text"]


def _config(*, admin_ids: tuple[int, ...] = ()) -> TelegramBotConfig:
    return TelegramBotConfig(
        start_balance_usdt=Decimal("1000"),
        wallet_trc20="trc-wallet",
        wallet_bep20="bep-wallet",
        wallet_polygon="polygon-wallet",
        fernet_key=Fernet.generate_key().decode("utf-8"),
        admin_telegram_ids=admin_ids,
    )
