from __future__ import annotations

from decimal import Decimal

from services.telegram_bot.keyboards import (
    demo_stats_keyboard,
    language_keyboard,
    main_menu_keyboard,
    networks_keyboard,
    plans_keyboard,
    wallet_keyboard,
)
from shared.models import Plan


def test_language_keyboard_includes_english() -> None:
    keyboard = language_keyboard()
    assert any(button.text == "English" for button in _buttons(keyboard))


def test_main_menu_includes_all_customer_actions() -> None:
    labels = {button.text for button in _buttons(main_menu_keyboard())}
    assert labels.issuperset(
        {
            "Subscribe",
            "Run Demo",
            "My Subscription",
            "Connect Exchange API",
            "Settings",
            "Help",
        }
    )


def test_demo_stats_keyboard_refreshes_and_returns_to_main_menu() -> None:
    buttons = _buttons(demo_stats_keyboard())
    assert [(button.text, button.callback_data) for button in buttons] == [
        ("Refresh demo stats", "main:demo"),
        ("Back to main menu", "main:menu"),
    ]


def test_plans_and_network_keyboards_use_expected_callbacks() -> None:
    plans = (
        Plan(code="P30", duration_days=30, price_usdt=Decimal("49"), is_active=True),
        Plan(code="P90", duration_days=90, price_usdt=Decimal("129"), is_active=True),
    )
    plan_callbacks = {button.callback_data for button in _buttons(plans_keyboard(plans))}
    assert {"plan:P30", "plan:P90"}.issubset(plan_callbacks)

    network_callbacks = {
        button.callback_data for button in _buttons(networks_keyboard())
    }
    assert {"network:TRC20", "network:BEP20", "network:POLYGON"}.issubset(
        network_callbacks
    )


def test_wallet_keyboard_contains_copyable_address() -> None:
    address = "TTestWalletAddress123"
    keyboard = wallet_keyboard(network="TRC20", wallet_address=address)
    assert address in keyboard.model_dump_json()


def _buttons(keyboard: object) -> list[object]:
    return [
        button
        for row in getattr(keyboard, "inline_keyboard")
        for button in row
    ]
