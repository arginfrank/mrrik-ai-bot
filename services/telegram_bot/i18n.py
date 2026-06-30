from __future__ import annotations

from typing import Any

from services.telegram_bot.constants import DEFAULT_LANGUAGE


SUPPORTED_LANGUAGES = ("en",)

_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "welcome": "Welcome to MRRIK AI bot.",
        "choose_language": "Choose your language:",
        "main_menu_title": "Main menu",
        "subscribe_intro": "Choose a subscription plan to continue.",
        "choose_plan": "Choose a plan:",
        "choose_network": "Choose the USDT payment network:",
        "payment_instructions": (
            "Send exactly {amount} USDT on {network} to:\n"
            "{wallet}\n\n"
            "After paying, send the transaction ID (TXID) here."
        ),
        "payment_submitted": (
            "Payment submitted and awaiting review. Your subscription is not active yet."
        ),
        "my_subscription_none": "You do not have a subscription yet.",
        "my_subscription_pending": "Your subscription payment is awaiting review.",
        "my_subscription_active": "Your subscription is active until {ends_at} UTC.",
        "my_subscription_expired": "Your subscription expired on {ends_at} UTC.",
        "connect_api_warning": (
            "Futures trading involves substantial risk, and past or demo performance does "
            "not guarantee future results.\n\n"
            "Send your Binance API key and API secret in one message, either on two lines "
            "or labeled as api_key and api_secret.\n\n"
            "Futures must be enabled. Withdrawals MUST be disabled. The message will be "
            "deleted immediately after reading."
        ),
        "api_credentials_received": (
            "API credentials received securely for verification. They are not marked valid "
            "or tradable yet."
        ),
        "api_credentials_invalid_format": (
            "Invalid format. Send exactly two values: API key on the first line and API "
            "secret on the second line, or use api_key: and api_secret: labels."
        ),
        "settings_title": (
            "Settings\n"
            "Fixed margin: {fixed_margin} USDT\n"
            "Risk model: {risk_model}\n"
            "Maximum concurrent trades: {max_concurrent}"
        ),
        "demo_created": "Your demo account is ready.",
        "demo_enabled": (
            "Demo account ENABLED. You will receive demo OPEN and CLOSE alerts for "
            "future signals."
        ),
        "demo_refresh_button": "Refresh demo stats",
        "demo_reset_button": "🔄 Reset demo",
        "demo_reset_confirmation": (
            "⚠️ This will permanently delete all your demo trades and reset your demo "
            "balance to the starting amount. Your settings (margin, risk model, leverage) "
            "are kept. Continue?"
        ),
        "demo_reset_confirm_button": "✅ Yes, reset",
        "demo_reset_cancel_button": "❌ Cancel",
        "demo_reset_cancelled": "Reset cancelled.",
        "demo_stats_unavailable": "Demo stats are currently unavailable.",
        "help": (
            "Use Subscribe to submit a subscription payment, Run Demo to view simulated "
            "performance, My Subscription for status, Connect Exchange API to securely "
            "submit Binance credentials, and Settings to adjust risk preferences."
        ),
        "expiry_24h": (
            "Your subscription expires within 24 hours. After expiry, no new trades will "
            "be opened; currently open trades remain until their own SL/TP."
        ),
        "subscription_expired": (
            "Subscription ended. No new trades will be opened; your currently open trades "
            "remain until their own SL/TP."
        ),
    }
}


def normalize_language(language: str | None) -> str:
    """Return a supported language code, defaulting to en."""
    if language is None:
        return DEFAULT_LANGUAGE
    normalized = language.strip().lower().split("-", maxsplit=1)[0]
    return normalized if normalized in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def t(key: str, lang: str | None = None, **kwargs: Any) -> str:
    """Translate a text key and format with kwargs."""
    language = normalize_language(lang)
    try:
        template = _TEXTS[language][key]
    except KeyError as error:
        raise KeyError(f"unknown translation key: {key}") from error
    return template.format(**kwargs)
