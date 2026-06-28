from __future__ import annotations

from services.telegram_bot.i18n import normalize_language, t


def test_language_normalization_defaults_to_english() -> None:
    assert normalize_language(None) == "en"
    assert normalize_language("fr") == "en"


def test_english_translation_and_formatting() -> None:
    assert t("main_menu_title") == "Main menu"
    rendered = t(
        "payment_instructions",
        amount="49",
        network="TRC20",
        wallet="wallet-address",
    )
    assert "49 USDT" in rendered
    assert "wallet-address" in rendered
    assert "ENABLED" in t("demo_enabled")
    assert "OPEN and CLOSE alerts" in t("demo_enabled")
    assert t("demo_refresh_button") == "Refresh demo stats"
