from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from aiogram.types import CopyTextButton
except ImportError:  # pragma: no cover - compatibility with older aiogram v3
    CopyTextButton = None  # type: ignore[assignment,misc]

from services.telegram_bot.constants import (
    CALLBACK_DEMO_RESET,
    CALLBACK_DEMO_RESET_CANCEL,
    CALLBACK_DEMO_RESET_CONFIRM,
    CALLBACK_LANGUAGE_PREFIX,
    CALLBACK_MAIN_PREFIX,
    CALLBACK_NETWORK_PREFIX,
    CALLBACK_PLAN_PREFIX,
    CALLBACK_SETTINGS_PREFIX,
    SUPPORTED_PAYMENT_NETWORKS,
)
from services.telegram_bot.i18n import t
from shared.models import Plan


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="English",
                    callback_data=f"{CALLBACK_LANGUAGE_PREFIX}en",
                )
            ]
        ]
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    items = (
        ("Subscribe", "subscribe"),
        ("Run Demo", "demo"),
        ("My Subscription", "subscription"),
        ("Connect Exchange API", "api"),
        ("Settings", "settings"),
        ("Help", "help"),
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{CALLBACK_MAIN_PREFIX}{action}",
                )
            ]
            for label, action in items
        ]
    )


def plans_keyboard(plans: Iterable[Plan]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for plan in plans:
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{plan.code} - {plan.duration_days} days - "
                        f"{_decimal_text(plan.price_usdt)} USDT"
                    ),
                    callback_data=f"{CALLBACK_PLAN_PREFIX}{plan.code}",
                )
            ]
        )
    rows.append([_back_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def networks_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=network,
                callback_data=f"{CALLBACK_NETWORK_PREFIX}{network}",
            )
        ]
        for network in SUPPORTED_PAYMENT_NETWORKS
    ]
    rows.append([_back_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wallet_keyboard(*, network: str, wallet_address: str) -> InlineKeyboardMarkup:
    """Return wallet keyboard with a copy-address button where supported."""
    if CopyTextButton is not None:
        copy_button = InlineKeyboardButton(
            text="Copy address",
            copy_text=CopyTextButton(text=wallet_address),
        )
    else:  # pragma: no cover - compatibility with older aiogram v3
        copy_button = InlineKeyboardButton(
            text=f"Copy address: {wallet_address}",
            callback_data=f"copy:{network}",
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[copy_button], [_back_button()]],
    )


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Risk model {model}",
                    callback_data=f"{CALLBACK_SETTINGS_PREFIX}risk:{model}",
                )
                for model in (1, 2, 3)
            ],
            [
                InlineKeyboardButton(
                    text="Set fixed margin",
                    callback_data=f"{CALLBACK_SETTINGS_PREFIX}fixed_margin",
                )
            ],
            [_back_button()],
        ]
    )


def back_to_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_back_button()]])


def demo_stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("demo_refresh_button"),
                    callback_data=f"{CALLBACK_MAIN_PREFIX}demo",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t("demo_reset_button"),
                    callback_data=CALLBACK_DEMO_RESET,
                )
            ],
            [_back_button()],
        ]
    )


def demo_reset_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("demo_reset_confirm_button"),
                    callback_data=CALLBACK_DEMO_RESET_CONFIRM,
                ),
                InlineKeyboardButton(
                    text=t("demo_reset_cancel_button"),
                    callback_data=CALLBACK_DEMO_RESET_CANCEL,
                ),
            ]
        ]
    )


def _back_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="Back to main menu",
        callback_data=f"{CALLBACK_MAIN_PREFIX}menu",
    )


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")
